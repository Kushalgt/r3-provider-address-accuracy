"""
================================================================================
BUILD ENRICHED / MERGED DATASET  (Base + Claims + NPPES)
================================================================================

Produces one merged row per Base-Data record (provider x location), left-joining
the free triangulation sources onto `OrigNPI`:

  Base Data  (data/raw/Base data_hackathon.xlsx, sheet "Base Data", header row 1)
    + Claims (data/external/claims_data.csv, key BASE_NPI)         [local, free]
    + NPPES  (CMS NPI Registry, key NPI)                           [free API]

Join discipline (from the provider-data-triangulation skill):
  * key = OrigNPI as a STRING (never int); claims BASE_NPI has a '.0' tail.
  * aggregate every source to ONE ROW PER NPI before joining (Base has ~515
    duplicate OrigNPI values → a raw join would fan rows out).
  * absence is NULL, not contradiction → found_in_<source> flags, missing = 0.
  * ~29 rows have no OrigNPI → kept, flagged has_external_key = 0.

NPPES access modes (the sandbox has no internet, so this supports both):
  --nppes-live            fetch every unique NPI live over HTTP (needs internet)
  --nppes-cache PATH      use a saved {npi: result} JSON (offline; what the
                          in-session sample demo uses)
  (neither)               claims-only merge; NPPES columns are written as NULL

USAGE
  # full run on a machine with internet:
  python build_enriched_dataset.py --nppes-live \
      --out data/processed/Base_enriched_merged.csv \
      --save-cache data/external/nppes_cache.json

  # offline, from a cache (or the demo sample):
  python build_enriched_dataset.py --nppes-cache data/external/nppes_sample.json \
      --out data/processed/Base_enriched_sample.csv
"""

import argparse
import datetime as _dt

import numpy as np
import pandas as pd

import nppes_enrich as ne

BASE_XLSX = 'data/raw/Base data_hackathon.xlsx'
CLAIMS_CSV = 'data/external/claims_data.csv'


def norm_npi(s):
    """Series/str → clean 10-digit string key ('123.0' → '123'); blanks → NaN."""
    out = (pd.Series(s).astype(str)
           .str.strip()
           .str.split('.').str[0])
    return out.mask(out.isin(['nan', 'None', 'NaN', '']), np.nan)


def load_base(path=BASE_XLSX):
    df = pd.read_excel(path, sheet_name='Base Data', header=1, dtype=str)
    df['OrigNPI_key'] = norm_npi(df['OrigNPI'])
    df['has_external_key'] = df['OrigNPI_key'].notna().astype(int)
    return df


def merge_claims(base, path=CLAIMS_CSV):
    claims = pd.read_csv(path, dtype=str)
    claims['BASE_NPI_key'] = norm_npi(claims['BASE_NPI'])
    claims = claims.dropna(subset=['BASE_NPI_key'])
    # one row per NPI (defensive — claims is already unique per BASE_NPI)
    num_cols = ['N_CLAIMS', 'DISTINCT_ORGS', 'DISTINCT_ADDRS', 'DAYS_SINCE',
                'ADDR_EXACT_MATCH', 'ZIP_MATCH', 'STREET_ZIP_MATCH',
                'RECENT_ZIP_MATCH', 'RECENT_STREET_ZIP_MATCH']
    for c in num_cols:
        if c in claims.columns:
            claims[c] = pd.to_numeric(claims[c], errors='coerce')
    agg = {c: 'max' for c in num_cols if c in claims.columns}
    agg['MOST_RECENT_DOS'] = 'max'
    claims1 = claims.groupby('BASE_NPI_key', as_index=False).agg(agg)
    claims1['found_in_claims'] = 1

    out = base.merge(claims1, how='left',
                     left_on='OrigNPI_key', right_on='BASE_NPI_key')
    out['found_in_claims'] = out['found_in_claims'].fillna(0).astype(int)
    # derived claims signals (extend the existing 10)
    out['claims_has_any'] = (out['N_CLAIMS'].fillna(0) > 0).astype(int)
    out['claims_recent_active'] = ((out['DAYS_SINCE'].notna()) &
                                   (out['DAYS_SINCE'] <= 180)).astype(int)
    out['claims_log_volume'] = np.log1p(out['N_CLAIMS'].fillna(0))
    out['claims_strong_corroborate'] = (out['RECENT_STREET_ZIP_MATCH'].fillna(0) > 0).astype(int)
    out['claims_strong_contradict'] = ((out['N_CLAIMS'].fillna(0) >= 20) &
                                       (out['ZIP_MATCH'].fillna(0) == 0)).astype(int)
    return out.drop(columns=['BASE_NPI_key'])


