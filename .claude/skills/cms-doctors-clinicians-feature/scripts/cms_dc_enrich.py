"""CMS Doctors & Clinicians (Care Compare) enrichment for the R3 model.

Joins the CMS **Doctors and Clinicians National Downloadable File** (dataset
`mj5m-pzi6` on the CMS Provider Data Catalog) onto our base data by NPI and adds
multi-site / practice-location features. This directly attacks the hospital-affiliated
segment: CMS lists ONE ROW per clinician x enrollment x group x practice address, so a
provider who practices at several sites shows up as several rows. That is exactly the
"which of the doctor's real sites is the roster address" signal R3 is missing.

WHY a NEW file: we never touch data/processed/Base_enriched_sample.csv. Output is a
separate file so the existing pipeline can't break.

Two ways to supply CMS data (pick one):
  1. BULK CSV (recommended, full scale): download the file once from
       https://data.cms.gov/provider-data/dataset/mj5m-pzi6   (the "National
       Downloadable File" -> DAC_NationalDownloadableFile.csv, ~2.5M rows) and pass
       its path with --cms-bulk.
  2. API mode (--cms-api): query the public datastore per NPI. Only 10-digit NPIs
       (public) leave the machine -- no names/addresses -- which satisfies the HIPAA
       rule in CLAUDE.md. Run this LOCALLY; it needs `requests`.

Usage:
    # full scale from the downloaded bulk file
    python cms_dc_enrich.py \
        data/processed/Base_enriched_sample.csv \
        data/processed/Base_enriched_cms.csv \
        --cms-bulk /path/to/DAC_NationalDownloadableFile.csv

    # or hit the API (local machine, unrestricted network)
    python cms_dc_enrich.py \
        data/processed/Base_enriched_sample.csv \
        data/processed/Base_enriched_cms.csv \
        --cms-api

The aggregation/feature functions (aggregate_cms, add_cms_features) are import-safe and
take an already-loaded CMS DataFrame, so the same logic works no matter how the CMS
rows were obtained.
"""
import argparse
import re
import sys

import numpy as np
import pandas as pd

# CMS Provider Data Catalog -- Doctors & Clinicians "National Downloadable File".
# Verified 2026-07-20: dataset id mj5m-pzi6, modified 2026-03-27. The datastore query
# API returns lowercase field names (npi, adr_ln_1, zip_code, org_pac_id, ...).
CMS_DATASET_ID = "mj5m-pzi6"
CMS_API = "https://data.cms.gov/provider-data/api/1/datastore/query/%s/0" % CMS_DATASET_ID

# Column names we rely on, in the API's lowercase form. The bulk CSV uses Title/mixed
# case (NPI, adr_ln_1, ...); we lowercase all CMS columns on load so one code path works.
C_NPI = "npi"
C_STREET = "adr_ln_1"
C_STREET2 = "adr_ln_2"
C_ZIP = "zip_code"
C_STATE = "state"
C_CITY = "citytown"
C_ORG_PAC = "org_pac_id"
C_ORG_MEM = "num_org_mem"
C_FACILITY = "facility_name"
C_PRI_SPEC = "pri_spec"
C_TELEHLTH = "telehlth"

_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD",
    "LANE": "LN", "COURT": "CT", "PLACE": "PL", "HIGHWAY": "HWY", "PARKWAY": "PKWY",
    "SUITE": "STE", "APARTMENT": "APT", "FLOOR": "FL", "BUILDING": "BLDG", "NORTH": "N",
    "SOUTH": "S", "EAST": "E", "WEST": "W",
}


