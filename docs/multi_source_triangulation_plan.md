# Multi-Source Triangulation Plan — Enriching the Base Data for Address Prediction

**Goal:** join the hackathon Base Data (2,493 provider×location records) to external
provider-data sources so the model has *independent corroboration* of where each provider
actually practices. Target label stays the same: **is the address correct** (Calling QC =
ground truth).

**Scope decided with Kushal:** written plan; **bulk/offline** ingestion preferred; all four
requested sources in scope (NPPES+taxonomy, CMS Doctors & Clinicians, state licensing boards,
DEA); costs shown for decision-making.

> Accuracy note: exact prices, file layouts, and access rules change. Every figure below is
> tagged with its source; treat dollar amounts and field lists as "verify before committing."
> I have **not** independently re-confirmed vendor pricing beyond the cited pages.

---

## 1. Guiding principles (from the provider-address-validation domain)

1. **Join everything on `OrigNPI`** (plaintext NPI). It is the only real, joinable key in the
   Base Data. The hashed `NPI` column was removed; do not resurrect it.
2. **Absence is NULL, not contradiction.** A provider missing from NPPES/CMS/a board is *no
   evidence* the address is wrong — only presence corroborates. Encode every source as
   "found / not-found" plus the signal, never as a negative.
3. **Validate the practice location, not the mailing address.** Several sources carry both;
   always target the practice/service location.
4. **Normalize before matching.** Standardize both addresses (USPS abbreviations,
   directionals, unit parsing, 5-digit + ZIP+4) *before* comparing, then score with tiered
   match logic (exact → street+ZIP → ZIP-only), with a recency window.
5. **Corroboration ≠ presence at an address.** NPI issuance, license, or even DEA
   registration prove the provider *exists*; they do not prove they practice at *this*
   address. Weight accordingly.

---

## 2. The join spine: `OrigNPI`

| Fact | Value | Implication |
|---|---|---|
| Base grain | provider × location (NOT one row per provider) | A provider can appear on multiple rows; joins can fan out. |
| `OrigNPI` duplicates | 515 | Aggregate external signals per NPI, then join back — don't join raw and multiply rows. |
| Rows missing `OrigNPI` | 29 | Cannot be externally matched; keep them, flag `has_external_key = 0`. |
| Claims key | `BASE_NPI` (plaintext) | Direct 1:1 with `OrigNPI` after string-normalizing both (`str.split('.').str[0]`). |

**Merge pattern for every source:** load source → normalize NPI key to string → aggregate to
**one row per NPI** (max/most-recent signal, sum counts) → `left join` onto Base on
`OrigNPI`. This mirrors how `build_merged.py` already collapses claims with `groupby('key')`.

---

## 3. Source-by-source design

### A. NPPES — the backbone (FREE, bulk)

- **What:** CMS National Plan & Provider Enumeration System full monthly dissemination file
  (`npidata_pfile_*.csv`), one row per NPI: names, credentials, taxonomy, licenses, org flags,
  and addresses.
- **Access:** bulk ZIP from CMS (weekly + monthly full replacement). **Free.** Use **Version 2**
  of the file — CMS retired Version 1 on 2026-03-03 with changed field lengths.
- **Join:** `NPI` (in file) ↔ `OrigNPI`. Direct.
- **Key columns to pull:**

  | Column (concept) | Type | Use for address prediction |
  |---|---|---|
  | Provider First Line Business **Practice Location** Address | text | Primary corroborating address — normalize & match tier vs R3 address. |
  | Practice Location City / State / Postal Code | text | ZIP/state components of match tiers. |
  | Provider Business **Mailing** Address | text | Secondary; a practice≠mailing gap is itself a weak instability signal. |
  | `Last Update Date` / `Certification Date` | date | **Staleness feature** — days since NPPES refresh. |
  | Entity Type Code (1=indiv / 2=org) | cat | HCP vs HCO; org rows behave differently (ghost-listing risk). |
  | Primary Taxonomy code | cat | Crosswalk to NUCC (source B); specialty-instability signal. |
  | Deactivation / Reactivation Date | date | Deactivated NPI → address almost certainly stale. |
- **Gotchas:** NPPES lags real-world moves (weekly/monthly). Practice location is
  self-reported and can be an org HQ. Use as corroboration, not truth.

