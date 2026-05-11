"""
================================================================================
R3 ADDRESS ACCURACY — MODEL TRAINING MODULE
================================================================================

PURPOSE
-------
Trains the two LightGBM models that power the prediction pipeline:

  Model A — P(R3 is wrong)
    * Target: y = 1 if R3 disagrees with Call QC (defined only on records
              where both R3 and Call QC are conclusive)
    * Used to: identify which records should be corrected or sent to call
    * Expected CV AUC: ~0.82
    * Expected precision @ top-450: ~87%

  Model B — P(call yields conclusive verdict)
    * Target: y = 1 if Call QC = ACCURATE or INACCURATE (defined on all records)
    * Used to: filter the call pool — don't waste calls on records that
              won't yield a verdict (e.g. complex hospital IVRs)
    * Expected CV AUC: ~0.96

ARCHITECTURE WHY
----------------
Two specialized models instead of one because:
  1. Triage decisions and correction decisions depend on different features
  2. The combined triage score is P(R3 wrong) x P(call conclusive) — a
     multiplicative combination that requires factoring
  3. Model B reaches AUC 0.96 because organization characteristics cleanly
     predict pickup rate; mixing it into Model A would dilute that

WHY LIGHTGBM
------------
  * Handles mixed numeric + categorical features natively
  * Handles missing values without imputation
  * Captures non-linear interactions automatically (e.g. taxonomyXno_claims)
  * Robust at this data size (~2k rows) without overfitting
  * Free feature importance for explanation

INPUT
-----
1. Path to base data Excel file (R3_BASEDATA)
2. Path to claims aggregate CSV (output of Snowflake aggregation query)

OUTPUT
------
1. models.pkl — pickled bundle containing both trained models, feature
                schema, and CV metrics for production deployment
2. oof_predictions.csv — out-of-fold predictions for evaluation

EXAMPLE USAGE
-------------
    python train.py base_data.xlsx claims_aggregates.csv
    # produces models.pkl and oof_predictions.csv in current directory
"""

import sys
import pickle
import warnings

import numpy as np
import pandas as pd
import lightgbm as lgb
import matplotlib.pyplot as plt
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, f1_score
from sklearn.isotonic import IsotonicRegression
from features import build_features, merge_claims, normalize_r3_label, load_config
from llm import call_llm,prompt_template
from shapAnalysis import run_shap_analysis, zone_accuracy_analysis
import shap
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================

RANDOM_STATE = 42
N_FOLDS = 5

# LightGBM hyperparameters (lightly tuned, robust at default-ish values)
LGBM_PARAMS = dict(
    n_estimators=800,         # high upper bound, early stopping picks the actual count
    learning_rate=0.04,       # moderate; balances training speed and stability
    num_leaves=31,            # default; matches max_depth=5
    min_child_samples=20,     # prevents overfitting on small leaves
    reg_alpha=0.1,            # L1 regularization
    reg_lambda=0.1,           # L2 regularization
    subsample=0.85,           # row subsampling for variance reduction
    colsample_bytree=0.85,    # feature subsampling
    random_state=RANDOM_STATE,
    n_jobs=-1,
    verbose=-1,
    importance_type='gain',
)


# ============================================================================
# DATA PREP
# ============================================================================

def prepare_X(features_df, config=None):
    """Convert the feature DataFrame into a model-ready matrix.
    
    Casts categorical features to pandas category dtype so LightGBM treats
    them natively. Coerces all other features to numeric and fills NaNs
    with -1 (sentinel that LightGBM splits cleanly).
    """
    if config is None:
        config = load_config()
    cat_list = config.get('categorical_features', [])
    X = features_df.copy()
    cat_cols = [c for c in cat_list if c in X.columns]
    for c in cat_cols:
        X[c] = X[c].astype(str).astype('category')
    for c in X.columns:
        if c not in cat_cols:
            X[c] = pd.to_numeric(X[c], errors='coerce').fillna(-1)
    return X, cat_cols


# ============================================================================
# CV TRAINING WITH OOF PREDICTIONS
# ============================================================================

# def cv_train(X, y, mask, cat_cols, name,sample_weights = None):
#     """Train with stratified k-fold CV; refit on full subset for inference.
    
