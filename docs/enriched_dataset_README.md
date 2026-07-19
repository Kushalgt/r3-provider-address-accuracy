# Enriched / Merged Dataset — Base + Claims + NPPES

One merged row per Base-Data record (provider × location), left-joining the free
triangulation sources onto the plaintext NPI (`OrigNPI`). Built by
`build_enriched_dataset.py` using `nppes_enrich.py`.

## Files

| File | What it is |
|---|---|
| `data/processed/Base_enriched_sample.csv` | **2,493 × 93.** Full base + full claims merge for all rows; NPPES columns populated on the 58 rows covered by the 30-NPI live sample, NULL elsewhere. Proves the enrichment path end-to-end offline. |
| `nppes_enrich.py` | Reusable NPPES module: live fetch, pure parser, feature build, address match-tiers, JSON cache. |
| `build_enriched_dataset.py` | Orchestrator: `load_base → merge_claims → merge_nppes → CSV`. |
| `data/external/nppes_sample.json` | 30-NPI cache used for the offline demo (29 found, 1 unassigned). |
| `data/external/_build_nppes_sample.py` | Generator that produced the sample cache (provenance of the sampled values). |

## Run at full scale (needs internet)

```bash
python build_enriched_dataset.py --nppes-live \
    --save-cache data/external/nppes_cache.json \
    --out data/processed/Base_enriched_merged.csv
```

This fetches all 1,949 unique NPIs from the public NPPES API (only the 10-digit
NPI leaves the machine — never names/addresses), then populates every NPPES
column for every matched row. Re-run offline later with
`--nppes-cache data/external/nppes_cache.json`.

## Join discipline (why counts don't inflate)

- Key = `OrigNPI` as a **string** (`str.split('.').str[0]`); claims `BASE_NPI`
  carries a `.0` float tail that is stripped the same way. Never cast NPI to int.
- Each source is aggregated to **one row per NPI before joining**. Base grain is
  provider × location, so 515 NPIs repeat across rows; a raw multi-row join would
  fan out and double-count. Example: 1,223 unique NPIs have claims → those expand
  to 1,632 base rows, but per-NPI claim *values* are unchanged.
- **Absence is NULL, not contradiction.** `found_in_claims` / `nppes_found` = 0
  means "no external witness," not "address is wrong."
- 29 rows have no `OrigNPI` → kept, flagged `has_external_key = 0`.

## Coverage (this build)

| Signal | Rows |
|---|---|
| Total base rows | 2,493 |
| `has_external_key = 1` | 2,464 |
| `found_in_claims = 1` | 2,464 |
| `N_CLAIMS > 0` (rows / unique NPIs) | 1,632 / 1,223 |
| `nppes_found = 1` (sample only) | 58 |

## Columns added

**Keys/flags:** `OrigNPI_key`, `has_external_key`.

**Claims (existing 10 + 6 derived):** `N_CLAIMS`, `DISTINCT_ORGS`,
`DISTINCT_ADDRS`, `DAYS_SINCE`, `ADDR_EXACT_MATCH`, `ZIP_MATCH`,
`STREET_ZIP_MATCH`, `RECENT_ZIP_MATCH`, `RECENT_STREET_ZIP_MATCH`,
`MOST_RECENT_DOS`, `found_in_claims`, `claims_has_any`, `claims_recent_active`
(DAYS_SINCE ≤ 180), `claims_log_volume`, `claims_strong_corroborate`
(RECENT_STREET_ZIP_MATCH > 0), `claims_strong_contradict` (N_CLAIMS ≥ 20 &
ZIP_MATCH = 0).

**NPPES (free registry + NUCC taxonomy desc + partial multi-site):**
`nppes_found`, `nppes_entity_type`, `nppes_is_org`, `nppes_deactivated`,
`nppes_last_updated`, `nppes_days_since_update` (staleness),
`nppes_credential`, `nppes_primary_taxonomy_code`, `nppes_primary_taxonomy_desc`,
`is_hospital_affiliated_specialty` (rotation-prone specialties that agree with
the phone ~2–5%), `nppes_practice_addr1/zip`, `nppes_mailing_addr1/zip`,
`nppes_practice_ne_mailing` (practice ≠ mailing mobility signal),
`nppes_num_locations` (multi-site count),
`nppes_practice_addr_match_tier`/`_label` (base R3 addr vs NPPES primary practice),
`nppes_any_location_match_tier`/`_label` (best match across all NPPES locations).

Match tiers: `NONE=0, ZIP=1, STREET_ZIP=2, EXACT=3` (recency handled on the
claims side). A ZIP-only match should never overturn a phone verdict.

## Validation spot-checks (sample)

- `1083291587`: base `13560 E MCNICHOLS RD` vs NPPES `13500 E MCNICHOLS RD`,
  same ZIP → **ZIP** (house numbers differ — real "close but wrong building").
- `1063067304` (3 locations) & `1659876282`: primary practice = NONE but base row
  matches a *secondary* practice location → **any_location = EXACT**.
- `1033397518`: only the suite differs (`STE 331`) → **STREET_ZIP**, not a false move.
- Staleness ranges 94 → 6,948 days; deactivation 0 for all (all status "A").

## Not included (paid — excluded this pass)

USPS CASS/DPV features (`dpv_confirmed`, `rdi_residential`, geocode distances)
require a paid vendor and were out of scope. CMS Doctors & Clinicians and FSMB
licensure were also skipped; NPPES `practiceLocations` covers most of the
multi-site signal for free. See `.claude/skills/provider-data-triangulation/`.

## Feeding the model

Adding these as a new feature family requires a **retrain** — `models.pkl`
expects the exact `feature_cols` schema it was trained on. Wire the columns into
`feature_config.yaml`, then `python train.py …`.
