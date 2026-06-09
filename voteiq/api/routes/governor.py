"""Governor action money analyst endpoints."""
from __future__ import annotations

import json
import os
import sqlite3
import traceback
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from voteiq.queries.governor_actions import (
    get_governor_action_counts,
    get_governor_action_money_patterns,
    get_top_sponsors_by_action,
)
from voteiq.services.governor_actions import (
    build_governor_action_money_analysis,
    list_governor_action_bills,
)

router = APIRouter(tags=["governor"])

_BASE_DIR = Path(__file__).resolve().parents[3]  # project root


def _polls_conn() -> sqlite3.Connection:
    """Locate polls.db using the same multi-path resolution as database_context.
    Tries DATA_DIR / VOTEIQ_DATA_DIR / RENDER_DISK_MOUNT_PATH env vars first,
    then the project root, then common Render disk mount points.
    Raises RuntimeError with a diagnostic message if none exist.
    """
    candidates: list[str] = []
    for env_var in ("DATA_DIR", "VOTEIQ_DATA_DIR", "RENDER_DISK_MOUNT_PATH"):
        val = os.getenv(env_var)
        if val:
            candidates.append(os.path.join(val, "polls.db"))
    candidates.append(os.path.join(str(_BASE_DIR), "polls.db"))
    for fixed in ("/data/polls.db", "/var/data/polls.db"):
        candidates.append(fixed)

    for path in candidates:
        if os.path.exists(path):
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn

    tried = ", ".join(candidates)
    raise RuntimeError(
        f"polls.db not found. Tried paths: {tried}. "
        "Set the DATA_DIR environment variable to the directory containing polls.db."
    )


def _inject(template: str, key: str, data) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / template).open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window.{key}={safe};</script></head>", 1)


@router.get("/api/governor/actions/summary")
def governor_action_summary(
    session: str = Query(default="2026"),
    finance_cycle: str = Query(default="2026"),
    governor: str = Query(default="Spanberger"),
    limit_per_action: int = Query(default=10, ge=1, le=50),
):
    """Return structured governor-action counts, money patterns, and top sponsors."""
    return {
        "status": "success",
        "session": session,
        "finance_cycle": finance_cycle,
        "governor": governor,
        "action_counts": get_governor_action_counts(
            session=session,
            governor=governor,
        ),
        "money_patterns": get_governor_action_money_patterns(
            session=session,
            finance_cycle=finance_cycle,
            governor=governor,
        ),
        "top_sponsors_by_action": get_top_sponsors_by_action(
            session=session,
            governor=governor,
            finance_cycle=finance_cycle,
            limit_per_action=limit_per_action,
        ),
    }


@router.get("/api/governor/actions/bills")
def governor_action_bills(
    session: str = Query(default="2026"),
    governor: str = Query(default="Spanberger"),
    action_label: str | None = Query(default=None),
):
    """List governor-action bills, optionally filtered by standardized action label."""
    return list_governor_action_bills(
        session=session,
        governor=governor,
        action_label=action_label,
    )


@router.get("/api/governor/db-check")
def governor_db_check():
    """Diagnose production DB state for governor_actions and governor_executive_orders."""
    data_dir = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(data_dir, "polls.db")
    result: dict = {
        "db_path": db_path,
        "db_exists": os.path.exists(db_path),
        "tables": [],
        "governor_actions": {},
        "governor_executive_orders": {},
        "signed_sample": [],
        "schema_columns": [],
        "error": None,
    }
    if not result["db_exists"]:
        return result
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        result["tables"] = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]

        if "governor_actions" in result["tables"]:
            result["schema_columns"] = [
                r[1] for r in conn.execute(
                    "PRAGMA table_info(governor_actions)"
                ).fetchall()
            ]
            result["governor_actions"] = {
                row["action"]: row["cnt"]
                for row in conn.execute("""
                    SELECT action,
                           COUNT(*) AS cnt
                    FROM governor_actions
                    WHERE lower(governor) LIKE '%spanberger%'
                    GROUP BY action
                    ORDER BY cnt DESC
                """).fetchall()
            }
            result["governor_actions"]["_distinct_governors"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT governor FROM governor_actions LIMIT 10"
                ).fetchall()
            ]
            result["governor_actions"]["_distinct_sessions"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT session FROM governor_actions ORDER BY session DESC LIMIT 5"
                ).fetchall()
            ]
            # Try the exact signed query used in the chat overview
            try:
                rows = conn.execute("""
                    SELECT bill_number, title, action_date, chapter_number, source_url
                    FROM governor_actions
                    WHERE session = '2026'
                      AND lower(governor) LIKE '%spanberger%'
                      AND action = 'signed'
                    ORDER BY action_date DESC, bill_number
                    LIMIT 5
                """).fetchall()
                result["signed_sample"] = [dict(r) for r in rows]
                result["signed_query"] = "OK"
            except Exception as e:
                result["signed_query"] = f"ERROR: {e}"
                # Try without chapter_number
                try:
                    rows = conn.execute("""
                        SELECT bill_number, title, action_date, NULL AS chapter_number, source_url
                        FROM governor_actions
                        WHERE session = '2026'
                          AND lower(governor) LIKE '%spanberger%'
                          AND action = 'signed'
                        ORDER BY action_date DESC, bill_number
                        LIMIT 5
                    """).fetchall()
                    result["signed_sample"] = [dict(r) for r in rows]
                    result["signed_query_fallback"] = "OK"
                except Exception as e2:
                    result["signed_query_fallback"] = f"ERROR: {e2}"

        if "governor_executive_orders" in result["tables"]:
            eo_count = conn.execute(
                "SELECT COUNT(*) FROM governor_executive_orders"
            ).fetchone()[0]
            result["governor_executive_orders"] = {
                "total_rows": eo_count,
                "sample": [
                    dict(r) for r in conn.execute("""
                        SELECT order_number, title, governor, signed_date
                        FROM governor_executive_orders
                        ORDER BY signed_date DESC
                        LIMIT 3
                    """).fetchall()
                ],
            }
        else:
            result["governor_executive_orders"] = {"total_rows": 0, "sample": []}

        conn.close()
    except Exception:
        result["error"] = traceback.format_exc()

    return result


