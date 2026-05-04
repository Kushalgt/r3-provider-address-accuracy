# shap_analysis.py
"""
SHAP analysis module for R3 accuracy model.

Provides three things:
1. run_shap_analysis()     - global feature importance plots
2. explain_record()        - per-record explanation (why is R3 wrong/right?)
3. zone_shap_comparison()  - do Zone1 and Zone2 have different SHAP patterns?

Used inside cv_train() in train.py after the final model is fit.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # safe for scripts — no display needed
import matplotlib.pyplot as plt
import shap
import warnings
warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────
# 1. GLOBAL SHAP ANALYSIS
#    Call this once after training is complete
#    Saves plots to disk + returns shap_values for further use
# ─────────────────────────────────────────────────────────────

def run_shap_analysis(model, X, y=None, model_name="model_a", output_dir="."):
    """
    Runs full SHAP analysis on the trained model.

    Args:
        model      : fitted LightGBM model
        X          : feature matrix (the full masked training set)
        y          : true labels (optional — used for zone comparison)
        model_name : prefix for output file names
        output_dir : where to save plots

    Returns:
        shap_values : numpy array of shape (n_samples, n_features)
        explainer   : TreeExplainer object (reuse for per-record explanations)
    """
    print(f"\n{'='*70}")
    print(f"SHAP ANALYSIS — {model_name}")
    print(f"{'='*70}")

    # Build explainer
    explainer   = shap.TreeExplainer(model)
    # shap_values returns list of 2 arrays for binary classifier
    # [0] = SHAP values for class 0 (R3 right)
    # [1] = SHAP values for class 1 (R3 wrong) ← this is what we want
    shap_values = explainer.shap_values(X)

    # For binary LightGBM, shap_values is a list [class0, class1]
    # We always want class 1 = P(R3 wrong)
    if isinstance(shap_values, list):
        sv = shap_values[1]   # shape: (n_samples, n_features)
    else:
        sv = shap_values      # some versions return single array directly

    print(f"  SHAP values computed for {sv.shape[0]} records, {sv.shape[1]} features")

    # ── Plot 1: Summary Bar Plot (global feature importance) ──
    # Shows mean absolute SHAP value per feature
    # Better than model.feature_importances_ because it shows TRUE contribution
    plt.figure(figsize=(10, 8))
    shap.summary_plot(
        sv, X,
        plot_type="bar",
        show=False,
        max_display=20,
    )
    plt.title(f"Global Feature Importance (SHAP) — {model_name}", fontsize=13)
    plt.tight_layout()
    bar_path = f"{output_dir}/{model_name}_shap_bar.png"
    plt.savefig(bar_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved → {bar_path}")

    # ── Plot 2: Beeswarm Plot (direction + magnitude) ──────────
    # Shows both HOW MUCH and IN WHICH DIRECTION each feature pushes the prediction
    # Red = high feature value, Blue = low feature value
    # Right = pushes toward R3 wrong, Left = pushes toward R3 right
    plt.figure(figsize=(10, 8))
    shap.summary_plot(
        sv, X,
        plot_type="dot",
        show=False,
        max_display=20,
    )
    plt.title(f"SHAP Beeswarm — {model_name}\nRight = pushes toward R3 wrong", fontsize=11)
    plt.tight_layout()
    bee_path = f"{output_dir}/{model_name}_shap_beeswarm.png"
    plt.savefig(bee_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved → {bee_path}")

    # ── Print top 10 features by mean |SHAP| ──────────────────
    mean_abs_shap = np.abs(sv).mean(axis=0)
    feat_shap     = pd.Series(mean_abs_shap, index=X.columns)
    top10         = feat_shap.sort_values(ascending=False).head(10)

    print(f"\n  Top 10 features by mean |SHAP|:")
    print(f"  {'Feature':<40} {'Mean |SHAP|':>12}  Direction")
    print(f"  {'-'*70}")
    for feat, val in top10.items():
        # Find direction: positive mean SHAP = pushes toward R3 wrong
        mean_shap_signed = sv[:, X.columns.get_loc(feat)].mean()
        direction = "→ pushes R3 WRONG" if mean_shap_signed > 0 else "→ pushes R3 RIGHT"
        print(f"  {feat:<40} {val:>12.4f}  {direction}")

    # ── Zone comparison (if labels provided) ──────────────────
    if y is not None:
        zone_shap_comparison(sv, X, y, model_name=model_name, output_dir=output_dir)

    return sv, explainer


# ─────────────────────────────────────────────────────────────
# 2. ZONE SHAP COMPARISON
#    Compares SHAP patterns between Zone 1 (R3 right) and Zone 2 (R3 wrong)
#    Directly answers Track 1: WHY does R3 fail?
# ─────────────────────────────────────────────────────────────

def zone_shap_comparison(shap_values, X, y, model_name="model_a", output_dir="."):
    """
    Compares mean |SHAP| values between Zone 1 and Zone 2 records.

    Zone 1 = R3 was correct (y=0) — agreement zone
    Zone 2 = R3 was wrong   (y=1) — disagreement zone

    Shows which features are most responsible for R3's failures.
    This is your Track 1 answer: "R3 fails because of feature X"
    """
    y_arr = np.array(y)

    zone1_mask = (y_arr == 0)   # R3 correct
    zone2_mask = (y_arr == 1)   # R3 wrong

    if zone1_mask.sum() == 0 or zone2_mask.sum() == 0:
        print("  [SHAP Zone] Skipping — one zone is empty")
        return

    sv_zone1 = shap_values[zone1_mask]
    sv_zone2 = shap_values[zone2_mask]

    mean_zone1 = np.abs(sv_zone1).mean(axis=0)
    mean_zone2 = np.abs(sv_zone2).mean(axis=0)

    df_zone = pd.DataFrame({
        "feature"     : X.columns,
        "zone1_shap"  : mean_zone1,   # how much feature contributed when R3 was RIGHT
        "zone2_shap"  : mean_zone2,   # how much feature contributed when R3 was WRONG
    })
    df_zone["diff"] = df_zone["zone2_shap"] - df_zone["zone1_shap"]
    df_zone = df_zone.sort_values("diff", ascending=False)

    print(f"\n  Zone SHAP Comparison (top features that differ between zones):")
    print(f"  Positive diff = feature matters MORE when R3 is WRONG (failure driver)")
    print(f"  {'Feature':<40} {'Zone1(R3 right)':>16} {'Zone2(R3 wrong)':>16} {'Diff':>10}")
    print(f"  {'-'*85}")
    for _, row in df_zone.head(15).iterrows():
        print(f"  {row['feature']:<40} {row['zone1_shap']:>16.4f} {row['zone2_shap']:>16.4f} {row['diff']:>10.4f}")

    # ── Plot: Zone comparison bar chart ───────────────────────
    top_diff = df_zone.head(12)
    x_pos    = np.arange(len(top_diff))
    width    = 0.35

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.bar(x_pos - width/2, top_diff["zone1_shap"], width, label="Zone 1 (R3 right)", color="#2196F3", alpha=0.85)
    ax.bar(x_pos + width/2, top_diff["zone2_shap"], width, label="Zone 2 (R3 wrong)", color="#F44336", alpha=0.85)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(top_diff["feature"], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Mean |SHAP value|")
    ax.set_title(f"Feature Contribution: Zone 1 vs Zone 2 — {model_name}\n"
                 f"Red taller = feature is a failure driver for R3", fontsize=11)
    ax.legend()
    plt.tight_layout()
    zone_path = f"{output_dir}/{model_name}_shap_zone_comparison.png"
    plt.savefig(zone_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n  Saved → {zone_path}")


# ─────────────────────────────────────────────────────────────
# 3. PER-RECORD EXPLANATION
#    For a single record: WHY does the model think R3 is wrong?
#    Used in pipeline.py for the human-readable explanation column
# ─────────────────────────────────────────────────────────────

def explain_record(explainer, record_features, feature_names, top_n=3):
    """
    Explains a single record's prediction.

    Args:
        explainer        : TreeExplainer from run_shap_analysis
        record_features  : single row as numpy array or Series (1D)
        feature_names    : list of feature column names
        top_n            : how many top reasons to return

    Returns:
        explanation : human-readable string
        reasons     : list of dicts with feature, shap_val, direction

    Example output:
        "R3 likely wrong because: phone linked to different org (+0.31),
         provider not in organization (+0.24), low R3 confidence score (+0.19)"
    """

    # Reshape to 2D if needed
    if hasattr(record_features, "values"):
        X_single = record_features.values.reshape(1, -1)
    else:
        X_single = np.array(record_features).reshape(1, -1)

    # Get SHAP values for this record
    sv = explainer.shap_values(X_single)
    if isinstance(sv, list):
        sv_single = sv[1][0]   # class 1 (R3 wrong), first (only) record
    else:
        sv_single = sv[0]

    # Build reasons sorted by absolute SHAP value
    reasons = []
    for i, (feat, val) in enumerate(zip(feature_names, sv_single)):
        reasons.append({
            "feature"   : feat,
            "shap_val"  : val,
            "abs_val"   : abs(val),
            "direction" : "R3 WRONG" if val > 0 else "R3 RIGHT",
        })

    # Sort by magnitude
    reasons = sorted(reasons, key=lambda x: x["abs_val"], reverse=True)
    top_reasons = reasons[:top_n]

    # Build human-readable explanation
    parts = []
    for r in top_reasons:
        sign = "+" if r["shap_val"] > 0 else ""
        parts.append(f"{r['feature']} ({sign}{r['shap_val']:.2f})")

    direction_summary = top_reasons[0]["direction"] if top_reasons else "UNCERTAIN"
    explanation = f"R3 likely {direction_summary} because: {', '.join(parts)}"

    return explanation, top_reasons


# ─────────────────────────────────────────────────────────────
# 4. ZONE ACCURACY ANALYSIS
#    Separate measurement of Zone 1 vs Zone 2 accuracy
#    Run this after every model iteration to check you're not breaking Zone 1
# ─────────────────────────────────────────────────────────────

def zone_accuracy_analysis(y_true, y_pred, threshold=0.95):
    """
    Separately measures accuracy on Zone 1 (R3 correct) and Zone 2 (R3 wrong).
    This is what the hackathon judges are actually measuring.

    Args:
        y_true    : true labels (0 = R3 correct, 1 = R3 wrong)
        y_pred    : model predictions (0 or 1)
        threshold : minimum acceptable Zone 1 accuracy (default 95%)

    Prints a clear breakdown and warns if Zone 1 is being broken.
    """
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)

    zone1_mask = (y_true == 0)   # R3 was correct — protect these
    zone2_mask = (y_true == 1)   # R3 was wrong  — improve these

    zone1_acc = (y_pred[zone1_mask] == y_true[zone1_mask]).mean()
    zone2_acc = (y_pred[zone2_mask] == y_true[zone2_mask]).mean()
    net_acc   = (y_pred == y_true).mean()

    # How many Zone 1 records did we accidentally flip?
    zone1_broken = ((y_pred[zone1_mask] == 1)).sum()
    zone2_caught = ((y_pred[zone2_mask] == 1)).sum()

    print(f"\n  {'='*55}")
    print(f"  ZONE ACCURACY ANALYSIS")
    print(f"  {'='*55}")
    print(f"  Zone 1 (R3 correct — DON'T BREAK):")
    print(f"    Records     : {zone1_mask.sum()}")
    print(f"    Accuracy    : {zone1_acc*100:.1f}%")
    print(f"    Broken      : {zone1_broken} records incorrectly flagged as R3-wrong")
    print()
    print(f"  Zone 2 (R3 wrong — IMPROVE THIS):")
    print(f"    Records     : {zone2_mask.sum()}")
    print(f"    Accuracy    : {zone2_acc*100:.1f}%")
    print(f"    Caught      : {zone2_caught} R3 failures correctly identified")
    print()
    print(f"  Net Accuracy  : {net_acc*100:.1f}%")
    print(f"  {'='*55}")

    # Warning if Zone 1 is being broken
    if zone1_acc < threshold:
        print(f"\n  ⚠️  WARNING: Zone 1 accuracy {zone1_acc*100:.1f}% < {threshold*100:.0f}% threshold")
        print(f"  You are breaking the agreement zone.")
        print(f"  Fix: increase prediction threshold OR increase Zone 1 sample weight")
    else:
        print(f"\n  ✅ Zone 1 protected ({zone1_acc*100:.1f}% ≥ {threshold*100:.0f}%)")

    return {
        "zone1_acc"    : zone1_acc,
        "zone2_acc"    : zone2_acc,
        "net_acc"      : net_acc,
        "zone1_broken" : zone1_broken,
        "zone2_caught" : zone2_caught,
    }