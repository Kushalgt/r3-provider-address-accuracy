import pandas as pd
import numpy as np
from datetime import date
from typing import Optional

# ---- Column name constants — adjust if your files use different names ----
# Base data
BASE_NPI_COL    = "OrigNPI"
BASE_ADDR_COL   = "Address1"
BASE_ZIP_COL    = "Zip"

# Claims data
CLAIM_HCP_NPI_COL = "CLAIMS_D_PRIMARY_HCP_NPI"           # join key (provider)
CLAIM_HCO_NPI_COL = "CLAIMS_D_PRIMARY_HCO_NPI"           # for DISTINCT_ORGS
CLAIM_ADDR_COL    = "HCO_ATTR_HCO_NPI_ADDRESS_LINE_1"    # claim address line 1
CLAIM_ZIP_COL     = "CLAIMS_PRIMARY_HCO_NPI_ZIP_5"       # claim ZIP5
CLAIM_DOS_COL     = "CLAIMS_DATE_OF_SERVICE"             # date of service


def _normalize_addr(s: pd.Series) -> pd.Series:
    """Uppercase + replace any non-[A-Z0-9 ] char with space. Mirrors the SQL regex."""
    return (
        s.astype("string")
         .str.upper()
         .str.replace(r"[^A-Z0-9 ]", " ", regex=True)
    )


def _street_num(s: pd.Series) -> pd.Series:
    """Extract leading street number (digits at start, after optional whitespace)."""
    return s.astype("string").str.extract(r"^\s*(\d+)", expand=False)


def _zip5(s: pd.Series) -> pd.Series:
    """First 5 chars of ZIP, as string."""
    return s.astype("string").str.slice(0, 5)


