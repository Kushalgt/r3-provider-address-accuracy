# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# R3 Accuracy Gap — Project Guide

## What This Is

HiLabs internal hackathon. Goal: improve R3's effective accuracy from ~75% to higher,
without degrading the ~80% "agreement zone" where R3 and Calling QC already agree.

**R3** is HiLabs' provider attribute validation engine. It ingests provider records,
runs web scraping + LLM parsing + ML classification, and outputs:
`ACCURATE | INACCURATE | INCONCLUSIVE`

**The gap**: ~25% of records disagree between R3 (web-based) and Calling QC (phone ground truth).
Web validation accuracy is 88% — the internet agrees with R3, but the phone says otherwise.

---

## Data Files

| File | Location | Description |
|---|---|---|
| Base Data (training) | `data/raw/Base data_hackathon.xlsx` | **2,493** labelled provider records (NOT 1,500 — the PDF problem statement says 1,500, but the delivered file has 2,493), sheet = "Base Data", header row = 1 |
| Claims aggregate | `data/external/claims_data.csv` | Snowflake-aggregated claims, keyed on `BASE_NPI` (plaintext NPI) |
| Merged dataset | `data/processed/R3_Claims_Merged_data.csv` | **NOT actually merged** — this file is byte-for-byte identical to `claims_data.csv` (1,950×11). Base data was never joined in. Do not trust the "merged" name. |
| Trained models | `models.pkl` | Pickled bundle: Model A + B + explainers + feature schema |
| OOF predictions | `outputs/oof_predictions.csv` | Out-of-fold predictions for evaluation |
| Pipeline output | `outputs/pipeline_output.xlsx` | End-to-end run output |

**HIPAA-adjacent data — do not share outside the team or upload to any public service.**
Anonymize before any external API calls.

---

## Verified Data Facts (audited 2026-07-15 on the actual files)

These supersede any conflicting numbers elsewhere in this doc. All figures computed directly
from `data/raw/Base data_hackathon.xlsx` (sheet "Base Data", header=1) and `data/external/claims_data.csv`.

**Row / key structure**
- Base data = **2,493 rows × 55 cols** (grain is provider×location, NOT one row per provider).
  The hashed `NPI` column was **removed on 2026-07-15** (see below), reducing 56→55 cols.
- The real, joinable NPI is **`OrigNPI`** (plaintext). `OrigNPI` has **515 duplicate values**
  (same provider at multiple rows) and **29 rows are missing `OrigNPI`** entirely (cannot be claims-matched).
- ✅ **`NPI` (hashed 32-char hex) column REMOVED (2026-07-15, "full clean removal").** It was not a
  usable identifier and did not join to anything. Changes made:
  - `features.py`: deleted the `feat_npi_changed_by_r3` computation (it compared plaintext `OrigNPI`
    to a hash → degenerate, always fired) and removed it from the provider_attributes active list.
  - `pipeline.py`: dropped `'NPI'` from `OUTPUT_COLUMNS` and the docstring identifier list.
  - `llm_explainer.py`: removed the `npi_changed_by_r3` cue and fact.
  - Data: `NPI` column dropped from `data/raw/Base data_hackathon.xlsx` (both sheets preserved) and
    `data/processed/Base_Claims_merged_PROPER.csv`. Originals backed up in `data/_backups_pre_npi_removal/`.
  - `models.pkl` retrained (old bundle backed up as `models.pkl.bak`). New schema = **57 features**,
    no `feat_npi_changed_by_r3`.
- ⚠️ **Pre-existing bugs found & fixed in `train.py` during the retrain** (unrelated to NPI):
  - **Target leakage**: `prepare_X` was passing the `target` column (== `R3 != CallQC`, the label
    itself) into the model, giving a fake 1.0 triage precision. Fixed by dropping label/bookkeeping
    columns (`target`, `y_r3_wrong`, `y_call_conclusive`, `CallQC`, `R3`) inside `prepare_X`.
  - **Broadcast crash**: `df` was pre-filtered to the 1,973 both-conclusive rows while `both_conclusive`
    (len 2,493) was still used at `np.where(...)`, crashing training and also starving Model B (which
    must see all records). Removed the premature filter; Model A is masked via `mask_a`, Model B uses all.
  - Post-fix CV: **Model A AUC ≈ 0.81**, **Model B AUC ≈ 0.94**, precision@top-450 ≈ 0.84 (realistic).

