"""
db_routing.py
Table-to-database routing constants and query dispatcher.

route_query(sql, params) inspects table names in the SQL, decides which
database to hit, and dispatches to the correct query function.
"""

from __future__ import annotations

import re
import sqlite3
import os
from pathlib import Path

# ── Table membership sets ─────────────────────────────────────────────────────

STATE_TABLES = {
    "va_sbe_contributions",
    "va_votes",
    "va_bills",
    "legislators",
    "va_member_committees",
    "donor_sector_totals",
    "governor_actions",
}

FEDERAL_TABLES = {
    "fec_individual_contributions",
    "congress_votes",
    "congress_bills",
    "congress_members",
    "congress_committees",
    "congress_floor_statements",
}

# ── DB paths ──────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = os.getenv("DATA_DIR", str(_BASE_DIR))

_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")
_LEG_DB   = str(_BASE_DIR / "legislative_intelligence.db")

# ── Table name extractor ──────────────────────────────────────────────────────

_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


def extract_table_names(sql: str) -> set[str]:
    """Return the set of table names referenced after FROM / JOIN in a SQL string."""
    return {m.group(1).lower() for m in _TABLE_RE.finditer(sql)}


# ── Per-DB query helpers ──────────────────────────────────────────────────────

def query_polls(sql: str, params: tuple = ()) -> list:
    """Execute a query against polls.db (federal data)."""
    conn = sqlite3.connect(_POLLS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        return [tuple(r) for r in conn.execute(sql, params or ()).fetchall()]
    finally:
        conn.close()


def query_legislative(sql: str, params: tuple = ()) -> list:
    """Execute a query against legislative_intelligence.db (state data)."""
    conn = sqlite3.connect(_LEG_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        return [tuple(r) for r in conn.execute(sql, params or ()).fetchall()]
    finally:
        conn.close()


# ── Router ────────────────────────────────────────────────────────────────────

def route_query(sql: str, params: tuple = ()) -> list:
    """
    Inspect table names in sql, pick the right database, and execute.

    Raises ValueError if the query mixes state and federal tables,
    or if no known tables are found.
    """
    tables = extract_table_names(sql)

    hits_state   = tables & STATE_TABLES
    hits_federal = tables & FEDERAL_TABLES

    if hits_state and hits_federal:
        raise ValueError(
            f"Query references tables from multiple databases: "
            f"state={hits_state}, federal={hits_federal}"
        )

    if hits_state:
        return query_legislative(sql, params)

    if hits_federal:
        return query_polls(sql, params)

    raise ValueError(
        f"Could not route query — no known tables found in: {tables or '{no tables detected}'}"
    )