### B. NUCC Taxonomy crosswalk (FREE, bulk)

- **What:** maps the 10-char taxonomy code (ends in `X`; 3 levels: Grouping → Classification →
  Specialization) to human specialty + grouping. Republished **January and July** — pin the
  version.
- **Access:** free CSV from NUCC. **Free.**
- **Join:** taxonomy code from NPPES (A) / Base `Specialty`. Not an NPI join — a lookup table.
- **Use:** derive `is_hospital_affiliated_specialty`, grouping-level flags. In this dataset,
  Peds/Cardiology/OB-GYN/Internal Medicine agree with the phone only ~2–5% → strong feature.

### C. CMS Doctors & Clinicians National Downloadable File (FREE, bulk)

- **What:** CMS Provider Data Catalog clinician file. Grain is finer than one-row-per-NPI:
  unique at **NPI + enrollment ID + Org PAC ID + address ID** — i.e., it *natively lists
  multiple practice locations per clinician*. This is the single best public source for the
  **multi-site** hypothesis.
- **Access:** free download from data.cms.gov. **Free.**
- **Join:** `NPI` ↔ `OrigNPI`.
- **Key columns / derived signals:**

  | Concept | Use |
  |---|---|
  | Multiple practice-location rows per NPI | Count distinct locations → `dc_num_practice_locations`. A provider with many sites is a legitimate multi-site case, not an error. |
  | Each location's street/city/ZIP | Extra corroborating addresses to match the R3 address against (any-location match = strong). |
  | Org PAC ID / group linkage | Group-practice context. |
- **Companion file — `Facility_Affiliation.csv`:** the "hospital affiliation" the PDF asks
  for. **Caveat (verify):** it is sparse and identifies facilities only by **CCN** (CMS
  Certification Number) and **PAC ID** — *no facility name or address*. To turn a CCN into an
  address you must join to the CMS **Hospital General Information** / POS file (also free).
  Useful as `has_hospital_affiliation` + affiliation count; weaker as a direct address source.

### D. Claims data (ALREADY HAVE — FREE)

- **Join:** `BASE_NPI` ↔ `OrigNPI`. Already implemented in `build_merged.py`.
- **Why it's the anchor:** records with any claims agree with the phone ~74% vs ~39% without.
  Claims are *behavioral* evidence of where care is billed — the closest passive proxy to
  "where the provider actually is."
- **Signals already present** (keep, and extend): `N_CLAIMS`, `DISTINCT_ORGS`,
  `DISTINCT_ADDRS`, `DAYS_SINCE`, `ADDR_EXACT_MATCH`, `ZIP_MATCH`, `STREET_ZIP_MATCH`,
  `RECENT_ZIP_MATCH`, `RECENT_STREET_ZIP_MATCH`.

### E. State licensing boards (PAID or high-effort)

- **What:** license status, issue/expiration, and sometimes address of record, per state
  board. **50+ fragmented sources; most have no bulk API.**
- **Two realistic paths:**
  1. **FSMB Physician Data Center (PDC)** — aggregates licensure + disciplinary data for
     ~1.2M MD/DO/PA, with API / Managed File Transfer. **Cost (verify): ~$12 per physician**
     for the Premium profile, charged per query. Only covers MD/DO/PA (not NPs, therapists,
     etc.).
  2. **Per-board scraping** — free but brittle, rate-limited, ToS-sensitive, 50 different
     formats. Not recommended for a hackathon timeline.
- **Join:** license number / name+state; **no direct NPI key** — must match on name + state +
  license, which is fuzzy. Budget for match error.
- **Use for address:** a license *address of record* gives another corroborating address;
  `license_active` + `license_expired` are instability signals. But license address is often
  an administrative address, not the practice site — moderate signal.
- **Cost math:** $12 × 2,493 ≈ **$29,900** to cover everyone. Restrict to the disagreement
  subset (~741 records) ≈ **$8,900**, or only the robocall pool. Verify FSMB pricing/coverage
  before committing.

### F. DEA registrations (RESTRICTED — likely not usable as-is)