#     Args:
#         X: full feature matrix (n_records, n_features)
#         y: target series (may contain NaN for unlabeled records)
#         mask: boolean mask of records with valid target
#         cat_cols: list of categorical feature names
#         name: human-readable model name for logging
    
#     Returns:
#         oof: out-of-fold predictions (length n_records, NaN where mask=False)
#         final: model refit on all masked data, ready for production inference
#         mean_auc: cross-validation mean AUC
#     """
#     print(f"\n{'='*70}\nMODEL: {name}\n{'='*70}")
#     if sample_weights is not None:
#         sample_weights_m = sample_weights[mask]
#     else:
#         sample_weights_m = None
#     X_m = X[mask].reset_index(drop=True)
#     y_m = y[mask].astype(int).reset_index(drop=True)
#     idx_m = np.where(mask)[0]
    
#     oof = np.full(len(X), np.nan)
#     skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)
#     aucs, aps = [], []
    
#     for fold, (tr, va) in enumerate(skf.split(X_m, y_m), 1):
#         m = lgb.LGBMClassifier(**LGBM_PARAMS)
#         if sample_weights_m is not None:
#             train_weight = sample_weights_m[tr]
#             eval_weight = [sample_weights_m[va]] # Must be a list to match eval_set
#         else:
#             train_weight = None
#             eval_weight = None
#         m.fit(X_m.iloc[tr], y_m.iloc[tr],
#               eval_set=[(X_m.iloc[va], y_m.iloc[va])],
#               categorical_feature=cat_cols,
#               eval_sample_weight=eval_weight,
#               sample_weight=train_weight,
#               callbacks=[lgb.early_stopping(50, verbose=False)])
#         p = m.predict_proba(X_m.iloc[va])[:, 1]
#         oof[idx_m[va]] = p
#         a = roc_auc_score(y_m.iloc[va], p)
#         ap = average_precision_score(y_m.iloc[va], p)
#         aucs.append(a)
#         aps.append(ap)
#         print(f"  Fold {fold}: AUC={a:.4f}  AP={ap:.4f}")
    
#     mean_auc = np.mean(aucs)
#     print(f"  CV mean: AUC={mean_auc:.4f} (±{np.std(aucs):.4f})  AP={np.mean(aps):.4f}")
#     print(np.isnan(oof))
#     valid = ~np.isnan(oof)
#     y_valid = y[valid].astype(int)
#     oof_valid = oof[valid]

#     pr_auc = average_precision_score(y_valid, oof_valid)

#     print(f"OOF PR-AUC for positive cases (R3 wrong): {pr_auc:.4f}")
#     y_valid_neg = 1 - y_valid
#     oof_valid_neg = 1 - oof_valid
#     print(f"OOF PR-AUC for negative cases (R3 right) : {average_precision_score(y_valid_neg, oof_valid_neg)}")
#     calibrator = IsotonicRegression(out_of_bounds='clip')
#     calibrator.fit(oof_valid, y_valid)
#     oof_cal = calibrator.transform(oof_valid)
#     brier = brier_score_loss(y_valid, oof_cal)
#     print(f"Brier Score (earlier): {brier_score_loss(y_valid, oof_valid):.4f}")
#     print(f"Brier Score (calibrated): {brier:.4f}")
#     print(f"OOF PR-AUC  calibrated for postives cases (R3 wrong) : {average_precision_score(y_valid, oof_cal)}")
#     print(f"OOF PR-AUC  calibrated for negative cases (R3 right) : {average_precision_score(y_valid_neg, 1- oof_cal)}")
#     best_f1 = 0
#     best_t = 0
#     for t in np.linspace(0.0, 1.0, 101):
#         preds = (oof_cal >= t).astype(int)
#         f1 = f1_score(y_valid, preds)
        
#         if f1 > best_f1:
#             best_f1 = f1
#             best_t = t

#     print(f"Best Threshold: {best_t:.3f}  F1: {best_f1:.4f}")
#     df_compare = pd.DataFrame({
#     "raw": oof_valid,
#     "calibrated": oof_cal
#     })

#     print(df_compare.head(20))
#     df_compare["diff"] = df_compare["calibrated"] - df_compare["raw"]
#     print(df_compare.describe())
#     plt.scatter(oof_valid, oof_cal, alpha=0.3)
#     plt.xlabel("Raw Probability")
#     plt.ylabel("Calibrated Probability")
#     plt.title("Calibration Effect")
#     plt.show()
    
