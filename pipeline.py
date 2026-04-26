"""
================================================================================
R3 ADDRESS ACCURACY — PIPELINE ORCHESTRATOR
================================================================================

End-to-end function that takes a base-data file path (and optionally a claims
source), runs the full pipeline, and produces an enriched per-record output
plus a summary dict.

The output DataFrame has these columns ready for export to Excel/CSV or
display in a UI data grid:

    Identifiers:
      Row ID, OrigNPI, NPI, FirstName, LastName, Specialty, Address1,
      City, State, Zip, Phone, OrganizationName

    Original verdict:
      R3_label                       (ACCURATE / INACCURATE / INCONCLUSIVE)
      R3_score                       (0-100)

    Model predictions:
      p_r3_wrong                     (0-1, Model A)
      p_r3_wrong_confidence          (max(p, 1-p))
      p_call_conclusive              (0-1, Model B)
      triage_priority                (p_r3_wrong * p_call_conclusive)

    Final pipeline decision:
      final_label                    (ACCURATE / INACCURATE / INCONCLUSIVE / SEND_TO_CALL)
      decision                       (KEEP / FLIP / CALL / LEAVE_INCONCLUSIVE)
      decision_reason_code           (machine-readable)
      decision_explanation           (human-readable, optionally LLM-generated)
      should_send_to_robocall        (bool)
      in_call_pool_priority_rank     (1 = highest priority, NaN if not in pool)

USAGE
-----
    from pipeline import run_pipeline
    output_df, summary = run_pipeline(
        base_path='upload.xlsx',
        claims_source='claims.csv',  # or 'empty', or a Snowflake conn
        models_path='models.pkl',
        output_path='predictions.xlsx',
        explain_mode='auto',         # 'auto'|'template'|'llm'
    )
"""

import os
import pickle
import pandas as pd
import numpy as np

from features import build_features, merge_claims, normalize_r3_label, load_config
from claims_loader import load_claims_aggregates
from decide import (apply_decision, select_call_set,
                     KEEP_THRESHOLD, FLIP_THRESHOLD,
                     CALL_PWRONG_MIN, CALL_PCONC_MIN, MAX_CALL_FRACTION)
from llm_explainer import explain_records


# ============================================================================
# OUTPUT COLUMN ORDER
# ============================================================================

OUTPUT_COLUMNS = [
    # identifiers
    'Row ID', 'OrigNPI', 'NPI', 'FirstName', 'LastName', 'Specialty',
    'Address1', 'City', 'State', 'Zip', 'Phone', 'OrganizationName',
    # original
    'R3_label', 'R3_score',
    # predictions
    'p_r3_wrong', 'p_r3_wrong_confidence', 'p_call_conclusive', 'triage_priority',
    # decisions
    'final_label', 'decision', 'decision_reason_code',
    'decision_explanation', 'should_send_to_robocall',
    'in_call_pool_priority_rank',
]


# ============================================================================
# DECISION-TYPE HELPER (collapses 7 reason codes into 4 user-facing buckets)
# ============================================================================

