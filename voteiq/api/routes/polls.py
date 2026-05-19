"""Polls routes — Virginia poll data from ingest_va_polls.py."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from voteiq.api.dependencies import require_admin_token

router = APIRouter(tags=["polls"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")


@router.get("/polls", response_class=HTMLResponse)
def polls_page():
    tpl = os.path.join(_BASE_DIR, "templates", "polls.html")
    with open(tpl, encoding="utf-8") as f:
        return f.read()


@router.get("/api/polls")
def polls_search(
    office: str | None = None,
    cycle: str | None = None,
    race_id: str | None = None,
    limit: int = 25,
):
    """Read Virginia poll rows ingested by ingest_va_polls.py."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "results": [], "source": "missing_polls_db"}
    limit = max(1, min(int(limit or 25), 100))
    where = ["LOWER(COALESCE(state, '')) = 'virginia'"]
    params: list[object] = []
    if office:
        where.append("LOWER(COALESCE(office_type, '')) LIKE ?")
        params.append(f"%{office.lower()}%")
    if cycle:
        where.append("cycle = ?")
        params.append(cycle)
    if race_id:
        where.append("race_id = ?")
        params.append(race_id)

    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='polls'"
        ).fetchone()
        if not table_exists:
            conn.close()
            return {"count": 0, "results": [], "source": "polls_table_not_initialized"}
        rows = conn.execute(
            f"""
            SELECT *
            FROM polls
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(end_date, start_date, created_at, fetched_at) DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        records = []
        for row in rows:
            results = conn.execute(
                """
                SELECT answer, candidate_name, candidate_party, pct
                FROM poll_results
                WHERE source_record_id = ?
                ORDER BY pct DESC
                """,
                (row["source_record_id"],),
            ).fetchall()
            item = dict(row)
            item["results"] = [dict(r) for r in results]
            item.pop("raw_json", None)
            records.append(item)
        conn.close()
        return {"count": len(records), "source": "local_sqlite_polls", "results": records}
    except Exception as exc:
        return {"count": 0, "results": [], "source": "polls_error", "error": str(exc)}


@router.get("/api/poll-articles")
def poll_articles(limit: int = 25):
    """Read poll-related news/RSS mentions ingested by ingest_va_polls.py."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "results": [], "source": "missing_polls_db"}
    limit = max(1, min(int(limit or 25), 100))
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='poll_articles'"
        ).fetchone()
        if not table_exists:
            conn.close()
            return {"count": 0, "results": [], "source": "poll_articles_table_not_initialized"}
        rows = conn.execute(
            """
            SELECT source, title, summary, published_at, url, matched_terms, extracted_numbers, fetched_at
            FROM poll_articles
            ORDER BY COALESCE(published_at, fetched_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return {"count": len(rows), "source": "local_sqlite_poll_articles", "results": [dict(r) for r in rows]}
    except Exception as exc:
        return {"count": 0, "results": [], "source": "poll_articles_error", "error": str(exc)}


@router.get("/api/polls-debug")
def polls_debug(_: None = Depends(require_admin_token)):
    """Diagnostic: DB path, file existence, row counts, last ingest time."""
    info: dict = {"db_path": _POLLS_DB, "db_exists": os.path.exists(_POLLS_DB)}
    if info["db_exists"]:
        info["db_size_bytes"] = os.path.getsize(_POLLS_DB)
    try:
        conn = sqlite3.connect(_POLLS_DB)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        info["tables"] = tables
        for tbl in ("polls", "poll_results", "poll_articles", "poll_ingest_runs"):
            if tbl in tables:
                info[f"{tbl}_count"] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                if tbl == "polls":
                    row = conn.execute(
                        "SELECT state, COUNT(*) FROM polls GROUP BY state ORDER BY COUNT(*) DESC LIMIT 5"
                    ).fetchall()
                    info["polls_by_state"] = [{"state": r[0], "count": r[1]} for r in row]
                    last = conn.execute("SELECT MAX(fetched_at) FROM polls").fetchone()[0]
                    info["polls_last_fetched"] = last
                if tbl == "poll_ingest_runs":
                    runs = conn.execute(
                        "SELECT source, status, rows_written, started_at FROM poll_ingest_runs ORDER BY id DESC LIMIT 10"
                    ).fetchall()
                    info["recent_ingest_runs"] = [dict(zip(["source", "status", "rows_written", "started_at"], r)) for r in runs]
        conn.close()
    except Exception as exc:
        info["db_error"] = str(exc)
    return info


@router.get("/api/polls-feed")
def polls_feed(
    office: str | None = None,
    limit: int = 60,
):
    """Return Virginia polls with nested candidate results for the polls page."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "results": [], "source": "missing_polls_db"}
    limit = max(1, min(int(limit or 60), 200))
    where = ["state = 'Virginia'"]
    params: list = []
    if office:
        where.append("LOWER(office_type) LIKE LOWER(?)")
        params.append(f"%{office}%")
    where_sql = " AND ".join(where)
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        for tbl in ("polls", "poll_results"):
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            if not exists:
                conn.close()
                return {"count": 0, "results": [], "source": f"{tbl}_not_initialized"}
        rows = conn.execute(
            f"""
            SELECT p.source_record_id, p.source, p.cycle, p.office_type, p.seat_name,
                   p.stage, p.pollster, p.sponsor, p.fte_grade, p.sample_size,
                   p.population, p.methodology, p.start_date, p.end_date,
                   p.election_date, p.url, p.notes, p.internal, p.partisan,
                   pr.answer, pr.candidate_name, pr.candidate_party, pr.pct
            FROM (
                SELECT * FROM polls
                WHERE {where_sql}
                ORDER BY COALESCE(end_date, start_date, created_at, fetched_at) DESC
                LIMIT ?
            ) p
            LEFT JOIN poll_results pr ON pr.source_record_id = p.source_record_id
            ORDER BY COALESCE(p.end_date, p.start_date, p.created_at) DESC, p.source_record_id
            """,
            params + [limit],
        ).fetchall()
        conn.close()
        polls: dict = {}
        order: list = []
        for row in rows:
            rid = row["source_record_id"]
            if rid not in polls:
                order.append(rid)
                polls[rid] = {
                    "source_record_id": rid,
                    "source": row["source"],
                    "cycle": row["cycle"],
                    "office_type": row["office_type"],
                    "seat_name": row["seat_name"],
                    "stage": row["stage"],
                    "pollster": row["pollster"],
                    "sponsor": row["sponsor"],
                    "fte_grade": row["fte_grade"],
                    "sample_size": row["sample_size"],
                    "population": row["population"],
                    "methodology": row["methodology"],
                    "start_date": row["start_date"],
                    "end_date": row["end_date"],
                    "election_date": row["election_date"],
                    "url": row["url"],
                    "notes": row["notes"],
                    "internal": row["internal"],
                    "partisan": row["partisan"],
                    "results": [],
                }
            if row["candidate_name"] or row["answer"]:
                polls[rid]["results"].append({
                    "answer": row["answer"],
                    "candidate_name": row["candidate_name"],
                    "candidate_party": row["candidate_party"],
                    "pct": row["pct"],
                })
        result_list = [polls[rid] for rid in order]
        return {"count": len(result_list), "source": "local_sqlite_polls_feed", "results": result_list}
    except Exception as exc:
        return {"count": 0, "results": [], "source": "polls_feed_error", "error": str(exc)}


@router.get("/api/spanberger-approval")
def spanberger_approval():
    """Return Spanberger poll numbers over time for the approval tracker chart."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "data": []}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT p.end_date, p.start_date, p.pollster, p.sample_size,
                   p.population, p.source, p.url,
                   pr.pct, pr.candidate_name, pr.candidate_party
            FROM poll_results pr
            JOIN polls p ON p.source_record_id = pr.source_record_id
            WHERE LOWER(pr.candidate_name) LIKE '%spanberger%'
              AND pr.pct >= 30
              AND (p.cycle >= '2024' OR COALESCE(p.end_date, p.start_date, '') >= '2024')
            ORDER BY COALESCE(p.end_date, p.start_date) ASC
            """
        ).fetchall()
        conn.close()
        data = [
            {
                "date": row["end_date"] or row["start_date"],
                "pollster": row["pollster"],
                "pct": row["pct"],
                "candidate_name": row["candidate_name"],
                "sample_size": row["sample_size"],
                "population": row["population"],
                "source": row["source"],
                "url": row["url"],
            }
            for row in rows
        ]
        return {"count": len(data), "data": data}
    except Exception as exc:
        return {"count": 0, "data": [], "error": str(exc)}
