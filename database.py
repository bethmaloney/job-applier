import sqlite3
from datetime import datetime

import config


def get_db():
    conn = sqlite3.connect(config.DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT,
            location TEXT,
            description TEXT,
            url TEXT,
            salary TEXT,
            posted_date TEXT,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, external_id)
        );

        CREATE TABLE IF NOT EXISTS job_status (
            job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
            seen INTEGER NOT NULL DEFAULT 0,
            relevance_score REAL,
            relevance_explanation TEXT,
            dismissed INTEGER NOT NULL DEFAULT 0,
            applied INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            skills TEXT,
            preferences TEXT,
            resume_text TEXT,
            target_titles TEXT,
            min_salary INTEGER,
            location TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            source TEXT NOT NULL,
            jobs_found INTEGER NOT NULL DEFAULT 0,
            new_jobs INTEGER NOT NULL DEFAULT 0,
            errors TEXT
        );
    """)
    # Migrations for existing databases
    cols = [r[1] for r in conn.execute("PRAGMA table_info(user_profile)").fetchall()]
    if "location" not in cols:
        conn.execute("ALTER TABLE user_profile ADD COLUMN location TEXT")
        conn.commit()

    # Clean up Seek salary placeholders stored in earlier scrapes
    conn.execute(
        "UPDATE jobs SET salary = '' WHERE lower(salary) LIKE 'add expected salary%'"
    )
    conn.commit()
    conn.close()


# --- Job queries ---

def insert_job(conn, source, external_id, title, company, location,
               description, url, salary, posted_date):
    """Insert a job, returning the job id. Returns None if duplicate."""
    try:
        cur = conn.execute(
            """INSERT INTO jobs (source, external_id, title, company, location,
                                description, url, salary, posted_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source, external_id, title, company, location,
             description, url, salary, posted_date),
        )
        job_id = cur.lastrowid
        conn.execute(
            "INSERT INTO job_status (job_id) VALUES (?)", (job_id,)
        )
        conn.commit()
        return job_id
    except sqlite3.IntegrityError:
        return None


def get_new_jobs(conn, source_filter=None, sort_by="relevance"):
    """Get unseen, non-dismissed jobs."""
    query = """
        SELECT j.*, js.relevance_score, js.relevance_explanation,
               js.seen, js.dismissed, js.applied
        FROM jobs j
        JOIN job_status js ON j.id = js.job_id
        WHERE js.seen = 0 AND js.dismissed = 0
    """
    params = []
    if source_filter:
        query += " AND j.source = ?"
        params.append(source_filter)

    if sort_by == "relevance":
        query += " ORDER BY js.relevance_score DESC NULLS LAST, j.fetched_at DESC"
    elif sort_by == "date":
        query += " ORDER BY j.fetched_at DESC"
    elif sort_by == "company":
        query += " ORDER BY j.company ASC, js.relevance_score DESC NULLS LAST"

    return conn.execute(query, params).fetchall()


def get_all_jobs(conn, page=1, per_page=20, show_dismissed=False):
    """Get all jobs with pagination."""
    offset = (page - 1) * per_page
    query = """
        SELECT j.*, js.relevance_score, js.relevance_explanation,
               js.seen, js.dismissed, js.applied
        FROM jobs j
        JOIN job_status js ON j.id = js.job_id
    """
    params = []
    if not show_dismissed:
        query += " WHERE js.dismissed = 0"

    query += " ORDER BY j.fetched_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    jobs = conn.execute(query, params).fetchall()

    count_query = "SELECT COUNT(*) FROM jobs j JOIN job_status js ON j.id = js.job_id"
    if not show_dismissed:
        count_query += " WHERE js.dismissed = 0"
    total = conn.execute(count_query).fetchone()[0]

    return jobs, total


