"""
Spike watchlist API — manage watches and surface triggered alerts.

GET  /api/watchlist                  list all watch entries
POST /api/watchlist                  create a new watch
DELETE /api/watchlist/{id}           deactivate a watch

GET  /api/watchlist/alerts           unseen alerts (default) or all
POST /api/watchlist/alerts/{id}/seen mark an alert as seen

GET  /api/watchlist/run              run the watchlist now (admin)
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["watchlist"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "polls.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_POLLS_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


_schema_ensured = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spike_watchlist (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            label            TEXT NOT NULL,
            donor_key        TEXT,
            donor_name_match TEXT,
            sector           TEXT,
            threshold_ratio  REAL    DEFAULT 2.0,
            parity           TEXT    DEFAULT 'both',
            min_amount       REAL    DEFAULT 100000,
            active           INTEGER DEFAULT 1,
            created_at       TEXT
        );
        CREATE TABLE IF NOT EXISTS spike_alerts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id     INTEGER,
            watch_label      TEXT,
            donor_key        TEXT,
            canonical_name   TEXT,
            election_cycle   TEXT,
            cycle_parity     TEXT,
            total_amount     REAL,
            ratio_vs_mean    REAL,
            pct_change_prior REAL,
            context_bills    TEXT,
            alerted_at       TEXT,
            seen             INTEGER DEFAULT 0,
            UNIQUE(watchlist_id, donor_key, election_cycle)
        );
    """)
    conn.commit()
    _schema_ensured = True


# ── Models ────────────────────────────────────────────────────────────────────

class WatchCreate(BaseModel):
    label:            str   = Field(..., description="Human-readable label, e.g. 'Dominion >2x'")
    donor_name_match: Optional[str] = Field(
        None, description="Pipe-separated LIKE patterns, e.g. '%dominion%|%appalachian%'"
    )
    sector:           Optional[str] = Field(None, description="e.g. 'Energy/Utilities'")
    threshold_ratio:  float = Field(2.0, ge=1.0, le=20.0,
                                    description="Trigger when ratio_vs_mean >= this")
    parity:           str   = Field("both", description="odd | even | both")
    min_amount:       float = Field(100_000, ge=0,
                                    description="Ignore donors who gave less than this")


# ── Watchlist CRUD ────────────────────────────────────────────────────────────

@router.get("/watchlist")
def list_watches():
    """All watchlist entries (active and inactive)."""
    conn = _conn()
    _ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM spike_watchlist ORDER BY active DESC, id"
    ).fetchall()
    conn.close()
    return JSONResponse([dict(r) for r in rows])


@router.post("/watchlist", status_code=201)
def create_watch(body: WatchCreate):
    """Create a new watchlist entry."""
    conn = _conn()
    _ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("""
        INSERT INTO spike_watchlist
            (label, donor_name_match, sector, threshold_ratio, parity, min_amount, active, created_at)
        VALUES (?,?,?,?,?,?,1,?)
    """, (
        body.label, body.donor_name_match, body.sector,
        body.threshold_ratio, body.parity, body.min_amount, now,
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM spike_watchlist WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return JSONResponse(dict(row), status_code=201)


@router.delete("/watchlist/{watch_id}", status_code=204)
def deactivate_watch(watch_id: int):
    """Deactivate (soft-delete) a watchlist entry."""
    conn = _conn()
    n = conn.execute(
        "UPDATE spike_watchlist SET active=0 WHERE id=?", (watch_id,)
    ).rowcount
    conn.commit()
    conn.close()
    if not n:
        raise HTTPException(404, f"Watch {watch_id} not found")
    return JSONResponse(None, status_code=204)


# ── Alerts ────────────────────────────────────────────────────────────────────

@router.get("/watchlist/alerts")
def get_alerts(
    unseen_only: bool = Query(True, description="Return only unseen alerts"),
    limit:       int  = Query(50, le=200),
):
    """
    Return triggered spike alerts.

    Each alert tells you:
    - which watch was triggered
    - which donor spiked
    - the cycle, amount, ratio vs. their own baseline
    - related legislation active that cycle
    """
    conn = _conn()
    _ensure_schema(conn)

    where = "WHERE seen = 0" if unseen_only else ""
    rows = conn.execute(f"""
        SELECT a.id, a.watch_label, a.canonical_name, a.election_cycle,
               a.cycle_parity, a.total_amount, a.ratio_vs_mean,
               a.pct_change_prior, a.context_bills, a.alerted_at, a.seen,
               w.threshold_ratio, w.donor_name_match
        FROM spike_alerts a
        LEFT JOIN spike_watchlist w ON w.id = a.watchlist_id
        {where}
        ORDER BY a.alerted_at DESC, a.ratio_vs_mean DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        # Parse context_bills JSON
        try:
            d["context_bills"] = json.loads(d["context_bills"] or "[]")
        except Exception:
            d["context_bills"] = []
        # Human-readable summary
        yr_type = "state-election year" if d["cycle_parity"] == "odd" else "federal-election year"
        pct_s   = f", up {d['pct_change_prior']:.0f}% from prior {yr_type}" \
                  if d["pct_change_prior"] else ""
        d["summary"] = (
            f"{d['canonical_name']} gave ${d['total_amount']:,.0f} in "
            f"{d['election_cycle']} ({yr_type}) — "
            f"{d['ratio_vs_mean']:.1f}x their typical {yr_type} giving{pct_s}."
        )
        results.append(d)

    return JSONResponse(results)


@router.post("/watchlist/alerts/{alert_id}/seen", status_code=200)
def mark_alert_seen(alert_id: int):
    """Mark an alert as seen/acknowledged."""
    conn = _conn()
    n = conn.execute(
        "UPDATE spike_alerts SET seen=1 WHERE id=?", (alert_id,)
    ).rowcount
    conn.commit()
    conn.close()
    if not n:
        raise HTTPException(404, f"Alert {alert_id} not found")
    return JSONResponse({"ok": True})


# ── Run now (admin trigger) ────────────────────────────────────────────────────

@router.post("/watchlist/run")
def run_watchlist_now(
    cycle: Optional[str] = Query(None, description="Cycle to check (default: latest)"),
):
    """
    Trigger a watchlist run immediately.
    Runs run_spike_watchlist.py as a subprocess.
    Returns new alert count.
    """
    script = str(_BASE_DIR / "run_spike_watchlist.py")
    cmd = [sys.executable, script]
    if cycle:
        cmd += ["--cycle", cycle]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise HTTPException(500, f"Watchlist run failed: {result.stderr[:400]}")

    # Count new unseen alerts
    conn = _conn()
    _ensure_schema(conn)
    unseen = conn.execute(
        "SELECT COUNT(*) FROM spike_alerts WHERE seen=0"
    ).fetchone()[0]
    conn.close()

    return JSONResponse({
        "ok":     True,
        "unseen": unseen,
        "output": result.stdout[-800:],
    })