@router.post("/api/governor/actions/money-analysis")
def governor_action_money_analysis(
    session: str = Query(default="2026"),
    finance_cycle: str = Query(default="2026"),
    governor: str = Query(default="Spanberger"),
    voice: str = Query(default="pro"),
):
    """Build the Governor Action Money Analyst payload and Claude narrative."""
    try:
        return build_governor_action_money_analysis(
            session=session,
            finance_cycle=finance_cycle,
            governor=governor,
            voice=voice,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── Governor overview API (one-shot data for /governor page) ──────────────────

@router.get("/api/governor/overview")
def governor_overview(
    session: str = Query(default="2026"),
    governor: str = Query(default="Spanberger"),
):
    """One-shot data payload for the /governor HTML page."""
    conn = _polls_conn()
    try:
        gov_like = f"%{governor.lower()}%"

        counts: dict[str, int] = {}
        for r in conn.execute(
            """
            SELECT action_key, COUNT(*) n FROM governor_actions
            WHERE session=? AND lower(governor) LIKE ?
            GROUP BY action_key
            """,
            (session, gov_like),
        ).fetchall():
            counts[r["action_key"]] = r["n"]

        bills = [
            dict(r)
            for r in conn.execute(
                """
                SELECT bill_number, title, action_key, action_label, action_date,
                       chapter_number, effective_date, sponsor_name, sponsor_party, source_url
                FROM governor_actions
                WHERE session=? AND lower(governor) LIKE ?
                ORDER BY action_date DESC, bill_number
                """,
                (session, gov_like),
            ).fetchall()
        ]

        eos = [
            dict(r)
            for r in conn.execute(
                """
                SELECT order_number, title, signed_date, summary,
                       policy_topics, source_url
                FROM governor_executive_orders
                WHERE lower(governor) LIKE ?
                ORDER BY signed_date DESC
                """,
                (gov_like,),
            ).fetchall()
        ]

        return {
            "governor": f"Governor {governor}",
            "session": session,
            "stats": {
                "signed": counts.get("signed", 0),
                "vetoed": counts.get("vetoed", 0),
                "amended": counts.get("amended", 0),
                "veto_sustained": counts.get("veto_sustained", 0),
                "total": sum(counts.values()),
            },
            "executive_orders": eos,
            "bills": bills,
        }
    finally:
        conn.close()


# ── Governor HTML page ────────────────────────────────────────────────────────

@router.get("/governor", response_class=HTMLResponse)
def governor_page():
    """Governor Spanberger 2026 bill actions and executive orders page."""
    import traceback as _tb
    conn = None
    try:
        conn = _polls_conn()
        bills = [
            dict(r)
            for r in conn.execute(
                """
                SELECT bill_number, title, action_key, action_label, action_date,
                       chapter_number, effective_date, sponsor_name, sponsor_party, source_url
                FROM governor_actions
                WHERE session='2026' AND lower(governor) LIKE '%spanberger%'
                ORDER BY action_date DESC, bill_number
                """
            ).fetchall()
        ]

        eos = [
            dict(r)
            for r in conn.execute(
                """
                SELECT order_number, title, signed_date, summary, policy_topics, source_url
                FROM governor_executive_orders
                WHERE lower(governor) LIKE '%spanberger%'
                ORDER BY signed_date DESC
                """
            ).fetchall()
        ]

        data = {
            "bills": bills,
            "executive_orders": eos,
            "stats": {
                "signed":   sum(1 for b in bills if b["action_key"] == "signed"),
                "vetoed":   sum(1 for b in bills if b["action_key"] == "vetoed"),
                "amended":  sum(1 for b in bills if b["action_key"] == "amended"),
                "total":    len(bills),
            },
        }
        return _inject("governor.html", "_GOV_DATA", data)
    except HTTPException:
        raise
    except Exception as exc:
        _trace = _tb.format_exc()
        print(f"[/governor ERROR] {exc}\n{_trace}", flush=True)
        raise HTTPException(
            status_code=500, detail=f"Governor page error: {exc}"
        ) from exc
    finally:
        if conn:
            conn.close()
