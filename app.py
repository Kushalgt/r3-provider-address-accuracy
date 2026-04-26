"""
================================================================================
R3 ADDRESS ACCURACY — WEB APP
================================================================================

FastAPI app exposing:
  GET  /                          home page (upload form + recent jobs)
  POST /upload                    accept base file (+ optional claims CSV)
  GET  /jobs                      list of all jobs
  GET  /jobs/{job_id}             results page for one job
  GET  /jobs/{job_id}/progress    progress JSON (polled by frontend)
  GET  /jobs/{job_id}/download    download the enriched output file
  GET  /api/jobs/{job_id}/decisions?filter=CALL  paginated decision rows

Pipeline runs in a background thread via FastAPI BackgroundTasks. For
production we'd swap in Celery or RQ, but for a hackathon prototype the
in-process worker is sufficient.

USAGE
-----
    python app.py
    # opens http://localhost:8000
"""

import os
import shutil
import uuid
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import db
from pipeline import run_pipeline


# ============================================================================
# APP SETUP
# ============================================================================

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / 'uploads'
OUTPUT_DIR = BASE_DIR / 'outputs'
TEMPLATE_DIR = BASE_DIR / 'templates'
STATIC_DIR = BASE_DIR / 'static'
MODELS_PATH = BASE_DIR / 'models.pkl'

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI(title='R3 Address Accuracy')
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
if STATIC_DIR.exists():
    app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')

db.init_db()


# ============================================================================
# BACKGROUND PIPELINE WORKER
# ============================================================================

def _run_pipeline_async(job_id, base_path, claims_path, claims_source_label, explain_mode):
    """Run the pipeline in a background thread and update the DB as it goes."""
    try:
        def progress(stage, pct):
            db.update_job_progress(job_id, pct, stage)

        output_path = OUTPUT_DIR / f'job_{job_id}_predictions.xlsx'

        # claims_path is the file path for CSV uploads, or 'empty', or 'snowflake'
        if claims_source_label == 'snowflake':
            # In production, look up Snowflake creds from env and pass conn here.
            # For prototype we treat 'snowflake' as 'empty' to keep this runnable.
            claims_source = 'empty'
        elif claims_source_label == 'csv' and claims_path:
            claims_source = str(claims_path)
        else:
            claims_source = 'empty'

        out_df, summary = run_pipeline(
            base_path=str(base_path),
            claims_source=claims_source,
            models_path=str(MODELS_PATH),
            output_path=str(output_path),
            explain_mode=explain_mode,
            progress_callback=progress,
        )

        db.insert_decisions(job_id, out_df)
        db.mark_job_done(job_id, str(output_path), summary, len(out_df))
    except Exception as e:
        traceback.print_exc()
        db.mark_job_failed(job_id, str(e))


# ============================================================================
# ROUTES
# ============================================================================

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    jobs = db.list_jobs(limit=10)
    return templates.TemplateResponse(
        request, 'upload.html', {'jobs': jobs}
    )


@app.post('/upload')
async def upload(
    request: Request,
    base_file: UploadFile = File(...),
    claims_file: UploadFile = File(None),
    claims_mode: str = Form('empty'),
    explain_mode: str = Form('template'),
):
    # Persist the uploaded base file
    job_uuid = uuid.uuid4().hex[:8]
    base_path = UPLOAD_DIR / f'{job_uuid}_{base_file.filename}'
    with open(base_path, 'wb') as f:
        shutil.copyfileobj(base_file.file, f)

    # Persist the optional claims CSV
    claims_path = None
    if claims_file is not None and claims_file.filename:
        claims_path = UPLOAD_DIR / f'{job_uuid}_claims_{claims_file.filename}'
        with open(claims_path, 'wb') as f:
            shutil.copyfileobj(claims_file.file, f)
        claims_mode = 'csv'

    job_id = db.create_job(base_file.filename, claims_mode)

    # Kick off the pipeline in a background thread
    th = threading.Thread(
        target=_run_pipeline_async,
        args=(job_id, base_path, claims_path, claims_mode, explain_mode),
        daemon=True,
    )
    th.start()

    return RedirectResponse(url=f'/jobs/{job_id}', status_code=303)


@app.get('/jobs', response_class=HTMLResponse)
def jobs_list(request: Request):
    jobs = db.list_jobs(limit=100)
    return templates.TemplateResponse(
        request, 'jobs.html', {'jobs': jobs}
    )


@app.get('/jobs/{job_id}', response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')

    decisions = []
    counts = {}
    summary = {}
    if job['status'] == 'done':
        decisions = db.get_decisions(job_id, limit=200)
        counts = db.decision_counts(job_id)
        if job.get('summary_json'):
            import json
            summary = json.loads(job['summary_json'])

    return templates.TemplateResponse(
        request,
        'results.html',
        {
            'job': job,
            'decisions': decisions,
            'counts': counts,
            'summary': summary,
        },
    )


@app.get('/jobs/{job_id}/progress')
def job_progress(job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    return JSONResponse({
        'status': job['status'],
        'progress_pct': job['progress_pct'] or 0,
        'progress_stage': job['progress_stage'] or '',
        'error': job.get('error_message'),
    })


@app.get('/jobs/{job_id}/download')
def download_output(job_id: int):
    job = db.get_job(job_id)
    if not job or job['status'] != 'done':
        raise HTTPException(404, 'Output not available')
    path = job['output_path']
    if not path or not os.path.exists(path):
        raise HTTPException(404, 'Output file missing')
    return FileResponse(path, filename=os.path.basename(path),
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get('/api/jobs/{job_id}/decisions')
def api_decisions(job_id: int, filter: str = None, limit: int = 200):
    rows = db.get_decisions(job_id, decision_filter=filter, limit=limit)
    return {'rows': rows, 'count': len(rows)}


# ============================================================================
# CLI ENTRY
# ============================================================================

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host='0.0.0.0', port=8000, reload=False)
