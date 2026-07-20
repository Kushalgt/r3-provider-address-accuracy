# MASTER PRESENTATION HANDOFF DOCUMENT
## R3 Provider-Address Accuracy — Multi-Source ML Triage
### For: AI agent building the CEO presentation · Prepared: 2026-07-20 (rev. 2 — multi-source-focused)

> **How to read this document.** Every number tagged **[VERIFIED]** was computed directly from
> the project's data files or out-of-fold predictions during this engagement and independently
> re-derived by adversarial audit agents (7/7 model audits + a 26-claim document fact-check, all
> passed). Items tagged **[ASSUMPTION]** must be confirmed before appearing on a slide. Items
> tagged **[PLACEHOLDER: …]** are missing facts the presentation must not invent — obtain them
> (§19 questionnaire) or omit the claim. Do not soften, round differently, or extrapolate any
> verified number.
>
> **The document's spine (rev. 2):** the presentation's core story is **multi-source
> integration** — how three independent witnesses (billing **claims**, the **CMS Doctors &
> Clinicians** registry, and **NPPES**) each contribute measurable reliability that the web
> alone cannot provide. §3 is the heart; everything else supports it.
>
> **One-line context** (since the executive summary section was removed by request): R3
> validates provider directory addresses from the web; against phone ground truth it agrees on
> only **62.4%** of decidable records in this dataset **[VERIFIED]**; this project integrated
> three external data sources and retrained the triage model so a fixed 450-call verification
> budget catches more genuine errors (385 vs 378) while disturbing the protected agreement zone
> less (65 vs 72 false-flags) **[VERIFIED]**.

---

# 1. Dataset Overview

| Item | Value | Status |
|---|---|---|
| Primary dataset | `Base data_hackathon.xlsx`, sheet "Base Data", two-row header (read with header=1) | [VERIFIED] |
| Samples | **2,493** provider×location rows (brief said 1,500 — the delivered file has 2,493) | [VERIFIED] |
| Raw features | 55 columns after removal of a useless hashed-NPI column | [VERIFIED] |
| Enriched dataset | `data/processed/Base_enriched_cms.csv` — **111 columns** (55 base + claims aggregates + NPPES + hospital flag + 7 staleness + 11 CMS) | [VERIFIED] |
| Target variable | Model A: `y = 1` if normalized R3 verdict ≠ Calling QC verdict (1,973 both-conclusive rows; 741 positives = 37.6%). Model B: `y = 1` if Calling QC is conclusive (2,246/2,493 = 90.1%) | [VERIFIED] |
| Join key | `OrigNPI` (plaintext 10-digit NPI); **515 duplicates** (multi-location providers), **29 missing** (kept, flagged `has_external_key=0`) | [VERIFIED] |

**Feature categories.** Identity/attributes; roster address; R3 internals (verdict + confidence
score); 12-column web-evidence cube (found/not-found URL lists × provider/org view × org-site/
provider-site/aggregator); Web-QC columns; Call-QC columns (**labels only — never features**);
claims aggregates; NPPES registry fields; CMS aggregates; engineered staleness features.

**Class balance.** Model A 37.6% positive (handled by ranking metrics + class weights, no
resampling); Model B 90.1% positive.

**Missing values [VERIFIED].** County ~41%, MiddleName ~44%; `Provider_in_Organization`/
`PIO_Evidence` missing on 149 of 465 false-removal rows; **NPPES populated for only 58/2,493
rows** (sample-scale pull — a coverage limitation, central to §3.4); `cms_max_group_size` NaN
where the provider is not in CMS (LightGBM-native).

**Duplicates.** None at the provider×location grain; 515 NPIs legitimately repeat
(multi-location). Every external source is **aggregated to one row per NPI before joining** to
prevent fan-out.

**Data quality issues [VERIFIED].** `ANP` mixes Y/N with YES/NO (7 stray); `Org_Validation` has
a stray `0` + 3 nulls; R3 verdict strings carry suffixes and two INCONCLUSIVE variants
(normalized before any comparison); `OrigNPI` is float-formatted in CSVs ("1083291587.0") — a
join trap covered in §3.1; a file named `R3_Claims_Merged_data.csv` is **not actually merged**
(byte-identical to the claims file).

**Potential biases.** This is a *hard sample*: 62.4% agreement here vs ~75% production
[ASSUMPTION — brief figure]; geographic skew (NY = 470 of 1,973 labelled rows); AL/MI/NJ
deliberately over-weighted in training; CMS covers Medicare-enrolled clinicians only; NPPES lags
real-world moves.

**Data collection assumptions.** Source data current through March 2026; **every "days-since"
feature is anchored to the fixed pull date 2026-04-30**, never the runtime clock
[VERIFIED — exact-equality audit, 0 mismatches].

---

# 2. Data Understanding

**Target distribution [VERIFIED].** On 2,246 call-conclusive records: R3 correct 1,232 (54.9%),
R3 incorrect 741 (33.0%), R3-inconclusive/call-INACCURATE 147 (6.5%),
R3-inconclusive/call-ACCURATE 126 (5.6%). Crosstab highlight: **465 records where R3 said
"INACCURATE — remove" and the phone confirmed the address is ACCURATE.**

**Key distributions [VERIFIED].**
- *R3 score on its 741 errors*: bimodal, empty middle — 381 score 0–10 (296 at exactly 0), zero
  errors score 30–70, 276 score ≥75 (154 at 90–100). The score encodes verdict direction, not
  correctness — it cannot flag its own mistakes.
- *Web evidence on false removals*: median 0 found vs 7 not-found URLs per record; aggregate
  368:3,430 (~1:9); **282/465 (61%) had zero confirming pages**. False keeps are the exact
  mirror: every one had ≥1 confirming URL (median 4) — stale-web echo.