**Claims coverage (join key = `OrigNPI` ↔ `BASE_NPI`)**
- Claims file = 1,950 rows, 1,949 unique NPIs.
- **2,464 / 2,493 base rows** have a claims key present; **1,223 records have N_CLAIMS > 0**.
- Records **with** claims activity agree with the phone **74%** of the time vs **only 38.6%** for records with no claims — claims presence is a strong reliability proxy.

**Address label picture (Call QC = ground truth; both-conclusive n = 1,973)**
- R3 vs Call QC agreement = **62.4%** on this dataset (741 disagreements). NOTE: production R3 is ~75%;
  this Base Data is a hard, small sample where R3 underperforms — do not treat 62% as the production number.
- R3 says **ACCURATE** → correct only **38.7%**; R3 says **INACCURATE** → correct **69.5%**.
  R3's *keeps* are far less trustworthy than its *removals*; R3 **over-removes** (465 false removals vs 276 missed-bad).
- R3 web-confidence is inverted at the top: score = 100 → 43% correct; score ≤ 0 → 74% correct.
- Geography drives the gap: **AL 6%**, MI 25%, NJ 31% agreement (the 5×-weighted states); NE/KY/WA > 88%.
- Hospital-affiliated physician specialties (Peds, Cardiology, OB/GYN, Internal Medicine) agree only **2–5%**.

**Categorical dirt to clean**: `ANP` mixes `Y/N` with `YES/NO` (7 stray); `Org_Validation` has a stray `0` + 3 nulls; `County` ~41% missing; `MiddleName` ~44% missing.

---

## Two-Model Architecture

### Model A — `P(R3 is wrong)`
- **Target**: `y = 1` if R3 label ≠ Calling QC label (only on records where both are conclusive)
- **Purpose**: identify which records should be corrected or sent to robocalling
- **Expected CV AUC**: ~0.82
- **Expected precision @ top-450**: ~87%

### Model B — `P(call yields conclusive verdict)`
- **Target**: `y = 1` if Call QC = ACCURATE or INACCURATE (defined on all records)
- **Purpose**: filter the call pool — avoid wasting calls on records that won't pick up
- **Expected CV AUC**: ~0.96

**Triage score** = Model A × Model B (multiplicative — ranks records for robocalling).

---

## Pipeline Decision Logic

`decide.py` applies thresholds to model scores:
- **KEEP**: high confidence R3 is right → preserve original label
- **FLIP**: high confidence R3 is wrong → override the label
- **CALL**: medium confidence → send to robocalling
- **LEAVE_INCONCLUSIVE**: low signal → don't touch

Key thresholds: `KEEP_THRESHOLD`, `FLIP_THRESHOLD`, `CALL_PWRONG_MIN`, `CALL_PCONC_MIN`, `MAX_CALL_FRACTION`

---

## Key Source Files

| File | Role |
|---|---|
| `train.py` | Trains Model A + B, saves `models.pkl` + `oof_predictions.csv` |
| `features.py` | All feature engineering; config-driven via `feature_config.yaml` |
| `feature_config.yaml` | Toggle feature families and thresholds — edit this, not features.py |
| `pipeline.py` | End-to-end orchestrator: load → features → predict → decide → output |
| `decide.py` | Decision logic: KEEP / FLIP / CALL thresholds |
| `claims_loader.py` | Loads/aggregates claims from CSV or Snowflake |
| `claims_merger.py` | Merges claims aggregates into the base dataframe |
| `shapAnalysis.py` | SHAP plots + zone accuracy analysis |
| `llm.py` | LLM call wrapper |
| `llm_explainer.py` | Generates per-record human-readable explanations |
| `app.py` | FastAPI web UI (uvicorn, port 8000) |
| `db.py` | SQLite database layer (`r3_app.db`) |
| `utis.py` | Small IO helpers — `load_dataframe` handles xlsx/csv autodetect (typo in filename is intentional; imported as `utis`) |

