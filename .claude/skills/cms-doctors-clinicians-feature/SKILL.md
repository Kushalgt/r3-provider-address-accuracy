---
name: cms-doctors-clinicians-feature
description: >
  Enrich the R3 model with CMS Doctors & Clinicians (Care Compare) practice-location
  data and build multi-site address features, using the bundled cms_dc_enrich.py script.
  CMS lists ONE ROW per clinician x enrollment x group x practice address, so a provider
  who works at several sites appears as several rows -- exactly the multi-site signal R3
  is missing on hospital-affiliated specialties (~2-5% agreement). Use this whenever the
  task involves joining CMS D&C / "National Downloadable File" (dataset mj5m-pzi6) onto
  the base data by NPI, adding cms_* features, matching a roster address against a
  provider's CMS practice locations, or scoring how many sites / states / org affiliations
  a clinician has. Trigger it even when the user only says "add CMS data", "pull Care
  Compare / Physician Compare", "how many practice locations does this doctor have",
  "does the roster address match any CMS site", "enrich with doctors and clinicians file",
  or references National Downloadable File, DAC_NationalDownloadableFile.csv, org_pac_id,
  or pri_spec. Always write output to a NEW file in data/processed/ -- never overwrite
  Base_enriched_sample.csv.
---

# CMS Doctors & Clinicians Feature Builder

## What this produces and why

Hospital-affiliated doctors rotate across several sites. R3 checks the web, which mostly
knows the **organization / primary** address, so it disagrees with the phone almost every
time on this group. The CMS **Doctors and Clinicians National Downloadable File** is a free,
authoritative fix: it is organized so that **each row is one clinician at one practice
address**, so a provider with multiple Medicare practice locations shows up as multiple rows.
Joining it in gives the model the **whole set of a provider's real sites**, so a valid
secondary site stops looking "wrong."

The verdict reframe (same as the hospital-affiliated skills): for this segment an address is
correct if it matches **ANY** of the provider's known sites, not only the primary one.

## Source (verified 2026-07-20)