def build_claims_aggregates(
    base_df: pd.DataFrame,
    claims_df: pd.DataFrame,
    reference_date=None,
    recent_window_days: int = 180,
    include_npis_without_claims: bool = True,
    output_csv_path: Optional[str] = None,
) -> pd.DataFrame:
    """Build the same per-NPI claims-aggregate scorecard the Snowflake query produces.

    Args:
        base_df: DataFrame with at least OrigNPI, Address1, Zip.
        claims_df: DataFrame with at least the five CLAIM_* columns defined above.
        reference_date: date used for DAYS_SINCE and the recency window.
                        Defaults to today.
        recent_window_days: window for RECENT_* flags. Defaults to 180.
        include_npis_without_claims: if True, base NPIs with no claims appear in the
                                     output with N_CLAIMS=0 and NaT/0 elsewhere.
        output_csv_path: if given, the result is also written to this CSV.

    Returns:
        DataFrame with columns:
            BASE_NPI, N_CLAIMS, DISTINCT_ORGS, DISTINCT_ADDRS,
            MOST_RECENT_DOS, DAYS_SINCE,
            ADDR_EXACT_MATCH, ZIP_MATCH, STREET_ZIP_MATCH,
            RECENT_ZIP_MATCH, RECENT_STREET_ZIP_MATCH
    """
    if reference_date is None:
        reference_date = pd.Timestamp(date.today())
    else:
        reference_date = pd.Timestamp(reference_date)

    # ---- Validate required columns up front (clean error if a file is renamed) ----
    required_base = [BASE_NPI_COL, BASE_ADDR_COL, BASE_ZIP_COL]
    required_claims = [CLAIM_HCP_NPI_COL, CLAIM_HCO_NPI_COL,
                       CLAIM_ADDR_COL, CLAIM_ZIP_COL, CLAIM_DOS_COL]

    missing_base = [c for c in required_base if c not in base_df.columns]
    missing_claims = [c for c in required_claims if c not in claims_df.columns]

    if missing_base or missing_claims:
        msg_parts = []
        if missing_base:
            msg_parts.append(
                f"Missing in base_df: {missing_base}. "
                f"Available: {base_df.columns.tolist()}"
            )
        if missing_claims:
            msg_parts.append(
                f"Missing in claims_df: {missing_claims}. "
                f"Available: {claims_df.columns.tolist()}"
            )
        raise KeyError(" | ".join(msg_parts))

    # ---- Prepare base side ----
    b = base_df[[BASE_NPI_COL, BASE_ADDR_COL, BASE_ZIP_COL]].copy()
    b["base_npi_key"]     = b[BASE_NPI_COL].astype("string")
    b["base_addr_norm"]   = _normalize_addr(b[BASE_ADDR_COL])
    b["base_street_num"]  = _street_num(b[BASE_ADDR_COL])
    b["base_zip5"]        = _zip5(b[BASE_ZIP_COL])
    base_keys = b[["base_npi_key", "base_addr_norm", "base_street_num", "base_zip5"]] \
                  .drop_duplicates(subset=["base_npi_key"])

    # ---- Prepare claims side ----
    c = claims_df[[
        CLAIM_HCP_NPI_COL, CLAIM_HCO_NPI_COL,
        CLAIM_ADDR_COL, CLAIM_ZIP_COL, CLAIM_DOS_COL,
    ]].copy()
    c["claim_npi_key"]    = c[CLAIM_HCP_NPI_COL].astype("string")
    c["claim_addr_norm"]  = _normalize_addr(c[CLAIM_ADDR_COL])
    c["claim_street_num"] = _street_num(c[CLAIM_ADDR_COL])
    c["claim_zip5"]       = _zip5(c[CLAIM_ZIP_COL])
    c["dos"]              = pd.to_datetime(c[CLAIM_DOS_COL], errors="coerce")
    c["hco_npi"]          = c[CLAIM_HCO_NPI_COL]

    # ---- Inner join base ↔ claims on NPI ----
    merged = c.merge(base_keys, left_on="claim_npi_key", right_on="base_npi_key", how="inner")

    if merged.empty:
        agg = pd.DataFrame(columns=[
            "BASE_NPI", "N_CLAIMS", "DISTINCT_ORGS", "DISTINCT_ADDRS",
            "MOST_RECENT_DOS", "DAYS_SINCE",
            "ADDR_EXACT_MATCH", "ZIP_MATCH", "STREET_ZIP_MATCH",
            "RECENT_ZIP_MATCH", "RECENT_STREET_ZIP_MATCH",
        ])
    else:
        # Per-row match flags
        # Note: comparing nullable-string Series yields a nullable boolean with <NA>
        # where either side is missing. fillna(False) treats "missing on either side"
        # as "not a match" — the right semantic for address validation. Without this,
        # .astype(int) raises "cannot convert NA to integer".
        days_since_dos = (reference_date - merged["dos"]).dt.days
        within_window  = days_since_dos.le(recent_window_days).fillna(False)

        addr_exact_b = (merged["base_addr_norm"]  == merged["claim_addr_norm"]).fillna(False)
        zip_match_b  = (merged["base_zip5"]       == merged["claim_zip5"]).fillna(False)
        street_zip_b = ((merged["base_street_num"] == merged["claim_street_num"]) &
                        (merged["base_zip5"]       == merged["claim_zip5"])).fillna(False)

        merged["addr_exact"]        = addr_exact_b.astype(int)
        merged["zip_match"]         = zip_match_b.astype(int)
        merged["street_zip"]        = street_zip_b.astype(int)
        merged["recent_zip"]        = (zip_match_b & within_window).fillna(False).astype(int)
        merged["recent_street_zip"] = (street_zip_b & within_window).fillna(False).astype(int)

        # Group + aggregate (MAX of 0/1 = "did this ever hold")
        agg = merged.groupby("base_npi_key", as_index=False).agg(
            N_CLAIMS=("claim_addr_norm", "count"),
            DISTINCT_ORGS=("hco_npi", "nunique"),
            DISTINCT_ADDRS=("claim_addr_norm", "nunique"),
            MOST_RECENT_DOS=("dos", "max"),
            ADDR_EXACT_MATCH=("addr_exact", "max"),
            ZIP_MATCH=("zip_match", "max"),
            STREET_ZIP_MATCH=("street_zip", "max"),
            RECENT_ZIP_MATCH=("recent_zip", "max"),
            RECENT_STREET_ZIP_MATCH=("recent_street_zip", "max"),
        )
        agg = agg.rename(columns={"base_npi_key": "BASE_NPI"})
        agg["DAYS_SINCE"] = (reference_date - agg["MOST_RECENT_DOS"]).dt.days

    # ---- Optionally include base NPIs that had no claims ----
    if include_npis_without_claims:
        all_base = base_keys[["base_npi_key"]].rename(columns={"base_npi_key": "BASE_NPI"})
        agg = all_base.merge(agg, on="BASE_NPI", how="left")
        zero_cols = ["N_CLAIMS", "DISTINCT_ORGS", "DISTINCT_ADDRS",
                     "ADDR_EXACT_MATCH", "ZIP_MATCH", "STREET_ZIP_MATCH",
                     "RECENT_ZIP_MATCH", "RECENT_STREET_ZIP_MATCH"]
        agg[zero_cols] = agg[zero_cols].fillna(0).astype(int)

    # ---- Final column order (matches the SQL output) ----
    agg = agg[[
        "BASE_NPI", "N_CLAIMS", "DISTINCT_ORGS", "DISTINCT_ADDRS",
        "MOST_RECENT_DOS", "DAYS_SINCE",
        "ADDR_EXACT_MATCH", "ZIP_MATCH", "STREET_ZIP_MATCH",
        "RECENT_ZIP_MATCH", "RECENT_STREET_ZIP_MATCH",
    ]]

    if output_csv_path is not None:
        agg.to_csv(output_csv_path, index=False)

    return agg