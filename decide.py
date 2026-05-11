"""
================================================================================
R3 ADDRESS ACCURACY — DECISION POLICY (v3)
================================================================================

CHANGES FROM v2
---------------
* Re-introduces a maximum-call cap, but expressed as a PERCENTAGE of total
  records (default 30%) rather than the magic number 450.
* The cap is a hard operational constraint — the team cannot send more than
  30% of records to robocalling regardless of model confidence.
* The dual-threshold rule still selects which records are eligible; if more
  than 30% qualify we keep only the highest-priority subset.

DECISION POLICY (rules fire in order)
-------------------------------------
  1. R3 was INCONCLUSIVE + record passes call thresholds + in cap → SEND_TO_CALL
  2. R3 was INCONCLUSIVE + does not qualify                       → leave INCONCLUSIVE
  3. P(R3 wrong) below KEEP_THRESHOLD                             → KEEP R3
  4. P(R3 wrong) > FLIP_THRESHOLD + in call set                   → SEND_TO_CALL
  5. P(R3 wrong) > FLIP_THRESHOLD + not in call set               → FLIP passively
  6. Medium uncertainty + in call set                             → SEND_TO_CALL
  7. Medium uncertainty + not in call set                         → KEEP R3 (conservative)
"""

import sys
import numpy as np
import pandas as pd

# ============================================================================
# POLICY THRESHOLDS — Balanced default
# ============================================================================

KEEP_THRESHOLD     = 0.34  # Anything below this is kept as R3's original answer
FLIP_THRESHOLD     = 0.80  # Highly confident failures
CALL_PWRONG_MIN    = 0.50
CALL_PCONC_MIN     = 0.80
CALL_YIELD         = 0.40

# Hard cap: at most this fraction of records can be sent to robocall.
# Per problem statement: 30% is the operational ceiling.
MAX_CALL_FRACTION  = 0.30


# ============================================================================
# CALL ELIGIBILITY (eligibility rule + cap)
# ============================================================================

import numpy as np
import pandas as pd

# Constants
MAX_CALL_FRACTION = 0.30

# Pass 1 / Pass 2 call_conclusive thresholds
PCONC_PASS1_MIN = 0.75
PCONC_PASS2_MIN = 0.65

# Model-inconclusive range
PWRONG_INCONC_LOW  = 0.14
PWRONG_INCONC_HIGH = 0.54

# Likely-wrong range
PWRONG_LIKELY_LOW  = 0.53
PWRONG_LIKELY_HIGH = 0.75

# Hospital IVR-waste guard
HOSPITAL_PCONC_MIN = 0.30