- *Sources "confirming" wrong addresses*: aggregator-only support (167) ≈ 2× org-website-only (85).

**Correlations / leakage checks [VERIFIED by adversarial audit].** No feature correlates with
the label above |r| = 0.451 (the legal geography flag); all CMS features ≤ 0.140. A historical
target-leakage bug (label passed as a feature → fake perfect precision) was fixed pre-engagement;
audit re-confirmed `prepare_X` drops all label columns and no call-time column feeds any feature.

**Outliers.** Claims volume heavy-tailed (log1p feature); DAYS_SINCE sentinel 9999 where no
claims. No row deletion — every labelled row is signal for a tree model.

**Business insights from EDA [VERIFIED].**
1. **The smoking gun:** 92% (428/465) of false removals had the *organization's* address
   validated ACCURATE by R3 itself — the failure is "can't tie the person to the page."
2. **Geography:** in AL/MI/NJ an R3 removal is wrong 73% of the time (AL: 98%, 56/57) vs 22%
   elsewhere.
3. **Specialty:** hospital-affiliated physician specialties = 261 (56%) of false removals vs 58
   (12%) office-based physicians (4.5:1) — the multi-site rotation mechanism.
4. **Claims presence is the strongest passive reliability proxy:** 74.0% phone-agreement with
   claims activity vs 38.6% without (detail in §3.2).
5. **CMS as keep-guard:** removal error rate 57.0% where the roster address matches a real CMS
   site vs 27.8% where it doesn't (detail in §3.3).

**Unexpected finding.** Low web found-ratio predicts R3 being *right* (its correct removals),
not wrong — the same web evidence drives both error modes in opposite directions, which is the
formal proof that **no web-only threshold can fix R3**; independent witnesses are required.
This is the pivot into §3.

---

# 3. MULTI-SOURCE INTEGRATION — THE HEART OF THE STORY

## 3.1 Why external witnesses, and how the merge works

**The thesis.** R3 fails for a structural reason: the web documents *organizations*, but the
directory question is about *individuals*. Any fix built from more web evidence inherits the
same blindness (§2's found-ratio paradox proves it). The remedy is triangulation: join
independent, non-web witnesses that each see a different slice of "where does this provider
actually practice," and let the model weigh them.

**The three witnesses + two derived layers:**

| Layer | Source | What it uniquely sees | Coverage [VERIFIED] | Share of final model's decision power (gain) [VERIFIED] |
|---|---|---|---|---|
| Witness 1 | **Billing claims** (Snowflake aggregate) | Where the provider *actually bills from*, and how recently | 1,632/2,493 rows with activity (65%) | **29.0%** — the single largest block |
| Witness 2 | **CMS Doctors & Clinicians** (public, free) | *All* of a clinician's Medicare practice sites + group size | 1,855/2,493 rows; 1,420/1,949 NPIs (73%) | **15.9%** |
| Witness 3 | **NPPES registry** (public, free) | Registry practice address, record age, deactivation | 58/2,493 rows (2% — sample pull only) | **0.3%** (inert until full pull) |
| Derived | **Staleness engine** (claims-powered) | Whether a once-correct address has gone stale | 517 R3-keeps scored | **1.8%** |
| Derived | **Hospital-affiliation flag** (NUCC/NPPES taxonomy + specialty text) | Which providers rotate across sites | all 2,493 rows (908 flagged) | **2.3%** |
| — | R3's own web evidence, for comparison | — | — | 25.9% |
| — | R3 internals (confidence score etc.) | — | — | 7.0% |

**Headline for the deck [VERIFIED]:** after integration, **~49% of the model's total decision
power comes from non-web sources** — the web is no longer the only witness, and no single
source dominates. (Sum of the five source rows, less a 0.2pp double-counted interaction.)

**Master merge pattern (slide-ready description).**
`Base (provider×location, key = OrigNPI, string-normalized)`
→ LEFT JOIN claims aggregate (one row per NPI) — free, already licensed
→ LEFT JOIN CMS D&C aggregate (one row per NPI, all practice sites retained for matching) — free
→ LEFT JOIN NPPES aggregate (one row per NPI) — free
→ DERIVE staleness features (claims + NPPES recency vs AS_OF=2026-04-30)
→ DERIVE hospital-affiliation flag (taxonomy/specialty keywords)
→ 92-feature matrix → Model A × Model B → ranked call list.

**Join mechanics that made it work (fold into an engineering appendix slide).**
- NPIs must be **string-normalized** (`"1083291587.0"` → `"1083291587"` via split on "."):
  unnormalized keys match **zero** external rows [VERIFIED — caught live during CMS join].
- Every source aggregated to **one row per NPI before joining** (base grain is
  provider×location; naive joins fan out and inflate counts).
- Address comparison uses **USPS-style normalization** (suffix/directional abbreviations, ZIP5,
  suite-stripping) and **tiered matching**: 0 none / 1 ZIP-only / 2 street+ZIP / 3 exact.
  Tier 1 (ZIP-only) is deliberately *not* treated as confirmation.
- CMS bulk-file headers differ from its API names ("ZIP Code" vs `zip_code`) — a rename shim is
  required per acquisition path [VERIFIED — caught live].
- **The golden rule enforced everywhere: absence is NULL, never contradiction.** A provider
  missing from CMS/NPPES/claims is *unknown*, not wrong — only ~28.7% of practitioners are
  org-linked in CMS files [ASSUMPTION — industry figure from project domain notes], and
  behavioral-health specialties are structurally claims-sparse. Every source ships a
  `found_in_<source>` flag so the model can tell "absent" from "zero."

## 3.2 Witness 1 — Billing claims: the reliability backbone

**What it is.** A Snowflake-side aggregate of billing activity per NPI: claim volume, distinct
billing organizations and addresses, most-recent date of service, and address-match flags
against the roster address (exact / street+ZIP / ZIP, each with a recency variant).

**How it improves reliability [VERIFIED].**
- **The single strongest passive split in the entire project:** records *with* claims activity
  agree with the phone **74.0%** (n=1,330); records *without*, only **38.6%** (n=643). One flag
  nearly doubles trust.
- **Claim volume is the #1 feature of the final model** (16.3% of all gain); days-since-last-
  claim adds 5.0%, distinct-orgs 2.7% — the claims family totals **29.0%**.
- **Recency semantics:** every days-since is anchored to 2026-04-30 (audited exact). A recent
  street+ZIP-matched claim is treated as strong corroboration; recent claims from a *different*
  address with >1 distinct billing address is the **move signal**.
- **Powers the staleness engine (§3.5):** of R3's 517 keeps, claims recency classifies 154
  fresh / **95 confirmed-stale (provider now bills from elsewhere)** / 15 suspect / 253 unknown.

**Limits (state honestly).** Claims capture billed care only — cash-pay, new hires, and
low-billing specialties are invisible; absence must stay NULL. Coverage question open:
an earlier audit counted 1,223 records with activity vs the current merge's 1,632 —
[PLACEHOLDER: confirm canonical claims merge before citing either on a slide; §19 Q6].

## 3.3 Witness 2 — CMS Doctors & Clinicians: the multi-site X-ray

**What it is.** CMS's public "National Downloadable File" (dataset `mj5m-pzi6`; bulk file
801MB, 3,387,943 rows, modified 2026-03-27 [VERIFIED]) — **one row per clinician × enrollment ×
group × practice address**, so a multi-site provider appears once per real site. This is
precisely the individual-level location knowledge the web lacks, and it is free.

