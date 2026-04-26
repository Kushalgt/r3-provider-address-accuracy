# R3 Address Accuracy — Prototype Web App (v3)

A working prototype that wraps the R3 prediction pipeline in a small FastAPI web app.
Upload a base-data file → background pipeline runs → download enriched output and view per-record decisions in a data grid.

## What changed from v2

| Change | Why |
|---|---|
| Hard 30% cap on robocall pool | You confirmed 30% is the operational ceiling, not just a PDF example |
| Configurable feature engineering via `feature_config.yaml` | Turn families/features on/off without touching Python |
| FastAPI web app with upload form, progress polling, results grid, download | Functional UI, not just CLI |
| SQLite metadata store (`r3_app.db`) | Tracks every job, its progress, and per-record outputs |
| Enriched output: 24 columns including p_r3_wrong, p_call_conclusive, decision, reason, explanation | Operations team can act on it directly |
| LLM explainer with template fallback | Per-record human-readable reason; falls back gracefully when `ANTHROPIC_API_KEY` isn't set |
| Snowflake / CSV / empty claims sources | Production-shaped — claims is a separate database, not a coupled file |

## Validated performance (re-run on your dataset)

| Metric | Value |
|---|---|
| R3 baseline accuracy | 54.85% |
| Pipeline accuracy | **74.99%** (±0.41) |
| **Net accuracy lift** | **+20.13 pp** |
| Agreement zone preserved | 99.07% |
| Calls used | 745 / 747 (30% cap of 2,493) |
| Cost | $460.76 |
| Records to keep R3 verdict | 1,463 |
| Records to flip passively | 14 |
| Records to send to robocall | 747 |
| Records left inconclusive | 269 |

## File layout

```
r3_app/
├── app.py                       # FastAPI web app — start here
├── db.py                        # SQLite metadata store
├── pipeline.py                  # End-to-end orchestrator
├── features.py                  # Feature engineering (config-driven)
├── feature_config.yaml          # Tunable feature switches — edit, retrain, iterate
├── claims_loader.py             # Snowflake / CSV / empty
├── train.py                     # Trains both LightGBM models
├── decide.py                    # Decision policy with 30% cap
├── llm_explainer.py             # Per-record explanations (template + LLM)
├── models.pkl                   # Pre-trained model bundle
├── templates/                   # Jinja2 HTML
│   ├── base.html
│   ├── upload.html
│   ├── jobs.html
│   └── results.html
└── static/
    └── app.css
```

## Quick start

### 1. Install dependencies

```bash
pip install pandas numpy scikit-learn lightgbm openpyxl pyyaml
pip install fastapi uvicorn jinja2 python-multipart aiosqlite

# Optional — for Snowflake claims source
pip install snowflake-connector-python

# Optional — for LLM-generated explanations
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Run the web app

```bash
python app.py
# Opens http://0.0.0.0:8000
```

### 3. Use the UI

Visit the home page, drop a base-data Excel file (same format as `Base_data_hackathon.xlsx`),
optionally drop a claims CSV (output of the Snowflake aggregation query) or pick "empty"
to run without claims, optionally pick LLM-explanation mode, click **Run pipeline**.

You'll be redirected to the job page which polls the backend every 1.5 seconds and shows
live progress. When the pipeline finishes, the page refreshes to show:

- A summary of decisions (Keep / Flip / Call / Leave-inconclusive)
- Total cost estimate
- A filterable per-record data grid
- A "Download enriched output" button (Excel)

### 4. Or use the pipeline programmatically

```python
from pipeline import run_pipeline

