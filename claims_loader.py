"""
================================================================================
R3 ADDRESS ACCURACY — CLAIMS LOADER
================================================================================

PURPOSE
-------
Production claims data lives in Snowflake. The base-data file is the only
input we receive at inference time; we pull NPIs from it and run the
aggregation query in Snowflake on demand.

This module is a thin adapter so the rest of the pipeline doesn't have to
know whether claims came from a database, a CSV, or somewhere else. It
exposes one main function:

    load_claims_aggregates(base_df, source) -> DataFrame

where `source` is either:
  - a `snowflake.connector` connection (production)
  - a path to a CSV file (testing)
  - the string 'empty' (force the claims layer to be skipped)

OUTPUT SCHEMA (always this regardless of source)
-----------------------------------------------
    BASE_NPI                      (int)
    N_CLAIMS                      (int)
    DISTINCT_ORGS                 (int)
    DISTINCT_ADDRS                (int)
    MOST_RECENT_DOS               (date or NaT)
    DAYS_SINCE                    (int or NaN)
    ADDR_EXACT_MATCH              (0/1)
    ZIP_MATCH                     (0/1)
    STREET_ZIP_MATCH              (0/1)
    RECENT_ZIP_MATCH              (0/1)
    RECENT_STREET_ZIP_MATCH       (0/1)

NPIs in the base file that have no matching claims simply don't appear
in the output. The merge step in features.py handles them by filling
with 0/sentinel values.

DEPENDENCY (production only)
----------------------------
    pip install snowflake-connector-python
"""

import pandas as pd
import numpy as np
from claims_merger import build_claims_aggregates
from utis import load_dataframe
# ============================================================================
# THE SQL QUERY (parameterized by NPI list)
# ============================================================================

CLAIMS_AGGREGATION_SQL = """
WITH input_npis AS (
    -- The list of NPIs we care about, passed in as a temp table
    SELECT DISTINCT npi FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%(npi_json)s))) AS f(npi)
),
provider_claims AS (
    SELECT
        b."OrigNPI"                                       AS base_npi,
        b."Address1"                                      AS base_addr,
        UPPER(REGEXP_REPLACE(b."Address1", '[^A-Z0-9 ]', ' '))   AS base_addr_norm,
        REGEXP_SUBSTR(b."Address1", '^\\\\s*(\\\\d+)')             AS base_street_num,
        LEFT(b."Zip"::STRING, 5)                          AS base_zip5,
        c.HCO_ATTR_HCO_NPI_ADDRESS_LINE_1                 AS claim_addr,
        UPPER(REGEXP_REPLACE(c.HCO_ATTR_HCO_NPI_ADDRESS_LINE_1, '[^A-Z0-9 ]', ' '))  AS claim_addr_norm,
        REGEXP_SUBSTR(c.HCO_ATTR_HCO_NPI_ADDRESS_LINE_1, '^\\\\s*(\\\\d+)')           AS claim_street_num,
        LEFT(c.HCO_ATTR_HCO_NPI_ZIP_5::STRING, 5)         AS claim_zip5,
        TO_DATE(c.CLAIMS_DATE_OF_SERVICE_formatted)       AS dos,
        c.CLAIMS_D_PRIMARY_HCO_NPI                        AS hco_npi
    FROM "R3_ADDRESS_ACCURACY_DATA"."PUBLIC"."R3_BASEDATA" b
    INNER JOIN input_npis n           ON b."OrigNPI"::STRING = n.npi::STRING
    INNER JOIN "R3_ADDRESS_ACCURACY_DATA"."PUBLIC"."R3_CLAIMS_DATA" c
                                      ON b."OrigNPI" = c.CLAIMS_D_PRIMARY_HCP_NPI
)
SELECT
    base_npi                                                                AS BASE_NPI,
    COUNT(claim_addr_norm)                                                  AS N_CLAIMS,
    COUNT(DISTINCT hco_npi)                                                 AS DISTINCT_ORGS,
    COUNT(DISTINCT claim_addr_norm)                                         AS DISTINCT_ADDRS,
    MAX(dos)                                                                AS MOST_RECENT_DOS,
    DATEDIFF('day', MAX(dos), CURRENT_DATE())                               AS DAYS_SINCE,
    MAX(CASE WHEN base_addr_norm = claim_addr_norm THEN 1 ELSE 0 END)       AS ADDR_EXACT_MATCH,
    MAX(CASE WHEN base_zip5 = claim_zip5 THEN 1 ELSE 0 END)                 AS ZIP_MATCH,
    MAX(CASE WHEN base_street_num = claim_street_num
              AND base_zip5 = claim_zip5 THEN 1 ELSE 0 END)                 AS STREET_ZIP_MATCH,
    MAX(CASE WHEN base_zip5 = claim_zip5
              AND DATEDIFF('day', dos, CURRENT_DATE()) <= 180
              THEN 1 ELSE 0 END)                                            AS RECENT_ZIP_MATCH,
    MAX(CASE WHEN base_street_num = claim_street_num
              AND base_zip5 = claim_zip5
              AND DATEDIFF('day', dos, CURRENT_DATE()) <= 180
              THEN 1 ELSE 0 END)                                            AS RECENT_STREET_ZIP_MATCH
FROM provider_claims
GROUP BY base_npi;
"""


