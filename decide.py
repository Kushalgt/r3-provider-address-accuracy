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

KEEP_THRESHOLD     = 0.30
FLIP_THRESHOLD     = 0.80
CALL_PWRONG_MIN    = 0.50
CALL_PCONC_MIN     = 0.80
CALL_YIELD         = 0.40

# Hard cap: at most this fraction of records can be sent to robocall.
# Per problem statement: 30% is the operational ceiling.
MAX_CALL_FRACTION  = 0.30


# ============================================================================
# CALL ELIGIBILITY (eligibility rule + cap)
# ============================================================================

def select_call_set(df, pw_min=CALL_PWRONG_MIN, pc_min=CALL_PCONC_MIN,
                    max_call_fraction=MAX_CALL_FRACTION):
    """Pick which records to call.
    
    Step 1: dual-threshold filter — record must look uncertain enough about
            R3 AND likely to actually answer the phone.
    Step 2: cap — keep at most max_call_fraction of total records, ranked
            by triage priority = P(R3 wrong) * P(call conclusive).
    """
    pw = df['p_r3_wrong'].fillna(0.5)
    pc = df['p_call_conclusive']
    eligible = (pw >= pw_min) & (pc >= pc_min)

    pool = df.loc[eligible].copy()
    pool['_priority'] = pw[eligible] * pc[eligible]
    pool = pool.sort_values('_priority', ascending=False)

    cap = int(np.floor(len(df) * max_call_fraction))
    if len(pool) > cap:
        pool = pool.head(cap)

    return set(pool['Row ID'].tolist())


# ============================================================================
# DECISION RULE (per record)
# ============================================================================

def apply_decision(row, call_set, keep_t=KEEP_THRESHOLD, flip_t=FLIP_THRESHOLD):
    """Determine the action for one record. Returns (decision_label, reason_code)."""
    r3 = row['R3']
    pw = row.get('p_r3_wrong', 0.5)
    if pd.isna(pw): pw = 0.5

    if r3 == 'INCONCLUSIVE':
        if row['Row ID'] in call_set:
            return ('SEND_TO_CALL', 'R3_INCONCLUSIVE_CALL_LIKELY_CONCLUSIVE')
        return ('INCONCLUSIVE', 'R3_INCONCLUSIVE_CALL_UNLIKELY')

    if pw < keep_t:
        return (r3, 'KEEP_R3_HIGH_CONFIDENCE')

    if pw > flip_t:
        if row['Row ID'] in call_set:
            return ('SEND_TO_CALL', 'HIGH_CONF_FLIP_VERIFY_BY_CALL')
        flipped = 'INACCURATE' if r3 == 'ACCURATE' else 'ACCURATE'
        return (flipped, 'HIGH_CONF_PASSIVE_FLIP')

    if row['Row ID'] in call_set:
        return ('SEND_TO_CALL', 'MEDIUM_UNCERTAINTY_CALLED')
    return (r3, 'MEDIUM_UNCERTAINTY_KEEP_R3_FAILED_CALL_THRESHOLDS')


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