---

## Commands

Install (no `pip install -r requirements.txt` recipe is validated end-to-end — `requirements.txt` lists deps but the README's explicit `pip install` list is what's actually been used):

```bash
pip install pandas numpy scikit-learn lightgbm openpyxl pyyaml \
            fastapi uvicorn jinja2 python-multipart aiosqlite \
            shap python-dotenv
# optional extras
pip install snowflake-connector-python   # Snowflake claims source
pip install anthropic                    # LLM explanations (needs ANTHROPIC_API_KEY)
```

Train both models (produces `models.pkl` and `outputs/oof_predictions.csv`):
```bash
python train.py data/raw/Base\ data_hackathon.xlsx data/external/claims_data.csv
```

Run the full pipeline via CLI (writes an Excel file):
```bash
python pipeline.py <base.xlsx> [claims.csv|empty] [output.xlsx]
```

Run the pipeline programmatically:
```python
from pipeline import run_pipeline
output_df, summary = run_pipeline(
    base_path='upload.xlsx',
    claims_source='claims.csv',   # or 'empty', or a live Snowflake conn
    models_path='models.pkl',
    output_path='predictions.xlsx',
    explain_mode='auto',          # 'auto' | 'template' | 'llm'
)
```

Run the web app (FastAPI + uvicorn, listens on 0.0.0.0:8000):
```bash
python app.py
```

Docker (README calls this the canonical way to run for evaluators — but see Gotchas):
```bash
docker-compose up --build
```

There is no test suite, linter, or formatter configured in the repo. "Testing" is done by inspecting `outputs/oof_predictions.csv` after training (K-fold OOF predictions on every input row) and by running the pipeline end-to-end against `data/raw/Base data_hackathon.xlsx`.

---

## Feature Families (feature_config.yaml)

| Family key | What it covers |
|---|---|
| `specialty_provenance` | Low-claims specialty flags (social workers, psych, etc.) |
| `r3_internals` | R3 confidence score, high-confidence flag |
| `evidence_cube_raw` | 12 raw URL counts from provider/org views × org/provider/aggregator |
| `evidence_rollups` | Total found/not-found, found ratio |
| `cross_view_agreement` | Provider-view vs. org-view agreement patterns |
| `geography` | State, high-risk state flag (AL, MI, NJ) |
| `provider_attributes` | Credentials, NPI changes, name change signal |
| `org_linkage` | Org name hospital flag, PIO evidence, org missing flag |
| `claims_raw` | n_claims, distinct orgs/addrs, days since, match flags |
| `claims_derived` | Log volume, high-volume, strong-corroborate, strong-contradict |
| `cross_interactions` | R3 accurate × claims contradict; R3 inaccurate × claims corroborate |

**Key threshold**: `claims_recent_days = 180` (window for "recent" claims activity).

---

## Hackathon Tracks

**Track 1 (Mandatory)** — Pattern Analysis: identify structural patterns in ~400 disagreement records.
Deliverable: segmentation taxonomy with resolution strategy per segment.

**Track 2** — Signal Engineering: new features / multi-source triangulation to passively resolve
disagreements (NPI registry, claims corroboration, staleness score, NLP on Call QC comments).
Deliverable: prototype with precision/recall against Call QC ground truth.

**Track 3** — Smart Robocalling Triage: rank-order the 1,500 records to maximize accuracy lift
within the 450-call budget (40% conclusivity rate → ~180 usable verdicts).
Deliverable: scoring model + simulation of expected accuracy lift.

---

## Robocalling Budget