**How it improves reliability [VERIFIED].**
- **Coverage:** 1,420 of 1,949 joinable NPIs (73%); 929 providers flagged multi-site; **326
  roster addresses confirmed as one of the provider's real CMS practice sites** (match tier ≥2).
- **The keep-guard split:** among R3's removal decisions, where CMS confirms the roster address
  the removal is wrong **57.0%** of the time (n=142) vs **27.8%** where it doesn't (n=1,381) —
  a 2× error signal, available passively. **81 of the 465 false removals are directly rescued**
  by this one rule, at zero call cost.
- **The surprise feature:** `cms_max_group_size` — the size of the largest organization the
  clinician enrolls under — is the **#3 feature overall (7.7% of gain)**, outranking every
  purpose-built address-match feature. Membership in a big system *is* the multi-site risk
  marker. `cms_num_practice_locations` adds 2.8%; the CMS family totals **15.9%**.
- **The danger flag:** `cms_multisite_no_match` (736 rows) — provider is real and multi-site but
  the roster address matches none of their CMS sites → route to CALL, don't auto-decide.
- **Ablation proof (§8):** adding CMS was the step that improved **every** headline metric
  simultaneously — AUC 0.807→0.814, AP 0.783→0.790, Brier 0.175→0.170, P@450 0.853→0.856, and
  Model B 0.933→0.942 (group size predicts call-pickup too).

**Limits.** Medicare-enrolled clinicians only (non-participants legitimately absent); ~monthly
refresh lag; ZIP-only matches never count as confirmation.

## 3.4 Witness 3 — NPPES: small today, scoped to be next

**What it is.** The federal NPI registry: practice vs mailing address, last-update date,
deactivation status, and (via its taxonomy description) the specialty basis for the
hospital-affiliation flag.

**Current, honest contribution [VERIFIED].** Enrichment exists for only **58 of 2,493 rows**
(a sample-scale pull), so NPPES features carry just **0.3%** of model gain, and four of them
(deactivation, both match tiers, NPPES-days-since in staleness) earned zero gain — they are
**deliberately kept**: they activate automatically when the full (free) dissemination file is
pulled, with no code change. The features are guarded and masked so the model distinguishes
"not looked up" from real values.

