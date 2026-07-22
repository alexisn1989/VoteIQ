"""
Per-member voting record + (where available) voting-bloc/finance summary,
built on top of the name resolution in council_roster.py. Works uniformly
across all 7 Hampton Roads cities' {prefix}_council_member_votes tables;
the bonus sections (voting blocs, campaign finance) only exist for
Virginia Beach and Norfolk and return empty/None elsewhere.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from voteiq.services.council_roster import (
    DB_PATH, TABLE_PREFIX, resolve_finance_key, resolve_vote_key,
)

# meeting_date format varies per city ("May 28, 2024", "2020-10-07",
# "12/13/2023", "Apr 28 2026", "MARCH 25, 2025" all seen for real) -- a
# plain SQL string ORDER BY would sort these wrong, and by id instead of
# date once surfaced a real bug: VB's own IQM2 system has genuine leftover
# test rows ("TEST AGENDA ITEM 1/13/2022") with a high id but an old real
# date, which `ORDER BY id DESC` put at the top of "recent votes."
_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
)


def _parse_date(raw: str) -> datetime:
    s = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.title() if fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y") else s, fmt)
        except ValueError:
            continue
    return datetime.min

_YES_VALUES = {"yes", "yes/aye", "aye"}
_NO_VALUES = {"no", "no/nay", "nay"}


def _norm_vote(raw: str) -> str:
    v = (raw or "").strip().lower()
    if v in _YES_VALUES:
        return "yes"
    if v in _NO_VALUES:
        return "no"
    if v in ("abstain",):
        return "abstain"
    if v in ("absent", "away"):
        return "absent"
    return v or "unknown"


def get_vote_summary(city_slug: str, roster_name: str) -> dict:
    """{total, yes, no, abstain, absent, vote_key} for this member. All
    zero (vote_key=None) if this member has no recorded votes yet -- a
    real, honest state (e.g. a newly-elected member), not an error."""
    prefix = TABLE_PREFIX.get(city_slug)
    vote_key = resolve_vote_key(city_slug, roster_name) if prefix else None
    summary = {"total": 0, "yes": 0, "no": 0, "abstain": 0, "absent": 0, "vote_key": vote_key}
    if not vote_key:
        return summary

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        f"SELECT vote FROM {prefix}_council_member_votes WHERE member_name = ?", (vote_key,)
    ).fetchall()
    conn.close()
    for (raw,) in rows:
        summary["total"] += 1
        norm = _norm_vote(raw)
        if norm in summary:
            summary[norm] += 1
    return summary


def get_recent_votes(city_slug: str, roster_name: str, limit: int = 25) -> list[dict]:
    """[{meeting_date, title, vote, result}], most recent first."""
    prefix = TABLE_PREFIX.get(city_slug)
    vote_key = resolve_vote_key(city_slug, roster_name) if prefix else None
    if not vote_key:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT meeting_date, title, vote, result FROM {prefix}_council_member_votes
        WHERE member_name = ?
    """, (vote_key,)).fetchall()
    conn.close()
    rows_sorted = sorted(rows, key=lambda r: _parse_date(r["meeting_date"]), reverse=True)
    return [
        {"meeting_date": r["meeting_date"], "title": r["title"],
         "vote": _norm_vote(r["vote"]), "result": r["result"] or ""}
        for r in rows_sorted[:limit]
    ]


def get_voting_blocs(city_slug: str, roster_name: str, limit: int = 5) -> list[dict]:
    """Top agreement partners -- Virginia Beach and Norfolk only, where
    {prefix}_voting_blocs is precomputed. [] for every other city."""
    prefix = TABLE_PREFIX.get(city_slug)
    if prefix not in ("vb", "norfolk"):
        return []
    vote_key = resolve_vote_key(city_slug, roster_name)
    if not vote_key:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT member_a, member_b, agreement_pct, shared_votes FROM {prefix}_voting_blocs
        WHERE member_a = ? OR member_b = ?
        ORDER BY agreement_pct DESC
    """, (vote_key, vote_key)).fetchall()
    conn.close()
    # Every pair is stored in both directions (A,B) and (B,A) -- dedupe by
    # the *other* member so each partner appears once, not twice.
    seen: set[str] = set()
    out = []
    for r in rows:
        other = r["member_b"] if r["member_a"] == vote_key else r["member_a"]
        if other in seen:
            continue
        seen.add(other)
        out.append({"member": other, "agreement_pct": r["agreement_pct"], "shared_votes": r["shared_votes"]})
        if len(out) >= limit:
            break
    return out


def get_finance_summary(city_slug: str, roster_name: str) -> dict | None:
    """Total raised + top donor sectors -- Virginia Beach and Norfolk
    only. None for every other city (no per-local-candidate finance
    aggregation exists yet elsewhere)."""
    prefix = TABLE_PREFIX.get(city_slug)
    if prefix not in ("vb", "norfolk"):
        return None
    finance_key = resolve_finance_key(city_slug, roster_name)
    if not finance_key:
        return None

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"""
        SELECT total_raised, top_sector, top_sector_amt, top_sector_pct, sector_json
        FROM {prefix}_finance_summary WHERE member_name = ?
    """, (finance_key,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        sectors = json.loads(row["sector_json"] or "[]")
    except Exception:
        sectors = []
    return {
        "total_raised": row["total_raised"],
        "top_sector": row["top_sector"],
        "top_sector_amt": row["top_sector_amt"],
        "top_sector_pct": row["top_sector_pct"],
        "sectors": sectors[:5],
    }
