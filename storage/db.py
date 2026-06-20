import sqlite3
import hashlib
import logging
import re
from datetime import datetime

logger          = logging.getLogger(__name__)
_DEFAULT_DB_PATH = "data/jobradar.db"


def _db(db_path: str | None) -> str:
    """Resolve the effective DB path: explicit arg > module default."""
    return db_path if db_path else _DEFAULT_DB_PATH


# ─────────────────────────────────────────────────────────────────
# NORMALISATION HELPERS
# ─────────────────────────────────────────────────────────────────

_COMPANY_NOISE = re.compile(
    r'\b(pvt\.?|private|limited|ltd\.?|inc\.?|llc|corp\.?|'
    r'technologies|technology|solutions|software|systems|services|'
    r'india|global|group|enterprises|co\.?)\b',
    re.IGNORECASE,
)
_CITY_ALIASES = {
    "bengaluru": "bangalore",
    "gurugram":  "gurgaon",
    "new delhi": "delhi",
}
_YEAR_RE = re.compile(r'\b20\d{2}\b')
_PUNCT   = re.compile(r'[^a-z0-9 ]')


def _normalize(text: str) -> str:
    s = text.lower().strip()
    s = _YEAR_RE.sub('', s)
    s = _PUNCT.sub(' ', s)
    s = ' '.join(s.split())
    return s


def _normalize_company(company: str) -> str:
    s = _normalize(company)
    s = _COMPANY_NOISE.sub('', s)
    return ' '.join(s.split())


def _normalize_location(location: str) -> str:
    s = _normalize(location)
    return _CITY_ALIASES.get(s, s)


# ─────────────────────────────────────────────────────────────────
# JOB ID FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def make_job_id(job: dict) -> str:
    key = (
        _normalize(job.get('title', ''))
        + _normalize_company(job.get('company', ''))
        + _normalize_location(job.get('location', ''))
    )
    return hashlib.md5(key.encode()).hexdigest()


def make_url_id(job: dict) -> str:
    url = job.get('url', '').strip().rstrip('/')
    url = re.sub(r'[?&](utm_[^&]+|ref=[^&]+|source=[^&]+)', '', url)
    return hashlib.md5(url.encode()).hexdigest() if url else ""


# ─────────────────────────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────────────────────────

def init_db(db_path: str | None = None):
    import os
    os.makedirs("data", exist_ok=True)

    path = _db(db_path)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,
            url_id       TEXT,
            title        TEXT,
            company      TEXT,
            location     TEXT,
            description  TEXT,
            url          TEXT,
            source       TEXT,
            salary       TEXT,
            posted_at    TEXT,
            seen_at      TEXT,
            score        INTEGER DEFAULT 0,
            score_reason TEXT,
            highlights   TEXT,
            red_flags    TEXT,
            notified     INTEGER DEFAULT 0
        )
    """)
    for col_def in [
        "ALTER TABLE jobs ADD COLUMN url_id TEXT",
    ]:
        try:
            conn.execute(col_def)
        except Exception:
            pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_url_id ON jobs(url_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            run_at       TEXT,
            total_raw    INTEGER,
            after_dedup  INTEGER,
            after_filter INTEGER,
            after_score  INTEGER,
            notified     INTEGER
        )
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
# DEDUP QUERY
# ─────────────────────────────────────────────────────────────────

def is_duplicate(job: dict, db_path: str | None = None) -> bool:
    """Returns True if this job was already seen (by title-hash OR URL)."""
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn   = sqlite3.connect(_db(db_path))
    row = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None and url_id:
        row = conn.execute("SELECT id FROM jobs WHERE url_id=?", (url_id,)).fetchone()
    conn.close()
    return row is not None


def is_already_notified(job: dict, db_path: str | None = None) -> bool:
    """Returns True if this job was already scored and notified (notified >= 1)."""
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn   = sqlite3.connect(_db(db_path))
    row = conn.execute(
        "SELECT notified FROM jobs WHERE id=? AND notified >= 1", (job_id,)
    ).fetchone()
    if row is None and url_id:
        row = conn.execute(
            "SELECT notified FROM jobs WHERE url_id=? AND notified >= 1", (url_id,)
        ).fetchone()
    conn.close()
    return row is not None


# ─────────────────────────────────────────────────────────────────
# WRITE / READ
# ─────────────────────────────────────────────────────────────────

def save_job(
    job: dict,
    score:       int  = 0,
    reason:      str  = "",
    highlights:  str  = "",
    red_flags:   str  = "",
    notified:    int  = 0,
    db_path:     str | None = None,
):
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn   = sqlite3.connect(_db(db_path))
    conn.execute("""
        INSERT OR IGNORE INTO jobs
        (id, url_id, title, company, location, description, url, source,
         salary, posted_at, seen_at, score, score_reason, highlights, red_flags,
         notified)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        job_id, url_id,
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("description", ""),
        job.get("url", ""),
        job.get("source", ""),
        job.get("salary", ""),
        job.get("posted_at", ""),
        datetime.now().isoformat(),
        score, reason, highlights, red_flags, notified,
    ))
    # Update notified flag if job already existed with notified=0
    conn.execute(
        "UPDATE jobs SET notified=?, score=?, score_reason=?, highlights=?, red_flags=? "
        "WHERE id=? AND notified=0",
        (notified, score, reason, highlights, red_flags, job_id)
    )
    conn.commit()
    conn.close()


def get_jobs_by_score(min_score: int = 6, db_path: str | None = None) -> list[dict]:
    conn = sqlite3.connect(_db(db_path))
    rows = conn.execute("""
        SELECT title, company, location, url, salary, score, score_reason,
               highlights
        FROM jobs WHERE score >= ? AND notified = 0
        ORDER BY score DESC
    """, (min_score,)).fetchall()
    conn.close()
    return [
        dict(zip(["title", "company", "location", "url", "salary",
                  "score", "reason", "highlights"], row))
        for row in rows
    ]