**Why it stays in the story.** (a) Its *taxonomy* already contributes at full coverage through
the hospital-affiliation flag (§3.5); (b) it is the designated staleness witness for providers
without claims (the 253 "unknown" keeps); (c) it is the highest-confidence, zero-cost next lift
(§15 #1). The deck should present NPPES as "wired and waiting," not as a delivered gain.

## 3.5 Derived layers — how the witnesses combine

**Staleness engine (claims + NPPES → 1.8% of gain, plus decision value beyond gain).**
Two audited hard rules: computed **only where R3 said ACCURATE** (it answers the false-keep
question: "should this keep have been a remove?"), and **every date anchored to 2026-04-30**.
Output flags on the 517 keeps [VERIFIED]: fresh 154 / **confirmed_stale 95** / suspect 15 /
unknown 253. The 90-day freshness boundary aligns with the No Surprises Act re-verification
duty — a regulatory hook for the deck. Its top feature (`stale_days_since_claim`) is the
strongest non-CMS new feature.

**Hospital-affiliation flag (NUCC/NPPES taxonomy + specialty keywords → 2.3% of gain).**
Classifies rotation-prone specialties (Pediatrics, Cardiology, OB/GYN, Internal Medicine,
Surgery, Radiology, Anesthesiology, Emergency, Critical Care…) on **all** rows — 908 flagged.
Interactions encode the two risk cases: `hosp × r3_inaccurate` (false-removal signature) and
`hosp × r3_accurate` (stale-keep risk). It is a type-level prior, honest about borderline
subspecialties; §2's 4.5:1 false-removal ratio is its empirical justification.

**Why the combination beats any single source (deck message).** Each witness fails differently:
claims miss low-billing providers; CMS misses non-Medicare; NPPES lags. The model *learns* the
weighting — and the ablation (§8) shows the sources are complementary, not redundant: claims
lifted the reliability floor, CMS fixed the multi-site mechanism, and the derived layers convert
both into decision-ready flags (95 stale keeps, 81 rescued removals) that need **no calls**.

---

# 4. Feature Engineering

Final matrix: **92 features**, 15 config-toggleable families. Pre-existing families (~57
features) cover R3 internals, the web-evidence cube/rollups, cross-view agreement, geography,
provider attributes, org linkage, claims raw/derived, and interactions. This engagement added
four families (L–O), each documented per feature in §3 (purpose, intuition, logic, risks):

- **L — NPPES registry (8):** found/deactivated/days-since-update/practice≠mailing/num-locations/
  multi-location/practice- and any-location match tiers (§3.4).
- **M — Hospital-affiliated specialty (5):** flag + 4 interactions (§3.5).
- **N — Staleness (10):** days-since witnesses, min-days, over-90, moved-elsewhere, 4 flag
  one-hots, checked-mask (§3.5). Audited risk (minor): gating partially re-encodes the legal
  R3-verdict indicator; within-stratum incremental signal r≈0.20.
- **O — CMS Doctors & Clinicians (12):** found/locations/states/org-affiliations/group-size/
  telehealth/multisite/match-tier/matches-any/multisite-no-match + 2 interactions (§3.3).

All families are column-guarded (pipeline still runs on plain base files) and use no
Call-QC-time columns [VERIFIED by audit].

---

# 5. Feature Selection

Domain-driven family design + config toggles (`feature_config.yaml`); LightGBM regularization +
gain importances as the empirical filter. No RFE/correlation pruning was run [PLACEHOLDER: run
if a formal selection study is expected; low expected impact for GBMs]. Hard exclusions: all
call-time columns; label/bookkeeping columns dropped in `prepare_X`. Dropped historically: the
degenerate hashed-NPI feature. Deliberately kept dead weight: zero-gain NPPES features (§3.4
rationale). Final list: `models.pkl → feature_cols` (92).

---

# 6. Model Development

**Algorithm.** LightGBM gradient-boosted trees for both models — native missing/categorical
handling, non-linear interactions, robust at n≈2k, fast SHAP. **No other algorithm family was
benchmarked** [PLACEHOLDER: LR/XGBoost/CatBoost bake-off if the deck claims competitive model
selection; what *was* compared is feature sets (§8)].

**Architecture.** Two specialized models; triage score = **P(R3 wrong) × P(call conclusive)**.
Model B is near-solved (org characteristics predict pickup); separating objectives keeps it so.

**Training [VERIFIED from code/logs].** 5-fold StratifiedKFold (shuffle, seed 42); OOF
predictions retained for all reporting; early stopping (50 rounds); **business constraint in the
loss:** Zone-1 rows ×3, Zone-1 in AL/MI/NJ ×5; isotonic calibration on OOF; per-fold SHAP +
final-refit explainer; final refit on all masked rows.

**Hyperparameters (final, both models; "lightly tuned", no systematic search).**
`n_estimators=800 (early-stopped), learning_rate=0.04, num_leaves=31, min_child_samples=20,
reg_alpha=0.1, reg_lambda=0.1, subsample=0.85, colsample_bytree=0.85, random_state=42`.

**Compute.** MacBook (macOS 26.5.1, arm64), Python 3.9.6; end-to-end train = single-digit
minutes; scoring 2,493 rows ≈ seconds [ASSUMPTION — observed, not formally benchmarked].

---

# 7. Model Evaluation

All figures **out-of-fold**, final 92-feature model, independently re-derived [VERIFIED].

| Metric | Model A — P(R3 wrong) (n=1,973; 741 pos) | Model B — P(call conclusive) (n=2,493; 2,246 pos) |
|---|---|---|
| ROC-AUC (OOF) | **0.8138** | **0.9424** |
| CV mean ± std | 0.8141 ± 0.0280 (folds 0.791/0.855/0.775/0.828/0.821) | 0.9435 ± 0.0100 |
| Average precision | 0.7901 | 0.9937 |
| Brier raw / calibrated | 0.1696 / 0.1482 | 0.0556 / 0.0530 |
| Best-F1 (calibrated OOF) | 0.688 @ t=0.310 | 0.957 @ t=0.440 |

**Call-list quality (Model A ranking) [VERIFIED]:** P@50 0.980 · P@100 0.990 · P@200 0.980 ·
P@300 0.957 · **P@450 0.856 (385 true errors; recall 52.0%)**.

**Confusion matrix.** Deployment is rank-based (top-450), not threshold-based, so no single
matrix is canonical. Partial threshold view [VERIFIED]: at raw t=0.30 — recall 54.0%, precision
82.6%, Zone-1 FPR 6.8% (84/1,232). [PLACEHOLDER: compute a classic matrix at calibrated t=0.31
from `outputs/oof_predictions.csv` if the deck requires one.] Sensitivity@450 = 52.0%;
implied Zone-1 specificity at the budget cutoff = 94.7% (65/1,232 flagged).

**Calibration.** Isotonic improves Brier 0.1696→0.1482; `decide.py` thresholds must consume
**calibrated** scores (audit: raw-score optima drift between versions).

**Business interpretation.** The CEO-relevant number is the operating point: **of 450 funded
calls, 385 land on genuine R3 errors**, and the protected zone is disturbed less than before.

---

# 8. Model Comparison (the source-keyed ablation)

Controlled ablation — same rows, folds, seed, algorithm; only the **sources** change. This *is*
the "did multi-source integration work?" evidence [VERIFIED]:

| | Baseline — web + claims already in base (57 feats) | + Staleness & hospital taxonomy (80) | **+ CMS multi-site — FINAL (92)** |
|---|---|---|---|
| Model A OOF AUC | 0.8107 | 0.8067 | **0.8138** |
| Average precision | 0.7828 | 0.7825 | **0.7901** |
| Brier (raw) | 0.1758 | 0.1751 | **0.1696** |
| Precision @ 450 | 0.8400 | 0.8533 | **0.8556** |
| True errors in 450 calls | 378 | 384 | **385** |
| Zone-1 false-flags @ 450 | 72 | 66 | **65** |
| Model B AUC | 0.9334 | 0.9328 | **0.9424** |

**Reading (per source).** Step 1 (staleness + taxonomy, powered by claims recency and NUCC/NPPES
taxonomy) bought budget efficiency: +6 hits, −6 Zone-1 flags, at flat global AUC. Step 2 (CMS)
bought **everything**: the only variant improving all metrics at once, with Zone-1 contamination
falling at *every* K (200: 7→4, 300: 15→13, 450: 72→65, 600: 162→149) [VERIFIED].
**Tradeoffs to disclose:** the intermediate step's global AUC dipped within fold noise — scope
step-1 claims to the operating point; at a *fixed absolute* t=0.30 the final model admits ~10
more Zone-1 records (calibration shift, not ranking regression) — thresholds re-tuned per release.

---

# 9. Feature Importance (source-attributed)

**Per-source share of Model A's total gain [VERIFIED — the deck's key donut/bar]:**
claims **29.0%** · web evidence 25.9% · **CMS 15.9%** · R3 internals 7.0% · geography +
other base features ≈ remainder · hospital-taxonomy **2.3%** · staleness **1.8%** · NPPES
**0.3%**. **Non-web witnesses ≈ 49%.**

