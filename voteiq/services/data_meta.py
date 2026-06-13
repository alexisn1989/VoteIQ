"""
data_meta — lightweight data-provenance tracker.

Every ingest script calls stamp() after a successful commit so the app
can show users when each dataset was last refreshed and from where.

Schema lives in polls.db (the main operational DB) so it survives
across redeploys alongside the rest of the operational tables.

Usage in an ingest script:
    from voteiq.services.data_meta import stamp
    stamp("va_campaign_finance", row_count=n, source_url="https://vpap.org/...")
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parents[2]


def _db_path() -> str:
    return os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "polls.db")


def ensure_schema() -> None:
    """Create __data_meta table if it doesn't exist. Safe to call at startup."""
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS __data_meta (
            table_name    TEXT PRIMARY KEY,
            last_ingested TEXT,
            row_count     INTEGER,
            source_url    TEXT
        )
    """)
    conn.commit()
    conn.close()


# Performance indexes that must exist for hot read paths but may be missing
# on deployments whose polls.db predates the index (the seed only adds them on
# a fresh rebuild). Each is cheap to create on its small table. Creating an
# index that already exists is a no-op (IF NOT EXISTS), so this is safe to run
# on every startup. Format: (index_name, table_name, create_sql).
_PERFORMANCE_INDEXES = [
    (
        "idx_lrv_voter_session",
        "va_legislator_recent_votes",
        "CREATE INDEX IF NOT EXISTS idx_lrv_voter_session "
        "ON va_legislator_recent_votes(voter_name, session)",
    ),
    (
        "idx_lrv_bill_session",
        "va_legislator_recent_votes",
        "CREATE INDEX IF NOT EXISTS idx_lrv_bill_session "
        "ON va_legislator_recent_votes(bill_id, session)",
    ),
]


def ensure_performance_indexes() -> None:
    """Create missing hot-path indexes. Safe + cheap to call at startup.

    Without idx_lrv_* the legislator-profile voting-similarity self-join makes
    SQLite build an AUTOMATIC index on every request, which stalls the profile
    page on memory-constrained hosts (Render). Each CREATE is a no-op when the
    index already exists, so this never does real work on a healthy DB.
    """
    try:
        conn = sqlite3.connect(_db_path(), timeout=15)
    except Exception:
        return
    try:
        existing_tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for _idx_name, table_name, sql in _PERFORMANCE_INDEXES:
            if table_name not in existing_tables:
                continue  # table not built yet on this deployment — skip
            try:
                conn.execute(sql)
            except Exception:
                pass
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def stamp(
    table_name: str,
    row_count: int | None = None,
    source_url: str | None = None,
) -> None:
    """
    Record a successful ingest for *table_name*.

    Safe to call from any ingest script — if polls.db is missing or locked,
    the error is swallowed so it never breaks an ingest run.
    """
    try:
        conn = sqlite3.connect(_db_path(), timeout=15)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS __data_meta (
                table_name    TEXT PRIMARY KEY,
                last_ingested TEXT,
                row_count     INTEGER,
                source_url    TEXT
            )
        """)
        conn.execute(
            """
            INSERT INTO __data_meta (table_name, last_ingested, row_count, source_url)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(table_name) DO UPDATE SET
                last_ingested = excluded.last_ingested,
                row_count     = excluded.row_count,
                source_url    = COALESCE(excluded.source_url, source_url)
            """,
            (
                table_name,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                row_count,
                source_url,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_all() -> list[dict]:
    """Return all rows as dicts — used by the /api/data-freshness endpoint."""
    try:
        conn = sqlite3.connect(_db_path(), timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT table_name, last_ingested, row_count, source_url "
            "FROM __data_meta ORDER BY last_ingested DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def freshness_lookup() -> dict[str, str]:
    """Return {table_name: 'YYYY-MM-DD'} for use in LLM system prompts."""
    try:
        rows = get_all()
        return {
            r["table_name"]: r["last_ingested"][:10]  # ISO date only
            for r in rows
            if r.get("last_ingested")
        }
    except Exception:
        return {}


def check_count(table_name: str, new_count: int) -> None:
    """
    Raise RuntimeError if new_count dropped more than 15% vs. the prior
    recorded count for *table_name*.

    Call this BEFORE conn.commit() in ingest scripts so a bad scrape
    never overwrites good data.

    Usage:
        from voteiq.services.data_meta import check_count, stamp
        check_count("va_campaign_contributions", len(rows))
        conn.commit()
        stamp("va_campaign_contributions", row_count=len(rows), source_url="...")
    """
    try:
        conn = sqlite3.connect(_db_path(), timeout=10)
        row = conn.execute(
            "SELECT row_count FROM __data_meta WHERE table_name = ?",
            (table_name,),
        ).fetchone()
        conn.close()
        if row is None or row[0] is None:
            return  # no prior baseline — first run, always allow
        prior = row[0]
        if new_count < prior * 0.85:
            raise RuntimeError(
                f"[data_meta] Sanity check FAILED for '{table_name}': "
                f"{prior:,} -> {new_count:,} rows "
                f"({100 * (1 - new_count / prior):.0f}% drop, threshold 15%). "
                f"Aborting - commit not executed."
            )
    except RuntimeError:
        raise
    except Exception:
        pass  # DB unavailable — don't block the ingest
