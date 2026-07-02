"""SQLite-backed chat reply cache (query_cache table in openstates_va.db).

Extracted verbatim from root main.py (decomposition phase 2, 2026-07-01).
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path

from config.db import POLLS_DB as _POLLS_DB_PATH

# Same derivation main.py uses for its _OPENSTATES_DB global.
_OPENSTATES_DB = os.path.join(str(Path(str(_POLLS_DB_PATH)).parent), "openstates_va.db")

_CACHE_TTL_SECONDS = 86400  # 24 hours for ad-hoc chat replies


def _init_query_cache():
    db = _OPENSTATES_DB
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS query_cache "
        "(cache_key TEXT PRIMARY KEY, reply TEXT, created_at INTEGER)"
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(query_cache)").fetchall()}
    if "cache_type" not in cols:
        conn.execute("ALTER TABLE query_cache ADD COLUMN cache_type TEXT DEFAULT 'ad_hoc'")
    conn.commit()
    conn.close()


def _cache_key(query: str, district_note: str) -> str:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    raw = f"{normalized}||{district_note}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached_reply(key: str, fallback_key: str | None = None) -> str | None:
    db = _OPENSTATES_DB
    if not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(db)
        for lookup_key in ([key] + ([fallback_key] if fallback_key and fallback_key != key else [])):
            row = conn.execute(
                "SELECT reply, created_at, COALESCE(cache_type, 'ad_hoc') FROM query_cache WHERE cache_key = ?",
                (lookup_key,),
            ).fetchone()
            if row and (row[2] == "prewarm" or (time.time() - row[1]) < _CACHE_TTL_SECONDS):
                conn.close()
                return row[0]
        conn.close()
    except Exception:
        pass
    return None


def _set_cached_reply(key: str, reply: str, cache_type: str = "ad_hoc"):
    db = _OPENSTATES_DB
    if not os.path.exists(db):
        return
    try:
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT OR REPLACE INTO query_cache (cache_key, reply, created_at, cache_type) VALUES (?, ?, ?, ?)",
            (key, reply, int(time.time()), cache_type),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