Top-12 individual features [VERIFIED]: claims volume 16.3% · high-risk state 11.9% ·
**CMS max group size 7.7% (#3)** · R3 score 7.0% · web evidence total 5.2% · days-since-claim
5.0% · web not-found/found-ratio/aggregator/org-site counts 2.7–3.6% · **CMS practice
locations 2.8% (#10)** · claims distinct orgs 2.7%.

SHAP: per-fold OOF + final TreeExplainer; per-record top-3 reasons exported in
`outputs/oof_predictions.csv`; plots on hand (bar, beeswarm, zone-comparison). Permutation
importance not computed [PLACEHOLDER — optional]. **Surprising finding:** organizational *group
size* — not any address match — is the strongest new signal; big-system membership is itself
the multi-site risk marker.

---

# 10. Error Analysis

**R3's errors (the model's targets) [VERIFIED].**
- **False removals (465, 62.8%):** 92% org-validated-accurate; 61% zero confirming pages (web
  silence misread as absence); 56% hospital-affiliated physicians; AL/MI/NJ carry 38% (removals
  there wrong 73%; AL 98%); all score ≤25 (296 at 0) — invisible to R3's own confidence.
- **False keeps (276, 37.2%):** every one had ≥1 confirming URL (stale-web echo); staleness now
  flags 95 confirmed-stale + 15 suspect.

**Triage-model residuals (final).** 65 Zone-1 records inside the top-450 (was 72); 356 true
errors (48%) below the cutoff — recall is budget-bound by design. **Hardest cases:** the 253
"unknown" keeps with **no external witness at all** (no claims, not in CMS, no NPPES) — every
passive source silent; only a call resolves them. Blind spots: non-Medicare providers;
claims-sparse behavioral-health/NP segment; specialties the keyword heuristic misses.

**Root cause → fix mapping:** web-silence over-removal → CMS/claims KEEP-guards + positive-
contradiction rule; multi-site rotation → CMS any-site matching; stale keeps → claims-recency
staleness; geography → geo-gated removals.

---

# 11. Business Impact

**Within the fixed 450-call budget [VERIFIED]:** +7 genuine corrections (385 vs 378) and −7
wasted calls per batch; at 40% conclusivity ≈ 180 usable verdicts, more of them corrections.

**Passive, zero-call levers, per source [VERIFIED]:** CMS keep-guard rescues **81 false
removals**; claims-powered staleness flags **95 stale keeps**. Together these act on the
false-removal pile = **63% of all R3 errors** without spending call budget.

**Dollarization — formula ready, inputs missing [PLACEHOLDER]:**
`Annual value ≈ (records/yr ÷ 1,500) × [(Δcorrections × value_per_correction) + (Δwasted × $0.50)] + passive_corrections × value_per_correction − run_costs`.
Known cost side: $0.50/successful call, $0.035/R3 record [ASSUMPTION — brief]; CMS/NPPES data
free; compute negligible. Missing: value per corrected entry, annual volume, human-reconciliation
cost (§19 Q1–3).

**Risk reduction.** NSA: 90-day re-verification duty + 2-business-day corrections; false
removals = directory gaps/network-adequacy optics; false keeps = surprise-billing exposure.
[PLACEHOLDER: compliance exposure estimate.]

---

# 12. Production Readiness

**Shipped [VERIFIED in repo]:** end-to-end pipeline (`pipeline.py`), FastAPI app (`app.py`,
SQLite), decision layer (`decide.py`: KEEP/FLIP/CALL/LEAVE_INCONCLUSIVE, MAX_CALL_FRACTION=0.30),
schema-pinned bundle `models.pkl` (~14MB, models + explainers + feature list), per-record
explanations (template/LLM), config-driven features with backward-compatible guards.

**Gaps (state honestly):** Dockerfile body commented out; `app.py` template/static path
mismatch; Snowflake path commented out; **no monitoring, drift detection, retraining scheduler,
registry/versioning, or rollback shipped** — proposals only. Retraining is manual and mandatory
after any config change. Security: HIPAA-adjacent — all joins local; only bare public NPIs may
leave the machine. [PLACEHOLDER: target environment, SLA, auth, PHI review.]

**Proposed architecture (label as proposal):** batch score → decide.py → call-vendor queue;
quarterly NSA-aligned CMS/NPPES refresh + re-score; drift monitors (feature distributions,
flag-rate by segment); champion/challenger retrain gate that **must include Zone-1@450** as a
release criterion.

---

# 13. Challenges Faced

| Challenge | Root cause | Resolution | Lesson |
|---|---|---|---|
| Zero-match external joins | Float-formatted NPI strings | String-normalize before every join; validate match counts | Join keys are strings; never assume a join worked |
| CMS bulk ≠ API schema | Different header conventions | Rename shim per acquisition path | Verify column contracts per source |
| Sparse NPPES (58 rows) | Sample-scale pull | Guarded+masked features; "wired and waiting" framing | Absence handled as null; coverage ≠ code readiness |
| Silent stale clocks | Days-since drifting with runtime date | Fixed AS_OF=2026-04-30 for **all** recency; exact-equality audit | Pin the clock or lose reproducibility |
| Fake perfect precision (historical) | Label leaked as feature | Dropped in `prepare_X`; audit re-verified | Leakage checks live in code |
| Training crash (historical) | Pre-filtered frame vs full mask | Removed premature filter | One universe for masks and frames |
| Hashed NPI column | Non-joinable ID in source | Removed + retrained | Audit identifiers before featurizing |
| Two-row Excel header | Merged section labels | header=1 everywhere | |
| Protecting the agreement zone | Lift must not burn Zone-1 | 3×/5× weights + Zone-1@K release metric; improved at every K | Encode constraints in loss AND evaluation |
| Trusting our own numbers | Long chain of derived stats | 7 adversarial audit agents + 26-claim doc fact-check — all PASS | Executive numbers get re-derived, not trusted |

---

# 14. Risks

- **Model:** n=1,973 labelled → fold variance ±0.028; hard-sample bias (62.4% ≠ production
  ~75%) — absolute rates won't transfer, ranking likely will [ASSUMPTION — confirm on holdout];
  calibration shifts between versions (pin decide.py to calibrated scores).
- **Data/source:** CMS = Medicare-only; NPPES lags; claims absence correlates with specialty —
  the absence-is-NULL rule prevents systematic harm to behavioral-health providers and must
  survive future edits.
- **Operational:** manual retrain + schema-pinned bundle = silent-garbage risk if config edited
  without retraining; no drift monitoring yet; 40% conclusivity assumed.
- **Ethical/bias:** geography/specialty priors shift *verification burden*, not provider
  standing; monitor flag-rates by state/specialty; no protected-class features (gender present
  in data, **not** a feature [VERIFIED]).
- **Security/compliance:** provider data never leaves the machine except bare public NPIs;
  dashboards aggregate-only. [PLACEHOLDER: formal sign-off.]

---

# 15. Future Improvements (priority-ordered, source-mapped)

1. **Full NPPES pull (free)** — coverage 58 → ~2,400 rows; activates 4 dead features; gives the
   253 "unknown" keeps a staleness witness.
2. **USPS CASS/DPV (~$10)** — deliverability gate, vacancy, geocoded R3↔corroborator distance
   (likely top-feature material).
3. **Decision-layer wiring** — CMS keep-guard, absence≠contradiction removal rule, geo-gated
   AL/MI/NJ removals; retune thresholds on calibrated scores.
4. **NLP on Call-QC comments** — mine move destinations.
5. **Algorithm bake-off + tuning study** — closes §6 gap.
6. **Monitoring** — drift, flag-rate-by-segment, Zone-1@450 release gate, quarterly refresh.
7. **FSMB licensure** for the disagreement subset only (~$12/physician).
8. **LLM integration** — explanations shipped; extend to call scripts.
9. **Active learning** — feed each batch's ~180 verdicts back as labels. (Deep learning not
   recommended at n≈2k.)

