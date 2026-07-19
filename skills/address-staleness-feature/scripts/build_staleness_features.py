"""Build address-staleness features for the R3 vs Calling-QC model.

TWO hard rules (see SKILL.md):
  1. stale_* features are computed ONLY where R3 == ACCURATE (the "false keep" pile).
     On other rows they are NaN and stale_is_checked = 0.
  2. Every "days since" is measured from a FIXED anchor date (data-pull date),
     NOT from today, so the feature is reproducible.

Usage:
    python build_staleness_features.py <in.csv> <out.csv>
    # or:  from build_staleness_features import add_staleness_features, AS_OF
"""
import sys
import pandas as pd
import numpy as np

# --- Rule 2: fixed anchor. Data was fetched end of April 2026. -----------------
AS_OF = pd.Timestamp("2026-04-30")
FRESH_DAYS = 90                        # No Surprises Act re-verification window

# R3's address verdict column in this repo. Values look like
# "ACCURATE - KEEP RECORD" / "INACCURATE - REMOVE RECORD" /
# "INCONCLUSIVE - RELIABLE EVIDENCE NOT FOUND" / "INCONCLUSIVE".
# The KEEP ("accurate") verdict is anything that STARTS WITH "ACCURATE"
# (note: "INACCURATE" does NOT start with "ACCURATE", so it is excluded).
R3_ADDR_COL = "Final_R3_Reco_Address"


def _days_since(date_series):
    """AS_OF minus a date column, in days. Unparseable dates -> NaN."""
    if date_series is None:
        return pd.Series(np.nan, index=range(0))
    d = pd.to_datetime(date_series, errors="coerce")
    return (AS_OF - d).dt.days


def _num(series, index, fill=0):
    if series is None:
        return pd.Series(fill, index=index)
    return pd.to_numeric(series, errors="coerce").fillna(fill)


def add_staleness_features(df: pd.DataFrame, r3_col: str = R3_ADDR_COL) -> pd.DataFrame:
    out = df.copy()
    idx = out.index

    # Rule 1 gate: only rows where R3's address verdict is the KEEP/ACCURATE case.
    is_accurate = out[r3_col].astype(str).str.strip().str.upper().str.startswith("ACCURATE")

    # Rule 2: recompute EVERY days-since from AS_OF (overwrite pre-anchored cols).
    days_claim = _days_since(out["MOST_RECENT_DOS"]) if "MOST_RECENT_DOS" in out else pd.Series(np.nan, index=idx)
    days_nppes = _days_since(out["nppes_last_updated"]) if "nppes_last_updated" in out else pd.Series(np.nan, index=idx)
    out["DAYS_SINCE"] = days_claim
    out["nppes_days_since_update"] = days_nppes

    # Freshest available witness.
    min_days = pd.concat([days_claim, days_nppes], axis=1).min(axis=1)

    # Move signal: billing recently, but not from the roster street+ZIP, and from
    # more than one distinct address -> provider likely bills from elsewhere now.
    recent_match = _num(out.get("RECENT_STREET_ZIP_MATCH"), idx)
    n_claims = _num(out.get("N_CLAIMS"), idx)
    distinct_addr = _num(out.get("DISTINCT_ADDRS"), idx)
    moved = ((recent_match == 0) & (n_claims > 0) & (distinct_addr > 1)).astype(int)

    # Gated numeric features (NaN where R3 not accurate).
    out["stale_days_since_claim"] = days_claim.where(is_accurate)
    out["stale_days_since_nppes"] = days_nppes.where(is_accurate)
    out["stale_min_days"] = min_days.where(is_accurate)
    out["stale_over_90"] = (min_days > FRESH_DAYS).astype("float").where(is_accurate)
    out["stale_moved_elsewhere"] = moved.where(is_accurate).astype("float")

    # Categorical flag, priority: confirmed > suspect > fresh > unknown > n/a.
    flag = pd.Series("not_applicable", index=idx)
    acc_known = is_accurate & min_days.notna()
    flag = flag.mask(is_accurate & min_days.isna(), "unknown")
    flag = flag.mask(acc_known & (min_days <= FRESH_DAYS), "fresh")
    flag = flag.mask(acc_known & (min_days > FRESH_DAYS), "stale_suspect")
    flag = flag.mask(is_accurate & (moved == 1), "confirmed_stale")
    out["staleness_flag"] = flag

    # Mask: 1 where the feature is real (R3=ACCURATE), else 0.
    out["stale_is_checked"] = is_accurate.astype(int)
    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    in_path, out_path = sys.argv[1], sys.argv[2]
    df = pd.read_csv(in_path)
    if R3_ADDR_COL not in df.columns:
        raise SystemExit(f"No '{R3_ADDR_COL}' column found — confirm the R3 address verdict column before running.")
    result = add_staleness_features(df, r3_col=R3_ADDR_COL)
    result.to_csv(out_path, index=False)
    checked = int(result["stale_is_checked"].sum())
    print(f"AS_OF = {AS_OF.date()} | rows = {len(result)} | R3=ACCURATE (checked) = {checked}")
    print(result["staleness_flag"].value_counts().to_string())
    print(f"wrote -> {out_path}")


if __name__ == "__main__":
    main()
