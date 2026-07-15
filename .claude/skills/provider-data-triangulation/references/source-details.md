# Source details for provider-data triangulation

Per-source columns, access, join keys, costs, and gotchas. Read the section for the source
you are ingesting. Every dollar figure and field list is tagged for verification — layouts
and prices change, and these have not been independently re-confirmed beyond the cited pages.

## Contents

- [A. NPPES — the backbone (free)](#a-nppes)
- [B. NUCC taxonomy crosswalk (free)](#b-nucc)
- [C. CMS Doctors & Clinicians (free, multi-location)](#c-cms-dc)
- [D. Claims (already have)](#d-claims)
- [E. State licensing / FSMB (paid, fuzzy)](#e-fsmb)
- [F. DEA registrations (restricted)](#f-dea)
- [G. USPS CASS/DPV enrichment (low cost)](#g-usps)
- [H. Commercial golden-record vendors (quote-only)](#h-vendors)

---

## A. NPPES — the backbone {#a-nppes}

**What:** CMS National Plan & Provider Enumeration System full monthly dissemination file
(`npidata_pfile_*.csv`), one row per NPI: names, credentials, taxonomy, licenses, org flags,
and addresses.

**Access:** bulk ZIP from CMS (weekly + monthly full replacement). Free. Use **Version 2** of
the file — CMS retired Version 1 on 2026-03-03 with changed field lengths (verify current
version before parsing).

**Join:** `NPI` (in file) ↔ `OrigNPI`. Direct, after string-normalizing both.

**Key columns to pull:**

| Column (concept) | Type | Use for address prediction |
|---|---|---|
| Provider First Line Business **Practice Location** Address | text | Primary corroborating address — normalize & match-tier vs R3 address. |
| Practice Location City / State / Postal Code | text | ZIP/state components of match tiers. |
| Provider Business **Mailing** Address | text | Secondary; practice≠mailing gap is a weak instability signal. |
| `Last Update Date` / `Certification Date` | date | **Staleness feature** — days since NPPES refresh. |
| Entity Type Code (1=indiv / 2=org) | cat | HCP vs HCO; org rows carry ghost-listing risk. |
| Primary Taxonomy code | cat | Crosswalk to NUCC (B); specialty-instability signal. |
| Deactivation / Reactivation Date | date | Deactivated NPI ⇒ address almost certainly stale. |

**Gotchas:** NPPES lags real-world moves (weekly/monthly cadence). Practice location is
self-reported and can be an org HQ rather than the point of care. Use as corroboration, not
truth.

---

## B. NUCC taxonomy crosswalk {#b-nucc}

**What:** maps the 10-character taxonomy code (ends in `X`; three levels: Grouping →
Classification → Specialization) to human specialty + grouping.

**Access:** free CSV from NUCC. Republished **January and July** — pin the version.

**Join:** taxonomy code from NPPES (A) or the Base `Specialty` field. This is a **lookup
table, not an NPI join**.

**Use:** derive `is_hospital_affiliated_specialty` and grouping-level flags. In this dataset,
Peds/Cardiology/OB-GYN/Internal Medicine agree with the phone only ~2–5% → strong feature.

---

## C. CMS Doctors & Clinicians National Downloadable File {#c-cms-dc}

**What:** CMS Provider Data Catalog clinician file. Grain is finer than one-row-per-NPI:
unique at **NPI + enrollment ID + Org PAC ID + address ID** — i.e., it *natively lists
multiple practice locations per clinician*. This is the single best public source for the
**multi-site** hypothesis.

**Access:** free download from data.cms.gov.

**Join:** `NPI` ↔ `OrigNPI`. Because it is multi-row per NPI, aggregate before joining.

**Key columns / derived signals:**

| Concept | Use |
|---|---|
| Multiple practice-location rows per NPI | Count distinct locations → `dc_num_practice_locations`. Many sites = legitimate multi-site case, not an error. |
| Each location's street/city/ZIP | Extra corroborating addresses to match R3 against (any-location match = strong). |
| Org PAC ID / group linkage | Group-practice context. |

**Companion file — `Facility_Affiliation.csv`** (the "hospital affiliation" the PDF asks for):
sparse, and identifies facilities only by **CCN** (CMS Certification Number) and **PAC ID** —
*no facility name or address* (verify). To turn a CCN into an address, join to the CMS
**Hospital General Information** / POS file (also free). Useful as `has_hospital_affiliation` +
affiliation count; weaker as a direct address source.

---

## D. Claims (already have) {#d-claims}

**Join:** `BASE_NPI` ↔ `OrigNPI`. Already implemented (claims aggregate collapses to one row
per NPI via `groupby`).

**Why it's the anchor:** records with any claims agree with the phone ~74% vs ~39% without.
Claims are *behavioral* evidence of where care is billed — the closest passive proxy to "where
the provider actually is."

**Signals already present** (keep, extend): `N_CLAIMS`, `DISTINCT_ORGS`, `DISTINCT_ADDRS`,
`DAYS_SINCE`, `ADDR_EXACT_MATCH`, `ZIP_MATCH`, `STREET_ZIP_MATCH`, `RECENT_ZIP_MATCH`,
`RECENT_STREET_ZIP_MATCH`.

**Loader caveat (repo state):** `claims_data.csv` is *pre-aggregated*, but `claims_merger.py`
expects raw claim rows (columns like `CLAIMS_D_PRIMARY_HCP_NPI`). Running the pipeline with
the aggregated CSV as `claims_source` raises a KeyError. Use `claims_source='empty'` or
reconcile the loader before wiring a new claims source through the merger.

---

## E. State licensing boards / FSMB {#e-fsmb}

**What:** license status, issue/expiration, and sometimes an address of record, per state
board. 50+ fragmented sources; most have no bulk API.

**Two realistic paths:**

1. **FSMB Physician Data Center (PDC)** — aggregates licensure + disciplinary data for ~1.2M
   MD/DO/PA, with API / Managed File Transfer. Cost (verify): **~$12 per physician** for the
   Premium profile, charged per query. Covers **only MD/DO/PA** — not NPs, therapists, etc.
2. **Per-board scraping** — free but brittle, rate-limited, ToS-sensitive, 50 different
   formats. Not recommended on a hackathon timeline.

**Join:** license number / name+state; **no direct NPI key** — fuzzy match on name + state +
license. Budget for match error.

**Use for address:** a license *address of record* is another corroborating address, but it is
often administrative, not the practice site — moderate signal. `license_active` /
`license_expired` are instability signals.

**Cost math:** $12 × 2,493 ≈ **$29,900** for everyone. Restrict to the disagreement subset
(~741 records) ≈ **$8,900**, or only the robocall pool. Verify FSMB pricing/coverage before
committing.

---

## F. DEA registrations {#f-dea}

**Reality check:** DEA registrant data is **not free/public**. NTIS **discontinued** its
Controlled Substances Act subscription on **2020-11-17**; the DEA now controls the Registration
Information database and **restricts access to DEA registrants** (and their authorized agents)
for credentialing/verification purposes. There is no open bulk file to buy off the shelf.

**Even if obtained:** DEA registration exists only for prescribers of controlled substances
(excludes many specialties), the address is the **registered** location (often admin, not
practice), and use may carry contractual/compliance constraints. It proves the provider
*exists and can prescribe*; it weakly corroborates location.

**Recommendation:** treat DEA as **out of scope** unless HiLabs already holds a licensed feed.
Compliant substitutes giving most of the same "active professional" signal for free/cheap:
NPPES deactivation status (A), CMS D&C enrollment status (C), FSMB license status (E). If
HiLabs already licenses it: join on the DEA number (not in Base — needs a crosswalk), derive
`dea_active`, `dea_state_matches_practice`; confirm the license permits analytical use first.

---

## G. USPS CASS/DPV enrichment {#g-usps}

Not a "merge on NPI" source — an **enrichment layer** applied to the address strings
themselves. Turns raw addresses into standardized, validated, geocoded ones and yields strong
"is this a real deliverable place" signals.

**Vendor example — Smarty (CASS-certified):** volume pricing ~$0.001–0.004 per lookup
(~$125 / 100k); free tier ~250 lookups/month; low-volume ~$0.60 / 1,000 (all verify).

**Cost math:** validating all 2,493 Base addresses is **~$1.50–$10** — negligible; the
cheapest high-value enrichment on the list.

**Signals produced:** `dpv_confirmed` (real delivery point), `rdi` (residential vs commercial —
a home address for a "practice" is suspicious), `vacancy` flag, ZIP+4, lat/long (enables
**distance between R3 address and each corroborating address** — a powerful continuous
feature), county FIPS, carrier route.

**Alternatives:** Melissa, Google Address Validation (pricier), USPS APIs (free but
standardization/DPV only, rate-limited).

---

## H. Commercial golden-record vendors {#h-vendors}

For completeness — commercial provider databases that pre-triangulate many sources: **IQVIA
OneKey, Definitive Healthcare, LexisNexis VerifyHCP/Provider Data, CarePrecise**. Pricing is
enterprise / quote-based and not publicly listed (do not invent a number). Worth a quote only
if this productionizes beyond the hackathon; overkill for the 2,493-record exercise given the
free backbone above.