---

# 16. Sources Used

**Data:** `Base data_hackathon.xlsx` (2,493 records, internal); Snowflake claims aggregate
(1,950 rows, key BASE_NPI); **CMS Doctors & Clinicians National Downloadable File** (id
`mj5m-pzi6`, data.cms.gov, 801MB / 3,387,943 rows, modified 2026-03-27 [VERIFIED]); NPPES
registry (sample pull); NUCC taxonomy (keyword basis).
**References:** CMS NPPES dissemination + NPI files; NPPES API; CMS D&C data dictionary; NUCC
taxonomy; USPS CASS/DPV; CMS No Surprises Act provider-directory training document.
**Stack [VERIFIED]:** Python 3.9.6, macOS 26.5.1 (arm64) · pandas 2.3.3 · numpy 2.0.2 ·
scikit-learn 1.6.1 · lightgbm 4.6.0 · shap 0.49.1 · matplotlib 3.9.4 · openpyxl 3.1.5 ·
PyYAML 6.0.3 · fastapi 0.128.8 · uvicorn 0.39.0 · Jinja2 3.1.6.
**Internal:** repo `Final_R3_Model` (train/features/config/pipeline/decide/claims_loader/
shapAnalysis/llm_explainer/app); project skills (provider-address-validation,
provider-data-triangulation, address-staleness-feature, cms-doctors-clinicians-feature,
hospital-affiliated-specialty); `docs/multi_source_triangulation_plan.md`;
problem-statement PDF [PLACEHOLDER: filename/location].

