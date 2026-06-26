"""SQLite connection substrate — shared by all database_context builders.

Owns:
  - DB_PATHS dict  (mutable; tests patch it in-place via dc.DB_PATHS[k] = v)
  - Per-request connection cache (_CachedConn, _request_connection_cache)
  - _connect / _resolve_db_path / _candidate_db_paths helpers
  - Generic query helpers (_table_exists, _query_rows, _row_to_line, etc.)

NOTE: _POLLS_DB is intentionally NOT exported here — several builders call
sqlite3.connect(_POLLS_DB) directly, and tests patch dc._POLLS_DB on the
database_context module.  Keeping it there avoids a re-export/monkeypatch
mismatch for immutable string names.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

# Project root: voteiq/services/context/_db.py -> up 4 levels
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

from config.db import POLLS_DB as _POLLS_DB_FOR_PATHS  # noqa: E402

DB_PATHS: dict[str, Path | str] = {
    "polls": _POLLS_DB_FOR_PATHS,
    "openstates": BASE_DIR / "openstates_va.db",
    "legislative_intelligence": BASE_DIR / "legislative_intelligence.db",
    "virginia_legislature": BASE_DIR / "virginia_legislature.db",
}

DATA_DIR_ENV_VARS = ("DATA_DIR", "VOTEIQ_DATA_DIR", "RENDER_DISK_MOUNT_PATH")
COMMON_DATA_DIRS = (Path("/data"), Path("/var/data"))

# ── Per-request SQLite connection cache ───────────────────────────────────────

_local = threading.local()


class _CachedConn:
    """Wraps sqlite3.Connection; makes close() a no-op so the per-request cache stays live."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        object.__setattr__(self, "_conn", conn)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def close(self) -> None:
        pass  # closed by _request_connection_cache when the request completes


@contextmanager
def _request_connection_cache():
    """Re-entrant: inner activation is a no-op. Closes all connections on outermost exit."""
    if getattr(_local, "cache", None) is not None:
        yield
        return
    _local.cache: dict[str, _CachedConn] = {}
    try:
        yield
    finally:
        for wrapped in _local.cache.values():
            try:
                object.__getattribute__(wrapped, "_conn").close()
            except Exception:
                pass
        _local.cache = None


# ── Path resolution ───────────────────────────────────────────────────────────

def _candidate_db_paths(db_key: str) -> list[Path]:
    base_path = DB_PATHS[db_key]
    candidates: list[Path] = []
    for env_var in DATA_DIR_ENV_VARS:
        data_dir = os.getenv(env_var)
        if data_dir:
            candidates.append(Path(data_dir) / Path(str(base_path)).name)
    candidates.append(Path(str(base_path)))
    for data_dir in COMMON_DATA_DIRS:
        candidates.append(data_dir / Path(str(base_path)).name)

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _resolve_db_path(db_key: str) -> Path | None:
    for path in _candidate_db_paths(db_key):
        if path.exists():
            return path
    return None


def _db_unavailable_line(db_key: str) -> str:
    expected = ", ".join(str(path) for path in _candidate_db_paths(db_key))
    return (
        f"- {db_key}: database_unavailable; reason=sqlite_file_missing; "
        f"expected_paths={expected}"
    )


def _connect(db_key: str) -> sqlite3.Connection | None:
    cache = getattr(_local, "cache", None)
    if cache is not None and db_key in cache:
        return cache[db_key]
    path = _resolve_db_path(db_key)
    if path is None:
        return None
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    if cache is not None:
        wrapped = _CachedConn(conn)
        cache[db_key] = wrapped
        return wrapped
    return conn


# ── Public query helpers ──────────────────────────────────────────────────────

def query_database(db_key: str, sql: str, params: Iterable | None = None) -> list[sqlite3.Row]:
    """Query one configured SQLite database, respecting DATA_DIR on Render."""
    conn = _connect(db_key)
    if not conn:
        return []
    try:
        return conn.execute(sql, tuple(params or ())).fetchall()
    finally:
        conn.close()


def query_legislative(sql: str, params: Iterable | None = None) -> list[sqlite3.Row]:
    """Query legislative_intelligence.db."""
    return query_database("legislative_intelligence", sql, params)


def query_polls(sql: str, params: Iterable | None = None) -> list[sqlite3.Row]:
    """Query polls.db."""
    return query_database("polls", sql, params)


# ── Row / table helpers ───────────────────────────────────────────────────────

def _row_to_line(row: sqlite3.Row, max_value: int = 360) -> str:
    parts = []
    for key in row.keys():
        value = row[key]
        if value is None or value == "":
            continue
        text = str(value).replace("\n", " ").strip()
        if len(text) > max_value:
            text = text[:max_value].rstrip() + "..."
        parts.append(f"{key}={text}")
    return "; ".join(parts)


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _table_column_info(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    except Exception:
        return []


def _is_internal_table(name: str) -> bool:
    return (
        name.startswith("sqlite_")
        or name.endswith("_data")
        or name.endswith("_idx")
        or name.endswith("_docsize")
        or name.endswith("_config")
        or name.endswith("_content")
    )


def _query_rows(
    conn: sqlite3.Connection,
    sql: str,
    params: Iterable,
    label: str,
    blocks: list[str],
    limit: int = 8,
) -> None:
    try:
        rows = conn.execute(sql, tuple(params)).fetchmany(limit)
    except Exception:
        return
    if not rows:
        return
    lines = [f"[Database Context - {label}]"]
    lines.extend(f"- {_row_to_line(row)}" for row in rows)
    blocks.append("\n".join(lines))