#     # Refit on full subset for production inference
#     final = lgb.LGBMClassifier(**LGBM_PARAMS)
#     final.fit(X_m, y_m, categorical_feature=cat_cols)
#     explainer = shap.TreeExplainer(final)
#     shap_values_oof = explainer.shap_values(X_m)[1]
#     return oof_cal, final, mean_auc
# ============================================================================
# UPDATED cv_train() — drop this into your existing train.py
# Only the cv_train function changes. Everything else stays the same.
# ============================================================================

# Add this import at the top of train.py (alongside your existing imports):
# from shap_analysis import run_shap_analysis, zone_accuracy_analysis
def get_top_reasons(shap_row, feature_names, n=3):
    pairs = sorted(
        zip(feature_names, shap_row),
        key=lambda x: abs(x[1]),
        reverse=True
    )[:n]
    reasons = []
    for feat, val in pairs:
        direction = "wrong" if val > 0 else "right"
        reasons.append(f"{feat}({'+' if val>0 else ''}{val:.2f}→{direction})")
    return " | ".join(reasons)
def cv_train(X, y, mask, cat_cols, name, sample_weights=None):
    """Train with stratified k-fold CV; refit on full subset for inference.

    Args:
        X             : full feature matrix (n_records, n_features)
        y             : target series (may contain NaN for unlabeled records)
        mask          : boolean mask of records with valid target
        cat_cols      : list of categorical feature names
        name          : human-readable model name for logging
        sample_weights: optional per-record weights (Zone 1 = 3x to protect agreement zone)

    Returns:
        oof      : out-of-fold predictions (length n_records, NaN where mask=False)
        final    : model refit on all masked data, ready for production inference
        mean_auc : cross-validation mean AUC
    """
    print(f"\n{'='*70}\nMODEL: {name}\n{'='*70}")

    if sample_weights is not None:
        sample_weights_m = sample_weights[mask]
    else:
        sample_weights_m = None

    X_m    = X[mask].reset_index(drop=True)
    y_m    = y[mask].astype(int).reset_index(drop=True)
    idx_m  = np.where(mask)[0]

    oof = np.full(len(X), np.nan)
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    aucs, aps = [], []
    oof_shap = np.zeros((len(X_m),X_m.shape[1])) #shape n_records, n_features
    for fold, (tr, va) in enumerate(skf.split(X_m, y_m), 1):
        m = lgb.LGBMClassifier(**LGBM_PARAMS)

        if sample_weights_m is not None:
            train_weight = sample_weights_m[tr]
            eval_weight  = [sample_weights_m[va]]
        else:
            train_weight = None
            eval_weight  = None

        m.fit(
            X_m.iloc[tr], y_m.iloc[tr],
            eval_set=[(X_m.iloc[va], y_m.iloc[va])],
            categorical_feature=cat_cols,
            eval_sample_weight=eval_weight,
            sample_weight=train_weight,
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )

        p  = m.predict_proba(X_m.iloc[va])[:, 1]
        oof[idx_m[va]] = p
        
        #COMPUTE SHAP FOR VALIDATION FOLD ONLY
        fold_explainer = shap.TreeExplainer(m)
        fold_shap       = fold_explainer.shap_values(X_m.iloc[va])
        if isinstance(fold_shap, list):
            fold_shap = fold_shap[1] 
        oof_shap[va] = fold_shap
        
        
        a  = roc_auc_score(y_m.iloc[va], p)
        ap = average_precision_score(y_m.iloc[va], p)
        aucs.append(a)
        aps.append(ap)
        print(f"  Fold {fold}: AUC={a:.4f}  AP={ap:.4f}")

    mean_auc = np.mean(aucs)
    print(f"  CV mean: AUC={mean_auc:.4f} (±{np.std(aucs):.4f})  AP={np.mean(aps):.4f}")

    # ── Calibration ───────────────────────────────────────────
    valid     = ~np.isnan(oof)
    y_valid   = y[valid].astype(int)
    oof_valid = oof[valid]

    calibrator = IsotonicRegression(out_of_bounds='clip')
    calibrator.fit(oof_valid, y_valid)
    oof_cal = calibrator.transform(oof_valid)

    print(f"  Brier Score raw       : {brier_score_loss(y_valid, oof_valid):.4f}")
    print(f"  Brier Score calibrated: {brier_score_loss(y_valid, oof_cal):.4f}")

    # ── Best threshold by F1 ──────────────────────────────────
    best_f1, best_t = 0, 0
    for t in np.linspace(0.0, 1.0, 101):
        preds = (oof_cal >= t).astype(int)
        f1    = f1_score(y_valid, preds)
        if f1 > best_f1:
            best_f1, best_t = f1, t
    print(f"  Best Threshold: {best_t:.3f}  F1: {best_f1:.4f}")

    # ── Zone accuracy check ───────────────────────────────────
    # This is the hackathon's key metric — check BEFORE declaring success
    # Zone 1 = R3 correct records → must stay above 95% accuracy
    y_pred_binary = (oof_cal >= best_t).astype(int)
    zone_accuracy_analysis(y_valid, y_pred_binary, threshold=0.95)

    # ── Refit final model on full masked data ─────────────────
    final = lgb.LGBMClassifier(**LGBM_PARAMS)
    final.fit(X_m, y_m, categorical_feature=cat_cols,
              sample_weight=sample_weights_m)

    # ── SHAP Analysis ─────────────────────────────────────────
    # Only run for Model A (P R3 wrong) — most informative
    # Model B SHAP is less useful since it's predicting call outcome, not R3 failures
    # Determine model name for output file naming
    if "R3 wrong" in name:
        model_name = "model_a"
    elif "call conclusive" in name:
        model_name = "model_b"
    else:
        model_name = name.replace(" ", "_").lower()
    shap_values, explainer = run_shap_analysis(
        model      = final,
        X          = X_m,
        y          = y_m,
        model_name = model_name,
        output_dir = ".",       # saves plots to current directory
    )

    # Save explainer to bundle so pipeline.py can use it for per-record explanations
    # We return it alongside the model
    return oof, final, mean_auc, explainer,oof_shap

