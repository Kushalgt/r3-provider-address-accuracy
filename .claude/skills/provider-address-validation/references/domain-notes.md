# Domain notes — provider data sources

Supplementary reference for `SKILL.md`. Read when you need deeper detail on a specific
source. Verify current versions/figures against the linked primary sources before quoting
exact numbers — these change on published cadences.

## NPPES data-dissemination file

- FOIA-disclosable, downloadable from CMS; a Zip containing a Read Me, a Code Values
  document, a Header File document, and the main `npidata_pfile_*.csv` (one row per NPI:
  identifiers, names, addresses, taxonomy, licenses, org flags).
- Distinguishes mailing address vs. practice-location address. Validate against the
  **practice location**.
- Refreshed weekly/monthly — lags real-world moves, so it is corroboration, not a live
  source.
- CMS retired Version 1 of the downloadable file on 2026-03-03; Version 2 has extended field
  lengths (First Name, Legal Business Name). Confirm the file version before parsing.
- Source: https://download.cms.gov/nppes/NPI_Files.html and
  https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/data-dissemination

## NPPES NPI Registry API (real-time lookup)

- Live per-NPI lookup and search; returns credentials, practice address, taxonomy, license.
- Max ~200 results per request, up to ~1,200 across paginated requests.
- Only U.S. providers; international providers do not appear.
- Source: https://npiregistry.cms.hhs.gov/api-page

## NUCC taxonomy specifics

- 10-char alphanumeric, ends in `X`; first four characters indicate the Level-2
  Classification.
- Three levels: Provider Grouping (L1) → Classification (L2) → Area of Specialization (L3).
- Republished January and July; used in HIPAA transactions and NPI enumeration.
- Source: https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40

## CMS Doctors & Clinicians / affiliation coverage

- The CMS Doctors & Clinicians National Downloadable File carries multiple practice locations
  and telehealth flags — useful for multi-site corroboration.
- Only a minority of practitioners are linked to organizations in CMS files (~28.7% in this
  project's context). Therefore **absence from an org linkage is null, not contradiction.**

## USPS address quality — one-line distinctions

- **Standardization**: formats/corrects, assigns ZIP+4 in-range. Does not prove
  deliverability.
- **CASS**: certifies the *software's* coding accuracy (high thresholds; verify current
  cycle). Property of the tool, not a single address.
- **DPV**: confirms a specific delivery point actually exists. Strongest "is it real" gate;
  still does not prove *provider presence*.
- Sources: https://postalpro.usps.com/certifications/cass ,
  https://www.smarty.com/articles/what-is-dpv

## No Surprises Act — directory accuracy

- Effective 2022-01-01. Plans must verify each directory entry at least every 90 days, apply
  provider corrections within two business days, and remove non-responsive providers until
  re-verified. Plans report directory-accuracy analyses to CMS.
- This is the regulatory "why" behind treating address freshness (≤90 days) as a staleness
  boundary.
- Source: https://www.cms.gov/files/document/a274577-1b-training-2nsa-disclosure-continuity-care-directoriesfinal-508.pdf