def _decision_bucket(label, reason):
    """Collapse the granular reason code into a 4-way user-facing decision."""
    if label == 'SEND_TO_CALL':
        return 'CALL'
    if reason == 'HIGH_CONF_PASSIVE_FLIP':
        return 'FLIP'
    if label == 'INCONCLUSIVE':
        return 'LEAVE_INCONCLUSIVE'
    return 'KEEP'


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def run_pipeline(base_path,
                 claims_source='empty',
                 models_path='models.pkl',
                 output_path=None,
                 explain_mode='template',
                 max_llm_calls=None,
                 progress_callback=None):
    """Run the full pipeline.

    Args:
        base_path: path to base data Excel/CSV with R3 output already populated
        claims_source: see claims_loader.load_claims_aggregates()
        models_path: path to pickled model bundle
        output_path: optional path to write enriched output (.xlsx or .csv)
        explain_mode: 'auto' | 'template' | 'llm'
        max_llm_calls: cap on LLM calls (cost control)
        progress_callback: optional fn(stage_name, percent_complete)

    Returns:
        (output_df, summary_dict)
    """
    def _p(stage, pct):
        if progress_callback:
            progress_callback(stage, pct)

    # ---- Load base ----
    _p('Loading input file', 5)
    if base_path.lower().endswith('.csv'):
        base = pd.read_csv(base_path)
    else:
        base = pd.read_excel(base_path, sheet_name='Base Data', header=1)
    if 'Manual_ Address' in base.columns:
        base = base.rename(columns={'Manual_ Address': 'Manual_Address'})
    base['R3'] = base['Final_R3_Reco_Address'].apply(normalize_r3_label)

    n_records = len(base)
    _p(f'Loaded {n_records} records', 15)

    # ---- Load claims ----
    _p('Loading claims aggregates', 25)
    claims_agg = load_claims_aggregates(base, source=claims_source)
    n_with_claims = (claims_agg['BASE_NPI'].notna().sum()
                     if 'BASE_NPI' in claims_agg.columns else 0)
    _p(f'Claims loaded ({n_with_claims} matched NPIs)', 35)

    # ---- Merge & build features ----
    _p('Building features', 45)
    df = merge_claims(base, claims_agg)
    config = load_config()
    feats = build_features(df, config=config)

    # ---- Load models & predict ----
    _p('Loading models', 55)
    with open(models_path, 'rb') as fh:
        bundle = pickle.load(fh)

    _p('Running predictions', 65)
    X = feats.reindex(columns=bundle['feature_cols'])
    cat_cols = bundle['cat_cols']
    for c in cat_cols:
        if c in X.columns:
            X[c] = X[c].astype(str).astype('category')
    for c in X.columns:
        if c not in cat_cols:
            X[c] = pd.to_numeric(X[c], errors='coerce').fillna(-1)

    p_wrong = bundle['model_r3_wrong'].predict_proba(X)[:, 1]
    p_conc  = bundle['model_call_conclusive'].predict_proba(X)[:, 1]

    # ---- Build per-record working frame ----
    work = base.copy()
    work['R3_label'] = work['R3']
    work['R3_score'] = work['Final_R3_Score_Address']
    work['p_r3_wrong'] = p_wrong
    work['p_r3_wrong_confidence'] = np.maximum(p_wrong, 1 - p_wrong)
    work['p_call_conclusive'] = p_conc
    work['triage_priority'] = p_wrong * p_conc

    # ---- Select call pool with 30% cap ----
    _p('Selecting call pool (30% cap)', 75)
    call_set = select_call_set(work)

    # Compute priority ranks within the call pool
    pool_df = work[work['Row ID'].isin(call_set)].sort_values('triage_priority', ascending=False)
    pool_df['_rank'] = range(1, len(pool_df) + 1)
    rank_map = dict(zip(pool_df['Row ID'], pool_df['_rank']))
    work['in_call_pool_priority_rank'] = work['Row ID'].map(rank_map)

    # ---- Apply decision rules ----
    _p('Applying decision rules', 80)
    decisions = work.apply(lambda r: apply_decision(r, call_set), axis=1)
    work['final_label'] = [d[0] for d in decisions]
    work['decision_reason_code'] = [d[1] for d in decisions]
    work['decision'] = work.apply(
        lambda r: _decision_bucket(r['final_label'], r['decision_reason_code']),
        axis=1
    )
    work['should_send_to_robocall'] = (work['final_label'] == 'SEND_TO_CALL')

    # ---- Generate per-record explanations ----
    _p('Generating explanations', 90)
    # Need feature columns alongside the prediction columns for the explainer.
    # The explainer expects 'decision_label' and 'decision_reason' field names —
    # we have 'final_label' and 'decision_reason_code'.
    expl_input = work.copy()
    expl_input['decision_label'] = expl_input['final_label']
    expl_input['decision_reason'] = expl_input['decision_reason_code']
    for c in feats.columns:
        if c not in expl_input.columns:
            expl_input[c] = feats[c].values
    explanations = explain_records(expl_input, mode=explain_mode,
                                    max_llm_calls=max_llm_calls)
    work['decision_explanation'] = explanations

    # ---- Build output frame ----
    _p('Writing output', 95)
    out = work.reindex(columns=OUTPUT_COLUMNS)

    # ---- Optional file write ----
    if output_path:
        if output_path.lower().endswith('.csv'):
            out.to_csv(output_path, index=False)
        else:
            with pd.ExcelWriter(output_path, engine='openpyxl') as w:
                out.to_excel(w, sheet_name='Predictions', index=False)
                # add a small summary tab
                pd.DataFrame([_build_summary(out, n_with_claims)]).T.reset_index().to_excel(
                    w, sheet_name='Summary', index=False, header=['metric','value'])

    summary = _build_summary(out, n_with_claims)
    _p('Done', 100)
    return out, summary


def _build_summary(out_df, n_with_claims):
    n = len(out_df)
    d = {
        'total_records': n,
        'records_with_claims': int(n_with_claims),
        'records_to_keep_r3': int((out_df['decision'] == 'KEEP').sum()),
        'records_to_flip_passively': int((out_df['decision'] == 'FLIP').sum()),
        'records_to_send_to_call': int((out_df['decision'] == 'CALL').sum()),
        'records_left_inconclusive': int((out_df['decision'] == 'LEAVE_INCONCLUSIVE').sum()),
        'call_cap': int(np.floor(n * MAX_CALL_FRACTION)),
        'call_pool_utilization': f"{out_df['should_send_to_robocall'].sum()}/{int(np.floor(n * MAX_CALL_FRACTION))}",
        'estimated_total_cost_usd': round(n * 0.035 + out_df['should_send_to_robocall'].sum() * 0.50, 2),
    }
    return d


# ============================================================================
# CLI USAGE
# ============================================================================

if __name__ == '__main__':
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <base.xlsx> [claims.csv|empty] [output.xlsx]")
        sys.exit(1)
    base = sys.argv[1]
    claims = sys.argv[2] if len(sys.argv) > 2 else 'empty'
    out = sys.argv[3] if len(sys.argv) > 3 else 'pipeline_output.xlsx'

    def progress(stage, pct):
        print(f"  [{pct:3d}%] {stage}")

    df, summary = run_pipeline(base, claims_source=claims, output_path=out,
                                progress_callback=progress)
    print(f"\nProcessed {len(df)} records")
    print(f"Output: {out}")
    print(f"\nSummary:")
    print(json.dumps(summary, indent=2))