def _norm_street(s):
    """Light USPS-style normalize so we compare like-with-like (not raw strings)."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    s = str(s).upper().strip()
    s = re.sub(r"[.,#]", " ", s)
    s = re.sub(r"\s+", " ", s)
    tokens = [_SUFFIX.get(t, t) for t in s.split(" ")]
    return " ".join(tokens).strip()


def _zip5(z):
    if z is None:
        return ""
    m = re.sub(r"\D", "", str(z))
    return m[:5]


def _match_tier(roster_street, roster_zip, cms_street, cms_zip):
    """0 NONE / 1 ZIP / 2 STREET+ZIP / 3 EXACT -- mirrors the repo's claims tiers."""
    rs, cs = _norm_street(roster_street), _norm_street(cms_street)
    rz, cz = _zip5(roster_zip), _zip5(cms_zip)
    if not rs or not cs or not rz or not cz:
        # ZIP-only is the most we can claim if street is missing on either side.
        return 1 if (rz and cz and rz == cz) else 0
    if rs == cs and rz == cz:
        return 3
    # street-number + primary-name agreement with same ZIP = STREET+ZIP tier.
    if rz == cz and (rs.split(" ")[0] == cs.split(" ")[0]) and _street_core(rs) == _street_core(cs):
        return 2
    if rz == cz:
        return 1
    return 0


def _street_core(s):
    """Street without secondary unit (STE/APT/FL/UNIT ...) so a suite diff isn't a 'move'."""
    return re.split(r"\b(STE|APT|FL|UNIT|BLDG|RM|SUITE)\b", s)[0].strip()


# ----------------------------------------------------------------------------- CMS load
def load_cms_bulk(path, npis):
    """Read the bulk DAC file, keep only rows for our NPIs, lowercase columns."""
    keep = set(str(n) for n in npis)
    chunks = []
    for ch in pd.read_csv(path, dtype=str, chunksize=200_000):
        ch.columns = [c.lower() for c in ch.columns]
        if C_NPI not in ch.columns:
            raise SystemExit("Bulk file has no NPI column after lowercasing -- check the file.")
        chunks.append(ch[ch[C_NPI].astype(str).isin(keep)])
    cms = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    return cms


def fetch_cms_api(npis, verbose=True):
    """Query the public datastore per NPI. LOCAL USE ONLY (needs `requests`)."""
    import requests  # local dependency; not used in the restricted sandbox
    rows = []
    npis = list(dict.fromkeys(str(n) for n in npis))
    for i, npi in enumerate(npis):
        params = {
            "conditions[0][property]": "npi",
            "conditions[0][value]": npi,
            "limit": 100, "schema": "false", "keys": "false",
        }
        try:
            r = requests.get(CMS_API, params=params, timeout=30)
            r.raise_for_status()
            rows.extend(r.json().get("results", []))
        except Exception as e:  # keep going; absence is NULL, not failure
            if verbose:
                print("  ! %s -> %s" % (npi, e), file=sys.stderr)
        if verbose and (i + 1) % 100 == 0:
            print("  fetched %d/%d NPIs" % (i + 1, len(npis)), file=sys.stderr)
    cms = pd.DataFrame(rows)
    cms.columns = [c.lower() for c in cms.columns]
    return cms


