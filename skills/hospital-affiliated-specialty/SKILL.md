---
name: hospital-affiliated-specialty
description: >
  Decide whether a provider specialty is a HOSPITAL-AFFILIATED (rotation-prone)
  specialty or a clinic/solo-based one, given either a specialty name
  ("Pediatrics", "Nurse Practitioner, Family") or a NUCC taxonomy code
  ("208000000X"). Returns Yes/No + a short reason + a confidence level.
  Use this whenever the task involves classifying a specialty by practice
  setting, explaining why R3 disagrees with the phone on certain doctors,
  scoring or bucketing address errors by specialty, building the
  is_hospital_affiliated_specialty feature, or reasoning about multi-site /
  org-vs-individual (HCP vs HCO) address failures. Trigger it even when the user
  only says "is Cardiology hospital-based", "which specialties rotate across
  sites", "why does R3 fail on pediatricians", or references a taxonomy code,
  NUCC, NPPES, or specialty in the context of address validation.
---

# Hospital-Affiliated Specialty Classifier

## What this decides and why it matters

A **hospital-affiliated specialty** is one where the provider's real work happens
**inside a hospital or large health system, usually across several sites**, not in
one fixed private clinic. This matters for R3 address validation because these
providers are the ones R3 gets wrong the most.

The reason is structural, not random. The web only knows the provider is affiliated
with an *organization* (the hospital), so it confirms the **org's** address. But the
roster row asks where that **individual** actually sees patients — which may be a
satellite clinic, or may rotate day to day. That schedule is never published online,
so a web-only engine cannot resolve it. This is the **HCP-vs-HCO gap** (individual vs
organization). In this project's Base Data, these specialties agree with the phone
only **~2–5%** of the time (per CLAUDE.md verified facts — treat as a
dataset-specific figure, not a universal constant).

So a "yes" here is a strong signal that an R3 disagreement is caused by the
multi-site / org-address problem, and that the address should not be removed on web
absence alone.

## Input

Accept **either**:
- a **specialty name** — e.g. `Pediatrics`, `Internal Medicine, Cardiovascular Disease`,
  `Nurse Practitioner, Family`; or
- a **NUCC taxonomy code** — e.g. `208000000X`.

If given a code, first turn it into its description. In this repo the NPPES record
already carries `nppes_primary_taxonomy_desc`; if you only have the bare code and a
NUCC taxonomy MCP/crosswalk is available, look the code up to get the description,
then classify the description. If you cannot resolve a code to a description, say so
rather than guessing — do not invent a taxonomy meaning.

## Decision rule

Match **case-insensitively** as substrings against the specialty name / taxonomy
description. This mirrors `is_hospital_affiliated_specialty()` in `nppes_enrich.py`,
which uses these keywords:

```
PEDIATRIC, CARDIOLOG, CARDIOVASCULAR, OBSTETRIC, GYNECOLOG, OB/GYN, OBGYN,
INTERNAL MEDICINE, HOSPITALIST, ANESTHESIOLOG, EMERGENCY MEDICINE, CRITICAL CARE,
NEONATAL, RADIOLOG, PATHOLOG, SURGERY, SURGICAL
```

Any keyword hit → **Yes (hospital-affiliated)**.

Confidence tiers (this skill adds nuance the bare function doesn't):

- **STRONG – Yes.** Core inpatient / rotation specialties: Pediatrics, Cardiology /
  Cardiovascular, OB/GYN, Internal Medicine, Hospitalist, Anesthesiology, Emergency
  Medicine, Critical Care, Neonatal, Radiology, Pathology, Surgery / Surgical.
  These are almost always hospital-based and multi-site.

- **BORDERLINE – lean Yes.** Specialties that are *often* hospital-based but sometimes
  run standalone outpatient clinics: Oncology / Hematology, Nephrology, Pulmonology,
  Infectious Disease, Neurology, Gastroenterology, most Internal Medicine
  subspecialties. Flag as Yes but mark confidence **borderline** — the address may
  still be a single stable clinic.

- **NOT hospital-affiliated – No.** Clinic / solo / community-based settings, which
  R3 usually handles well because they have one stable address: Family Medicine,
  Dermatology, Psychology, Counseling (Mental Health / Professional), Social Work,
  Marriage & Family Therapy, Behavior Analyst, most Nurse Practitioners
  (Family / Psych-Mental Health), Optometry, Chiropractic, Podiatry, Physical Therapy.

If nothing matches and it isn't clearly on the "No" list, return **No** with
confidence **low**, and note it wasn't in the known lists (so the user can extend the
keyword set from the full NUCC crosswalk).

## Reuse the repo function when you're in the project

Inside `Final_R3_Model`, prefer the existing function so answers match the pipeline
exactly:

```python
from nppes_enrich import is_hospital_affiliated_specialty
is_hospital_affiliated_specialty("Pediatrics")                       # -> 1
is_hospital_affiliated_specialty("Nurse Practitioner, Family")       # -> 0
is_hospital_affiliated_specialty(nppes_primary_taxonomy_desc, base_specialty)  # OR across sources
```

The function returns `1`/`0` (yes/no) with no reason or confidence. Use this skill's
tiers to add the reason and confidence on top. If you extend the keyword list here,
extend `_HOSPITAL_AFFIL_KEYWORDS` in `nppes_enrich.py` too, or the feature and the
skill will disagree.

## Output format

Return exactly:

```
Hospital-affiliated: <Yes | No>
Confidence: <strong | borderline | low>
Reason: <one sentence — the matched keyword/specialty and the practice-setting logic>
```

## Examples

**Example 1**
Input: `Pediatrics`
Output:
```
Hospital-affiliated: Yes
Confidence: strong
Reason: Matches "PEDIATRIC" — pediatricians typically practice inside hospital systems across multiple sites, so the roster address often reflects the org, not the specific site.
```

**Example 2**
Input: `208000000X`
Output:
```
Hospital-affiliated: Yes
Confidence: strong
Reason: Code 208000000X = Pediatrics; core inpatient/rotation specialty.
```

**Example 3**
Input: `Nurse Practitioner, Family`
Output:
```
Hospital-affiliated: No
Confidence: strong
Reason: No hospital keyword; family NPs usually work at one stable community clinic, which R3 validates reliably.
```

**Example 4**
Input: `Internal Medicine, Endocrinology, Diabetes & Metabolism`
Output:
```
Hospital-affiliated: Yes
Confidence: borderline
Reason: Contains "INTERNAL MEDICINE" so it hits the rule, but endocrinology is frequently a standalone outpatient clinic — the single-address case is plausible, so verify with claims/NPPES before trusting the org-address explanation.
```

## Honesty limits

This is a **keyword heuristic over specialty text**, not a lookup of each provider's
actual employer. It answers "is this *type* of specialty usually hospital-based,"
which is a useful prior — but a given individual may not fit their specialty's norm.
Confirm with real evidence (claims location history, NPPES `practiceLocations`,
`nppes_num_locations`) before concluding a specific address is multi-site. The keyword
list is not exhaustive; extend it from the NUCC crosswalk as new specialties appear.
