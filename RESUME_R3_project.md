# R3 Provider Address Accuracy — Resume Rewrite

All numbers below were re-derived from repo artifacts on 2026-07-26
(`models.pkl`, `outputs/oof_predictions.csv`, `decide.py`, `README.md`). Provenance tags:
**[VERIFIED]** = computed this session. **[ASSUMPTION]** = stated in repo docs, not re-derived.

---

## 1. Correction table — what your current resume gets wrong

| Claim on resume now | Reality | Severity |
|---|---|---|
| `XGBoost` | **LightGBM** (`lightgbm.sklearn.LGBMClassifier`, both models). No xgboost import anywhere in the repo. **[VERIFIED]** | 🔴 Critical — a stack lie an interviewer will probe ("why XGB over LGBM?") |
| `70 features` | **92** features in the `models.pkl` schema **[VERIFIED]** | 🟡 Understates you |
| `AUC 0.83` | **Model A = 0.8141**, **Model B = 0.9435** (5-fold CV) **[VERIFIED]** | 🟡 Neither model is 0.83 |
| `Zone-1 protection 91.3%` | **95.45%** agreement zone preserved **[VERIFIED]** | 🟡 Understates you |
| `100% precision at top-200` | **98.0%** **[VERIFIED]** | 🔴 Critical — "100%" reads as leakage to any senior reviewer. It *was* a leak: `prepare_X` used to pass the `target` column into the model, which produced fake 1.0 precision. That bug is now fixed (`train.py:113-118`). The old bullet is a pre-fix number. |
| `90.2% at 450` | **85.3%** **[VERIFIED]** | 🔴 Same origin |
| `"Closed the 25% accuracy gap"` | Vague. Real result: **54.85% → 75.25%, +20.40 pp** (±0.36 over 10 seeds) **[VERIFIED]** | 🟡 You're hiding your best number |

## 2. Verified metric sheet (use only these)

| Metric | Value |
|---|---|
| Records (labelled, provider×location grain) | 2,493 |
| Both-conclusive evaluation universe | 1,973 |
| R3 baseline accuracy (CallQC-conclusive rows) | **54.85%** |
| Pipeline accuracy | **75.25%** (±0.36, 10 seeds) |
| **Net lift** | **+20.40 pp** |
| Agreement zone preserved (Zone-1 protection) | **95.45%** |
| Model A — P(R3 wrong), 5-fold CV AUC | **0.8141** |
| Model B — P(call conclusive), 5-fold CV AUC | **0.9435** |
| Feature count | **92** |
| Triage precision @ top-100 / 200 / 300 / 450 | **99.0% / 98.0% / 96.0% / 85.3%** |
| Call budget | 747 calls = 30% cap of 2,493 |
| Cost | 747 × $0.50 + 2,493 × $0.035 = **$460.75** |
| Algorithms | LightGBM ×2 + rule-based decision policy + TreeSHAP |

### Feature-importance share by data source (Model A gain) **[VERIFIED]**

| Source | #feat | Gain share |
|---|---|---|
| Claims (Snowflake) | 17 | 29.0% |
| Web evidence cube (R3 scrape) | 26 | 28.2% |
| R3 internals / provider attrs / geography | 19 | 24.8% |
| CMS Doctors & Clinicians | 12 | **15.9%** |
| Staleness (derived) | 9 | 1.8% |
| NPPES registry | 9 | **0.3%** |

External corroboration (claims + CMS + NPPES + staleness) = **47.0% of total gain.**
Note NPPES contributed almost nothing — say "CMS D&C" not "NPPES" if you name one source.

---

## 3. Recommended rewrite (drop-in LaTeX, 4 bullets, same footprint)

```latex
\resumeProjectHeading
  {\textbf{R3 Provider Address Accuracy} $|$ \emph{Python, LightGBM, SHAP, Snowflake, FastAPI, Docker} $|$ \href{https://github.com/Kushalgt/r3-provider-address-accuracy}{\large\faGithub}}{Apr. 2026}
  \resumeItemListStart
    \resumeItem{Lifted provider-directory accuracy \textbf{54.9\%$\to$75.3\%} (\textbf{+20.4 pp}, $\pm$0.36 over 10 seeds) on 2,493 phone-verified records, while preserving \textbf{95.5\%} of the existing agreement zone.}
    \resumeItem{Diagnosed the \textbf{25\% web-vs-phone gap} via SHAP-backed EDA: R3's \emph{keeps} were only \textbf{38.7\%} correct vs \textbf{69.5\%} for removals, and web-confidence was \textbf{inverted} (score 100$\to$43\% correct) — reframing it as a \textbf{multi-site address} problem, not a scraping one.}
    \resumeItem{Engineered \textbf{92 features} over 4 sources (Snowflake claims, CMS Doctors \& Clinicians, NPPES, web evidence); external corroboration drove \textbf{47\% of model gain} and fixed the hospital-affiliated segment (2--5\% agreement) via any-site matching.}
    \resumeItem{Ranked a \textbf{two-model LightGBM} cascade — P(R3 wrong)$\times$P(call connects), AUC \textbf{0.81}/\textbf{0.94} — to triage a hard 30\% call budget: \textbf{98\% precision @ top-200}, 85\% @ 450 for \$461; shipped as a Dockerized \textbf{FastAPI} service with per-record SHAP explanations.}
  \resumeItemListEnd
```