| Parameter | Value |
|---|---|
| Max records to call | 450 (30% of 1,500) |
| Conclusivity rate | 40% (60% return voicemail/inconclusive) |
| Effective yield | ~180 usable verdicts at full budget |
| Cost | $0.50/successful call, $0.035/R3 record |

---

## Evaluation Rubric (100 pts)

| Dimension | Points | Notes |
|---|---|---|
| Validation Set Accuracy | 35 | Net accuracy on unseen holdout. Deductions for agreement-zone degradation. |
| Problem Discovery Depth | 20 | Quality of disagreement pattern taxonomy |
| Cost Efficiency | 20 | Judicious robocalling allocation |
| Feasibility & Scalability | 15 | Can it be productionized? |
| Presentation & Clarity | 10 | Final 10-min presentation |

**Critical constraint**: do not degrade accuracy on the ~80% agreement zone.
Zone 1 records (R3 correct) receive 3× sample weight during training; high-risk states (AL, MI, NJ) get 5×.

---

## External Data Sources (planned/in-use)

- **NPPES NPI Registry** — bulk dissemination file (NPI key); practice locations, last-updated date
- **CMS Doctors & Clinicians National Downloadable File** — telehealth flag, multiple practice locations
- **NUCC Taxonomy crosswalk** — specialty code normalization

**Absence from CMS files ≠ invalid provider** (only ~28.7% of practitioners linked to orgs).
Treat presence as positive corroboration; absence as null, not contradiction.

---

## Submission Requirements

- Presentation deck (10 min + 5 min Q&A)
- Code repo + README + data quality report
- Output file runnable on unseen holdout
- Docker image with required endpoints (write-only role assigned per team)

---

## Gotchas (repo state — verify before trusting)

- **`Dockerfile` is entirely commented out** except the `FROM python:3.9-slim` line. `docker-compose up --build` will build an empty image and won't actually run the app. Uncomment the `Dockerfile` body (or write a real one) before relying on the Docker path.
- **`app.py` references `templates/` and `static/` subdirectories that don't exist** — the Jinja templates (`base.html`, `upload.html`, `jobs.html`, `results.html`) and `app.css` all live at the repo root. Either move them into `templates/`+`static/` or change `TEMPLATE_DIR`/`STATIC_DIR` in `app.py` before the app can serve pages.
- **`decide.py` defines `MAX_CALL_FRACTION = 0.30` twice** (once at the top, once again mid-file) and re-imports numpy/pandas below the first definition. It's not a bug (both values agree) but be aware when editing — change both, or consolidate.
- **`pipeline.py` imports `from dotenv import load_dotenv` but never calls it.** `.env` is loaded only if some other module does it. If you need env vars in the pipeline, call `load_dotenv()` explicitly.
- **README's headline numbers (2,493 records, 74.99% accuracy, 745/747 calls) refer to a larger evaluation dataset**, not the 1,500-record Base Data described above. The `MAX_CALL_FRACTION = 0.30` is the source of truth for the call cap — the "450 calls" figure in this doc is 30% of the 1,500 training set specifically.
- **`Call qc extractor.py`** (space in filename) is a one-off preprocessing script, not part of the runtime pipeline. Don't `import` it — the space breaks module resolution.
- **The Snowflake path in `app.py` is commented out** — uploads with `claims_source='snowflake'` silently fall back to `'empty'`. Uncomment the block and set `SNOWFLAKE_*` env vars to actually hit Snowflake.
- **`models.pkl` is a 14MB pickled bundle** (`model_r3_wrong`, `model_call_conclusive`, `explainer_r3_wrong`, `feature_cols`, `cat_cols`). It's checked into the repo — if you retrain, it gets overwritten. Consider whether that's desired before running `train.py`.
- **Feature-config changes require a retrain.** Editing `feature_config.yaml` and re-running the pipeline against the existing `models.pkl` will produce garbage — the pickled models expect the exact `feature_cols` schema they were trained on. Always `python train.py …` after touching the config.