def mark_seen(conn, job_id):
    conn.execute(
        "UPDATE job_status SET seen = 1, updated_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()


def mark_dismissed(conn, job_id):
    conn.execute(
        "UPDATE job_status SET dismissed = 1, updated_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()


def mark_applied(conn, job_id):
    conn.execute(
        "UPDATE job_status SET applied = 1, seen = 1, updated_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()


def update_relevance(conn, job_id, score, explanation):
    conn.execute(
        """UPDATE job_status
           SET relevance_score = ?, relevance_explanation = ?, updated_at = datetime('now')
           WHERE job_id = ?""",
        (score, explanation, job_id),
    )
    conn.commit()


def get_unranked_jobs(conn):
    """Get jobs that haven't been scored yet."""
    return conn.execute("""
        SELECT j.*, js.relevance_score
        FROM jobs j
        JOIN job_status js ON j.id = js.job_id
        WHERE js.relevance_score IS NULL AND js.dismissed = 0
    """).fetchall()


def get_jobs_for_refresh(conn, source=None, limit=50):
    """Get non-dismissed jobs that are missing a description or unranked, newest first."""
    query = """
        SELECT j.id, j.source, j.external_id, j.url, j.description, j.salary
        FROM jobs j
        JOIN job_status js ON j.id = js.job_id
        WHERE js.dismissed = 0
          AND (j.description IS NULL OR j.description = '' OR j.relevance_score IS NULL)
        ORDER BY j.date_scraped DESC
        LIMIT ?
    """
    params = [limit]
    if source:
        query = """
            SELECT j.id, j.source, j.external_id, j.url, j.description, j.salary
            FROM jobs j
            JOIN job_status js ON j.id = js.job_id
            WHERE js.dismissed = 0
              AND (j.description IS NULL OR j.description = '' OR j.relevance_score IS NULL)
              AND j.source = ?
            ORDER BY j.date_scraped DESC
            LIMIT ?
        """
        params = [source, limit]
    return conn.execute(query, params).fetchall()


def update_job_detail(conn, job_id, description, salary=None):
    """Update a job's description (and optionally salary), then clear its ranking."""
    if salary:
        conn.execute(
            "UPDATE jobs SET description = ?, salary = ? WHERE id = ?",
            (description, salary, job_id),
        )
    else:
        conn.execute(
            "UPDATE jobs SET description = ? WHERE id = ?",
            (description, job_id),
        )
    conn.execute(
        """UPDATE job_status
           SET relevance_score = NULL, relevance_explanation = NULL, updated_at = datetime('now')
           WHERE job_id = ?""",
        (job_id,),
    )
    conn.commit()


def get_dashboard_stats(conn):
    """Get counts for the stats bar."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN js.seen = 0 AND js.dismissed = 0 THEN 1 ELSE 0 END) as new,
            SUM(CASE WHEN js.dismissed = 1 THEN 1 ELSE 0 END) as dismissed,
            SUM(CASE WHEN js.applied = 1 THEN 1 ELSE 0 END) as applied,
            SUM(CASE WHEN js.relevance_score IS NULL AND js.dismissed = 0 THEN 1 ELSE 0 END) as unranked
        FROM jobs j
        JOIN job_status js ON j.id = js.job_id
    """).fetchone()
    return dict(row)


# --- User profile ---

def get_profile(conn):
    row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    return dict(row) if row else None


def save_profile(conn, skills, preferences, resume_text, target_titles, min_salary, location):
    conn.execute(
        """INSERT INTO user_profile (id, skills, preferences, resume_text, target_titles, min_salary, location, updated_at)
           VALUES (1, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
               skills = excluded.skills,
               preferences = excluded.preferences,
               resume_text = excluded.resume_text,
               target_titles = excluded.target_titles,
               min_salary = excluded.min_salary,
               location = excluded.location,
               updated_at = datetime('now')""",
        (skills, preferences, resume_text, target_titles, min_salary, location),
    )
    conn.commit()


# --- Fetch log ---

def log_fetch(conn, source, jobs_found, new_jobs, errors=None):
    conn.execute(
        "INSERT INTO fetch_log (source, jobs_found, new_jobs, errors) VALUES (?, ?, ?, ?)",
        (source, jobs_found, new_jobs, errors),
    )
    conn.commit()


def get_fetch_logs(conn, limit=20):
    return conn.execute(
        "SELECT * FROM fetch_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