# ------------------------------------------------------------------------ aggregate CMS
def aggregate_cms(cms: pd.DataFrame) -> pd.DataFrame:
    """One row per NPI with multi-site features + the raw location list for matching."""
    if cms is None or cms.empty:
        return pd.DataFrame(columns=[C_NPI])
    cms = cms.copy()
    for c in (C_NPI, C_STREET, C_ZIP, C_STATE, C_ORG_PAC, C_PRI_SPEC, C_FACILITY):
        if c not in cms.columns:
            cms[c] = ""
    cms["_loc_key"] = cms[C_STREET].map(_norm_street) + "|" + cms[C_ZIP].map(_zip5)

    rows = []
    for npi, g in cms.groupby(C_NPI, dropna=True):
        locs = g[g["_loc_key"] != "|"]["_loc_key"].unique().tolist()
        states = sorted(set(x for x in g[C_STATE].astype(str) if x and x.lower() != "nan"))
        orgs = sorted(set(x for x in g[C_ORG_PAC].astype(str) if x and x.lower() != "nan"))
        spec = next((x for x in g[C_PRI_SPEC].astype(str) if x and x.lower() != "nan"), "")
        mem = pd.to_numeric(g.get(C_ORG_MEM), errors="coerce").max() if C_ORG_MEM in g else np.nan
        tele = (g.get(C_TELEHLTH, pd.Series(dtype=str)).astype(str).str.upper() == "Y").any()
        rows.append({
            C_NPI: npi,
            "cms_dc_found": 1,
            "cms_num_practice_locations": len(locs),
            "cms_num_states": len(states),
            "cms_num_org_affiliations": len(orgs),
            "cms_primary_specialty": spec,
            "cms_max_group_size": mem,
            "cms_telehealth": int(bool(tele)),
            "cms_is_multisite": int(len(locs) > 1 or len(states) > 1),
            "_cms_loc_list": "||".join(locs),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- add features to base
def add_cms_features(base: pd.DataFrame, cms: pd.DataFrame,
                     npi_col="OrigNPI", street_col="Address1", zip_col="Zip") -> pd.DataFrame:
    """Left-join CMS aggregates onto base and compute roster-vs-CMS match tier.

    Absence is NULL, not contradiction: an NPI not found in CMS gets cms_dc_found=0 and
    the match features stay 0 -- never treat 'not in CMS' as 'address is wrong'.
    """
    agg = aggregate_cms(cms)
    b = base.copy()
    b["_npi"] = b[npi_col].astype(str)
    agg = agg.rename(columns={C_NPI: "_npi"})
    merged = b.merge(agg, on="_npi", how="left")

    merged["cms_dc_found"] = merged["cms_dc_found"].fillna(0).astype(int)
    for c in ["cms_num_practice_locations", "cms_num_states", "cms_num_org_affiliations",
              "cms_is_multisite", "cms_telehealth"]:
        merged[c] = pd.to_numeric(merged.get(c), errors="coerce").fillna(0).astype(int)

    # Match roster address against ANY of the NPI's CMS practice locations -> best tier.
    loc_lists = merged.get("_cms_loc_list", pd.Series("", index=merged.index)).fillna("")

    def _best(row):
        locs = str(row["_loc_list"]).split("||") if row["_loc_list"] else []
        best = 0
        for loc in locs:
            if "|" not in loc:
                continue
            cs, cz = loc.split("|", 1)
            best = max(best, _match_tier(row["_street"], row["_zip"], cs, cz))
            if best == 3:
                break
        return best

    tmp = pd.DataFrame({
        "_street": merged.get(street_col, ""),
        "_zip": merged.get(zip_col, ""),
        "_loc_list": loc_lists,
    })
    merged["cms_any_location_match_tier"] = tmp.apply(_best, axis=1).astype(int)
    merged["cms_matches_any_location"] = (merged["cms_any_location_match_tier"] >= 2).astype(int)
    # danger signal: found in CMS, is multi-site, but roster matches NONE of the CMS sites.
    merged["cms_multisite_no_match"] = (
        (merged["cms_dc_found"] == 1) & (merged["cms_is_multisite"] == 1)
        & (merged["cms_matches_any_location"] == 0)
    ).astype(int)

    return merged.drop(columns=["_npi", "_cms_loc_list"], errors="ignore")


# ------------------------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_csv")
    ap.add_argument("out_csv")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cms-bulk", metavar="PATH", help="path to DAC_NationalDownloadableFile.csv")
    src.add_argument("--cms-api", action="store_true", help="query the CMS datastore per NPI (local)")
    ap.add_argument("--npi-col", default="OrigNPI")
    args = ap.parse_args()

    base = pd.read_csv(args.base_csv, dtype=str)
    npis = base[args.npi_col].dropna().astype(str).unique().tolist()
    print("base rows=%d, unique NPIs=%d" % (len(base), len(npis)))

    if args.cms_bulk:
        cms = load_cms_bulk(args.cms_bulk, npis)
    else:
        cms = fetch_cms_api(npis)
    print("CMS rows matched=%d, NPIs found=%d"
          % (len(cms), cms[C_NPI].nunique() if C_NPI in cms.columns else 0))

    result = add_cms_features(base, cms, npi_col=args.npi_col)
    result.to_csv(args.out_csv, index=False)
    print("cms_dc_found=%d | matches_any_location=%d | multisite_no_match=%d"
          % (int(result["cms_dc_found"].sum()),
             int(result["cms_matches_any_location"].sum()),
             int(result["cms_multisite_no_match"].sum())))
    print("wrote -> %s" % args.out_csv)


if __name__ == "__main__":
    main()
