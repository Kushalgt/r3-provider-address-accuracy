---
name: provider-data-triangulation
description: >
  How to enrich a provider dataset by merging it with external U.S. provider-data
  sources (NPPES, NUCC taxonomy, CMS Doctors & Clinicians, billing claims, state
  licensing/FSMB, DEA, USPS CASS/DPV) to get independent corroboration of where a
  provider practices. Use this skill whenever the task involves multi-source
  triangulation, "merge/enrich/join our provider data," combining Base Data with the
  NPI registry or claims, adding external signals to improve address prediction, or
  deciding which provider data source is worth the cost. Trigger it even when the user
  says only "how do I merge on NPI", "what other data would help predict the address",
  "should we buy license/DEA data", "join NPPES to our records", or references bulk
  provider files, staleness features, or multi-location providers. Prefer this skill
  over generic data-joining advice because provider-source joins have healthcare-specific
  key, grain, and licensing traps. Pair it with `provider-address-validation` for the
  domain reasoning behind the features this skill builds.
---

# Provider Data Triangulation

Enriching a provider dataset means joining it to *independent* sources so a model has more
than one witness to where each provider actually practices. The goal is corroboration, not
just more columns: NPPES, claims, CMS enrollment, licensure, and USPS deliverability each see
a different slice of "where is this provider," and disagreements between them are exactly the
signal that predicts a wrong address.

This skill encodes the *mechanics* of doing that safely — the right join key, the grain trap,
the free-first source order, and the licensing landmines. For the domain reasoning about
*why* these signals predict address correctness, pair this with the
`provider-address-validation` skill.

Read this whole file when triggered. Read `references/source-details.md` on demand for
per-source columns, access, and cost detail.

## When this applies

Use this skill for any task that merges or enriches provider records with external data:
joining Base Data to the NPI registry or claims, adding staleness / multi-location /
licensure / deliverability features, or deciding whether a paid source is worth buying. Do
**not** reach for generic "join two dataframes on a key" advice — provider sources have a
specific key (NPI), a specific grain trap (provider×location), and specific licensing rules
that generic advice will miss.

## The five rules that keep triangulation honest

1. **Join everything on the plaintext NPI.** In this project that key is `OrigNPI`. It is the
   only real, joinable identifier in the Base Data. A previously-present hashed `NPI` column
   was removed because it joined to nothing — do not resurrect it. Claims join via `BASE_NPI`,
   which is 1:1 with `OrigNPI` after string-normalizing both (`str.split('.').str[0]`, keep as
   string — never cast NPI to int).

2. **Aggregate each source to one row per NPI *before* joining.** The Base grain is
   provider×location, so a single NPI can appear on several rows (this dataset has ~515
   duplicate `OrigNPI` values). If you left-join a raw multi-row source onto that, rows fan out
   and counts inflate. Collapse the source with `groupby(NPI)` (max/most-recent for signals,
   sum for counts) first, then join. This mirrors how claims are already collapsed.

3. **Absence is NULL, not contradiction.** A provider missing from NPPES, CMS, or a licensing
   board is *no evidence* the address is wrong — only presence corroborates. Encode every
   source as a `found_in_<source>` flag plus its signal; a missing source is 0/NULL, never a
   negative score. (Only ~28.7% of practitioners are org-linked in CMS files, so absence is
   common and uninformative.)

4. **Corroboration proves existence, not location.** An NPI, an active license, or a DEA
   registration proves the provider *exists and is authorized*; none of them prove they
   practice at *this* address. Weight "the provider is real" evidence far below "a recent claim
   was billed from this exact street+ZIP" evidence.

5. **Validate the practice location, not the mailing/registered address.** Several sources
   carry both a mailing/administrative address and a practice/service address. Always target
   the practice location; a practice≠mailing gap is itself a weak mobility signal, not the
   thing to match on.

## Keyless records

Records missing the NPI key (this dataset has ~29 with no `OrigNPI`) cannot be externally
matched. Keep them — do not drop — and flag `has_external_key = 0` so the model can learn that
these records simply have no external corroboration available (which is different from being
contradicted).

## Source priority — free first, always

Do the free backbone before spending a cent. The free sources hit the two dominant failure
modes (staleness and multi-site) directly; paid sources add marginal, fuzzier signal.

| Priority | Source | Access | Cost (verify) | What it adds |
|---|---|---|---|---|
| **P0** | NPPES full dissemination file | bulk, free | $0 | Independent registry practice address, last-update date (staleness), deactivation, entity type. |
| **P0** | NUCC taxonomy crosswalk | bulk, free | $0 | Maps taxonomy code → specialty; derives hospital-affiliated-specialty instability flags. |
| **P1** | CMS Doctors & Clinicians National Downloadable File | bulk, free | $0 | *Natively multi-location* — directly attacks the multi-site error pattern; hospital affiliation (via CCN). |
| **P1** | USPS CASS/DPV enrichment (e.g. Smarty) | per-lookup | ~$1.50–$10 for 2,493 addrs | Real-delivery-point gate, residential-vs-commercial flag, vacancy, geocode → R3↔corroborator distance. |
| Have it | Billing claims aggregate | already merged | $0 | Strongest passive proxy (records with claims agree ~74% vs ~39% without). |
| **P2** | FSMB Physician Data Center (state licensure) | API/MFT | ~$12/physician, MD/DO/PA only → ~$9k subset / ~$30k all | License active/expired; extra (fuzzy, name+state) corroborating address. Subset only. |
| Skip/future | DEA registrations | restricted | no open feed since 2020-11-17 | Only if HiLabs already licenses a feed; NPPES/CMS/FSMB status are compliant substitutes. |
| Skip | Commercial golden-record vendors (IQVIA, Definitive, LexisNexis, CarePrecise) | enterprise | quote-only | Overkill for a 2,493-record exercise. |