def select_call_set(df, max_call_fraction=MAX_CALL_FRACTION):
    """Pick which records to call, using the 3-priority / 2-pass triage rule.

    Pass 1: p_call_conclusive >= 0.75
        Priority 1 — R3 engine output = INCONCLUSIVE
        Priority 2 — model-inconclusive (0.14 <= p_r3_wrong <= 0.54)
        Priority 3 — likely R3 wrong (0.53 <= p_r3_wrong <= 0.75)

    Pass 2: 0.65 <= p_call_conclusive < 0.75 (only if cap not yet hit)
        Same 3 priorities, same order.

    Within each priority, records are ranked by p_call_conclusive descending.
    Priorities are mutually exclusive: a record that qualifies for P1 will
    not also be considered under P2/P3.
    """
    cap = int(np.floor(len(df) * max_call_fraction))
    if cap <= 0:
        return set()

    pw = df['p_r3_wrong'].fillna(0.5)
    pc = df['p_call_conclusive'].fillna(0.0)
    r3 = df.get('R3', pd.Series(['UNKNOWN'] * len(df), index=df.index)) \
            .astype(str).str.upper()

    # Hospital IVR-waste guard: a hospital with very low p_call_conclusive
    # is removed from call eligibility entirely (no point burning the budget).
    is_hosp = df.get('feat_org_is_hospital', pd.Series(0, index=df.index)).fillna(0).astype(int)
    hosp_block = (is_hosp == 1) & (pc < HOSPITAL_PCONC_MIN)

    # Per-record priority bucket (1 = highest, 3 = lowest, 0 = not eligible).
    # Mutually exclusive: assigned to the highest priority it qualifies for.
    priority = pd.Series(0, index=df.index, dtype=int)
    is_p1 = (r3 == 'INCONCLUSIVE')
    is_p2 = (pw >= PWRONG_INCONC_LOW) & (pw <= PWRONG_INCONC_HIGH)
    is_p3 = (pw >= PWRONG_LIKELY_LOW) & (pw <= PWRONG_LIKELY_HIGH)
    priority[is_p3] = 3
    priority[is_p2] = 2  # overwrites P3 where overlap
    priority[is_p1] = 1  # overwrites P2/P3 where overlap

    # Pass = 1 if pc >= 0.75, 2 if 0.65 <= pc < 0.75, else 0 (not eligible)
    pass_id = pd.Series(0, index=df.index, dtype=int)
    pass_id[(pc >= PCONC_PASS1_MIN)] = 1
    pass_id[(pc >= PCONC_PASS2_MIN) & (pc < PCONC_PASS1_MIN)] = 2

    eligible = (priority > 0) & (pass_id > 0) & (~hosp_block)

    pool = df.loc[eligible, ['Row ID']].copy()
    pool['_pass']     = pass_id[eligible].values
    pool['_priority'] = priority[eligible].values
    pool['_pc']       = pc[eligible].values

    # Sort: pass asc → priority asc → p_call_conclusive desc
    # That's exactly the order the spec describes.
    pool = pool.sort_values(
        by=['_pass', '_priority', '_pc'],
        ascending=[True, True, False],
    )

    if len(pool) > cap:
        pool = pool.head(cap)

    return set(pool['Row ID'].tolist())
def select_call_set(df, max_call_fraction=MAX_CALL_FRACTION):
    """... (same docstring) ...

    Returns:
        (call_set, rank_map) where
            call_set: set of Row IDs selected for the call queue
            rank_map: dict {Row ID -> rank} with rank=1 being highest priority
    """
    cap = int(np.floor(len(df) * max_call_fraction))
    if cap <= 0:
        return set(), {}

    pw = df['p_r3_wrong'].fillna(0.5)
    pc = df['p_call_conclusive'].fillna(0.0)
    r3 = df.get('R3', pd.Series(['UNKNOWN'] * len(df), index=df.index)) \
            .astype(str).str.upper()

    is_hosp = df.get('feat_org_is_hospital', pd.Series(0, index=df.index)).fillna(0).astype(int)
    hosp_block = (is_hosp == 1) & (pc < HOSPITAL_PCONC_MIN)

    priority = pd.Series(0, index=df.index, dtype=int)
    is_p1 = (r3 == 'INCONCLUSIVE')
    is_p2 = (pw >= PWRONG_INCONC_LOW) & (pw <= PWRONG_INCONC_HIGH)
    is_p3 = (pw >= PWRONG_LIKELY_LOW) & (pw <= PWRONG_LIKELY_HIGH)
    priority[is_p3] = 3
    priority[is_p2] = 2
    priority[is_p1] = 1

    pass_id = pd.Series(0, index=df.index, dtype=int)
    pass_id[pc >= PCONC_PASS1_MIN] = 1
    pass_id[(pc >= PCONC_PASS2_MIN) & (pc < PCONC_PASS1_MIN)] = 2

    eligible = (priority > 0) & (pass_id > 0) & (~hosp_block)

    pool = df.loc[eligible, ['Row ID']].copy()
    pool['_pass']     = pass_id[eligible].values
    pool['_priority'] = priority[eligible].values
    pool['_pc']       = pc[eligible].values

    pool = pool.sort_values(
        by=['_pass', '_priority', '_pc'],
        ascending=[True, True, False],
    )

    if len(pool) > cap:
        pool = pool.head(cap)

    call_set = set(pool['Row ID'].tolist())
    rank_map = {row_id: i + 1 for i, row_id in enumerate(pool['Row ID'].tolist())}
    return call_set, rank_map