- Dataset: CMS Provider Data Catalog, **"National Downloadable File"**, id **`mj5m-pzi6`**
  (landing page https://data.cms.gov/provider-data/dataset/mj5m-pzi6 ). Refreshed roughly
  monthly; re-check the `modified` date before trusting freshness.
- Two ways to get the data (the script supports both):
  - **Bulk CSV** (`DAC_NationalDownloadableFile.csv`, ~2.5M rows) from the dataset page.
  - **Per-NPI API**: `https://data.cms.gov/provider-data/api/1/datastore/query/mj5m-pzi6/0`
    with a `conditions[0][property]=npi` filter. Only the public 10-digit NPI leaves the
    machine (no names/addresses), which satisfies the HIPAA rule in CLAUDE.md.
- The API returns **lowercase** field names (`npi`, `adr_ln_1`, `zip_code`, `org_pac_id`,
  `num_org_mem`, `pri_spec`, `state`, `citytown`, `facility_name`, `telehlth`). The bulk CSV
  uses mixed case; the script lowercases all CMS columns on load so one code path works.

## Inputs to populate (what the base file must have)

The base file (e.g. `data/processed/Base_enriched_sample.csv`) must contain:
- **`OrigNPI`** — plaintext 10-digit NPI, the join key (pass a different name with `--npi-col`).
- **`Address1`** and **`Zip`** — the roster practice address, used to compute the match tier
  against CMS locations. (Change the column names in `add_cms_features(...)` if yours differ.)

Nothing else is required; every CMS field is fetched by the script.

## How to run

The bundled script is the source of truth: `scripts/cms_dc_enrich.py`.

```bash
# Full scale from the downloaded bulk file (recommended)
python scripts/cms_dc_enrich.py \
    data/processed/Base_enriched_sample.csv \
    data/processed/Base_enriched_cms.csv \
    --cms-bulk /path/to/DAC_NationalDownloadableFile.csv

# Or query the API per NPI (run locally; needs `requests`; no URL-length/throttle limits there)
python scripts/cms_dc_enrich.py \
    data/processed/Base_enriched_sample.csv \
    data/processed/Base_enriched_cms.csv \
    --cms-api
```

Or import the logic (it takes an already-loaded CMS DataFrame, so it works no matter how the
CMS rows were obtained — bulk, API, or a hand-built sample):

```python
from cms_dc_enrich import add_cms_features, aggregate_cms
df = add_cms_features(base_df, cms_df, npi_col="OrigNPI", street_col="Address1", zip_col="Zip")
```

**Always write to a NEW file in `data/processed/`.** Never overwrite `Base_enriched_sample.csv`
— the existing pipeline depends on it.

## Feature outputs

The script left-joins CMS aggregates (one row per NPI) onto the base and adds:

| Column | Meaning |
|---|---|
| `cms_dc_found` | 1 if the NPI exists in the CMS D&C file, else 0 |
| `cms_num_practice_locations` | distinct CMS practice addresses (street+ZIP) for this NPI |
| `cms_num_states` | distinct states across the NPI's CMS locations |
| `cms_num_org_affiliations` | distinct `org_pac_id` groups the clinician enrolls under |
| `cms_primary_specialty` | CMS `pri_spec` (authoritative specialty label) |
| `cms_max_group_size` | largest `num_org_mem` (group size) — big group ⇒ multi-site org |
| `cms_telehealth` | 1 if any CMS row flags telehealth |
| `cms_is_multisite` | 1 if `cms_num_practice_locations > 1` or `cms_num_states > 1` |
| `cms_any_location_match_tier` | best roster-vs-CMS match, 0 NONE / 1 ZIP / 2 STREET+ZIP / 3 EXACT |
| `cms_matches_any_location` | 1 if `cms_any_location_match_tier >= 2` (roster is one of the doctor's real CMS sites) |
| `cms_multisite_no_match` | 1 if found in CMS, multi-site, but roster matches NO CMS site — the **danger** signal |

## How this maps to the decision (why it lifts accuracy)

- `cms_matches_any_location == 1` → **KEEP guard**: the roster address is a genuine CMS
  practice location for this provider — don't let R3 remove a valid secondary site.
- `cms_multisite_no_match == 1` → **CALL**: the provider is real and multi-site, but the roster
  address isn't among the CMS sites we can see — a passive signal can't settle it, so verify.
- `cms_is_multisite` / `cms_num_practice_locations` are strong priors for the hospital-affiliated
  segment; pair them with `is_hospital_affiliated_specialty`.
- Never auto-remove on `cms_dc_found == 0` — absence is NULL, not contradiction (see below).

## Honesty limits (do not overclaim)

- **Absence is NULL, not contradiction.** Not being in the CMS file, or no CMS location
  matching the roster, does **not** prove the address is wrong — the site may simply not be
  captured. That's why the residual is `CALL`, not auto-remove.
- CMS D&C only covers clinicians **enrolled in Medicare**; providers outside Medicare are
  legitimately absent.
- CMS lags real-world moves (roughly monthly refresh) — a strong corroborator, not real-time.
- The address match is a light USPS-style normalize (suffix/directional abbreviations, ZIP5,
  suite-stripping) — a ZIP-only match (tier 1) is weak and should never overturn a phone verdict.

## Wiring into the model (retrain required)

Add the `cms_*` columns as a new feature family in `feature_config.yaml`, then `python train.py …`.
The pickled `models.pkl` expects its exact `feature_cols` schema, so editing the config without
retraining produces garbage. Feed `cms_dc_found` alongside the features so the model can tell
"not in CMS" from a real 0 on the other `cms_*` columns.

## Optional second source

The companion **Facility Affiliation** file links a clinician NPI to hospitals by CCN. It can
add explicit hospital-affiliation counts, but D&C alone already gives the multi-location and
group-size signal. Add it only if the extra hospital-linkage feature is needed; verify its
current dataset id/columns before parsing (CMS renames/restructures files).
