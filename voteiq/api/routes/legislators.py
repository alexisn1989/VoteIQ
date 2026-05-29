"""VA Legislators visualization endpoints — sortable/filterable HTML + JSON API."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legislators"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")


def _polls_conn():
    path = _POLLS_DB
    if not os.path.isfile(path):
        raise HTTPException(status_code=503, detail="Legislator database not available")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# Common first-name nicknames for fuzzy campaign-finance matching.
# The finance table sometimes lists a legislator under a nickname
# (e.g. "Joe McNamara" vs the formal "Joseph P. McNamara").
_NICKNAMES = {
    "david": "dave", "joseph": "joe", "joshua": "josh", "william": "bill",
    "robert": "bob", "michael": "mike", "christopher": "chris", "daniel": "dan",
    "thomas": "tom", "kathleen": "kathy", "jennifer": "jen", "matthew": "matt",
    "richard": "rick", "edward": "ed", "anthony": "tony", "nicholas": "nick",
    "samuel": "sam", "benjamin": "ben", "andrew": "andy", "stephen": "steve",
    "steven": "steve", "patricia": "pat", "deborah": "debbie", "elizabeth": "liz",
}


def _name_key(full_name: str) -> str:
    """Normalize a name to a 'first last' key for fuzzy finance matching.

    Drops middle names/initials, generational suffixes (Jr/Sr/II/III), and
    quoted nicknames, then applies a nickname map to the first name. Requires
    BOTH first and last name to match — this prevents false positives between
    different legislators who share a surname (e.g. Gretchen Bulova must NOT
    match David Bulova; Kirk McPike must NOT match Jeremy McPike).
    """
    n = re.sub(r'"[^"]*"', "", full_name)                       # drop "Buddy" etc.
    n = re.sub(r"\b(Jr|Sr|II|III|IV)\b", "", n, flags=re.I)     # drop suffixes
    n = re.sub(r"[.,]", " ", n)                                  # punctuation -> space
    tokens = [t for t in n.split() if len(t) > 1]               # drop single-letter initials
    if not tokens:
        return ""
    first = _NICKNAMES.get(tokens[0].lower(), tokens[0].lower())
    last = tokens[-1].lower()
    return f"{first} {last}"


def _inject(template: str, key: str, data) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / template).open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window.{key}={safe};</script></head>", 1)


def _legislators_list(conn) -> list[dict]:
    rows = conn.execute("""
        SELECT v.voter_name, v.chamber, v.party,
               v.yes_count, v.no_count, v.not_voting, v.abstain, v.total_votes,
               ROUND(CAST(v.yes_count AS REAL) / MAX(v.total_votes, 1) * 100, 1) AS yes_rate,
               COUNT(DISTINCT sb.bill_id) AS bills_introduced,
               COUNT(DISTINCT cb.bill_id) AS bills_cosponsored
        FROM va_legislator_vote_summary v
        LEFT JOIN va_legislator_sponsored_bills sb
               ON lower(sb.legislator_name) = CASE
                      WHEN instr(v.voter_name, '. ') > 0
                      THEN lower(substr(v.voter_name, 1, instr(v.voter_name, ' ')-1)
                               || substr(v.voter_name, instr(v.voter_name, '. ')+1))
                      ELSE lower(v.voter_name)
                  END
              AND sb.session = v.session
        LEFT JOIN va_legislator_cosponsor_bills cb
               ON lower(cb.legislator_name) = CASE
                      WHEN instr(v.voter_name, '. ') > 0
                      THEN lower(substr(v.voter_name, 1, instr(v.voter_name, ' ')-1)
                               || substr(v.voter_name, instr(v.voter_name, '. ')+1))
                      ELSE lower(v.voter_name)
                  END
              AND cb.session = v.session
        WHERE v.session = '2026'
        GROUP BY v.voter_name
        ORDER BY v.chamber, v.party, v.voter_name
    """).fetchall()
    return [dict(r) for r in rows]


def _fetch_profile(conn, name: str) -> dict:
    """Return full legislator profile or raise 404."""
    row = conn.execute("""
        SELECT voter_name, chamber, party,
               yes_count, no_count, not_voting, abstain, total_votes,
               ROUND(CAST(yes_count AS REAL) / MAX(total_votes, 1) * 100, 1) AS yes_rate
        FROM va_legislator_vote_summary
        WHERE session = '2026' AND lower(voter_name) = lower(?)
        LIMIT 1
    """, (name,)).fetchone()

    if not row:
        last = name.strip().split()[-1] if name.strip().split() else name
        row = conn.execute("""
            SELECT voter_name, chamber, party,
                   yes_count, no_count, not_voting, abstain, total_votes,
                   ROUND(CAST(yes_count AS REAL) / MAX(total_votes, 1) * 100, 1) AS yes_rate
            FROM va_legislator_vote_summary
            WHERE session = '2026' AND lower(voter_name) LIKE lower(?)
            LIMIT 1
        """, (f"%{last}%",)).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Legislator '{name}' not found")

    resolved = row["voter_name"]
    profile = dict(row)

    # vote_summary uses formal names (e.g. "Aaron R. Rouse") but bill tables
    # use short names ("Aaron Rouse") — strip middle initial for bill lookups
    bill_name = re.sub(r'\s+[A-Z]\.\s+', ' ', resolved).strip()

    profile["recent_votes"] = [dict(r) for r in conn.execute("""
        SELECT bill_id, vote_date, option, chamber, motion, title
        FROM va_legislator_recent_votes
        WHERE session = '2026' AND voter_name = ?
        ORDER BY vote_date DESC LIMIT 30
    """, (resolved,)).fetchall()]

    profile["sponsored_bills"] = [dict(r) for r in conn.execute("""
        SELECT bill_id, bill_number, title, status_label, subject, status_date
        FROM va_legislator_sponsored_bills
        WHERE session = '2026' AND lower(legislator_name) = lower(?)
        ORDER BY status_date DESC
    """, (bill_name,)).fetchall()]

    profile["cosponsored_bills"] = [dict(r) for r in conn.execute("""
        SELECT bill_id, bill_number, title, status_label, subject, sponsor_order
        FROM va_legislator_cosponsor_bills
        WHERE session = '2026' AND lower(legislator_name) = lower(?)
        ORDER BY sponsor_order LIMIT 50
    """, (bill_name,)).fetchall()]

    # ── Bill topic breakdown — combined introduced + cosponsored ─────────────
    # (text before first ';' in title; deduped by bill_id within each table)
    profile["bill_topics"] = [dict(r) for r in conn.execute("""
        SELECT topic, SUM(cnt) AS count
        FROM (
            SELECT TRIM(SUBSTR(title, 1, INSTR(title || ';', ';') - 1)) AS topic,
                   COUNT(DISTINCT bill_id) AS cnt
            FROM va_legislator_sponsored_bills
            WHERE session = '2026' AND lower(legislator_name) = lower(?)
              AND title != '' AND title IS NOT NULL
            GROUP BY topic
            UNION ALL
            SELECT TRIM(SUBSTR(title, 1, INSTR(title || ';', ';') - 1)) AS topic,
                   COUNT(DISTINCT bill_id) AS cnt
            FROM va_legislator_cosponsor_bills
            WHERE session = '2026' AND lower(legislator_name) = lower(?)
              AND title != '' AND title IS NOT NULL
            GROUP BY topic
        )
        WHERE topic != ''
        GROUP BY topic
        ORDER BY count DESC
        LIMIT 12
    """, (bill_name, bill_name)).fetchall()]

    # ── Campaign finance sectors ─────────────────────────────────────────────
    fin_row = conn.execute("""
        SELECT total_raised, top_sector, top_sector_pct, latest_cycle,
               by_sector_json, top_donors_json
        FROM campaign_finance_summary
        WHERE lower(name) = lower(?)
        LIMIT 1
    """, (bill_name,)).fetchone()

    # Fuzzy fallback: the finance table may list this legislator under a
    # nickname or dropped middle name (e.g. "Joe McNamara" for "Joseph P.
    # McNamara"). Match on a first+last key so we never cross-match two
    # different legislators who happen to share a surname.
    if not fin_row:
        target_key = _name_key(resolved)
        if target_key:
            for cand in conn.execute("""
                SELECT total_raised, top_sector, top_sector_pct, latest_cycle,
                       by_sector_json, top_donors_json, name
                FROM campaign_finance_summary
            """).fetchall():
                if _name_key(cand["name"]) == target_key:
                    fin_row = cand
                    break

    if fin_row:
        profile["finance_meta"] = {
            "total_raised": fin_row["total_raised"],
            "top_sector":   fin_row["top_sector"],
            "top_sector_pct": fin_row["top_sector_pct"],
            "latest_cycle": fin_row["latest_cycle"],
        }
        profile["finance_sectors"] = json.loads(fin_row["by_sector_json"] or "[]")
        profile["top_donors"]      = json.loads(fin_row["top_donors_json"] or "[]")[:10]
    else:
        profile["finance_meta"]    = None
        profile["finance_sectors"] = []
        profile["top_donors"]      = []

    return profile


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/legislators")
def api_legislators(
    chamber: str = Query(default=""),
    party: str = Query(default=""),
):
    """Return all VA legislators with aggregated vote/bill stats."""
    conn = _polls_conn()
    try:
        data = _legislators_list(conn)
        if chamber:
            data = [r for r in data if r["chamber"].lower() == chamber.lower()]
        if party:
            data = [r for r in data if r["party"].lower() == party.lower()]
        return {"legislators": data, "total": len(data)}
    finally:
        conn.close()


@router.get("/api/legislators/{name}")
def api_legislator_profile(name: str):
    """Return full profile for one legislator: votes, bills, co-sponsorships."""
    name = unquote(name).strip()
    conn = _polls_conn()
    try:
        return _fetch_profile(conn, name)
    finally:
        conn.close()


# ── HTML pages ────────────────────────────────────────────────────────────────

@router.get("/legislators", response_class=HTMLResponse)
def legislators_page():
    """Sortable/filterable table of all 145 VA legislators."""
    conn = _polls_conn()
    try:
        data = _legislators_list(conn)
        return _inject("legislators.html", "_LEG_DATA", data)
    finally:
        conn.close()


@router.get("/legislators/{name}", response_class=HTMLResponse)
def legislator_profile_page(name: str):
    """Individual legislator profile: vote record, bills, co-sponsorships."""
    name = unquote(name).strip()
    conn = _polls_conn()
    try:
        profile = _fetch_profile(conn, name)
        return _inject("legislator_profile.html", "_PROFILE_DATA", profile)
    finally:
        conn.close()