### Why this is stronger

1. **Result first, and it's a big one.** "+20.4 pp" beats "closed the 25% gap" — the latter is a
   restatement of the problem, not an outcome. The `±0.36 over 10 seeds` signals you know the
   difference between a lucky run and a measured one; almost no candidate writes that.
2. **The insight bullet is the differentiator.** Anyone can say "I did EDA." Saying *R3's keeps
   are less trustworthy than its removals, and confidence is inverted* proves you found a
   non-obvious, counterintuitive truth — and it names the reframe that motivated the solution.
   Inverted confidence is the kind of finding an interviewer remembers.
3. **Ablation-style attribution.** "47% of gain from external sources" answers "did the new data
   actually matter?" in one number. This is the question a staff engineer asks about every
   integration.
4. **Constraint-aware engineering.** "Hard 30% budget" + precision@K + dollar cost shows you
   optimized under a real operating constraint, not for a leaderboard.
5. **The cascade is stated as a product**, `P(wrong) × P(connects)`, which instantly conveys
   the insight that a correct prediction is worthless if the phone never gets answered.

---

## 4. 🔴 Fix before you link the repo

`decide.py` defines `select_call_set` **twice** (lines 71 and 133). The second definition wins
and returns a tuple `(call_set, rank_map)`, but `main()` at line 321 does
`call_set = select_call_set(df)` **without unpacking**. Consequences **[VERIFIED]**:

- `len(call_set)` prints `2` (the tuple's length), so the CLI reports "Call set: 2 records"
- every `row['Row ID'] in call_set` membership test fails → **0 calls are ever placed**
- `python decide.py outputs/oof_predictions.csv` reports **57.44% / +2.58 pp**, not 75.25%

`pipeline.py:205` unpacks correctly (`call_set, rank_map = ...`), which is why the README and the
FastAPI path are right. **But anyone who clones your repo and runs the CLI sees +2.58 pp.**

Two-line fix:

```python
# decide.py line 321
call_set, rank_map = select_call_set(df)   # was: call_set = select_call_set(df)
```

...and delete the dead first definition (lines 71–131) so there's one source of truth.

---

## 5. Interview drill-down — what I'd ask you

Expect these. Have crisp answers ready.

1. **"98% precision at top-200 but AUC only 0.81 — explain."** This looks like leakage and I
   would push hard. Your answer: the head of the ranking is 94% concentrated in MI/NJ/AL and
   hospital-affiliated specialties, segments where R3's true agreement is 2–31% — so the top
   decile is near-purely positive while global separation stays moderate. **[VERIFIED: top-200 =
   MI 80, NJ 58, AL 49, NY 13; Internal Medicine 42, Cardiology 25, Peds 21, OB/GYN 20]**
2. **"You had target leakage. How did you catch it?"** Say it plainly: triage precision came back
   at exactly 1.00, which is not a real number, so you traced `prepare_X` and found the `target`
   column entering the matrix. Volunteering a bug you found and fixed reads as senior. Hiding it
   reads as junior.
3. **"Why two models instead of one?"** Because P(R3 wrong) alone spends budget on providers who
   never answer the phone. The 40% conclusivity rate makes call-connectivity a first-class
   objective, not a nuisance.
4. **"How do you know you didn't degrade the 80% that already worked?"** Zone-1 preservation was
   a tracked metric (95.45%), Zone-1 rows carried 3× sample weight and high-risk states 5×.
5. **"NPPES is in your bullet but contributes 0.3% of gain. Why keep it?"** Honest answer:
   near-zero measured lift; retained because `nppes_found` disambiguates "absent" from a real 0,
   and absence is NULL not contradiction. Consider dropping the word NPPES from the resume.
6. **"What would you do differently?"** Real answers exist: no algorithm bake-off was run
   (LightGBM was chosen, not benchmarked), no threshold-tuning study, no monitoring/drift plan,
   no test suite. Naming these preempts the "did you think about..." trap.

## 6. Honesty limits

- Precision@K figures are **out-of-fold**, not a held-out test set — say "out-of-fold" if asked.
- `75.25%` assumes the stated **40% call-conclusivity** yield and that a connected call returns
  ground truth. That yield is a problem-statement input **[ASSUMPTION]**, not something measured.
- `54.85%` baseline counts R3's INCONCLUSIVE as a miss over CallQC-conclusive rows. On the
  both-conclusive subset only, R3 = 62.44% and the pipeline lift is smaller. Use the 54.85→75.25
  framing (it's the README's and the business metric), but know both.
- Sample weights are label-derived (Zone-1 3×); legitimate cost-sensitive learning, but be ready
  to say why that isn't leakage (weights affect training only, metrics are out-of-fold).
