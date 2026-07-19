---
name: address-staleness-feature
description: >
  Build ML features that estimate whether a provider PRACTICE ADDRESS has gone
  STALE (was accurate once, may not be now) for the R3 vs Calling-QC model.
  Use this whenever the task involves engineering staleness / recency features,
  "days since" recomputation, detecting a provider move, scoring address freshness,
  or adding staleness signals to feature_config.yaml / train.py in the Final_R3_Model
  repo. TWO non-negotiable rules this skill enforces: (1) staleness is computed ONLY
  on rows where R3 said ACCURATE, and (2) every "days since" is measured from a fixed
  anchor date of 2026-04-30 (the data-pull date), never from today. Trigger it even
  when the user only says "make a stale-address feature", "recompute days_since",
  "flag providers who moved", "freshness score", or references MOST_RECENT_DOS,
  nppes_days_since_update, or the 90-day directory rule.
---

# Address-Staleness Feature Builder

## What this produces and why

A once-correct address goes wrong over time when the provider **moves, leaves, retires,
or the practice is acquired/closed**, or when they **drop one of several sites**. This is
the *stale* failure — different from a "never there" ghost listing. Staleness is the main
way an address that R3 correctly *kept* (called ACCURATE) is nonetheless wrong on the phone.

So these features exist to catch **false keeps**: R3 said ACCURATE, but fresh evidence says
the provider is no longer at that address.

## Rule 1 — only compute staleness when R3 said ACCURATE

Compute every `stale_*` feature **only on rows where R3's address verdict is the KEEP /
ACCURATE case**. On INACCURATE and INCONCLUSIVE rows, set the `stale_*` features to missing
(`NaN`) and mark them not-applicable.

The R3 address verdict lives in the column **`Final_R3_Reco_Address`** (verified in this
repo's data). Its values are `ACCURATE - KEEP RECORD`, `INACCURATE - REMOVE RECORD`,
`INCONCLUSIVE - RELIABLE EVIDENCE NOT FOUND`, and `INCONCLUSIVE`. Match the accurate case as
**"starts with ACCURATE"** (uppercased/trimmed) — note `INACCURATE` does *not* start with
`ACCURATE`, so this cleanly excludes removals. Do **not** match a bare `R3` column; there
isn't one.

Why gate on accurate-only: staleness only answers the "should this KEEP have been a REMOVE?"
question. R3 removals and inconclusives are a different problem; computing staleness there
adds noise and can leak label structure. Also emit a mask column `stale_is_checked`
(1 where R3=ACCURATE, else 0) so the model knows where the feature is real vs. blank.

## Rule 2 — anchor every "days since" to 2026-04-30

The source data was fetched at the end of April 2026. If you compute "days since" against
*today's* date, every record silently ages by however long ago the pull was, which corrupts
the feature and makes it non-reproducible. So use a **fixed anchor**:

```
AS_OF = 2026-04-30
```

Recompute **all** days-since fields from this anchor — not just the new ones. That includes,
at minimum:
- `DAYS_SINCE` (claims) → `AS_OF − MOST_RECENT_DOS`
- `nppes_days_since_update` → `AS_OF − nppes_last_updated`
- any other "days since <date>" feature that exists now or is added later.

Overwrite the pre-existing `DAYS_SINCE` / `nppes_days_since_update` columns with the
AS_OF-anchored values so the whole dataset shares one clock. Keep `AS_OF` a single named
constant so it is easy to change if the data is re-pulled.

## The staleness signals (ranked)

Recency is everything: fresh evidence beats old, and evidence pointing *elsewhere* is
stronger than evidence merely fading.

1. **Claims recency at the roster address (strongest).** Recent claims billed from the same
   street+ZIP → currently true. Claims that stop long ago → suspect.
2. **Claims pointing to a different address** (`RECENT_STREET_ZIP_MATCH == 0` while
   `N_CLAIMS > 0` and `DISTINCT_ADDRS > 1`) → the provider is billing from somewhere else →
   likely **moved**.
3. **NPPES record age** (`nppes_days_since_update`) → weak, because NPPES lags real moves,
   but an old record with a changed practice address supports a move.
4. **90-day freshness boundary.** The No Surprises Act expects directory re-verification every
   ~90 days, so treat newest-evidence older than 90 days as "possibly stale, re-check."

## Feature outputs

Produce these columns (all `stale_*` gated to R3=ACCURATE per Rule 1):

| Column | Meaning |
|---|---|
| `stale_days_since_claim` | AS_OF − MOST_RECENT_DOS (days) |
| `stale_days_since_nppes` | AS_OF − nppes_last_updated (days) |
| `stale_min_days` | min of the available days-since witnesses (freshest evidence) |
| `stale_over_90` | 1 if `stale_min_days > 90`, else 0 |
| `stale_moved_elsewhere` | 1 if claims recently bill from a *different* address (move signal) |
| `staleness_flag` | `fresh` / `stale_suspect` / `confirmed_stale` / `unknown` / `not_applicable` |
| `stale_is_checked` | 1 where R3=ACCURATE (feature is real), else 0 |

`staleness_flag` priority: `confirmed_stale` (moved elsewhere) > `stale_suspect` (>90 days,
no move seen) > `fresh` (≤90 days) > `unknown` (accurate but no evidence at all) >
`not_applicable` (R3 not accurate).

## Reference implementation

Use the bundled script rather than re-writing this each time:

```bash
python skills/address-staleness-feature/scripts/build_staleness_features.py \
    data/processed/Base_enriched_sample.csv \
    data/processed/Base_enriched_staleness.csv
```

Or import it:

```python
from scripts.build_staleness_features import add_staleness_features, AS_OF
df = add_staleness_features(df)   # defaults to r3_col="Final_R3_Reco_Address"
```

Verified run on `Base_enriched_sample.csv` (2,493 rows): 517 rows are R3=ACCURATE and get a
staleness verdict — 154 `fresh`, 95 `confirmed_stale`, 15 `stale_suspect`, 253 `unknown`
(accurate but no claims/NPPES witness); the other 1,976 rows are `not_applicable`.
(On the sample file NPPES is populated for only 58 rows, so most of the signal here comes from
claims; run the full NPPES enrichment to strengthen `stale_days_since_nppes`.)

The script is the source of truth for the exact logic (see `scripts/build_staleness_features.py`).

## Honesty limits (do not overclaim)

- **Absence is NULL, not contradiction.** No recent claims at the address does **not** prove
  the provider left — their billing may simply not be captured. Only fresh evidence pointing
  *elsewhere* confirms a move (`confirmed_stale`); "old / no evidence" is `stale_suspect` or
  `unknown`, never a hard "wrong."
- NPPES lags reality; use it as a weak corroborator, not a real-time source.
- The 90-day boundary is a regulatory freshness convention, not a physical expiry — tune it
  and re-derive any rates on your own data.

## Wiring into the model (retrain required)

Add these as a new feature family in `feature_config.yaml`, then `python train.py …`. The
pickled `models.pkl` expects the exact `feature_cols` schema it was trained on, so editing the
config without retraining produces garbage. Feed `stale_is_checked` alongside the features so
the model can distinguish "fresh" from "not checked" (both may look like 0/NaN otherwise).