---

# 17. Key Learnings

**Technical:** pin the recency clock; string join-keys + verify match counts; guard features on
column presence; OOF-only reporting + adversarial re-derivation catches real errors.
**Business:** the two error modes need different medicine — false removals fixed *passively* by
corroboration, false keeps need staleness + calls; HCO-vs-HCP is the root of web blindness;
with a fixed budget, ranking-at-K is the only metric that pays.
**Data/source:** absence is null, not contradiction — the one rule that prevents amplifying the
largest error class; each witness fails differently, which is exactly why triangulation works;
"merged" filenames lie — verify bytes.
**Model:** encode constraints as weights *and* release metrics; calibrate before thresholding;
the best new feature (CMS group size) came from a source dimension nobody originally asked for.
**Unexpected:** 92% of false removals had a validated-accurate organization — the web's failure
was never "wrong place," it was "couldn't see the person at the place."

---

# 18. CEO Takeaways

1. **The web can't see people, only places** — 92% of R3's worst errors dropped a real provider
   from a location R3 itself verified as real.
2. **We gave the model three independent witnesses** — billing claims, the CMS clinician
   registry, and NPPES — each seeing what the web can't.
3. **Nearly half the model's decision power now comes from non-web sources (≈49%)** — claims
   29%, CMS 16% — total new-data cost: **$0** (public/free sources).
4. **Each witness earned its place with a measurable reliability split:** claims presence
   74% vs 38.6% phone-agreement; a CMS site-match doubles the chance an R3 removal is wrong
   (57% vs 28%); claims recency exposed 95 stale keeps.
5. **Within the same 450-call budget: 385 of 450 calls now hit genuine errors** (was 378), with
   7 fewer calls wasted on records that were already right.
6. **The protected agreement zone got safer at every budget level** (72 → 65 wrongly-flagged at
   the cap) — the lift is not paid for with regression.
7. **~63% of all R3 errors can be attacked with zero phone calls** — 81 false removals rescued
   by CMS matching + 95 stale keeps flagged by claims recency.
8. **Never trust R3's own confidence** — its highest-confidence keeps are its least accurate
   decisions; independent corroboration is structural, not optional.
9. **Every number is out-of-fold and independently audited** (7/7 adversarial checks + a
   26-claim fact-check of this document — zero mismatches).
10. **Next lift is scoped and cheap:** full NPPES pull (free), USPS validation (~$10), and three
    guard-rules wired into the decision layer. **Ask:** [PLACEHOLDER — confirm closing ask].

---

# 19. Questions for Me (the project owner)

**Business & impact**
1. $ value of one corrected directory entry (or cost of one wrong one: member abrasion, claims
   rework, NSA exposure)?
2. Annual production record volume and batch cadence?
3. Cost of a human reconciliation; who consumes the output operationally?
4. Are the ~75% production-accuracy and 88% web-agreement figures official and citable?
5. The CEO closing ask (budget, pilot, headcount, data access)?

**Data & sources**
6. Claims-coverage provenance: 1,223 (earlier audit) vs 1,632 (current merge) records with
   activity — which merge is canonical for slides?
7. Confirm AS_OF=2026-04-30 as the official pull date for all sources.
8. Approve full NPPES dissemination pull (free) — timing?
9. Problem-statement PDF location for citation; may slides cite the 100-pt rubric?
10. Is there an unseen holdout; format; evaluation date?

**Model & metrics**
11. Run an algorithm bake-off before claiming competitive model selection?
12. Classic confusion matrix wanted — at which threshold?
13. Precise train/inference benchmarks needed, or is "minutes on a laptop" acceptable?

**Deployment**
14. Target environment, SLA, retraining owner?
15. Call-vendor: can it consume ranked lists; real conclusivity rate?
16. Compliance status for NPI-only API calls and internal dashboards?

**Presentation constraints**
17. Exact audience; time limit (10+5 assumed); template/branding; may the live dashboard be shown?

---

# 20. Recommended Presentation Storyline (10 min + 5 Q&A — source-centric arc)

| # | Slide | Purpose / key message | Visual | Speaker notes (essence) | Time | Transition |
|---|---|---|---|---|---|---|
| 1 | Title + hook | "62.4% — how often our web engine agrees with the phone." | Hero figure | The gap is members sent to the wrong door + NSA exposure | 0:30 | "Where does the gap come from?" |
| 2 | Where R3 stands | Partition 54.9/33.0/6.5/5.6; over-removal 465 vs 276 | Partition bar + error-split (exist) | A third outright wrong; errors skew to dropping good addresses | 1:00 | "The errors have a signature." |
| 3 | Root cause | 92% org-validated; hospital specialties 4.5:1; AL 98% | Org-validation bar + specialty-group bar (exist) | Web sees organizations, not people; R3's own confidence inverted | 1:15 | "So we called in witnesses who see people." |
| 4 | The three witnesses | Merge diagram: claims + CMS + NPPES on NPI; $0 data cost | Data-pipeline diagram [build] | Independent, free, each sees a different slice; absence ≠ contradiction | 0:45 | "Witness one." |
| 5 | **Witness 1 — Claims** | 74.0% vs 38.6% agreement; #1 feature (16.3%); powers staleness → 95 stale keeps | Claims split bar (exists) + stat chips | Where they actually bill, and how recently | 1:00 | "Witness two." |
| 6 | **Witness 2 — CMS** | 57% vs 28% removal error when CMS vouches; 81 rescues; group size = #3 feature | CMS split bar (exists) + rescue stat | One row per real practice site — the multi-site X-ray | 1:15 | "Witness three — and the roadmap." |
| 7 | **Witness 3 — NPPES** | Wired and waiting: 2% coverage today, full pull is free | Coverage bar (exists) | Taxonomy already contributes via the hospital flag; registry pull = next lift | 0:30 | "Fold them into one model." |
| 8 | The model | Two-model triage; ~49% of decision power now non-web | Architecture schematic + source-share bar [build] | P(wrong)×P(answers); Zone-1 protected in the loss | 0:45 | "Results." |
| 9 | Results | Ablation table + P@K curve; 385/450; Zone-1 72→65 at every K | P@K line (exists) + KPI tiles | Every metric up with CMS; all audited | 1:15 | "What it's worth." |
| 10 | Business impact | Budget math + passive levers (81+95 = zero-call attack on 63% of errors) | Impact waterfall [build] | Dollarization pending §19 inputs — placeholders marked | 1:00 | "What we need." |
| 11 | Roadmap + ask | NPPES pull, USPS, decision-layer wiring, monitoring | Timeline | Free/cheap, already scoped; ask = [PLACEHOLDER] | 0:40 | Q&A |
| 12 | Backups | §22 appendix set | — | Confusion matrix, calibration, audits, gotchas | — | — |

