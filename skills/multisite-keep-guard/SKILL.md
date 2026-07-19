---
name: multisite-keep-guard
description: >
  Resolve R3 address disagreements for HOSPITAL-AFFILIATED / multi-site providers
  (Pediatrics, Cardiology, OB/GYN, Internal Medicine, etc.) — the worst-performing
  segment, where R3 agrees with the phone only ~2-5%. This is the "Segment A"
  org-linkage KEEP guard. Use it whenever the task involves fixing hospital-affiliated
  specialty errors, multi-site providers, matching a roster address against ALL of a
  provider's practice locations (not just the primary), deciding KEEP vs CALL vs REMOVE
  for rotation-prone doctors, or wiring an org-linkage guard into decide.py. Trigger it
  even when the user only says "fix the hospital specialties", "provider practices at
  several sites", "why does R3 keep removing valid addresses", "match against all
  locations", or references practiceLocations, any-location match, or Segment A.
---

# Multi-Site KEEP Guard (Segment A resolution)

## The core reframe

For hospital-affiliated / rotation-prone providers, **do not ask "is this THE address."
Ask "is this ONE OF the provider's real, current practice sites."**

R3 fails on this segment (~2-5% agreement) because it validates the *organization* /
primary address from the web, while the roster row often asks about a *different but
legitimate* site the provider rotates through. The web never publishes the per-site
schedule, so a web-only engine is structurally blind here. The fix is to match the roster
address against the provider's **whole set of known sites**, recency-weighted, and to stop
R3 from removing an address just because it isn't the primary one.

Identify the segment with the `hospital-affiliated-specialty` skill (or the
`is_hospital_affiliated_specialty` column). Apply this guard only to those rows.

## Sources that verify a specific site (ranked)

Use the sources that know *which sites the individual actually practices at* — not the ones
that only echo the org address:

1. **Claims service-location history (strongest, already in the data).** Recent claims billed
   from the roster street+ZIP = the provider genuinely sees patients there. For multi-site
   doctors, claims reveal which sites are currently active. Signals:
   `RECENT_STREET_ZIP_MATCH`, `STREET_ZIP_MATCH`, `ADDR_EXACT_MATCH`, `ZIP_MATCH`, `N_CLAIMS`,
   `DISTINCT_ADDRS`.
2. **NPPES `practiceLocations[]` (already fetched).** The provider's primary + secondary
   registered sites. Signal: `nppes_any_location_match_tier` (best match across ALL locations,
   not just the primary `nppes_practice_addr_match_tier`).
3. **CMS Doctors & Clinicians National Downloadable File (optional, free).** Built for
   multi-location; lists each clinician's several practice addresses and group/hospital
   (facility) affiliations. Add it to widen site coverage. Verify the current file layout
   before parsing — CMS restructures these. The skill degrades gracefully if it's absent.

What does NOT help: web/aggregators (they echo the org address — the root cause), and USPS DPV
(deliverability only, and it's the excluded paid source).

## Decision rule (wire into decide.py)

Compute the **best site-match tier** across claims and NPPES (`NONE=0, ZIP=1, STREET_ZIP=2,
EXACT=3`), preferring recent evidence. Then:

- **KEEP_GUARD** — roster matches a real site at street+ZIP or better (`RECENT_STREET_ZIP_MATCH
  > 0`, or best tier ≥ 2). The address is one of the provider's genuine sites → do **not**
  remove. This corrects R3's main failure here (false removals of valid secondary sites).
- **CALL** — roster matches a known site only weakly or without recency (ZIP-only, or old
  street+ZIP). Real but unconfirmed-current → verify by phone, don't auto-decide.
- **REMOVE_CANDIDATE** — no site match anywhere, but the provider has claims from *other*
  addresses (billing elsewhere). Lean toward removal — but prefer CALL, because absence is
  NULL, not proof.
- **CALL_NO_EVIDENCE** — no claims and no registry match. Passive signals can't resolve it;
  this is where the robocall budget is best spent (high-value, unresolvable otherwise).

**Ignore R3's web-confidence (`Final_R3_Score_Address`) for this segment** — it is inverted at
the top in this dataset (highest confidence = least accurate), so it must not override the
guard.

## Do not degrade the agreement zone

Apply this only to hospital-affiliated rows. Leave the ~80% agreement zone untouched — the
rubric penalizes degrading it. This guard is a targeted override for one segment, not a global
rule.

## Reference implementation

```bash
python skills/multisite-keep-guard/scripts/build_multisite_decision.py \
    data/processed/Base_enriched_sample.csv \
    data/processed/Base_enriched_multisite.csv
```

Or import:

```python
from scripts.build_multisite_decision import add_multisite_decision
df = add_multisite_decision(df)
```

Outputs: `multisite_best_tier`, `multisite_action`
(`KEEP_GUARD` / `CALL` / `REMOVE_CANDIDATE` / `CALL_NO_EVIDENCE` / `not_segment_A`),
and `multisite_reason`. See the script for exact logic.

## Combine with the other skills

- `hospital-affiliated-specialty` → decides *who* is in Segment A.
- `address-staleness-feature` → within a KEEP_GUARD site, separates "still there" from
  "moved" (a hospital doctor who dropped this site).
- This skill → decides the action per row.

## Honesty limits

- The ~2-5% agreement is *this dataset's* number, not universal — re-derive on new data.
- A claims service address is usually the practice site but can sometimes be a billing/org
  address — treat a claims match as strong, not absolute.
- CMS D&C and NPPES only cover enrolled/registered activity — **absence is NULL, not
  contradiction.** Never auto-remove on absence alone for this segment; that is exactly the
  error the guard exists to prevent.

## Wiring in (retrain if used as features)

If you add `multisite_best_tier` / `multisite_action` as model features, register them in
`feature_config.yaml` and `python train.py …` — `models.pkl` expects the exact trained schema.
If you use them purely as decision overrides in `decide.py`, no retrain is needed, but keep the
override auditable (log which rows the guard changed and why).
