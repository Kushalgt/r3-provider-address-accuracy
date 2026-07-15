---
name: provider-address-validation
description: >
  Domain expertise for validating U.S. healthcare provider PRACTICE ADDRESSES:
  NPI/NPPES data, NUCC taxonomy, HCP-vs-HCO semantics, why web-scraped validation
  disagrees with phone ground truth, and the USPS address-quality stack (standardization,
  CASS, DPV). Use this skill whenever the task involves checking, correcting, scoring,
  or reasoning about a provider's address or location — including R3-vs-Call-QC
  disagreement analysis, claims-based corroboration, staleness scoring, address
  normalization/matching, or provider-directory accuracy. Trigger it even when the
  user says only "is this address right", "why does the web say X but the phone say Y",
  "provider moved", "ghost listing", "match the claims address", or references NPI,
  NPPES, taxonomy, or the No Surprises Act. Prefer this skill over generic address
  advice because provider addresses have healthcare-specific failure modes.
---

# Provider Address Validation

Validating **where a healthcare provider actually practices** is not the same as validating
a mailing address. A provider can be enumerated, licensed, and billing claims and *still*
have a wrong address in a directory — because they rotate across hospital sites, because an
organization lists a location the provider left, or because the web copy of the record is
stale. This skill encodes the domain knowledge needed to reason about those failure modes
and to weigh evidence correctly.

Read this whole file when triggered. Read the reference sections below on demand.

## When this applies

Use this skill for any task that touches a provider's practice location: judging whether an
address is correct, correcting/standardizing it, scoring confidence or staleness, matching
an address against claims or a registry, or explaining a disagreement between two sources
(classically **R3 web validation vs. Calling QC phone verification**, where the phone is
treated as ground truth in this project).

Do **not** reach for generic postal-address advice first — provider addresses fail in
healthcare-specific ways that generic CASS/DPV logic alone will miss.

## Core identifiers and reference data

**NPI (National Provider Identifier).** A 10-digit identifier issued by CMS through NPPES.
Two entity types matter, and conflating them is a common bug:

- **NPI-1 (Individual / HCP):** a person — physician, NP, PA, therapist, etc.
- **NPI-2 (Organization / HCO):** a hospital, clinic, group, lab, pharmacy, etc.

An individual usually has *one* practice location on record but may legitimately practice at
several; an organization address is the *entity's* location, which is not necessarily where a
given affiliated provider sees patients. This individual-vs-org gap is a primary source of
"the org lists this address but the provider isn't really there" (ghost-listing) errors.

Issuance of an NPI does **not** confirm the provider is currently licensed, credentialed, or
practicing at the enumerated address. Treat NPPES as *corroboration*, not proof.

**NPPES addresses.** The NPPES data-dissemination (FOIA-disclosable) file distinguishes at
least two address roles per record:

- **Mailing address** — where correspondence is sent.
- **Practice location address** — where care is delivered.

For address validation the **practice location** is the target; do not validate against the
mailing address by mistake. NPPES is refreshed on a weekly/monthly cadence, so it lags
real-world moves — useful for corroboration, weak as a real-time source. (Operational note:
CMS retired Version 1 of the downloadable file on 2026-03-03 in favor of Version 2 with
extended field lengths — verify the current file version before parsing, as field layouts
have changed.)

**NUCC Health Care Provider Taxonomy.** A 10-character alphanumeric code (it ends in the
letter `X`) with a three-level structure — Provider Grouping → Classification → Area of
Specialization. It is maintained by the NUCC and republished twice a year (January and July),
so pin the version you crosswalk against. Taxonomy matters here because **specialty predicts
address-instability**: hospital-affiliated physician specialties (in this project's data:
Pediatrics, Cardiology, OB/GYN, Internal Medicine) show very low web-vs-phone agreement
(~2–5%), consistent with providers who rotate across facility sites rather than sitting at
one clinic address.

See `references/domain-notes.md` for the fuller data-source rundown (CMS Doctors &
Clinicians file, absence-is-not-contradiction rule, etc.).

## Why web validation disagrees with the phone

The central problem: web-scraped validation (R3) agrees with the internet ~88% of the time,
yet the **phone** (Calling QC, ground truth) frequently disagrees. The disagreements are not
random noise — they cluster into explainable patterns. When reasoning about a disagreement,
work through these hypotheses:

1. **Provider departure / stale web.** The provider moved; the web still shows the old
   address. Signature: personal sources (provider-view) still cite the address but the org no
   longer does.
2. **Org ghost listing.** The organization lists a location where the provider has no real
   presence. Signature: org-view finds the address, provider-view does not.
3. **Multi-site / hospital rotation.** The provider genuinely practices at several sites, so
   *both* the web address and the phone address can be "true" — the label depends on which
   location the question is about. Strongly associated with hospital-affiliated specialties.
4. **Confidently wrong.** R3's own web-confidence score is *inverted at the top* in this
   dataset: the highest-confidence "keep" decisions are the least accurate. High web
   confidence is therefore not a safe reason to skip verification — flag high-confidence +
   no-corroboration records for calling first.
5. **Over-removal bias.** R3 tends to over-remove: its "remove/INACCURATE" calls are far more
   trustworthy than its "keep/ACCURATE" calls. Weight a *keep* decision more skeptically than
   a *remove*.