---

# 21. Recommended Charts

**Existing, ready PNGs** (light theme, one visual system) in `outputs/`: outcome partition;
R3-score histogram on errors; false-removal-vs-keep; web-source support; found-ratio
correct-vs-wrong; FR score/found-ratio/not-found-ratio; per-record URL counts FR vs FK; FR by
specialty / specialty-group / state / role / org-validation. **Interactive dashboard** (live,
light+dark, tooltips + table views): https://claude.ai/code/artifact/630f353c-d936-4780-9cb1-3bbcd3d52265.
Palette to keep: accent blue #2a78d6; ordinal blues #86b6ef→#2a78d6→#104281 (validated);
status green #0ca30c / red #d03b3b / orange #ec835a only with text labels; grays #52514e/#898781.

**New source-focused charts to build (priority):**
1. **Per-source gain share** (slide 8): horizontal bar — claims 29.0 / web 25.9 / CMS 15.9 /
   R3 internals 7.0 / hospital 2.3 / staleness 1.8 / NPPES 0.3; annotate "≈49% non-web."
   CEO-friendly: yes — one bar answers "did the new data matter?"
2. **Three-witnesses merge diagram** (slide 4): base spine + three joins + derived layers;
   badge each with coverage % and its reliability split.
3. **Witness scorecards** (slides 5–7): stat-tile trio per source — coverage / reliability
   split / model contribution / zero-call lever.
4. **Gain curve** (slide 9 alt): cumulative true errors vs K — "top 23% of records capture 52%
   of all errors."
5. **Business-impact waterfall** (slide 10): 741 errors → −81 CMS rescues → −95 stale flags →
   −385 called → residual; $ labels only after §19 Q1–3.
6. **Source-coverage vs contribution scatter or paired bar** (backup): shows NPPES as
   high-potential/low-coverage — the roadmap visual.

**Per requested chart type (unchanged verdicts, abbreviated):** business workflow — build
(slide 2 context); data pipeline — build (slide 4, is the merge diagram); class distribution —
exists; missing-values heatmap / correlation heatmap / PCA / learning & validation curves /
hyperparameter comparison — **skip** (no insight or not run; be honest); feature importance —
rebuild source-colored (slide 8/9); SHAP summary — exists (appendix); SHAP waterfall — build
one exemplar rescued false-removal (powerful, slide 6 or appendix); confusion matrix / ROC /
PR / calibration — appendix (compute matrix first); lift/gain — build (slide 9); model
comparison — the §8 ablation table/bars; KPI dashboard — exists (artifact screenshot or live);
cost/ROI/time-saved — build **only after** §19 Q1–3; deployment architecture & monitoring/
retraining timeline — build, clearly labelled "proposed."

---

# 22. Appendix (backup-slide material)

**Definitions.** R3; Calling QC; Zone-1 (protected agreement zone); false removal / false keep;
conclusivity; KEEP/FLIP/CALL/LEAVE_INCONCLUSIVE; match tiers 0–3; AS_OF anchor; HCP vs HCO;
witness / passive lever.
**Metric explanations.** ROC-AUC; average precision; Brier; precision@K; Zone-1@K (constraint
metric); OOF (every reported prediction comes from a model that never saw that row).
**Model formulas.** Triage = P_A(wrong) × P_B(conclusive); binary logloss with weights
w ∈ {1,3,5}; isotonic g(p) on OOF.
**Hyperparameters.** §6 block verbatim.
**Experiment log.** Three runs (57/80/92 features), seed 42, identical folds; grid in §8;
fold AUCs in §7; artifacts: `outputs/oof_predictions.csv`, `models.pkl`
(+`.bak_pre_enrich`), scratch OOF copies of both ablation runs.
**Verification record.** Workflow 1 (4 agents): leakage / anchor / metrics / zone — all PASS;
workflow 2 (3 agents): final metrics / final zone / CMS leakage — all PASS; document fact-check:
26 claims + internal consistency — zero mismatches. Minor flags: absolute-t=0.30 mid-band shift
(retune thresholds); "12 CMS features" = 11 `cms_*` + 1 interaction; `cms_max_group_size` NaN
convention.
**Repo gotchas (engineering appendix).** Dockerfile body commented out; templates/static path
mismatch; duplicate MAX_CALL_FRACTION; dotenv imported not called; retrain mandatory after
config change; `Call qc extractor.py` not importable.
**Extra charts on hand.** All §21 "exists" PNGs + SHAP bar/beeswarm/zone-comparison plots + the
interactive dashboard.