Do all of P0 + P1 before spending anything beyond the ~$10 USPS pass. See
`references/source-details.md` for each source's columns, join key, and gotchas.

## Master merge pattern

```
Base (provider × location, key = OrigNPI)
  │  normalize OrigNPI → string
  ├── LEFT JOIN  NPPES agg (per NPI)          free   ← registry address + staleness
  ├── LOOKUP     NUCC taxonomy → specialty    free   ← lookup table, not an NPI join
  ├── LEFT JOIN  CMS D&C agg (per NPI)         free   ← multi-location counts
  ├── LEFT JOIN  Claims agg (per NPI)          free   ← already done
  ├── LEFT JOIN  FSMB license (name+state)     paid   ← fuzzy, subset only
  ├── (skip)     DEA                           restricted
  └── ENRICH     USPS CASS/DPV on addresses    ~$0.002/addr
        → one row per Base record + found_in_<source> flags + per-source match tiers
```

Every source that carries an address contributes a **match tier** against the R3 address
(exact → street+ZIP → ZIP-only), plus a recency variant where the source has dates. Prefer
recent, higher-tier matches; a ZIP-only match should never overturn a phone verdict. (Match
tier and normalization mechanics are covered in `provider-address-validation`.)

## Candidate features (grouped like `feature_config.yaml`)

Add these as new feature families so they can be toggled and a retrain can measure their lift.
A `feature_config.yaml` change requires re-running `train.py` — the pickled models expect the
exact `feature_cols` schema they were trained on.

| Family | Feature | Source | Rationale |
|---|---|---|---|
| nppes | `nppes_practice_addr_match_tier` | NPPES | Independent registry corroboration of the address. |
| nppes | `nppes_days_since_update` | NPPES | Staleness — older records more likely wrong. |
| nppes | `nppes_deactivated` | NPPES | Deactivated NPI ⇒ address almost certainly stale. |
| nppes | `nppes_practice_ne_mailing` | NPPES | Practice≠mailing = mobility signal. |
| taxonomy | `is_hospital_affiliated_specialty` | NUCC | Rotation-driven instability (Peds/Cards/OB-GYN/IM ~2–5% agree). |
| dc | `dc_num_practice_locations` | CMS D&C | Multi-site providers ⇒ both addresses can be true. |
| dc | `dc_any_location_matches_r3` | CMS D&C | Match R3 addr against *all* CMS locations. |
| dc | `has_hospital_affiliation` / count | CMS D&C | Facility-based practice context. |
| claims | (existing 10 signals) | Claims | Strongest passive proxy; keep. |
| claims | `claims_addr_geo_distance_km` | Claims + USPS | Distance R3↔claims address (needs geocode). |
| license | `license_active`, `license_expired` | FSMB | Active professional; weak address corroboration. |
| license | `license_addr_match_tier` | FSMB | Extra corroborating address (fuzzy join). |
| usps | `dpv_confirmed` | USPS | Is the R3 address even a real delivery point. |
| usps | `rdi_residential` | USPS | A residential "practice" address is suspect. |
| usps | `address_vacancy` | USPS | Vacant ⇒ likely stale/ghost listing. |
| usps | `r3_vs_corroborator_distance_km` | USPS | Continuous disagreement magnitude — likely a top feature. |

## HIPAA and governance

Provider records here are HIPAA-adjacent. Run every join **locally**. Do not upload provider
data to any public API. The one deliberate exception is the address-validation vendor (USPS
CASS/DPV) — confirm its BAA/terms first, and anonymize if any external call is unavoidable.
For paid sources, confirm the license permits analytical/model use before ingesting.

## Pitfalls

- Joining a raw multi-row source without aggregating to one-row-per-NPI first (row fan-out).
- Casting NPI to a numeric type (loses leading structure / precision) instead of string.
- Treating a source's absence as evidence the address is wrong.
- Matching against a mailing/registered address instead of the practice location.
- Treating NPI/license/DEA existence as proof of presence at the address.
- Buying FSMB for the full population when the disagreement subset captures nearly all the lift.
- Quoting vendor prices or file layouts from memory — all are tagged "verify" for a reason.

## Sources

Verify current file versions and pricing before committing — layouts and prices change.

- CMS NPPES Data Dissemination — https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/data-dissemination
- NPPES NPI Files (bulk) — https://download.cms.gov/nppes/NPI_Files.html
- CMS Doctors & Clinicians National Downloadable File — https://data.cms.gov/provider-data/dataset/mj5m-pzi6
- CMS Doctors & Clinicians Data Dictionary — https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/physician/DOC_Data_Dictionary.pdf
- NUCC Health Care Provider Taxonomy — https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40
- FSMB Physician Data Center — https://www.fsmb.org/PDC/
- DEA Diversion Control — Registration — https://www.deadiversion.usdoj.gov/drugreg/registration.html
- NTIS ending DEA subscription (2020) — https://www.deachronicles.com/2020/10/ntis-ending-its-dea-registration-subscription-service/
- Smarty address-verification pricing — https://www.smarty.com/pricing
- USPS DPV explainer — https://www.smarty.com/articles/what-is-dpv

The full worked plan (join spine detail, cost math, roadmap) lives in this repo at
`docs/multi_source_triangulation_plan.md`. Project-specific numbers (74%/39% agreement, 2–5%
specialty agreement, 515 duplicate / 29 missing NPIs) come from `CLAUDE.md` "Verified Data
Facts" and are specific to the hackathon Base Data — re-derive them on any new dataset.