# ============================================================================
# DECISION RULE (per record)
# ============================================================================
def apply_decision(row, call_set):
    """Determine the final label for one record.

    Order of evaluation:
      1. Hard rules forcing INACCURATE
      2. Hard rules forcing ACCURATE (incl. ML vetoes / false-negative rescues)
      3. High-confidence R3 veto (R3 score >= 90 and pw < 0.75)
      4. Triage outcome (SEND_TO_CALL if in call_set)
      5. Status assignment from p_r3_wrong thresholds:
            pw > 0.53          → Flip R3
            pw < 0.15          → Retain R3
            0.14 <= pw <= 0.54 → Inconclusive
    """
    r3 = str(row.get('R3', 'UNKNOWN')).upper()
    pw = row.get('p_r3_wrong', 0.5)
    if pd.isna(pw):
        pw = 0.5

    # ---------------------------------------------------------
    # STAGE 1 — Hard deterministic rules: FORCE INACCURATE
    # ---------------------------------------------------------
    if row.get('feat_npi_deactivated') == 1:
        return ('INACCURATE', 'RULE_INACC_NPI_DEACTIVATED')

    if row.get('feat_claims_distinct_addrs', 0) >= 2 and row.get('feat_claims_zip_match', 1) == 0:
        return ('INACCURATE', 'RULE_INACC_CLAIMS_MISMATCH_ROVING')

    if row.get('feat_web_says_not_found_and_not_empty') == 1:
        return ('INACCURATE', 'RULE_INACC_WEB_EXPLICIT_NOT_FOUND')

    if row.get('feat_claims_zip_but_no_match') == 1 and row.get('feat_web_comment_empty_or_zero') == 1:
        return ('INACCURATE', 'RULE_INACC_74_PERCENT_TRAP')

    # ---------------------------------------------------------
    # STAGE 2 — Hard deterministic rules: FORCE ACCURATE
    # ---------------------------------------------------------
    # Active billing at the same address — strongest single signal that the
    # provider is currently practising there.
    if row.get('feat_claims_recent_street_zip') == 1:
        return ('ACCURATE', 'RULE_ACC_CLAIMS_RECENT_EXACT')

    # R3 said INACCURATE but claims strongly corroborate the address.
    if row.get('feat_r3ina_x_claims_corroborate') == 1:
        return ('ACCURATE', 'RULE_ACC_FALSE_NEGATIVE_RESCUE')

    # 3+ distinct web sources all confirm — convergent evidence.
    if row.get('feat_ev_total_found', 0) >= 3:
        return ('ACCURATE', 'RULE_ACC_WEB_CONVERGENCE')

    # ---------------------------------------------------------
    # STAGE 3 — High-confidence R3 veto
    # If R3 was very confident and the model isn't strongly against it,
    # keep R3's verdict regardless of the model.
    # ---------------------------------------------------------
    if row.get('feat_r3_score_numeric', 0) >= 90 and pw < 0.75:
        return (r3, 'ML_VETO_R3_HIGH_CONFIDENCE')

    # ---------------------------------------------------------
    # STAGE 4 — Triage outcome
    # ---------------------------------------------------------
    if row.get('Row ID') in call_set:
        return ('SEND_TO_CALL', 'TRIAGED_TO_CALL')

    # ---------------------------------------------------------
    # STAGE 5 — Status from p_r3_wrong thresholds
    # ---------------------------------------------------------
    if pw < 0.15:
        return (r3, 'RETAIN_R3_LOW_PWRONG')

    if pw > 0.53:
        # Flip the existing R3 verdict.
        flipped = 'INACCURATE' if r3 == 'ACCURATE' else 'ACCURATE'
        return (flipped, 'FLIP_R3_HIGH_PWRONG')

    # Anything else (0.15 <= pw <= 0.53) is model-inconclusive territory.
    return ('INCONCLUSIVE', 'MODEL_INCONCLUSIVE_NOT_CALLED')

# ============================================================================
# CALL OUTCOME SIMULATION (for evaluation only)
# ============================================================================

