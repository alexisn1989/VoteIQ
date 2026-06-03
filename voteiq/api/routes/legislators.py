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

# ── Donor-legislation correlation cache ───────────────────────────────────────
# Pre-loaded once; silently absent if cache not yet built.
_DONOR_ANALYSIS_LOOKUP: dict[str, dict] = {}
try:
    _cache = json.loads(
        (_BASE_DIR / "data" / "donor_map_cache.json").read_text(encoding="utf-8")
    )
    for _rec in _cache.get("state", {}).get("donor_legislation", []):
        _nm = (_rec.get("name") or "").strip().lower()
        if _nm:
            _DONOR_ANALYSIS_LOOKUP[_nm] = _rec
except Exception:
    pass
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")

# Name prefixes/suffixes stripped when normalizing a full legal name to a
# "first last" lookup key. Mirrors the logic used to build cf_candidate_keys.
_NAME_PREFIXES = {"mr", "mrs", "ms", "miss", "dr", "rev", "hon", "sen", "rep",
                  "the", "honorable"}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "esq", "esquire"}


def _norm_cand_key(name: str) -> str:
    """Normalize a full name to a 'first last' key (lowercased, titles dropped).

    'Mr. Aaron Roosevelt Rouse' -> 'aaron rouse'; 'Aaron R. Rouse' -> 'aaron rouse'.
    Used to match a legislator against the donor table via the indexed
    cf_candidate_keys lookup instead of a slow leading-wildcard LIKE scan.
    """
    if not name:
        return ""
    toks = [t.strip(".,").lower() for t in name.split()]
    toks = [t for t in toks if t]
    while toks and toks[0] in _NAME_PREFIXES:
        toks.pop(0)
    while toks and toks[-1] in _NAME_SUFFIXES:
        toks.pop()
    if not toks:
        return ""
    if len(toks) == 1:
        return toks[0]
    return toks[0] + " " + toks[-1]


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


def _bill_match_key(voter_name: str) -> str:
    """Normalize a vote-summary name to the short form used in the bill tables.

    Replicates the SQL CASE expression formerly used in the join:
    "Aaron R. Rouse" -> "aaron rouse" (drops the middle initial). Names without
    a "<initial>. " pattern are simply lowercased.
    """
    if ". " in voter_name:
        first_space = voter_name.find(" ")
        dot = voter_name.find(". ")
        return (voter_name[:first_space] + voter_name[dot + 1:]).lower()
    return voter_name.lower()