def merge_nppes(base, results_by_npi, as_of):
    npis = sorted(base['OrigNPI_key'].dropna().unique().tolist())
    spec_by_npi = (base.dropna(subset=['OrigNPI_key'])
                   .drop_duplicates('OrigNPI_key')
                   .set_index('OrigNPI_key')['Specialty'].to_dict())
    feats = ne.build_nppes_features(npis, results_by_npi, as_of=as_of,
                                    base_specialty_by_npi=spec_by_npi)
    out = base.merge(feats, how='left', left_on='OrigNPI_key', right_on='OrigNPI',
                     suffixes=('', '_nppesdup'))
    if 'OrigNPI_nppesdup' in out.columns:
        out = out.drop(columns=['OrigNPI_nppesdup'])
    out['nppes_found'] = out['nppes_found'].fillna(0).astype(int)
    out = ne.add_row_level_match(out, addr_col='Address1', zip_col='Zip')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE_XLSX)
    ap.add_argument('--claims', default=CLAIMS_CSV)
    ap.add_argument('--out', default='data/processed/Base_enriched_merged.csv')
    ap.add_argument('--nppes-live', action='store_true',
                    help='fetch all unique NPIs live over HTTP (needs internet)')
    ap.add_argument('--nppes-cache', default=None,
                    help='path to a saved {npi: result} JSON to use offline')
    ap.add_argument('--save-cache', default=None,
                    help='when --nppes-live, also save the fetched cache here')
    ap.add_argument('--as-of', default=None,
                    help='reference date for staleness (YYYY-MM-DD); default today')
    args = ap.parse_args()

    as_of = _dt.date.fromisoformat(args.as_of) if args.as_of else _dt.date.today()

    print('Loading base…')
    base = load_base(args.base)
    print(f'  {len(base)} rows, {base["OrigNPI_key"].nunique()} unique NPIs, '
          f'{(base["has_external_key"]==0).sum()} keyless')

    print('Merging claims (local)…')
    merged = merge_claims(base, args.claims)
    print(f'  claims matched on {merged["found_in_claims"].sum()} rows')

    results_by_npi = {}
    if args.nppes_live:
        npis = sorted(base['OrigNPI_key'].dropna().unique().tolist())
        print(f'Fetching NPPES live for {len(npis)} NPIs…')
        results_by_npi = ne.fetch_many_live(npis)
        if args.save_cache:
            ne.save_cache(results_by_npi, args.save_cache)
            print(f'  cache saved → {args.save_cache}')
    elif args.nppes_cache:
        print(f'Loading NPPES cache ← {args.nppes_cache}')
        results_by_npi = ne.load_cache(args.nppes_cache)
    else:
        print('No NPPES source given → NPPES columns will be NULL (claims-only).')

    print('Merging NPPES…')
    merged = merge_nppes(merged, results_by_npi, as_of=as_of)
    if results_by_npi:
        print(f'  NPPES matched on {merged["nppes_found"].sum()} rows')

    # drop the python-list helper column before writing to CSV
    if 'nppes_location_addrs' in merged.columns:
        merged['nppes_num_locations'] = merged['nppes_num_locations'].fillna(0)
        merged = merged.drop(columns=['nppes_location_addrs'])

    import os
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    merged.to_csv(args.out, index=False)
    print(f'Wrote {len(merged)} rows × {len(merged.columns)} cols → {args.out}')


if __name__ == '__main__':
    main()