These are project-specific empirical patterns (see the repo's `CLAUDE.md` "Verified Data
Facts"); re-derive them on any new dataset rather than assuming the same magnitudes.

## The address-quality stack (USPS)

Do not treat "the address parses" as "the address is real." Three distinct capabilities,
often confused:

- **Standardization** — corrects spelling, applies USPS-approved abbreviations, fills
  directionals (NW/SE), and assigns ZIP+4 *if the number falls in a valid range*. It does
  **not** confirm anyone can receive mail there — a standardized address can still be a vacant
  lot.
- **CASS (Coding Accuracy Support System)** — USPS's certification program that measures how
  accurately *software* standardizes and codes addresses (ZIP+4, carrier route, LACSLink,
  delivery-point coding, DPV, etc.). CASS certification is a property of the software, not of
  a single address; certification thresholds are high (on the order of ~98.5%+ for ZIP+4 and
  100% for delivery-point coding — verify the current cycle's exact figures before quoting).
- **DPV (Delivery Point Validation)** — confirms a specific address, down to the
  house/suite number, exists as a real USPS delivery point. This is the strongest "is it
  real" signal. Standardization formats; DPV verifies deliverability.

For provider validation, use standardization to *normalize before comparing*, and treat DPV
as a real-existence gate — but remember DPV confirms *deliverability*, not that *this
provider practices there*. Provider-presence still requires corroboration (claims, registry,
phone).

## Normalization before matching

Never compare raw address strings. Normalize both sides first, then match. Minimum steps:

1. Uppercase; trim; collapse internal whitespace.
2. Apply USPS abbreviations (STREET→ST, AVENUE→AVE, SUITE→STE) and directionals.
3. Separate and normalize the secondary unit (STE/APT/FL/UNIT) — a suite mismatch inside the
   same building is a *weak* discrepancy, not a real move.
4. Normalize ZIP to 5-digit and, where available, ZIP+4.
5. Only then compare, using tiered matching (next section).

## Match tiers (strongest → weakest)

Score corroboration by *how much* of the address agrees, not a boolean. This mirrors the
claims-match flags used in this project:

- **Exact address match** — full street + unit + ZIP agree (strongest).
- **Street + ZIP match** — same street and ZIP, unit/format differs.
- **ZIP match** — same ZIP only (geographic proximity, weak).
- **Recent-* variants** — the same match evaluated *within a recency window* (this project
  uses 180 days). A recent street+ZIP match is much stronger evidence than an old one.

Prefer recent, higher-tier matches. A ZIP-only match should never on its own overturn a
phone verdict.

## Corroboration and staleness

**Claims are the strongest passive signal in this project.** Records with any billing-claims
activity agree with the phone ~74% of the time vs. ~39% for records with no claims. Rules:

- **Presence of recent claims from the same street+ZIP** → strong corroboration the provider
  is really there.
- **Absence of claims (or registry presence) is NULL, not contradiction.** Only ~28.7% of
  practitioners are linked to organizations in CMS files; a provider missing from a source may
  simply not be captured there. Never treat absence as evidence the address is wrong.

**Staleness scoring.** Newer corroboration outranks old. Build a staleness score from
"days since most recent claim / most recent registry update," and decay confidence as it
grows. Regulatory anchor: the **No Surprises Act** (effective 2022-01-01) requires health
plans to verify each provider-directory entry at least **every 90 days** and to apply
provider-supplied corrections within **two business days**; non-responsive providers must be
removed until they re-verify. Ninety days is a defensible "freshness" boundary for a
directory-accuracy staleness score, and it explains *why* address accuracy carries real
compliance and financial stakes (surprise-billing exposure, directory-accuracy penalties).

## Decision guidance

- Trust a **remove/INACCURATE** verdict more than a **keep/ACCURATE** verdict.
- Do **not** let high web-confidence suppress verification — it is inverted at the top here.
- Triage the **confidently-wrong danger zone** first: high web confidence + no claims
  corroboration is the lowest-accuracy segment.
- Watch geography and specialty: some states and hospital-affiliated specialties concentrate
  the error mass; give them extra scrutiny (and, in modeling, extra sample weight) but never
  degrade the large "agreement zone" where web and phone already concur.
- When two sources both look valid, suspect **multi-site**, not error — ask *which location*
  the question is about.

## Pitfalls

- Validating the mailing address instead of the practice location.
- Comparing un-normalized strings (suite/format noise reads as a false move).
- Treating NPI issuance, or mere DPV deliverability, as proof the provider practices there.
- Treating source absence as contradiction.
- Quoting exact CASS thresholds, NPPES field layouts, or NSA sub-rules from memory —
  these change; verify against the primary source before asserting a specific number.

## Sources

Primary references this skill is grounded in (verify current versions before quoting exact
figures):

- CMS NPPES Data Dissemination — https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/data-dissemination
- NPPES NPI Files (downloadable file + Read Me) — https://download.cms.gov/nppes/NPI_Files.html
- NPPES NPI Registry API — https://npiregistry.cms.hhs.gov/api-page
- NUCC Health Care Provider Taxonomy — https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40
- NUCC "What do the Levels mean?" — https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/more-information-mainmenu-55/95-what-do-the-levels-mean
- USPS CASS (PostalPro) — https://postalpro.usps.com/certifications/cass
- Coding Accuracy Support System (overview) — https://en.wikipedia.org/wiki/Coding_Accuracy_Support_System
- USPS DPV explainer (Smarty) — https://www.smarty.com/articles/what-is-dpv
- CMS No Surprises Act — provider directories training — https://www.cms.gov/files/document/a274577-1b-training-2nsa-disclosure-continuity-care-directoriesfinal-508.pdf

Project-specific empirical facts (agreement rates, confidence inversion, specialty/geography
patterns, the 180-day window) come from this repo's `CLAUDE.md` "Verified Data Facts" section
and are specific to the hackathon Base Data — re-derive them on any new dataset.
