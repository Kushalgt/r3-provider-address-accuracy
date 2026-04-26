"""
================================================================================
R3 ADDRESS ACCURACY — METADATA DATABASE
================================================================================

SQLite database tracking each upload, its progress, and the resulting outputs.
Used by the FastAPI app to render the jobs/results pages.

SCHEMA
------
    jobs
      id                INTEGER PRIMARY KEY
      filename          TEXT       (original upload name)
      uploaded_at       TIMESTAMP
      claims_source     TEXT       ('snowflake'|'csv'|'empty')
      n_records         INTEGER    (None until processed)
      status            TEXT       ('queued'|'running'|'done'|'failed')
      progress_pct      INTEGER    (0-100)
      progress_stage    TEXT
      error_message     TEXT       (only on failure)
      output_path       TEXT       (only on success)
      summary_json      TEXT       (JSON-encoded summary dict)

    decisions
      id                INTEGER PRIMARY KEY
      job_id            INTEGER     (FK to jobs.id)
      row_id            TEXT
      orig_npi          TEXT
      r3_label          TEXT
      final_label       TEXT
      decision          TEXT
      p_r3_wrong        REAL
      p_call_conclusive REAL
      explanation       TEXT
"""

import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.environ.get('R3_DB_PATH', 'r3_app.db')

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claims_source TEXT,
    n_records INTEGER,
    status TEXT DEFAULT 'queued',
    progress_pct INTEGER DEFAULT 0,
    progress_stage TEXT,
    error_message TEXT,
    output_path TEXT,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    row_id TEXT,
    orig_npi TEXT,
    r3_label TEXT,
    final_label TEXT,
    decision TEXT,
    p_r3_wrong REAL,
    p_call_conclusive REAL,
    explanation TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_job ON decisions(job_id);
CREATE INDEX IF NOT EXISTS idx_decisions_decision ON decisions(decision);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA_SQL)


def create_job(filename, claims_source):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO jobs (filename, claims_source) VALUES (?, ?)",
            (filename, claims_source),
        )
        return cur.lastrowid


def update_job_progress(job_id, pct, stage):
    with conn() as c:
        c.execute(
            "UPDATE jobs SET progress_pct=?, progress_stage=?, status='running' WHERE id=?",
            (pct, stage, job_id),
        )


def mark_job_done(job_id, output_path, summary, n_records):
    with conn() as c:
        c.execute(
            "UPDATE jobs SET status='done', progress_pct=100, "
            "output_path=?, summary_json=?, n_records=? WHERE id=?",
            (output_path, json.dumps(summary), n_records, job_id),
        )


def mark_job_failed(job_id, error_message):
    with conn() as c:
        c.execute(
            "UPDATE jobs SET status='failed', error_message=? WHERE id=?",
            (error_message, job_id),
        )


def insert_decisions(job_id, output_df):
    """Store per-record decisions for the results page."""
    rows = []
    for _, r in output_df.iterrows():
        rows.append((
            job_id,
            str(r.get('Row ID', '')),
            str(r.get('OrigNPI', '')),
            r.get('R3_label'),
            r.get('final_label'),
            r.get('decision'),
            float(r.get('p_r3_wrong', 0)) if r.get('p_r3_wrong') is not None else None,
            float(r.get('p_call_conclusive', 0)) if r.get('p_call_conclusive') is not None else None,
            r.get('decision_explanation'),
        ))
    with conn() as c:
        c.executemany(
            "INSERT INTO decisions "
            "(job_id, row_id, orig_npi, r3_label, final_label, decision, "
            " p_r3_wrong, p_call_conclusive, explanation) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )


def get_job(job_id):
    with conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(limit=50):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM jobs ORDER BY uploaded_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_decisions(job_id, decision_filter=None, limit=500):
    with conn() as c:
        if decision_filter:
            rows = c.execute(
                "SELECT * FROM decisions WHERE job_id=? AND decision=? LIMIT ?",
                (job_id, decision_filter, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM decisions WHERE job_id=? LIMIT ?", (job_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]


def decision_counts(job_id):
    with conn() as c:
        rows = c.execute(
            "SELECT decision, COUNT(*) as n FROM decisions WHERE job_id=? GROUP BY decision",
            (job_id,),
        ).fetchall()
        return {r['decision']: r['n'] for r in rows}


if __name__ == '__main__':
    init_db()
    print(f"Initialized {DB_PATH}")