- **Reality check:** DEA registrant data is **not free/public.** NTIS **discontinued** its
  Controlled Substances Act (CSA) subscription on **2020-11-17**; the DEA now controls the
  Registration Information database and **restricts access to DEA registrants** (and their
  authorized agents) for credentialing/verification purposes. There is no open bulk file to
  buy off the shelf like there was.
- **Even if obtained:** DEA registration only exists for prescribers of controlled substances
  (excludes many specialties), the address is the **registered** location (often admin, not
  practice), and using it may carry contractual/compliance constraints. It proves the provider
  *exists and can prescribe*, weakly corroborates location.
- **Recommendation:** treat DEA as **out of scope for the hackathon** unless HiLabs already
  holds a licensed feed. Document it as a future signal. **Compliant substitutes** that give
  most of the same "active professional" signal for free/cheap: NPPES deactivation status (A),
  CMS D&C enrollment status (C), and FSMB license status (E).
- **If HiLabs already licenses it:** join on the DEA number (not in Base — would need a
  crosswalk), derive `dea_active`, `dea_state_matches_practice`. Confirm the license permits
  analytical use first.

### G. USPS address-quality enrichment — CASS/DPV (LOW COST, per-lookup)

Not a "merge on NPI" source — an **enrichment layer** applied to the address strings
themselves. Turns raw addresses into standardized, validated, geocoded ones and yields strong
"is this a real deliverable place" signals.

- **Vendor example — Smarty (CASS-certified):** volume pricing **~$0.001–0.004 per lookup**
  (~$125 / 100k); free tier ~250 lookups/month; low-volume ~$0.60 / 1,000 (all **verify**).
- **Cost math:** validating all 2,493 Base addresses is **~$1.50–$10** — negligible. This is
  the cheapest high-value enrichment on the list.
- **Signals produced:** `dpv_confirmed` (address is a real delivery point), `rdi`
  (residential vs commercial — a home address for a "practice" is suspicious), `vacancy`
  flag, ZIP+4, lat/long (enables **distance between R3 address and each corroborating
  address** — a powerful continuous feature), county FIPS, carrier route.
- **Alternatives:** Melissa, Google Address Validation (pricier), USPS APIs (free but
  standardization/DPV only, rate-limited).

### H. Optional paid provider-data vendors (SHOW-COST, enterprise)

For completeness — commercial "golden record" provider databases that pre-triangulate many
sources: **IQVIA OneKey, Definitive Healthcare, LexisNexis VerifyHCP/Provider Data,
CarePrecise**. Pricing is **enterprise / quote-based and not publicly listed** (I won't invent
a number). Worth a quote only if this productionizes beyond the hackathon; overkill for the
2,493-record exercise given the free backbone above.

---

## 4. Master merge design

```
Base (2,493 × OrigNPI)
  │  normalize OrigNPI → string key
  ├── LEFT JOIN  NPPES agg (per NPI)          [A]  free
  ├── LOOKUP     NUCC taxonomy → specialty    [B]  free
  ├── LEFT JOIN  CMS D&C agg (per NPI)         [C]  free   ← multi-location counts
  ├── LEFT JOIN  Claims agg (per NPI)          [D]  free   ← already done
  ├── LEFT JOIN  FSMB license (name+state)     [E]  paid, fuzzy   (subset only)
  ├── (skip)     DEA                           [F]  restricted
  └── ENRICH     USPS CASS/DPV on addresses    [G]  ~$0.002/addr
        → one row per Base record, + has_<source> flags + match tiers
```

**Rules:**
- Aggregate each source to **one row per NPI** before joining (avoid row fan-out from the 515
  duplicate NPIs).
- Every source contributes a `found_in_<source>` flag; missing = 0, never negative.
- Compute **per-source address match tiers** (exact / street+ZIP / ZIP) against the R3
  address, each with a recency variant where the source has dates.
- Keep the 29 keyless rows with `has_external_key = 0`.
- **HIPAA:** all joins run **locally**; do not upload provider data to any public API except
  the address-validation vendor, and confirm that vendor's BAA/terms first. Anonymize if any
  external call is unavoidable.

---

## 5. New candidate features (grouped like `feature_config.yaml`)