output_df, summary = run_pipeline(
    base_path='base.xlsx',
    claims_source='merged.csv',         # or Snowflake conn, or 'empty'
    models_path='models.pkl',
    output_path='predictions.xlsx',
    explain_mode='auto',
)
```

## Output schema (24 columns per record)

| Column | Meaning |
|---|---|
| Row ID, OrigNPI, NPI, FirstName, LastName, Specialty, Address1, City, State, Zip, Phone, OrganizationName | Identifiers carried through from input |
| `R3_label` | R3's original verdict |
| `R3_score` | R3's confidence 0–100 |
| `p_r3_wrong` | Model A probability that R3 is wrong (0–1) |
| `p_r3_wrong_confidence` | max(p, 1-p) — useful as a single confidence |
| `p_call_conclusive` | Model B probability the call will yield a verdict |
| `triage_priority` | p_r3_wrong × p_call_conclusive — used to rank for the call cap |
| `final_label` | Final verdict from the pipeline (or "SEND_TO_CALL") |
| `decision` | One of KEEP / FLIP / CALL / LEAVE_INCONCLUSIVE |
| `decision_reason_code` | Machine-readable reason (7 values) |
| `decision_explanation` | Human-readable one-sentence reason |
| `should_send_to_robocall` | Boolean, redundant with decision but easier to filter |
| `in_call_pool_priority_rank` | 1 = highest priority; NaN if not in pool |

## Configurable features

`feature_config.yaml` controls which features are computed. Three levels of granularity:

```yaml
families:
  specialty_provenance: true       # turn the whole family off
  claims_raw: true
  ...

features:
  feat_has_middle_name: false      # drop a single feature
  feat_specialty_length: true

thresholds:
  claims_high_volume_min: 20       # tweak rule thresholds
  claims_recent_days: 180

categorical_features:
  - feat_state
  - feat_zip_prefix
```

After editing, re-train:

```bash
python train.py base.xlsx claims.csv
# Produces models.pkl and oof_predictions.csv
```

The CV AUC and feature-importance output tells you immediately whether the new feature
set helped.

## Snowflake integration

Production claims data lives in Snowflake. The pipeline pulls aggregates on demand using
the NPIs in the uploaded base file:

```python
import snowflake.connector
from pipeline import run_pipeline

conn = snowflake.connector.connect(
    user='...', password='...', account='...',
    warehouse='...', database='R3_ADDRESS_ACCURACY_DATA',
)

output, summary = run_pipeline(
    base_path='base.xlsx',
    claims_source=conn,           # ← live connection
    models_path='models.pkl',
    output_path='predictions.xlsx',
)
```

`claims_loader.py` runs the aggregation SQL parameterized by the NPI list — only those
providers' claims get pulled, so even a large claims table stays performant.

## LLM explanations

Set `ANTHROPIC_API_KEY` and pick `explain_mode='auto'` (or `'llm'`) to use Claude Haiku for
one-sentence per-record explanations. Without the key, the pipeline uses a deterministic
template-based explainer that produces equivalently structured but less varied explanations.
Cost: ~$0.0001 per record at Haiku rates.

The LLM call wraps every individual record in try/except — if the API errors out for any
reason, that record falls back to the template explanation. The pipeline never fails because
of the explainer.

## Database schema

`r3_app.db` is a SQLite file with two tables:

```sql
jobs
  id, filename, uploaded_at, claims_source, n_records,
  status, progress_pct, progress_stage, error_message,
  output_path, summary_json

decisions
  id, job_id, row_id, orig_npi, r3_label, final_label,
  decision, p_r3_wrong, p_call_conclusive, explanation
```

For production, swap to PostgreSQL by changing one line in `db.py` (`sqlite3.connect` →
`psycopg2.connect`) — the schema is portable.

## What's not in this prototype

- **Authentication** — anyone with the URL can upload. Add OAuth/SSO before exposing externally.
- **Long-running queue** — pipeline runs in an in-process thread. For >5 concurrent users, swap to Celery + Redis.
- **Full Snowflake test** — the `claims_loader.load_from_snowflake` path is implemented but not test-runnable from this sandbox (no DB access). The CSV path and 'empty' path are both verified end-to-end.
- **Rich data-grid filtering/sorting** — current grid is a server-rendered table with chip filters. For sortable columns, drop in DataTables.js or AG Grid.

The core pipeline (features → models → decisions → output) is fully functional and
verified. Everything above the pipeline is the prototype layer that you can swap or
extend without touching the ML.