EMPTY_CLAIMS_SCHEMA = pd.DataFrame(columns=[
    'BASE_NPI', 'N_CLAIMS', 'DISTINCT_ORGS', 'DISTINCT_ADDRS',
    'MOST_RECENT_DOS', 'DAYS_SINCE',
    'ADDR_EXACT_MATCH', 'ZIP_MATCH', 'STREET_ZIP_MATCH',
    'RECENT_ZIP_MATCH', 'RECENT_STREET_ZIP_MATCH',
])


# ============================================================================
# LOADER
# ============================================================================

def _extract_npis(base_df):
    """Get unique non-null NPIs from the base data as a list of strings."""
    npis = pd.to_numeric(base_df['OrigNPI'], errors='coerce').dropna()
    npis = npis.astype('int64').astype(str).unique().tolist()
    return npis


def load_from_snowflake(base_df, conn):
    """Run the aggregation query in Snowflake, scoped to the NPIs in base_df.

    Args:
        base_df: pd.DataFrame with at least an 'OrigNPI' column
        conn: live snowflake.connector.SnowflakeConnection

    Returns:
        pd.DataFrame with the standard claims aggregate schema
    """
    import json
    npis = _extract_npis(base_df)
    if not npis:
        return EMPTY_CLAIMS_SCHEMA.copy()

    cursor = conn.cursor()
    try:
        cursor.execute(CLAIMS_AGGREGATION_SQL, {'npi_json': json.dumps(npis)})
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return pd.DataFrame(rows, columns=cols)
    finally:
        cursor.close()


def load_from_csv(base_df, csv_path):
    """Load pre-computed claims aggregates from a CSV file.

    Useful for offline testing — the CSV should have the same schema the
    Snowflake query produces.

    Args:
        base_df: pd.DataFrame with 'OrigNPI' column (used to filter the CSV
                  to just the NPIs we care about)
        csv_path: str or Path to a CSV with the standard schema

    Returns:
        pd.DataFrame with the standard claims aggregate schema
    """
    claims_df = load_dataframe(csv_path)
    # Build the aggregates
    result = build_claims_aggregates(
        base_df=base_df,
        claims_df=claims_df,
        output_csv_path="claims_aggregates.csv",  # optional — writes CSV if given
    )
    # result is also returned as a DataFrame, so you can use it directly
    print(result.head())
    return result


def load_empty(base_df):
    """Return an empty claims aggregate frame.

    Useful for fallback when Snowflake is unavailable — the pipeline still
    runs, but features that need claims data will be filled with sentinels
    (zero counts, 9999 days_since). The model handles this gracefully
    because it was trained on data where ~35% of records have no claims.
    """
    return EMPTY_CLAIMS_SCHEMA.copy()


# ============================================================================
# CONVENIENCE WRAPPER
# ============================================================================
def build_claims_aggregates_from_excel(
    base_df,
    claims_path: str,
    claims_sheet=0,
    **kwargs,
) -> pd.DataFrame:
    claims_df = pd.read_excel(claims_path, sheet_name=claims_sheet)
    return build_claims_aggregates(base_df, claims_df, **kwargs)


def load_claims_aggregates(base_df, source='empty'):
    """Single entry point for loading claims aggregates.

    Args:
        base_df: pd.DataFrame with 'OrigNPI' column.
        source: one of:
            * a snowflake.connector connection      → load_from_snowflake
            * a string path to a CSV file           → load_from_csv
            * the string 'empty' or None            → load_empty
            * a pd.DataFrame already in the schema  → return it as-is

    Returns:
        pd.DataFrame with the standard claims aggregate schema.

    Examples:
        # Production
        import snowflake.connector
        conn = snowflake.connector.connect(user=..., password=..., ...)
        agg = load_claims_aggregates(base_df, conn)

        # Offline testing
        agg = load_claims_aggregates(base_df, 'merged.csv')

        # No claims (fallback)
        agg = load_claims_aggregates(base_df, 'empty')
    """
    if source is None or (isinstance(source, str) and source.lower() == 'empty'):
        return load_empty(base_df)
    if isinstance(source, pd.DataFrame):
        return source
    if isinstance(source, str):
        
        return load_from_csv(base_df, source)
    # Otherwise assume it's a Snowflake connection
    return load_from_snowflake(base_df, source)
