---
name: hospital-affiliated-address-feature
description: >
  Build ML features that judge whether a roster PRACTICE ADDRESS is correct for a
  HOSPITAL-AFFILIATED specialty (Pediatrics, Cardiology, OB/GYN, Internal Medicine,
  and other rotation-prone specialties) in the Final_R3_Model repo. These are R3's
  worst-performing records (~2–5% web-vs-phone agreement) because the doctor practices
  at several sites and R3 only checks the org / primary address. Use this whenever the
  task involves feature engineering for the hospital-affiliated / multi-site segment,
  an "org-linkage KEEP guard", matching a roster address against ALL of a provider's
  known locations, or adding these signals to feature_config.yaml / train.py. The core
  idea it enforces: for this segment the question is "does the address match ANY of the
  provider's real, current sites", NOT "is it THE one address". Trigger it even when the
  user only says "fix the hospital specialties", "multi-site match feature", "why do
  pediatricians fail", "keep guard", or references is_hospital_affiliated_specialty,
  nppes_any_location_match_tier, practiceLocations, or PIO / Provider_in_Organization.
---

# Hospital-Affiliated Address Feature Builder

## What this produces and why

Hospital-affiliated doctors rotate across several sites. R3 checks the web, which only
knows the **organization / primary** address, so it disagrees with the phone almost every
time on this group (~2–5% agreement in this project's data — a dataset-specific figure).

The fix is a reframe: **for this segment, an address is correct if it matches ANY of the
provider's real, current sites — not only the primary one.** These features give the model
that multi-site view, so it stops treating a valid secondary site as "wrong."

## The reframe, as features

Judge the roster address against the provider's **whole set of known sites**, built from
free evidence you already have:
- **NPPES all-locations match** — `nppes_any_location_match_tier` (primary + `practiceLocations`).
- **Claims location match** — `ADDR_EXACT_MATCH` / `STREET_ZIP_MATCH` / `ZIP_MATCH`, and the
  recent variants (`RECENT_STREET_ZIP_MATCH`) for "is this site currently active".
- **Multi-site evidence** — `nppes_num_locations`, claims `DISTINCT_ADDRS`, and the number of
  distinct org sources in `Provider_in_Organization` (PIO).

The best match across NPPES-any-location and claims is the real signal — call it the
**best known-site tier**. A street+ZIP or exact match to *any* site means the roster address
is one of the doctor's genuine locations.

## Gating: focus the segment-specific outputs on hospital-affiliated rows

Compute the raw match signals (best tier, any-site match, multi-site, recent activity) for
**all** rows — they're generally useful. But the **verdict** (`haf_keep_guard`) and the
**interaction** features are meaningful only where `is_hospital_affiliated_specialty == 1`;
elsewhere set them not-applicable / 0 and expose a mask `haf_is_checked` so the model can tell
"not a hospital specialty" from a real 0. This mirrors the staleness skill's gating pattern.

`is_hospital_affiliated_specialty` is a keyword heuristic (see the
`hospital-affiliated-specialty` skill); it's a useful prior, not a per-doctor employment
lookup.

## Feature outputs

| Column | Meaning |
|---|---|
| `haf_flag` | 1 if hospital-affiliated specialty (from `is_hospital_affiliated_specialty`) |
| `best_known_site_tier` | max match tier (0–3) across NPPES-any-location and claims |
| `matches_any_known_site` | 1 if `best_known_site_tier >= 2` (street+ZIP or exact to some site) |
| `is_multisite` | 1 if `nppes_num_locations>1` or `DISTINCT_ADDRS>1` or >1 PIO org source |
| `recent_site_activity` | 1 if recent claims bill from the roster street+ZIP |
| `haf_x_any_site_match` | `haf_flag` × `matches_any_known_site` (the KEEP-guard signal) |
| `haf_x_recent_activity` | `haf_flag` × `recent_site_activity` |
| `haf_x_no_site_match` | `haf_flag` × (has evidence but matches no known site) — the danger signal |
| `haf_keep_guard` | `keep_multisite_confirmed` / `verify_call` / `unresolved_site` / `no_evidence` / `not_applicable` |
| `haf_is_checked` | 1 where `haf_flag==1` (verdict/interactions are real), else 0 |

`haf_keep_guard` priority: `keep_multisite_confirmed` (matches a site, recent or NPPES-listed)
> `verify_call` (matches a known site but no recent activity) > `unresolved_site` (has evidence
but matches no known site → call, don't auto-remove) > `no_evidence` (nothing to go on) >
`not_applicable` (not hospital-affiliated).

## How this maps to the decision (why it lifts accuracy)

- `keep_multisite_confirmed` → **KEEP guard**: stop R3 from removing a valid secondary site
  (the dominant failure on this segment).
- `verify_call` / `unresolved_site` → **CALL**: passive signals can't settle it; spend budget here.
- Never auto-remove on "no known-site match" — absence is NULL, not contradiction.
- Ignore R3's web confidence for this segment; it is inverted at the top in this data.

## Reference implementation

```bash
python skills/hospital-affiliated-address-feature/scripts/build_haf_features.py \
    data/processed/Base_enriched_sample.csv \
    data/processed/Base_enriched_haf.csv
```

```python
from scripts.build_haf_features import add_haf_features
df = add_haf_features(df)
```

The script is the source of truth for the exact logic.

## Honesty limits

- **Absence is NULL, not contradiction.** No claims/NPPES match to any site does not prove the
  address is wrong — the site may simply not be captured. That's why the residual is `CALL`,
  not auto-remove.
- Claims service address is usually the practice site but can occasionally be a billing/org
  address — a claims match is strong, not absolute.
- On the sample file NPPES is populated for only 58 rows, so `nppes_any_location_match_tier` is
  mostly 0 there; run the full NPPES fetch to make the multi-site signal real at scale.
- `is_hospital_affiliated_specialty` is specialty-level; confirm a specific provider with
  claims/NPPES before trusting the segment label for one record.

## Wiring into the model (retrain required)

Add as a feature family in `feature_config.yaml`, then `python train.py …`. The pickled
`models.pkl` expects its trained `feature_cols` schema, so editing the config without
retraining produces garbage. Feed `haf_is_checked` alongside the features so the model can
separate "not a hospital specialty" from a genuine 0.