| Family | Feature | Source | Rationale |
|---|---|---|---|
| nppes | `nppes_practice_addr_match_tier` | A | Independent registry corroboration of the address. |
| nppes | `nppes_days_since_update` | A | Staleness — old records more likely wrong. |
| nppes | `nppes_deactivated` | A | Deactivated NPI ⇒ address stale. |
| nppes | `nppes_practice_ne_mailing` | A | Practice≠mailing = mobility signal. |
| taxonomy | `is_hospital_affiliated_specialty` | B | Rotation-driven instability (2–5% agree). |
| dc | `dc_num_practice_locations` | C | Multi-site providers ⇒ "both addresses true." |
| dc | `dc_any_location_matches_r3` | C | Match R3 addr against *all* CMS locations. |
| dc | `has_hospital_affiliation` / count | C | Facility-based practice context. |
| claims | (existing 10 signals) | D | Strongest passive proxy; keep. |
| claims | `claims_addr_geo_distance_km` | D+G | Distance R3↔claims address (needs geocode). |
| license | `license_active`, `license_expired` | E | Active pro; weak address corroboration. |
| license | `license_addr_match_tier` | E | Extra corroborating address (fuzzy join). |
| usps | `dpv_confirmed` | G | Is the R3 address even a real delivery point. |
| usps | `rdi_residential` | G | A residential "practice" address is suspect. |
| usps | `address_vacancy` | G | Vacant ⇒ likely stale/ghost. |
| usps | `r3_vs_corroborator_distance_km` | G | Continuous disagreement magnitude — likely a top feature. |

---

## 6. Cost summary

| Source | Access | Cost (verify) | Priority |
|---|---|---|---|
| NPPES (A) | Bulk, free | $0 | **P0 — do first** |
| NUCC taxonomy (B) | Bulk, free | $0 | **P0** |
| CMS Doctors & Clinicians + Facility Affiliation (C) | Bulk, free | $0 | **P1** |
| Claims (D) | Have it | $0 | Done |
| USPS CASS/DPV enrichment (G) | Per-lookup | ~$1.50–$10 for 2,493 | **P1 — best $/signal** |
| FSMB license (E) | API/MFT | ~$12/physician (MD/DO/PA only) → ~$8.9k subset / ~$29.9k all | P2 — subset only |
| DEA (F) | Restricted | N/A (no open feed since 2020) | Skip / future |
| Vendor golden records (H) | Enterprise | Quote-only | Skip for hackathon |

---

## 7. Recommended roadmap

1. **P0 (free, high ROI):** NPPES + NUCC. Adds an independent registry address, staleness, and
   specialty-instability features. Pure local join on `OrigNPI`.
2. **P1:** CMS Doctors & Clinicians (multi-location — directly attacks the biggest error
   pattern) + USPS DPV enrichment (real-address gate, residential flag, geodistance) for ~$10.
3. **P2 (paid, targeted):** FSMB license lookups **only** on the disagreement subset or the
   robocall pool, to control the ~$9k cost. Accept fuzzy name+state matching.
4. **Future / production:** DEA (only if HiLabs licenses a feed) and commercial golden-record
   vendors.

Do all of P0+P1 before spending anything — they're free (or ~$10) and hit the two dominant
failure modes (staleness and multi-site) head-on.

---

## 8. Sources

- CMS NPPES Data Dissemination — https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/data-dissemination
- NPPES NPI Files (bulk) — https://download.cms.gov/nppes/NPI_Files.html
- CMS Doctors & Clinicians — National Downloadable File — https://data.cms.gov/provider-data/dataset/mj5m-pzi6
- CMS Doctors & Clinicians Data Dictionary — https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/physician/DOC_Data_Dictionary.pdf
- NUCC Health Care Provider Taxonomy — https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40
- FSMB Physician Data Center (pricing/access) — https://www.fsmb.org/PDC/
- DEA Diversion Control — Registration — https://www.deadiversion.usdoj.gov/drugreg/registration.html
- NTIS ending DEA subscription (2020) — https://www.deachronicles.com/2020/10/ntis-ending-its-dea-registration-subscription-service/
- Smarty address-verification pricing — https://www.smarty.com/pricing
- USPS DPV explainer — https://www.smarty.com/articles/what-is-dpv

Project-specific numbers (74%/39%, 2–5%, 515 dup NPIs, 29 missing) are from this repo's
`CLAUDE.md` "Verified Data Facts" and are specific to the hackathon Base Data.
