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
| Base Data (training) | `data/raw/Base data_hackathon.xlsx` | 1,500 labelled provider records, sheet = "Base Data", header row = 1 |
| Claims aggregate | `data/external/claims_data.csv` | Snowflake-aggregated claims, keyed on `BASE_NPI` |
| Merged dataset | `data/processed/R3_Claims_Merged_data.csv` | Base + claims merged, intermediate artifact |
| Trained models | `models.pkl` | Pickled bundle: Model A + B + explainers + feature schema |
| OOF predictions | `outputs/oof_predictions.csv` | Out-of-fold predictions for evaluation |
| Pipeline output | `outputs/pipeline_output.xlsx` | End-to-end run output |

**HIPAA-adjacent data — do not share outside the team or upload to any public service.**
Anonymize before any external API calls.

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
| `app.py` | Flask web UI |
| `db.py` | SQLite database layer (`r3_app.db`) |

---

## How to Train

```bash
python train.py data/raw/Base\ data_hackathon.xlsx data/external/claims_data.csv
# produces: models.pkl, oof_predictions.csv
```

To run the full pipeline on new data:
```python
from pipeline import run_pipeline
output_df, summary = run_pipeline(
    base_path='upload.xlsx',
    claims_source='claims.csv',   # or 'empty'
    models_path='models.pkl',
    output_path='predictions.xlsx',
    explain_mode='auto',          # 'auto' | 'template' | 'llm'
)
```

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