# ============================================================================
# MAIN
# ============================================================================

def main(base_xlsx_path, claims_csv_path, models_out='models.pkl',
         oof_out='oof_predictions.csv'):
    """Train both models and save artifacts.
    
    Args:
        base_xlsx_path: path to base data Excel
        claims_csv_path: path to claims aggregate CSV
        models_out: path for pickled model bundle
        oof_out: path for out-of-fold predictions CSV
    """
    print(f"Loading base data from {base_xlsx_path}...")
    base = pd.read_excel(base_xlsx_path, sheet_name='Base Data', header=1)
    base = base.rename(columns={'Manual_ Address': 'Manual_Address'})
    base['R3'] = base['Final_R3_Reco_Address'].apply(normalize_r3_label)
    base['CallQC'] = base['Calling_Address'].fillna('UNKNOWN').str.upper()
    
    print(f"Loading claims aggregates from {claims_csv_path}...")
    claims = pd.read_csv(claims_csv_path)
    
    df = merge_claims(base, claims)
    claims.to_csv("claims_data.csv", index=False)
    config = load_config()
    feats = build_features(df, config=config)
    temp_feats = feats
    
    
    # ----- Define targets -----
    # Target A: R3 disagrees with Call QC 
    both_conclusive = (df['R3'].isin(['ACCURATE', 'INACCURATE']) &
                       df['CallQC'].isin(['ACCURATE', 'INACCURATE']))
    only_call_conclusive = (df['CallQC'].isin(['ACCURATE','INACCURATE']))
    # temp_feats["target"] = df["R3"] != df["CallQC"]
    # temp_feats    = temp_feats[both_conclusive.values].reset_index(drop=True)
    
    # temp_feats.to_csv("features.csv",index=False)
    y_r3_wrong = np.where(both_conclusive,
                           (df['R3'] != df['CallQC']).astype(int),
                           np.nan)
    
    # Target B: Call QC produced a conclusive verdict (defined on all records)
    y_call_conclusive = df['CallQC'].isin(['ACCURATE', 'INACCURATE']).astype(int)
    
    print(f"\nDataset: {len(df)} records")
    print(f"  Target A (R3 wrong) defined on: {both_conclusive.sum()} records")
    print(f"  Of which R3 is actually wrong: {int((y_r3_wrong==1).sum())} "
          f"({(y_r3_wrong==1).sum()/both_conclusive.sum()*100:.1f}%)")
    
    X, cat_cols = prepare_X(feats, config=config)
    print(f"Total Features fed to model: {len(X.columns)}")
    print(X.columns.tolist())
    sample_weights = np.where(y_r3_wrong == 0,3.0,1.0)
    high_risk_states = df['State'].isin(['MI', 'NJ', 'AL'])
    sample_weights = np.where((y_r3_wrong == 0) & high_risk_states, 5.0, sample_weights)
    # ----- Train Model A: P(R3 wrong) -----
    mask_a = both_conclusive.values
    # mask_a = np.ones(len(df), dtype=bool)
    oof_a, model_a, auc_a, explainer_a,oof_shap_a = cv_train(
        X, pd.Series(y_r3_wrong), mask_a, cat_cols,
        'A — P(R3 wrong)',
        sample_weights=sample_weights
    )
    
    # Show top features
    fi_a = pd.Series(model_a.feature_importances_,
                     index=model_a.feature_name_).sort_values(ascending=False)
    print(f"\n  Top 15 features by gain:")
    for i, (name, val) in enumerate(fi_a.head(15).items(), 1):
        tag = '[CLAIMS]' if 'claims' in name else '        '
        print(f"    {i:2d}. {tag} {name:<42} {val:>9.1f}")
    
    # ----- Train Model B: P(call conclusive) -----
    mask_b = np.ones(len(df), dtype=bool)
    oof_b, model_b, auc_b, explainer_b,shap_b  = cv_train(X, y_call_conclusive, mask_b, cat_cols,
                                       'B — P(call conclusive)')
    
    # ----- Triage precision @ top-K (validates triage quality) -----
    print(f"\n{'='*70}\nTRIAGE PRECISION @ TOP-K  (Model A on labeled subset)\n{'='*70}")
    valid = ~np.isnan(oof_a)
    p = oof_a[valid]
    yt = y_r3_wrong[valid].astype(int)
    order = np.argsort(-p)
    print(f"\n{'Top-K':<8} {'Precision':<12} {'Recall':<12} {'R3-wrong captured'}")
    for k in [50, 100, 200, 300, 450, 600, 800]:
        if k > len(order):
            continue
        top = order[:k]
        hits = yt[top].sum()
        print(f"  {k:<6} {hits/k:<12.4f} {hits/yt.sum():<12.4f} {hits}")
    
    # ----- Save model bundle -----
    bundle = {
        'model_r3_wrong'            : model_a,
        'model_call_conclusive'     : model_b,
        'explainer_r3_wrong'        : explainer_a,
        'explainer_call_conclusive': explainer_b, 
        'feature_cols'              : list(X.columns),
        'cat_cols'                  : cat_cols,
        'cv_auc_r3_wrong'           : auc_a,
        'cv_auc_call_conclusive'    : auc_b,
        'feature_importance_r3_wrong': fi_a.to_dict(),
    }
    with open(models_out, 'wb') as f:
        pickle.dump(bundle, f)
    print(f"\nSaved {models_out}")
    
    # ----- Save OOF predictions for evaluation -----
    df_out = df[['Row ID', 'OrigNPI', 'R3', 'CallQC', 'State',
                 'Specialty', 'Final_R3_Score_Address']].copy()
    df_out['oof_p_r3_wrong'] = oof_a
    df_out['oof_p_call_conclusive'] = oof_b
    df_out['both_conclusive'] = both_conclusive
    df_out['y_r3_wrong'] = y_r3_wrong
    idx_a = df.index[mask_a]
    shap_a_df = pd.DataFrame(
        oof_shap_a,                          # returned from cv_train
        columns=[f"shap_a_{c}" for c in X.columns],
        index= idx_a
        )
    df_out = df_out.join(shap_a_df)
    feature_names = list(X.columns)
    shap_idx = 0
    reasons = []
    for i in range(len(df_out)):
        if mask_a[i]:
            reasons.append(get_top_reasons(oof_shap_a[shap_idx], feature_names))
            shap_idx += 1
        else:
            reasons.append("N/A - Not in Model A")
            
    df_out['top_reasons_r3_wrong'] = reasons
    
    df_out.to_csv(oof_out, index=False)
    print(f"Saved {oof_out}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python train.py <base_data.xlsx> <claims_aggregates.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