def _legislators_list(conn) -> list[dict]:
    # NOTE: This was previously a single query with two LEFT JOINs whose ON
    # clauses matched on a computed expression (lower(substr(...)||substr(...)))
    # on both sides. SQLite could not use any index for that, producing a full
    # cross-product (~145 x 7.3k x 24k rows) that took ~160s. We now run three
    # fast indexed scans and merge the bill counts in Python — identical output,
    # sub-second runtime.
    rows = conn.execute("""
        SELECT voter_name, chamber, party,
               yes_count, no_count, not_voting, abstain, total_votes,
               ROUND(CAST(yes_count AS REAL) / MAX(total_votes, 1) * 100, 1) AS yes_rate
        FROM va_legislator_vote_summary
        WHERE session = '2026'
        ORDER BY chamber, party, voter_name
    """).fetchall()

    sponsored = {
        r[0]: r[1] for r in conn.execute("""
            SELECT lower(legislator_name), COUNT(DISTINCT bill_id)
            FROM va_legislator_sponsored_bills
            WHERE session = '2026'
            GROUP BY lower(legislator_name)
        """).fetchall()
    }
    cosponsored = {
        r[0]: r[1] for r in conn.execute("""
            SELECT lower(legislator_name), COUNT(DISTINCT bill_id)
            FROM va_legislator_cosponsor_bills
            WHERE session = '2026'
            GROUP BY lower(legislator_name)
        """).fetchall()
    }

    out = []
    for r in rows:
        d = dict(r)
        key = _bill_match_key(d["voter_name"])
        d["bills_introduced"] = sponsored.get(key, 0)
        d["bills_cosponsored"] = cosponsored.get(key, 0)
        out.append(d)
    return out


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
               by_sector_json, top_donors_json, overall_va_pct, alignment_json
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
                       by_sector_json, top_donors_json, overall_va_pct, alignment_json, name
                FROM campaign_finance_summary
            """).fetchall():
                if _name_key(cand["name"]) == target_key:
                    fin_row = cand
                    break

    if fin_row:
        profile["finance_meta"] = {
            "total_raised":   fin_row["total_raised"],
            "top_sector":     fin_row["top_sector"],
            "top_sector_pct": fin_row["top_sector_pct"],
            "latest_cycle":   fin_row["latest_cycle"],
            "overall_va_pct": fin_row["overall_va_pct"],
        }
        profile["finance_sectors"] = json.loads(fin_row["by_sector_json"] or "[]")
        profile["top_donors"]      = json.loads(fin_row["top_donors_json"] or "[]")[:10]
        profile["donor_alignment"] = json.loads(fin_row["alignment_json"] or "[]")
    else:
        profile["finance_meta"]    = None
        profile["finance_sectors"] = []
        profile["top_donors"]      = []
        profile["donor_alignment"] = []

    # ── Donor tiers: corporate/PAC vs individual size buckets ───────────────
    def _donor_tiers(cand_names) -> list:
        """Aggregate donor tiers across one or more exact candidate_name values.

        Each name hits the lower(candidate_name) functional index, so an IN-list
        of variants stays index-fast (no full-table scan)."""
        if isinstance(cand_names, str):
            cand_names = [cand_names]
        cand_names = [n for n in cand_names if n]
        if not cand_names:
            return []
        placeholders = ",".join("lower(?)" for _ in cand_names)
        rows = conn.execute(f"""
            SELECT
                CASE
                    WHEN is_individual = 0 THEN 'Corporate / PAC'
                    WHEN amount < 200      THEN 'Small (< $200)'
                    WHEN amount < 1000     THEN 'Mid ($200–$999)'
                    WHEN amount < 2500     THEN 'Large ($1k–$2.5k)'
                    ELSE                        'Major ($2,500+)'
                END AS tier,
                COUNT(*)    AS cnt,
                SUM(amount) AS total
            FROM va_cf_schedule_a
            WHERE lower(candidate_name) IN ({placeholders}) AND amount > 0
            GROUP BY tier ORDER BY total DESC
        """, cand_names).fetchall()
        return [{"tier": r["tier"], "count": r["cnt"], "total": r["total"]} for r in rows]

    tiers = _donor_tiers(bill_name) or _donor_tiers(resolved)
    if not tiers:
        # Indexed fuzzy fallback: normalize the legislator name to a
        # "first last" key and pull every matching donor-name variant from the
        # pre-built cf_candidate_keys table, then aggregate them in one shot.
        # Replaces a 15s leading-wildcard LIKE scan over 2.2M rows.
        key = _norm_cand_key(bill_name) or _norm_cand_key(resolved)
        if key:
            try:
                variants = [
                    m["candidate_name"] for m in conn.execute(
                        "SELECT candidate_name FROM cf_candidate_keys WHERE cand_key = ?",
                        (key,)
                    ).fetchall()
                ]
            except sqlite3.OperationalError:
                variants = []
            if variants:
                tiers = _donor_tiers(variants)
    profile["donor_tiers"] = tiers

    # ── Donor-industry analysis (from pre-computed correlation cache) ──────────
    # Try exact name match first, then last-name fuzzy fallback.
    resolved_lower = resolved.strip().lower()
    donor_rec = _DONOR_ANALYSIS_LOOKUP.get(resolved_lower)
    if not donor_rec and resolved_lower.split():
        last_lower = resolved_lower.split()[-1].rstrip(".,")
        donor_rec = next(
            (v for k, v in _DONOR_ANALYSIS_LOOKUP.items() if k.split()[-1] == last_lower),
            None,
        )
    profile["donor_analysis"] = donor_rec  # None if no match

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