def simulate_call_outcomes(df, seed=0, flip_t=FLIP_THRESHOLD):
    rng = np.random.default_rng(seed)
    df = df.copy()
    df['call_resolves'] = False
    mask_call = df['decision_label'] == 'SEND_TO_CALL'
    n_calls = mask_call.sum()
    resolved = rng.random(n_calls) < CALL_YIELD
    df.loc[mask_call, 'call_resolves'] = resolved

    def post_call(row):
        if row['decision_label'] != 'SEND_TO_CALL':
            return row['decision_label']
        if row['call_resolves'] and row['CallQC'] in ('ACCURATE','INACCURATE'):
            return row['CallQC']
        if row['p_r3_wrong'] > flip_t:
            if row['R3']=='ACCURATE': return 'INACCURATE'
            if row['R3']=='INACCURATE': return 'ACCURATE'
        return row['R3']

    df['final_label'] = df.apply(post_call, axis=1)
    return df


def evaluate_one_run(df):
    eval_mask = df['CallQC'].isin(['ACCURATE','INACCURATE'])
    sub = df[eval_mask]
    baseline_acc = (sub['R3']==sub['CallQC']).mean()
    pipeline_acc = (sub['final_label']==sub['CallQC']).mean()

    az = (df['R3'].isin(['ACCURATE','INACCURATE']) &
          df['CallQC'].isin(['ACCURATE','INACCURATE']) &
          (df['R3']==df['CallQC']))
    az_sub = df[az]
    az_preserved = (az_sub['final_label']==az_sub['CallQC']).mean()

    n_calls = (df['decision_label']=='SEND_TO_CALL').sum()
    return {'baseline_acc': baseline_acc,
            'pipeline_acc': pipeline_acc,
            'lift_pp': (pipeline_acc - baseline_acc) * 100,
            'az_preserved': az_preserved,
            'n_calls': n_calls}


# ============================================================================
# MAIN (CLI)
# ============================================================================

def main(oof_csv='oof_predictions.csv', out_csv='decisions_final.csv', n_seeds=10):
    print(f"Loading {oof_csv}...")
    df = pd.read_csv(oof_csv)
    df = df.rename(columns={'oof_p_r3_wrong': 'p_r3_wrong',
                             'oof_p_call_conclusive': 'p_call_conclusive'})

    call_set = select_call_set(df)
    cap = int(np.floor(len(df) * MAX_CALL_FRACTION))
    print(f"\nCall set: {len(call_set)} records (max-cap: {cap} = {MAX_CALL_FRACTION:.0%} of {len(df)})")
    print(f"  Dual threshold: P(R3 wrong) >= {CALL_PWRONG_MIN} AND P(call conc.) >= {CALL_PCONC_MIN}")

    decisions = df.apply(lambda r: apply_decision(r, call_set), axis=1)
    df['decision_label'] = [d[0] for d in decisions]
    df['decision_reason'] = [d[1] for d in decisions]

    print(f"\nDecision breakdown:")
    print(df['decision_reason'].value_counts())

    print(f"\n{'='*70}")
    print(f"SIMULATING ACROSS {n_seeds} RANDOM SEEDS (40% conclusive yield)")
    print(f"{'='*70}")
    results = []
    last_df = None
    for seed in range(n_seeds):
        sim_df = simulate_call_outcomes(df, seed=seed)
        m = evaluate_one_run(sim_df); m['seed'] = seed
        results.append(m)
        if seed == 0:
            last_df = sim_df

    res = pd.DataFrame(results)
    print(f"\nBaseline R3 accuracy:    {res['baseline_acc'].mean()*100:.2f}%")
    print(f"Pipeline accuracy:       {res['pipeline_acc'].mean()*100:.2f}% (±{res['pipeline_acc'].std()*100:.2f})")
    print(f"Net accuracy lift:       +{res['lift_pp'].mean():.2f} pp (±{res['lift_pp'].std():.2f})")
    print(f"Agreement zone preserved: {res['az_preserved'].mean()*100:.2f}%")
    print(f"Calls used:              {int(res['n_calls'].mean())} / cap {cap}")
    cost = len(df) * 0.035 + res['n_calls'].mean() * 0.5
    print(f"Total cost:              ${cost:.2f}")

    last_df.to_csv(out_csv, index=False)
    print(f"\nSaved {out_csv}")


if __name__ == '__main__':
    oof = sys.argv[1] if len(sys.argv) > 1 else 'oof_predictions.csv'
    main(oof)
