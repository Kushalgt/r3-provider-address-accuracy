"""Build hospital-affiliated / multi-site address features for the R3 model.

Core idea (see SKILL.md): for hospital-affiliated specialties an address is correct
if it matches ANY of the provider's real, current sites -- not only the primary one.
R3 only checks the org/primary address, which is why this segment (~2-5% agreement)
is its worst. These features give the model the multi-site view.

Gating: raw match signals are computed for all rows; the verdict (haf_keep_guard) and
interaction features are meaningful only where is_hospital_affiliated_specialty == 1,
with a haf_is_checked mask so 'not a hospital specialty' differs from a real 0.

Usage:
    python build_haf_features.py <in.csv> <out.csv>
    # or:  from build_haf_features import add_haf_features
"""
import sys
import numpy as np
import pandas as pd


def _num(df, col, index, fill=0):
    if col not in df:
        return pd.Series(fill, index=index)
    return pd.to_numeric(df[col], errors="coerce").fillna(fill)


def _claims_tier(df, index):
    """Claims match tier 0-3 from the boolean match flags (EXACT>STREET_ZIP>ZIP>NONE)."""
    exact = _num(df, "ADDR_EXACT_MATCH", index)
    sz = _num(df, "STREET_ZIP_MATCH", index)
    z = _num(df, "ZIP_MATCH", index)
    return pd.Series(np.select([exact == 1, sz == 1, z == 1], [3, 2, 1], default=0), index=index)


def add_haf_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = out.index

    # Segment flag (keyword heuristic already built into the enriched dataset).
    haf = _num(out, "is_hospital_affiliated_specialty", idx).eq(1)
    out["haf_flag"] = haf.astype(int)

    # Best match tier across ALL known sites: NPPES any-location and claims.
    nppes_any = _num(out, "nppes_any_location_match_tier", idx)
    claims_tier = _claims_tier(out, idx)
    best_tier = pd.concat([nppes_any, claims_tier], axis=1).max(axis=1)
    out["best_known_site_tier"] = best_tier.astype(int)

    # Matches some genuine site (street+ZIP or exact = tier >= 2).
    matches_any = (best_tier >= 2).astype(int)
    out["matches_any_known_site"] = matches_any

    # Multi-site evidence: NPPES locations, distinct claims addresses, or >1 PIO org source.
    n_loc = _num(out, "nppes_num_locations", idx)
    distinct_addr = _num(out, "DISTINCT_ADDRS", idx)
    if "Provider_in_Organization" in out:
        n_org = out["Provider_in_Organization"].fillna("").astype(str).apply(
            lambda s: len([p for p in s.split("|") if p.strip()])
        )
    else:
        n_org = pd.Series(0, index=idx)
    out["is_multisite"] = ((n_loc > 1) | (distinct_addr > 1) | (n_org > 1)).astype(int)

    # Recent activity at the roster address (currently-active site).
    recent = _num(out, "RECENT_STREET_ZIP_MATCH", idx).gt(0).astype(int)
    out["recent_site_activity"] = recent

    # Evidence available at all?
    found_claims = _num(out, "found_in_claims", idx).eq(1)
    nppes_practice = _num(out, "nppes_practice_addr_match_tier", idx)
    has_evidence = (found_claims | (nppes_any > 0) | (nppes_practice > 0) | (n_loc > 0))

    # Interaction features (encode the segment behaviour for the model).
    out["haf_x_any_site_match"] = (haf & (matches_any == 1)).astype(int)
    out["haf_x_recent_activity"] = (haf & (recent == 1)).astype(int)
    out["haf_x_no_site_match"] = (haf & has_evidence & (matches_any == 0)).astype(int)

    # Gated categorical verdict. Priority: keep > verify_call > unresolved > no_evidence > n/a.
    nppes_listed = nppes_any >= 2
    keep = haf & (matches_any == 1) & ((recent == 1) | nppes_listed)
    verify = haf & (matches_any == 1)                      # matched a site but not recent
    unresolved = haf & has_evidence & (matches_any == 0)   # evidence, but no site match
    noev = haf & (~has_evidence)

    verdict = pd.Series("not_applicable", index=idx)
    verdict = verdict.mask(noev, "no_evidence")
    verdict = verdict.mask(unresolved, "unresolved_site")
    verdict = verdict.mask(verify, "verify_call")
    verdict = verdict.mask(keep, "keep_multisite_confirmed")
    out["haf_keep_guard"] = verdict

    out["haf_is_checked"] = haf.astype(int)
    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    in_path, out_path = sys.argv[1], sys.argv[2]
    df = pd.read_csv(in_path)
    if "is_hospital_affiliated_specialty" not in df.columns:
        raise SystemExit("Missing 'is_hospital_affiliated_specialty' — run the NPPES enrichment first.")
    result = add_haf_features(df)
    result.to_csv(out_path, index=False)
    haf_n = int(result["haf_flag"].sum())
    print(f"rows = {len(result)} | hospital-affiliated (checked) = {haf_n}")
    print(result.loc[result['haf_flag'] == 1, 'haf_keep_guard'].value_counts().to_string())
    print(f"wrote -> {out_path}")


if __name__ == "__main__":
    main()
