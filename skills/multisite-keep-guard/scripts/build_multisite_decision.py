"""Segment A resolver: multi-site KEEP guard for hospital-affiliated providers.

Reframe: for rotation-prone doctors the question is "is the roster address ONE OF the
provider's real, current sites" -- so match against ALL known sites (claims + NPPES
practiceLocations + optional CMS D&C), recency-weighted, and stop R3 from removing a
valid secondary site.

Applies only to hospital-affiliated rows (is_hospital_affiliated_specialty == 1).
Web confidence (Final_R3_Score_Address) is deliberately ignored for this segment
because it is inverted at the top in this dataset.

Usage:
    python build_multisite_decision.py <in.csv> <out.csv>
    # or: from build_multisite_decision import add_multisite_decision
"""
import sys
import numpy as np
import pandas as pd

# match tiers: NONE=0, ZIP=1, STREET_ZIP=2, EXACT=3
HOSP_COL = "is_hospital_affiliated_specialty"


def _num(df, col, fill=0):
    if col not in df:
        return pd.Series(fill, index=df.index)
    return pd.to_numeric(df[col], errors="coerce").fillna(fill)


def add_multisite_decision(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = out.index

    hosp = _num(out, HOSP_COL).astype(int)

    # claims-side match evidence
    exact = _num(out, "ADDR_EXACT_MATCH")
    sz = _num(out, "STREET_ZIP_MATCH")
    zipm = _num(out, "ZIP_MATCH")
    recent_sz = _num(out, "RECENT_STREET_ZIP_MATCH")
    n_claims = _num(out, "N_CLAIMS")

    # claims tier (0..3)
    claims_tier = np.where(exact > 0, 3, np.where(sz > 0, 2, np.where(zipm > 0, 1, 0)))

    # NPPES best match across ALL locations (0..3)
    npp_any = _num(out, "nppes_any_location_match_tier").astype(int).values

    best_tier = np.maximum(claims_tier, npp_any)
    out["multisite_best_tier"] = best_tier

    has_any_evidence = (n_claims.values > 0) | (npp_any > 0) | \
        (_num(out, "nppes_found").values > 0)

    # decision ladder (only meaningful for Segment A)
    keep = (recent_sz.values > 0) | (best_tier >= 2)
    call = (~keep) & (best_tier >= 1)                       # weak/old known site
    remove_cand = (~keep) & (~call) & (n_claims.values > 0)  # billing only elsewhere
    call_noev = (~keep) & (~call) & (~remove_cand)           # nothing to go on

    action = np.full(len(out), "not_segment_A", dtype=object)
    seg = hosp.values == 1
    action = np.where(seg & keep, "KEEP_GUARD", action)
    action = np.where(seg & call, "CALL", action)
    action = np.where(seg & remove_cand, "REMOVE_CANDIDATE", action)
    action = np.where(seg & call_noev, "CALL_NO_EVIDENCE", action)
    out["multisite_action"] = action

    reason = {
        "KEEP_GUARD": "roster matches a real practice site (street+ZIP or better) across claims/NPPES; do not remove a valid secondary site",
        "CALL": "roster matches a known site only weakly/old (ZIP-only or non-recent); verify by phone",
        "REMOVE_CANDIDATE": "no site match anywhere but provider bills from other addresses; lean remove, prefer CALL (absence is NULL)",
        "CALL_NO_EVIDENCE": "no claims and no registry match; passive signals cannot resolve - best use of call budget",
        "not_segment_A": "not a hospital-affiliated / multi-site provider; guard does not apply",
    }
    out["multisite_reason"] = out["multisite_action"].map(reason)
    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    in_path, out_path = sys.argv[1], sys.argv[2]
    df = pd.read_csv(in_path)
    if HOSP_COL not in df.columns:
        raise SystemExit(f"No '{HOSP_COL}' column — run the enrichment / hospital-affiliated flag first.")
    result = add_multisite_decision(df)
    seg = (pd.to_numeric(result[HOSP_COL], errors="coerce").fillna(0).astype(int) == 1)
    print(f"rows = {len(result)} | Segment A (hospital-affiliated) = {int(seg.sum())}")
    print(result.loc[seg, "multisite_action"].value_counts().to_string())
    print(f"wrote -> {out_path}")
    result.to_csv(out_path, index=False)


if __name__ == "__main__":
    main()
