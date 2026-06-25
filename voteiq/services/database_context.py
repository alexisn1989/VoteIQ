from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent.parent.parent

from config.db import POLLS_DB as _POLLS_DB  # noqa: E402

DB_PATHS = {
    "polls": _POLLS_DB,
    "openstates": BASE_DIR / "openstates_va.db",
    "legislative_intelligence": BASE_DIR / "legislative_intelligence.db",
    "virginia_legislature": BASE_DIR / "virginia_legislature.db",
}

# ── Per-request SQLite connection cache ───────────────────────────────────────
# build_database_context fires ~35 builders per chat message, many hitting the
# same DB. Without caching, each _connect() opens a new file handle + PRAGMA
# round-trip. The cache keeps one connection per db_key for the duration of a
# single build_database_context call, then closes all on exit.

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


# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR_ENV_VARS = ("DATA_DIR", "VOTEIQ_DATA_DIR", "RENDER_DISK_MOUNT_PATH")
COMMON_DATA_DIRS = (Path("/data"), Path("/var/data"))

BILL_RE = re.compile(r"\b(HB|SB|HJ|SJ|HR|SR|HJR|SJR)\s*-?\s*(\d{1,5})\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")
_HIST_YEAR_RE = re.compile(r"\b(19\d{2})\b")

KNOWN_FEDERAL_PEOPLE = {
    # ── Dual-role members: federal record + current state office ─────────────────
    # Show BOTH their congressional record and current state role on profile queries.
    "spanberger": {
        "name": "Abigail Spanberger",
        "bioguide_id": "S001209",
        "fec_candidate_ids": ["H8VA07094"],
        "office_note": "Former U.S. Representative for Virginia's 7th District; current Virginia Governor (2026–). Show both her congressional voting record AND her governor bill-action record on profile queries.",
        "is_current_federal": True,
    },
    # ── Active VA federal delegation (is_current_federal=True) ─────────────────
    # Inject federal vote/bill context for any profile or vote query about them.
    "kiggans": {
        "name": "Jennifer A. Kiggans",
        "bioguide_id": "K000399",
        "fec_candidate_ids": ["H2VA02064"],
        "office_note": "U.S. Representative, Virginia 2nd District (119th Congress).",
        "is_current_federal": True,
    },
    "wittman": {
        "name": "Rob Wittman",
        "bioguide_id": "W000804",
        "fec_candidate_ids": ["H8VA01147"],
        "office_note": "U.S. Representative, Virginia 1st District (119th Congress).",
        "is_current_federal": True,
    },
    "bobby scott": {
        "name": "Bobby Scott",
        "bioguide_id": "S000185",
        "fec_candidate_ids": ["H6VA03088"],
        "office_note": "U.S. Representative, Virginia 3rd District (119th Congress).",
        "is_current_federal": True,
    },
    "mcclellan": {
        "name": "Jennifer McClellan",
        "bioguide_id": "M001227",
        "fec_candidate_ids": ["H2VA04175"],
        "office_note": "U.S. Representative, Virginia 4th District (119th Congress).",
        "is_current_federal": True,
    },
    "mcguire": {
        "name": "John McGuire",
        "bioguide_id": "M001239",
        "fec_candidate_ids": ["H0VA05220"],
        "office_note": "U.S. Representative, Virginia 5th District (119th Congress).",
        "is_current_federal": True,
    },
    "ben cline": {
        "name": "Ben Cline",
        "bioguide_id": "C001118",
        "fec_candidate_ids": ["H8VA06104"],
        "office_note": "U.S. Representative, Virginia 6th District (119th Congress).",
        "is_current_federal": True,
    },
    "vindman": {
        "name": "Eugene Vindman",
        "bioguide_id": "V000138",
        "fec_candidate_ids": ["H4VA07234"],
        "office_note": "U.S. Representative, Virginia 7th District (119th Congress).",
        "is_current_federal": True,
    },
    "don beyer": {
        "name": "Don Beyer",
        "bioguide_id": "B001292",
        "fec_candidate_ids": ["H4VA08224"],
        "office_note": "U.S. Representative, Virginia 8th District (119th Congress).",
        "is_current_federal": True,
    },
    "morgan griffith": {
        "name": "Morgan Griffith",
        "bioguide_id": "G000568",
        "fec_candidate_ids": ["H0VA09055"],
        "office_note": "U.S. Representative, Virginia 9th District (119th Congress).",
        "is_current_federal": True,
    },
    "subramanyam": {
        "name": "Suhas Subramanyam",
        "bioguide_id": "S001230",
        "fec_candidate_ids": ["H4VA10279"],
        "office_note": "U.S. Representative, Virginia 10th District (119th Congress).",
        "is_current_federal": True,
    },
    "walkinshaw": {
        "name": "James Walkinshaw",
        "bioguide_id": "W000831",
        "fec_candidate_ids": ["H6VA11066"],
        "office_note": "U.S. Representative, Virginia 11th District (119th Congress).",
        "is_current_federal": True,
    },
    "mark warner": {
        "name": "Mark Warner",
        "bioguide_id": "W000805",
        "fec_candidate_ids": ["S6VA00093"],
        "office_note": "U.S. Senator, Virginia (119th Congress).",
        "is_current_federal": True,
    },
    "tim kaine": {
        "name": "Tim Kaine",
        "bioguide_id": "K000384",
        "fec_candidate_ids": ["S2VA00142"],
        "office_note": "U.S. Senator, Virginia (119th Congress).",
        "is_current_federal": True,
    },
}


def _norm_bill(value: str) -> str:
    match = BILL_RE.search(value or "")
    return f"{match.group(1).upper()}{match.group(2)}" if match else ""


def _bill_numbers(query: str) -> list[str]:
    seen: set[str] = set()
    bills: list[str] = []
    for match in BILL_RE.finditer(query or ""):
        bill = f"{match.group(1).upper()}{match.group(2)}"
        if bill not in seen:
            seen.add(bill)
            bills.append(bill)
    return bills


def _session_year(query: str) -> str:
    years = YEAR_RE.findall(query or "")
    return years[-1] if years else "2026"


def _keywords(query: str) -> list[str]:
    stop = {
        "what", "when", "where", "which", "about", "with", "from", "that",
        "this", "have", "does", "did", "were", "they", "them", "your",
        "show", "tell", "give", "list", "vote", "votes", "voted", "bill",
        "bills", "governor",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", query or "")
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        low = word.lower().strip("'")
        if len(low) < 4 or low in stop or low in seen:
            continue
        seen.add(low)
        result.append(low)
        if len(result) >= 8:
            break
    return result


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


def _candidate_db_paths(db_key: str) -> list[Path]:
    base_path = DB_PATHS[db_key]
    candidates: list[Path] = []
    for env_var in DATA_DIR_ENV_VARS:
        data_dir = os.getenv(env_var)
        if data_dir:
            candidates.append(Path(data_dir) / base_path.name)
    candidates.append(base_path)
    for data_dir in COMMON_DATA_DIRS:
        candidates.append(data_dir / base_path.name)

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
    # timeout=30: wait up to 30 s for a lock instead of raising immediately.
    # busy_timeout covers the SQLite layer for any lock not caught by Python.
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    if cache is not None:
        wrapped = _CachedConn(conn)
        cache[db_key] = wrapped
        return wrapped
    return conn


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


_FINANCE_QUERY_TERMS = (
    "finance", "financial", "finicial", "fiance", "finace",
    "fundraising", "fundraiser",
    "raised", "donor", "donors", "money", "contribution", "contributions",
    "donate", "donated", "donation", "donations", "campaign", "campaing", "campain",
    "filing", "filings", "sbe", "vpap", "funding", "funded", "funds",
    "bankroll", "bankrolled", "backers",
)

# Queries matching this pattern are handled by the dedicated Norfolk/VB context
# functions which query norfolk_finance_summary / vb_finance_summary directly.
# Skipping the GA-level va_cf_schedule_a search here prevents a false
# "zero records" block from overriding the municipal finance context already injected.
_MUNICIPAL_COUNCIL_FINANCE_SKIP_RE = re.compile(
    r"norfolk\s+(city\s+)?council"
    r"|norfolk.*(donor|fund|financ|contribut|receiv|raised|money)"
    r"|virginia\s+beach\s+(city\s+)?council"
    r"|vb\s+(city\s+)?council",
    re.IGNORECASE,
)


def _is_campaign_finance_query(query: str) -> bool:
    q_lower = (query or "").lower()
    return any(term in q_lower for term in _FINANCE_QUERY_TERMS)


def _campaign_finance_terms(query: str, terms: list[str]) -> list[str]:
    generic = {
        "campaign", "campaing", "campain", "finance", "financial",
        "finicial", "fiance", "finace", "fundraising",
        "fundraiser", "raised", "donor", "donors", "money", "contribution",
        "contributions", "record", "records", "filing", "filings", "sbe",
        "vpap", "funding", "funded", "funds", "fund", "bankroll",
        "bankrolled", "backers", "public", "source", "sources",
        "research", "report", "overview", "with", "why", "return", "returned", "data",
        "issue", "problem", "lookup", "retrieval", "debug", "debugger",
        "bill", "bills", "action", "actions", "activity", "activities",
        "veto", "vetoes", "vetoed",
        "signed", "amended", "governor", "correlation", "correlate",
        "correlated", "relationship", "align", "alignment", "state",
        "virginia", "office", "candidate", "committee", "vote", "votes",
        "voted", "voting", "record", "records", "analysis", "analyze",
        "pattern", "patterns", "compare", "comparison", "between",
        "donation", "donations", "former", "federal", "congress",
        "congressional", "representative",
    }
    names = [term for term in terms if term not in generic]
    q_lower = (query or "").lower()
    if "spanberger" in q_lower or "spanberge" in q_lower:
        names.insert(0, "spanberger")
    elif "governor" in q_lower and _is_campaign_finance_query(query):
        names.insert(0, "spanberger")

    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        low = name.lower()
        if low and low not in seen:
            seen.add(low)
            result.append(low)
        if len(result) >= 5:
            break
    return result


def _known_federal_person(query: str) -> dict | None:
    q_lower = (query or "").lower()
    for key, person in KNOWN_FEDERAL_PEOPLE.items():
        if key in q_lower:
            return person
    return None


# ── City → district mapping for "who represents me in [city]" queries ─────────
# Keyed by lowercase city name. state_reps = [(chamber, district), ...]
_CITY_REPRESENTATIVES: dict[str, dict] = {
    "portsmouth": {
        "city": "Portsmouth, VA",
        "note": (
            "Portsmouth, VA is in Virginia House District 88, "
            "Senate District 18, and U.S. Congressional District 2 (VA-02)."
        ),
        "state_reps": [("Delegate", "88"), ("Senator", "18")],
        "federal_district": "2",
    },
    "virginia beach": {
        "city": "Virginia Beach, VA",
        "note": (
            "Virginia Beach, VA is in U.S. Congressional District 2 (VA-02). "
            "State House and Senate districts vary by neighborhood within the city."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
    "norfolk": {
        "city": "Norfolk, VA",
        "note": (
            "Norfolk, VA is in U.S. Congressional District 2 (VA-02). "
            "State districts vary by neighborhood."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
    "chesapeake": {
        "city": "Chesapeake, VA",
        "note": (
            "Chesapeake, VA is in U.S. Congressional District 2 (VA-02). "
            "State districts vary by neighborhood."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
    "hampton": {
        "city": "Hampton, VA",
        "note": (
            "Hampton, VA is in U.S. Congressional District 2 (VA-02). "
            "State districts vary by neighborhood."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
}

_REPRESENTS_ME_RE = re.compile(
    r"\b(who\s+represents?\s+me|my\s+rep(?:resentatives?)?|represent(?:ing|s|ed)\s+me"
    r"|my\s+delegate|my\s+senator|my\s+congress(?:man|woman|member|person)?"
    r"|who\s+is\s+my\s+rep|who\s+represents?\s+\w+\s*,?\s*va)\b",
    re.I,
)


def _add_city_district_context(blocks: list[str], query: str) -> None:
    """Inject legislator records when a known city appears in a 'who represents me' query."""
    if not _REPRESENTS_ME_RE.search(query):
        return
    q_lower = (query or "").lower()
    city_info = next(
        (v for k, v in _CITY_REPRESENTATIVES.items() if k in q_lower), None
    )
    if not city_info:
        return

    conn = _connect("polls")
    if not conn:
        return

    lines: list[str] = [city_info["note"], ""]
    try:
        # State legislators
        for chamber, district in city_info.get("state_reps", []):
            row = conn.execute(
                "SELECT l.name, l.party, l.chamber, l.district, "
                "       n.narrative_short "
                "FROM legislators l "
                "LEFT JOIN legislator_narratives n ON lower(n.name) LIKE '%' || lower(l.name) || '%' "
                "WHERE l.chamber=? AND l.district=? AND l.active=1 "
                "LIMIT 1",
                (chamber, district),
            ).fetchone()
            if row:
                short = (row["narrative_short"] or "").strip()[:300]
                lines.append(
                    f"Virginia {row['chamber']}, District {row['district']}: "
                    f"{row['name']} ({row['party']})"
                )
                if short:
                    lines.append(f"  Profile: {short}")

        # Federal representative
        fed_district = city_info.get("federal_district")
        if fed_district:
            row = conn.execute(
                "SELECT name, party, district FROM congress_members "
                "WHERE state='Virginia' AND district=? AND chamber='House of Representatives' "
                "LIMIT 1",
                (fed_district,),
            ).fetchone()
            if row:
                lines.append(
                    f"U.S. Representative, VA-{str(row['district']).zfill(2)}: "
                    f"{row['name']} ({row['party']})"
                )
    except Exception:
        pass
    finally:
        conn.close()

    if len(lines) > 2:
        blocks.append(
            f"[Database Context - representatives for {city_info['city']}]\n"
            + "\n".join(lines)
            + "\n\nSource: VoteIQ SQL database (legislators + congress_members)."
        )


def _add_known_person_scope_context(blocks: list[str], query: str) -> None:
    """Inject a scope hint for any person in KNOWN_FEDERAL_PEOPLE who appears in the query."""
    q_lower = (query or "").lower()
    person = _known_federal_person(query)
    if not person:
        return

    name = person["name"]
    note = person.get("office_note", "")

    # Spanberger: dual scope — current governor + former congressional record
    if "spanberger" in q_lower or "spanberge" in q_lower:
        if _is_explicit_federal_vote_query(query):
            blocks.append(
                "[Database Context - person scope]\n"
                f"person={name}\n"
                "requested_scope=former federal/congressional record\n"
                "lookup_policy=User explicitly asked for congressional/federal record. "
                "Use congress_votes and congress_bills tables for her roll-call history. "
                "Also include her governor bill-action summary for full context."
            )
        else:
            blocks.append(
                "[Database Context - person scope]\n"
                f"person={name}\n"
                "current_voteiq_scope=Virginia state governor (2026–) AND former U.S. Representative\n"
                "lookup_policy=Include BOTH (1) governor bill-action records (signed/vetoed/amended) "
                "from va_governor_bill_actions, AND (2) her former congressional voting record from "
                "congress_votes / congress_bills. Show the congressional record as 'Previous congressional record' "
                "in your response. Use Virginia state campaign-finance tables for donor context."
            )
        return

    # Warner and Kaine (senators) and all other active federal members
    blocks.append(
        "[Database Context - person scope]\n"
        f"person={name}\n"
        f"office_note={note}\n"
        "lookup_policy=Use congress_votes for roll-call vote history, congress_bills for sponsored/cosponsored "
        "legislation, and va_campaign_finance / va_campaign_contributions for donor context."
    )


def _is_explicit_federal_vote_query(query: str) -> bool:
    q_lower = (query or "").lower()
    federal_terms = (
        "federal", "congress", "congressional", "u.s. house", "us house",
        "house of representatives", "representative", "roll call", "roll-call",
        "former congress", "former representative", "former u.s. representative",
        "former us representative", "bioguide",
    )
    return any(term in q_lower for term in federal_terms)


def _like_clause(columns: list[str], terms: list[str]) -> tuple[str, list[str]]:
    clauses = []
    params: list[str] = []
    for term in terms:
        term_clauses = []
        for col in columns:
            term_clauses.append(f"lower({col}) LIKE ?")
            params.append(f"%{term.lower()}%")
        clauses.append("(" + " OR ".join(term_clauses) + ")")
    return " AND ".join(clauses), params


def _like_any_clause(columns: list[str], terms: list[str]) -> tuple[str, list[str]]:
    clauses = []
    params: list[str] = []
    for term in terms:
        for col in columns:
            clauses.append(f"lower({col}) LIKE ?")
            params.append(f"%{term.lower()}%")
    return " OR ".join(clauses), params


def _add_campaign_finance_context(blocks: list[str], query: str, terms: list[str]) -> None:
    if not _is_campaign_finance_query(query):
        return

    # Municipal council finance is handled by _add_norfolk_council_context /
    # _add_vb_council_context — bail out here to avoid a false "zero records"
    # block from va_cf_schedule_a overriding the already-injected municipal data.
    if _MUNICIPAL_COUNCIL_FINANCE_SKIP_RE.search(query or ""):
        return

    q_lower = (query or "").lower()
    finance_terms = _campaign_finance_terms(query, terms)

    conn = _connect("polls")
    if not conn:
        blocks.append(
            "[Database Context - campaign finance lookup]\n"
            "lookup_status=lookup_error\n"
            "detail=polls.db unavailable"
        )
        return

    if not finance_terms:
        _append_campaign_finance_inventory(
            blocks,
            conn,
            status="needs_entity",
            detail="Campaign finance query needs a candidate, donor, committee, or office name before row lookup can run.",
        )
        conn.close()
        return

    found_any = False
    lookup_failed = False
    searched_tables: list[str] = []

    try:
        if _table_exists(conn, "candidate_registry"):
            searched_tables.append("candidate_registry")
            columns = _table_columns(conn, "candidate_registry")
            search_cols = [col for col in ("name", "office", "party", "state", "level") if col in columns]
            select_cols = [
                col for col in (
                    "candidate_key", "fec_candidate_id", "bioguide_id", "lis_id",
                    "name", "party", "level", "office", "state", "cycle",
                )
                if col in columns
            ]
            if search_cols and select_cols:
                clause, params = _like_all_text_clause(search_cols, finance_terms)
                rows = conn.execute(f"""
                    SELECT {', '.join(_quote_identifier(col) for col in select_cols)}
                    FROM candidate_registry
                    WHERE {clause}
                    ORDER BY {_quote_identifier('cycle') if 'cycle' in columns else select_cols[0]} DESC
                    LIMIT 8
                """, tuple(params)).fetchall()
                if rows:
                    found_any = True
                    lines = ["[Database Context - polls.candidate_registry campaign finance identity]"]
                    lines.extend(f"- {_row_to_line(row)}" for row in rows)
                    blocks.append("\n".join(lines))

        if _table_exists(conn, "va_finance_people"):
            searched_tables.append("va_finance_people")
            columns = _table_columns(conn, "va_finance_people")
            search_cols = [
                col for col in ("person_name", "committee_name", "office", "party", "role")
                if col in columns
            ]
            select_cols = [
                col for col in (
                    "person_name", "office", "district", "party", "role",
                    "committee_name", "finance_url", "source_url", "data_confidence",
                    "fetched_at",
                )
                if col in columns
            ]
            if search_cols and select_cols:
                clause, params = _like_all_text_clause(search_cols, finance_terms)
                rows = conn.execute(f"""
                    SELECT {', '.join(_quote_identifier(col) for col in select_cols)}
                    FROM va_finance_people
                    WHERE {clause}
                    ORDER BY {_quote_identifier('fetched_at') if 'fetched_at' in columns else select_cols[0]} DESC
                    LIMIT 8
                """, tuple(params)).fetchall()
                if rows:
                    found_any = True
                    lines = ["[Database Context - polls.va_finance_people campaign finance profile]"]
                    lines.append("Source: Virginia SBE Campaign Finance / VPAP-linked local records")
                    lines.extend(f"- {_row_to_line(row)}" for row in rows)
                    blocks.append("\n".join(lines))

        if _table_exists(conn, "va_cf_reports"):
            searched_tables.append("va_cf_reports")
            columns = _table_columns(conn, "va_cf_reports")
            search_cols = [
                col for col in ("CandidateName", "CommitteeName", "OfficeSought", "Party")
                if col in columns
            ]
            select_cols = [
                col for col in (
                    "CandidateName", "CommitteeName", "CommitteeType", "OfficeSought",
                    "Party", "ElectionCycle", "ReportYear", "FilingType",
                    "FilingDate", "StartDate", "EndDate", "NoActivity",
                    "BalanceLastReportingPeriod", "source_period",
                )
                if col in columns
            ]
            if search_cols and select_cols:
                clause, params = _like_all_text_clause(search_cols, finance_terms)
                where_parts = [f"({clause})"]
                if "CommitteeType" in columns and "inaugural" not in q_lower:
                    where_parts.append("lower(CAST(\"CommitteeType\" AS TEXT)) NOT LIKE '%inaugural%'")
                order_col = "FilingDate" if "FilingDate" in columns else select_cols[0]
                rows = conn.execute(f"""
                    SELECT {', '.join(_quote_identifier(col) for col in select_cols)}
                    FROM va_cf_reports
                    WHERE {' AND '.join(where_parts)}
                    ORDER BY {_quote_identifier(order_col)} DESC
                    LIMIT 8
                """, tuple(params)).fetchall()
                if rows:
                    found_any = True
                    lines = ["[Database Context - polls.va_cf_reports campaign finance filings]"]
                    lines.append("Source: Virginia SBE Campaign Finance filings")
                    lines.extend(f"- {_row_to_line(row)}" for row in rows)
                    blocks.append("\n".join(lines))

        if _table_exists(conn, "va_cf_schedule_a"):
            searched_tables.append("va_cf_schedule_a")
            columns = _table_columns(conn, "va_cf_schedule_a")
            required = {"candidate_name", "election_cycle", "amount"}
            if required.issubset(columns):
                clause, params = _like_all_text_clause(["candidate_name"], finance_terms)
                first_date_expr = "MIN(transaction_date)" if "transaction_date" in columns else "''"
                latest_date_expr = "MAX(transaction_date)" if "transaction_date" in columns else "''"
                rows = conn.execute(f"""
                    SELECT
                        candidate_name,
                        election_cycle,
                        COUNT(*) AS contribution_records,
                        ROUND(SUM(amount), 2) AS total_amount,
                        {first_date_expr} AS first_transaction_date,
                        {latest_date_expr} AS latest_transaction_date,
                        MIN(report_uid) AS example_report_uid
                    FROM va_cf_schedule_a
                    WHERE ({clause})
                      AND amount > 0
                    GROUP BY candidate_name, election_cycle
                    ORDER BY CAST(election_cycle AS INTEGER) DESC, total_amount DESC
                    LIMIT 8
                """, tuple(params)).fetchall()
                if rows:
                    found_any = True
                    lines = ["[Database Context - polls.va_cf_schedule_a campaign finance totals]"]
                    lines.append("source=Virginia SBE Schedule A; public_url=unavailable (no per-record permalinks; example_report_uid is internal SBE GUID for cross-reference)")
                    lines.extend(f"- {_row_to_line(row)}" for row in rows)
                    blocks.append("\n".join(lines))

                donor_name_expr = (
                    "CASE "
                    "WHEN CAST(is_individual AS TEXT) IN ('1', 'True', 'true') "
                    "THEN TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_or_company, '')) "
                    "ELSE TRIM(COALESCE(last_or_company, '')) "
                    "END"
                    if {"is_individual", "first_name", "last_or_company"}.issubset(columns)
                    else "TRIM(COALESCE(candidate_name, 'Unknown donor'))"
                )
                donor_selects = [
                    f"{donor_name_expr} AS donor_name",
                    "election_cycle",
                    "COUNT(*) AS contribution_records",
                    "ROUND(SUM(amount), 2) AS total_amount",
                    "MIN(report_uid) AS example_report_uid",
                ]
                group_cols = ["donor_name", "election_cycle"]
                if "employer" in columns:
                    donor_selects.append("employer")
                    group_cols.append("employer")
                if "occupation" in columns:
                    donor_selects.append("occupation")
                    group_cols.append("occupation")
                rows = conn.execute(f"""
                    SELECT {', '.join(donor_selects)}
                    FROM va_cf_schedule_a
                    WHERE ({clause})
                      AND amount > 0
                    GROUP BY {', '.join(group_cols)}
                    ORDER BY total_amount DESC
                    LIMIT 10
                """, tuple(params)).fetchall()
                if rows:
                    found_any = True
                    lines = ["[Database Context - polls.va_cf_schedule_a top contributors]"]
                    lines.append("source=Virginia SBE Schedule A; public_url=unavailable (no per-record permalinks; example_report_uid is internal SBE GUID for cross-reference)")
                    lines.extend(f"- {_row_to_line(row)}" for row in rows)
                    blocks.append("\n".join(lines))
    except Exception as exc:
        lookup_failed = True
        blocks.append(
            "[Database Context - campaign finance lookup]\n"
            "lookup_status=lookup_error\n"
            f"detail={type(exc).__name__}: {str(exc)[:240]}\n"
            f"searched_tables={', '.join(searched_tables) if searched_tables else 'none'}"
        )
    finally:
        conn.close()

    if not found_any and not lookup_failed:
        detail = (
            f"No rows matched searched_terms={', '.join(finance_terms)}. "
            "Finance tables may still exist; inspect table counts below."
        )
        _append_campaign_finance_inventory(blocks, conn=None, status="zero_records", detail=detail)


def _append_campaign_finance_inventory(
    blocks: list[str],
    conn: sqlite3.Connection | None,
    *,
    status: str,
    detail: str,
) -> None:
    lines = ["[Database Context - campaign finance lookup]"]
    lines.append(f"lookup_status={status}")
    lines.append(f"detail={detail}")
    finance_tables = [
        "candidate_registry",
        "va_finance_people",
        "va_cf_reports",
        "va_cf_schedule_a",
        "campaign_finance_summary",
        "candidate_finance_reports",
        "candidate_sector_totals",
        "fec_individual_contributions",
        "fec_industry_totals",
        "fec_independent_expenditures",
    ]
    if conn is None:
        conn = _connect("polls")
        close_conn = True
    else:
        close_conn = False
    try:
        if not conn:
            lines.append("finance_tables_available=unknown; polls.db unavailable")
        else:
            for table in finance_tables:
                if not _table_exists(conn, table):
                    lines.append(f"- {table}: table_missing")
                    continue
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]
                except Exception as exc:
                    count = f"count_error:{type(exc).__name__}"
                columns = sorted(_table_columns(conn, table))
                lines.append(
                    f"- {table}: rows={count}; columns={', '.join(columns[:12])}"
                    + ("..." if len(columns) > 12 else "")
                )
    finally:
        if close_conn and conn:
            conn.close()
    blocks.append("\n".join(lines))


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _add_bill_context(blocks: list[str], bills: list[str], session: str, pro: bool = False) -> None:
    if not bills:
        return

    _before = len(blocks)

    conn = _connect("polls")
    if conn:
        for bill in bills:
            _query_rows(conn, """
                SELECT bill_number, session, title, status, description, introduced_by, introduced_date
                FROM va_bills
                WHERE bill_number=? AND session=?
                LIMIT 6
            """, (bill, session), f"polls.va_bills {bill}", blocks)
            _query_rows(conn, """
                SELECT bill_number, session, title, action_label, raw_status, action_date, governor, sponsor_name, source_url
                FROM governor_actions
                WHERE bill_number=? AND session=?
                LIMIT 6
            """, (bill, session), f"polls.governor_actions {bill}", blocks)

            # ── Auto-inject testimony proxy (pro/newsroom only) ───────────────
            # Gated to pro tier — free users don't see the donor/lobby pattern
            # unprompted; they can still ask directly and trigger the explicit
            # _add_testimony_proxy_context via lobby keyword triggers.
            if not pro:
                continue
            try:
                has_proxy = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='committee_testimony_proxy'"
                ).fetchone()
                if has_proxy:
                    proxy_rows = conn.execute("""
                        SELECT principal_name, principal_sector, lobbyist_count,
                               likely_position, confidence, total_donated_to_sponsors
                        FROM committee_testimony_proxy
                        WHERE upper(bill_number) = ? AND session = ?
                        ORDER BY
                            CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                            lobbyist_count DESC
                        LIMIT 12
                    """, (bill.upper(), session)).fetchall()
                    if proxy_rows:
                        total_proxy = conn.execute(
                            "SELECT COUNT(*) FROM committee_testimony_proxy "
                            "WHERE upper(bill_number)=? AND session=?",
                            (bill.upper(), session),
                        ).fetchone()[0]
                        supporters = sum(1 for r in proxy_rows if r[3] == "support")
                        opposers  = sum(1 for r in proxy_rows if r[3] == "oppose")
                        lines = [
                            f"[Lobbying Activity — {bill} ({session})]",
                            f"Registered organizations in this bill's sector: {total_proxy}",
                            f"Position signals — support: {supporters}  oppose: {opposers}  "
                            f"unknown: {total_proxy - supporters - opposers}",
                            "",
                        ]
                        for r in proxy_rows:
                            donated = (
                                f" | donated ${r[5]:,.0f} to sponsors"
                                if r[5] and float(r[5]) > 0 else ""
                            )
                            lines.append(
                                f"- {r[0]} ({r[1]}) | {r[2]} lobbyists | "
                                f"position: {r[3] or 'unknown'} | confidence: {r[4]}{donated}"
                            )
                        lines.append(
                            "\nNOTE: Virginia does not require bill-specific lobbying disclosure. "
                            "This is a proxy built from registered lobbyist principals active "
                            "in the same policy sector during this session."
                        )
                        blocks.append("\n".join(lines))
            except Exception:
                pass  # proxy unavailable — silent fallback

        conn.close()

    conn = _connect("openstates")
    if conn:
        for bill in bills:
            _query_rows(conn, """
                SELECT bill_id, session, title, sponsors, latest_action, latest_date, result, openstates_url
                FROM bills
                WHERE bill_id=? AND session=?
                LIMIT 6
            """, (bill, session), f"openstates.bills {bill}", blocks)
            # Aggregate vote events rather than sending individual voter rows.
            # The LLM cannot reliably count 30 raw rows to derive totals, and
            # it cannot distinguish floor votes from committee votes without help.
            # Pre-aggregate here so the LLM reads "62-Y 33-N" directly.
            _FLOOR_MOTION_KEYS = ("VOTE:", "Passage", "Concur", "Conference report",
                                  "Adoption", "Agree to", "Adopted", "Adopt ")
            try:
                agg_rows = conn.execute("""
                    SELECT vote_date, chamber, motion, result,
                           SUM(CASE WHEN option='yes' THEN 1 ELSE 0 END)        AS y,
                           SUM(CASE WHEN option='no'  THEN 1 ELSE 0 END)        AS n,
                           SUM(CASE WHEN option='not voting' THEN 1 ELSE 0 END) AS nv
                    FROM votes
                    WHERE id IN (
                        SELECT MAX(id) FROM votes
                        WHERE bill_id=? AND session=?
                        GROUP BY voter_name, bill_id, session, motion
                    )
                    GROUP BY vote_date, chamber, motion
                    ORDER BY vote_date DESC
                    LIMIT 20
                """, (bill, session)).fetchall()
            except Exception:
                agg_rows = []
            if agg_rows:
                floor_lines, cmte_lines = [], []
                for r in agg_rows:
                    dt, cham, motion, result, y, n, nv = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
                    is_floor = any(k in (motion or "") for k in _FLOOR_MOTION_KEYS)
                    nv_s = f" {nv}-NV" if nv else ""
                    line = f"  {dt or 'n/a'} {cham} — {(motion or '').strip()[:50]} — {y}-Y {n}-N{nv_s} ({result})"
                    (floor_lines if is_floor else cmte_lines).append(line)
                out = [f"[Database Context - vote events for {bill} ({session})]"]
                if floor_lines:
                    out.append("Full-chamber (floor) votes:")
                    out.extend(floor_lines)
                if cmte_lines:
                    out.append("Committee/procedural votes:")
                    out.extend(cmte_lines)
                out.append(
                    "NOTE: Floor vote totals are authoritative. VA House has 100 members; "
                    "Senate has 40 members. Committee totals reflect smaller panels."
                )
                try:
                    url_row = conn.execute(
                        "SELECT openstates_url FROM bills WHERE bill_id=? AND session=? LIMIT 1",
                        (bill, session),
                    ).fetchone()
                    if url_row and url_row["openstates_url"]:
                        out.append(f"source=OpenStates; openstates_url={url_row['openstates_url']}")
                except Exception:
                    pass
                blocks.append("\n".join(out))
            if _table_exists(conn, "bill_descriptions"):
                _query_rows(conn, """
                    SELECT bill_id, session, title, description, source_url
                    FROM bill_descriptions
                    WHERE bill_id=? AND session=?
                    LIMIT 4
                """, (bill, session), f"openstates.bill_descriptions {bill}", blocks)
        conn.close()

    conn = _connect("legislative_intelligence")
    if conn:
        for bill in bills:
            _query_rows(conn, """
                SELECT bill_number, session, legislator_id, vote, vote_date
                FROM va_votes
                WHERE bill_number=? AND session=?
                ORDER BY vote_date DESC
                LIMIT 40
            """, (bill, session), f"legislative_intelligence.va_votes {bill}", blocks, limit=40)
        conn.close()

    conn = _connect("virginia_legislature")
    if conn:
        for bill in bills:
            _query_rows(conn, """
                SELECT Bill_id, Bill_description, Patron_id, Patron_name,
                       Last_house_action, Last_house_action_date,
                       Last_senate_action, Last_senate_action_date,
                       Last_conference_action, Last_conference_action_date
                FROM bills
                WHERE Bill_id=?
                LIMIT 4
            """, (bill,), f"virginia_legislature.bills {bill}", blocks)
        conn.close()

    for bill in bills:
        b_up = bill.upper()
        if not any(b_up in block.upper() for block in blocks[_before:]):
            blocks.append(
                f"[Database Context - polls.va_bills {bill}]\n"
                f"lookup_status=zero_records\n"
                f"detail=No record found for bill {bill} in session {session}. "
                f"This bill number does not exist in the VoteIQ dataset. "
                f"Do NOT fabricate a vote or outcome for this bill."
            )


def _add_keyword_context(blocks: list[str], query: str, terms: list[str], session: str) -> None:
    if not terms:
        return

    q_lower = (query or "").lower()
    is_finance = _is_campaign_finance_query(query) or "pac" in q_lower

    conn = _connect("polls")
    if conn and is_finance:
        finance_terms = _campaign_finance_terms(query, terms)
        if finance_terms:
            clause, params = _like_clause(["person_name", "committee_name", "office", "party"], finance_terms[:4])
            _query_rows(conn, f"""
                SELECT person_name, office, district, party, committee_name, finance_url, source_url
                FROM va_finance_people
                WHERE {clause}
                LIMIT 8
            """, params, "polls.va_finance_people keyword", blocks, limit=8)
            clause, params = _like_clause(["candidate_name", "sector"], finance_terms[:4])
            try:
                cst_rows = conn.execute(f"""
                    SELECT candidate_name, sector, total_amount, donor_count, cycle
                    FROM candidate_sector_totals
                    WHERE {clause}
                    ORDER BY total_amount DESC
                    LIMIT 8
                """, tuple(params)).fetchmany(8)
                if cst_rows:
                    lines = [
                        "[Database Context - polls.candidate_sector_totals]",
                        "SCOPE: 2024 cycle SBE keyword-classified subset ONLY.",
                        "These amounts come from Virginia SBE occupation/employer keyword matching "
                        "for a single cycle and cover only contributions where an employer/occupation "
                        "keyword matched a sector. They are NOT the full FEC total and will be far "
                        "smaller than FEC-filed industry totals from fec_industry_totals.",
                        "Caption: Top Sector Totals (2024 Cycle Subset — Keyword-Classified)",
                        "When presenting these numbers alongside fec_industry_totals, always state "
                        "that this is a partial subset and explain why the totals differ.",
                    ]
                    lines.extend(f"- {_row_to_line(row)}" for row in cst_rows)
                    blocks.append("\n".join(lines))
            except Exception:
                pass
        conn.close()

    if is_finance and not any(term in q_lower for term in ("bill", "hb", "sb", "legislation", "veto", "signed")):
        return

    conn = _connect("openstates")
    if conn:
        if _table_exists(conn, "bill_descriptions_fts"):
            fts = " ".join(f'"{term}"' for term in terms[:5])
            _query_rows(conn, """
                SELECT bill_id, session, title, description
                FROM bill_descriptions_fts
                WHERE bill_descriptions_fts MATCH ?
                  AND session=?
                LIMIT 8
            """, (fts, session), "openstates.bill_descriptions_fts", blocks, limit=8)
        clause, params = _like_clause(["title", "sponsors", "latest_action", "subjects"], terms[:4])
        _query_rows(conn, f"""
            SELECT bill_id, session, title, sponsors, latest_action, latest_date, result, openstates_url
            FROM bills
            WHERE session=? AND {clause}
            ORDER BY latest_date DESC
            LIMIT 8
        """, [session, *params], "openstates.bills keyword", blocks, limit=8)
        conn.close()

    conn = _connect("polls")
    if conn:
        clause, params = _like_clause(["title", "description", "status"], terms[:4])
        _query_rows(conn, f"""
            SELECT bill_number, session, title, status, description
            FROM va_bills
            WHERE session=? AND {clause}
            LIMIT 8
        """, [session, *params], "polls.va_bills keyword", blocks, limit=8)

        if any(term in q_lower for term in ("news", "article", "reported", "recent", "latest")):
            clause, params = _like_clause(["title", "gemini_json", "source"], terms[:4])
            try:
                news_rows = conn.execute(f"""
                    SELECT source, title, published_at, url, gemini_json
                    FROM va_news
                    WHERE {clause}
                    ORDER BY COALESCE(published_at, fetched_at) DESC
                    LIMIT 6
                """, tuple(params)).fetchall()
                if news_rows:
                    import json as _json
                    lines = ["[Recent Virginia News Coverage]"]
                    for row in news_rows:
                        gj = {}
                        try:
                            gj = _json.loads(row[4] or "{}")
                        except Exception:
                            pass
                        # Extract attribution fields
                        outlet  = gj.get("outlet") or row[0] or "Unknown outlet"
                        author  = gj.get("author")
                        headline = gj.get("headline") or row[1] or ""
                        summary  = gj.get("summary") or ""
                        pub_date = (row[2] or "")[:10]
                        url      = row[3] or ""
                        quote    = gj.get("key_quote") or ""
                        # Format with clear attribution and clickable link
                        byline = f"{author}, {outlet}" if author else outlet
                        line = f'- "{headline}" — {byline}'
                        if pub_date:
                            line += f" ({pub_date})"
                        if summary:
                            line += f". {summary}"
                        if quote:
                            line += f' Key quote: "{quote}"'
                        if url:
                            line += f"\n  Read full article: {url}"
                        lines.append(line)
                    blocks.append("\n".join(lines))
            except Exception:
                pass

        conn.close()


def _add_pac_context(blocks: list[str], query: str, terms: list[str]) -> None:
    q_lower = (query or "").lower()
    if not any(term in q_lower for term in (
        "pac", "political action", "committee money", "outside money",
        "outside spending", "independent expenditure", "super pac",
        "who funds", "who funded", "who backs", "special interest",
    )):
        return

    conn = _connect("polls")
    if not conn:
        return

    generic = {
        "pac", "pacs", "political", "action", "committee", "committees",
        "money", "outside", "spending", "independent", "expenditure",
        "super", "funds", "funded", "backs", "special", "interest",
        "show", "list", "table", "tables", "database", "databases",
        "which", "what", "have",
    }
    pac_terms = [term for term in terms if term not in generic]

    if pac_terms:
        clause, params = _like_any_clause(
            [
                "committee_name", "short_name", "ideology", "alignment",
                "network", "issue_focus", "description", "foreign_country",
            ],
            pac_terms[:5],
        )
        _query_rows(conn, f"""
            SELECT committee_id, committee_name, short_name, ideology, alignment,
                   network, issue_focus, description, source_url, verified,
                   foreign_alignment, foreign_country
            FROM pac_ideology
            WHERE {clause}
            ORDER BY committee_name
            LIMIT 10
        """, params, "polls.pac_ideology keyword", blocks, limit=10)

        clause, params = _like_any_clause(["pac_name", "industry", "source"], pac_terms[:5])
        _query_rows(conn, f"""
            SELECT pac_name, industry, source, confidence, created_at
            FROM pac_name_lookup
            WHERE {clause}
            ORDER BY confidence DESC, pac_name
            LIMIT 10
        """, params, "polls.pac_name_lookup keyword", blocks, limit=10)

        clause, params = _like_any_clause(["committee_name", "candidate_name", "office", "support_oppose"], pac_terms[:5])
        _query_rows(conn, f"""
            SELECT candidate_name, committee_name, support_oppose,
                   expenditure_amount, expenditure_date, cycle, office, state
            FROM fec_independent_expenditures
            WHERE {clause}
            ORDER BY expenditure_date DESC, expenditure_amount DESC
            LIMIT 12
        """, params, "polls.fec_independent_expenditures keyword", blocks, limit=12)

        clause, params = _like_any_clause(["member_name", "industry", "top_donors"], pac_terms[:5])
        try:
            fit_rows = conn.execute(f"""
                SELECT member_name, cycle, industry, total_amount,
                       contributor_count, top_donors
                FROM fec_industry_totals
                WHERE {clause}
                ORDER BY cycle DESC, total_amount DESC
                LIMIT 12
            """, tuple(params)).fetchmany(12)
            if fit_rows:
                lines = [
                    "[Database Context - polls.fec_industry_totals]",
                    "SCOPE: Full FEC-filed industry/sector totals across all cycles.",
                    "Includes Retired/Individual, Other/Unknown, and all classified sectors. "
                    "This is the AUTHORITATIVE FEC total. Summing all rows for a single cycle "
                    "gives the full multi-sector federal fundraising total.",
                    "WARNING: Do NOT directly compare these totals to candidate_sector_totals — "
                    "that table is a 2024 cycle SBE keyword-classified subset covering only a "
                    "fraction of contributions. Always clarify the scope difference to the reader.",
                ]
                lines.extend(f"- {_row_to_line(row)}" for row in fit_rows)
                blocks.append("\n".join(lines))
        except Exception:
            pass
    else:
        _query_rows(conn, """
            SELECT committee_id, committee_name, short_name, ideology, alignment,
                   network, issue_focus, source_url, verified,
                   foreign_alignment, foreign_country
            FROM pac_ideology
            ORDER BY committee_name
            LIMIT 15
        """, (), "polls.pac_ideology sample", blocks, limit=15)

        _query_rows(conn, """
            SELECT pac_name, industry, source, confidence, created_at
            FROM pac_name_lookup
            ORDER BY confidence DESC, pac_name
            LIMIT 15
        """, (), "polls.pac_name_lookup sample", blocks, limit=15)

        _query_rows(conn, """
            SELECT candidate_name, committee_name, support_oppose,
                   expenditure_amount, expenditure_date, cycle, office, state
            FROM fec_independent_expenditures
            ORDER BY expenditure_date DESC, expenditure_amount DESC
            LIMIT 15
        """, (), "polls.fec_independent_expenditures sample", blocks, limit=15)

    conn.close()


def _person_terms(terms: list[str]) -> list[str]:
    generic = {
        "vote", "votes", "voted", "voting", "record", "records", "bill", "bills",
        "legislator", "legislators", "delegate", "senator", "representative",
        "official", "officials", "member", "members", "show", "tell", "list",
        "public", "search", "research", "report", "overview", "profile",
        "office", "status", "finance", "financial", "campaign", "donor",
        "donors", "money", "contribution", "contributions", "voteiq",
        "funds", "fund", "funded", "funding", "bankroll", "bankrolled",
        "backers", "who",
    }
    return [term for term in terms if term not in generic]


def _add_targeted_vote_lookup(
    blocks: list[str], query: str, bills: list[str], terms: list[str], session: str
) -> None:
    """Targeted single-legislator × single-bill vote lookup.

    Fires when at least one bill number AND at least one person name appear
    together in the query. Uses WHERE voter_name LIKE + bill LIKE rather than
    pulling the full roll-call blob.  Prevents alphabetical truncation (e.g.
    a 666-row roll-call ordered A→Z that cuts off before 'H' names).
    """
    if not bills:
        return
    people = _person_terms(terms)
    if not people:
        return
    q_lower = (query or "").lower()
    has_vote_signal = any(
        kw in q_lower
        for kw in ("vote", "voted", "voting", "how did", "position", "support", "oppose")
    )
    if not has_vote_signal:
        return

    found_any = False

    # ── polls.va_legislator_recent_votes JOIN va_bills ─────────────────────────
    conn = _connect("polls")
    if conn:
        try:
            if _table_exists(conn, "va_legislator_recent_votes") and _table_exists(conn, "va_bills"):
                for bill in bills[:3]:
                    bill_pat = f"%{bill.upper()}%"
                    for person in people[:2]:
                        person_pat = f"%{person}%"
                        rows = conn.execute("""
                            SELECT v.voter_name, vb.bill_number, vb.session,
                                   vb.title, v.vote_date, v.option AS vote,
                                   v.motion, v.is_procedural
                            FROM va_legislator_recent_votes v
                            JOIN va_bills vb ON vb.bill_number = v.bill_id
                                            AND vb.session    = v.session
                            WHERE lower(vb.bill_number) LIKE lower(?)
                              AND lower(v.voter_name)   LIKE lower(?)
                            ORDER BY v.vote_date DESC
                            LIMIT 4
                        """, (bill_pat, person_pat)).fetchall()
                        if rows:
                            found_any = True
                            lines = [
                                f"[Database Context - targeted vote: {bill} × {person}]",
                                "Source: va_legislator_recent_votes JOIN va_bills (name-filtered, not full roll-call)",
                                "STAGE NOTE: 'Concur House Substitute/Amendment' and 'Adopt Conference Committee Report' "
                                "are concurrence/procedural motions, not original floor-passage votes.",
                            ]
                            for r in rows:
                                proc = " [PROCEDURAL]" if r["is_procedural"] else ""
                                lines.append(
                                    f"voter={r['voter_name']} | bill={r['bill_number']} ({r['session']}) "
                                    f"| vote={r['vote']}{proc} | date={r['vote_date']} "
                                    f"| motion={r['motion'] or 'n/a'} | title={r['title'] or 'n/a'}"
                                )
                            blocks.append("\n".join(lines))
        except Exception:
            pass
        finally:
            conn.close()

    # ── openstates.votes ───────────────────────────────────────────────────────
    conn = _connect("openstates")
    if conn:
        try:
            if _table_exists(conn, "votes"):
                for bill in bills[:3]:
                    bill_pat = f"%{bill.upper()}%"
                    for person in people[:2]:
                        person_pat = f"%{person}%"
                        rows = conn.execute("""
                            SELECT voter_name, bill_id, session, vote_date,
                                   chamber, motion, result, option, party, district
                            FROM votes
                            WHERE id IN (
                                SELECT MAX(id) FROM votes
                                WHERE lower(bill_id)    LIKE lower(?)
                                  AND lower(voter_name) LIKE lower(?)
                                GROUP BY voter_name, bill_id, session, motion
                            )
                            ORDER BY vote_date DESC
                            LIMIT 4
                        """, (bill_pat, person_pat)).fetchall()
                        if rows:
                            found_any = True
                            lines = [
                                f"[Database Context - targeted vote (openstates): {bill} × {person}]",
                                "Source: openstates.votes (name-filtered, not full roll-call)",
                            ]
                            for r in rows:
                                lines.append(
                                    f"voter={r['voter_name']} | bill={r['bill_id']} ({r['session']}) "
                                    f"| vote={r['option']} | result={r['result']} "
                                    f"| date={r['vote_date']} | chamber={r['chamber']} "
                                    f"| motion={r['motion'] or 'n/a'}"
                                )
                            blocks.append("\n".join(lines))
        except Exception:
            pass
        finally:
            conn.close()

    if found_any:
        blocks.append(
            "[Database Context - vote lookup routing]\n"
            "targeted_lookup=true\n"
            "instruction=The targeted vote blocks above give the exact vote for this "
            "legislator+bill combination. Use them directly — do NOT say the vote is "
            "unknown or scan a full roll-call list."
        )


# Sponsorship-intent terms: the user is asking what a legislator authored/introduced.
_SPONSORSHIP_TRIGGER = re.compile(
    r"\b(sponsor|sponsored|sponsorship|patron|patroned|introduce[d]?|"
    r"author(?:ed)?|carried|legislation|bills?)\b",
    re.I,
)

# va_legislator_sponsored_bills.status_label → plain-English outcome bucket.
_SPONSOR_STATUS_OUTCOME = {
    "Passed": "passed",
    "Vetoed": "vetoed",
    "Engrossed": "in progress (passed one chamber)",
    "Introduced": "did not pass (died without final action)",
}


def _add_sponsorship_summary_context(
    blocks: list[str], query: str, terms: list[str], session: str
) -> None:
    """Inject the AUTHORITATIVE chief-patron sponsorship record for a named (or
    address-resolved) legislator from va_legislator_sponsored_bills — deduplicated,
    chief-patron only, with a real passed / did-not-pass breakdown, and with
    co-patron bills counted SEPARATELY.

    Rationale: the generic `sponsors LIKE '%name%'` sweep fuses chief patron with
    co-patron and carries no authoritative totals. That let the model overcount
    ("133 sponsored" when the legislator chief-patroned 53), present co-patroned bills
    as authored, and — because no status is literally 'Failed' — report "0 failed"
    while bills had in fact died in committee. When a sponsorship question identifies a
    legislator, serve the structured truth so the model does not have to infer counts.
    """
    if not _SPONSORSHIP_TRIGGER.search(query or ""):
        return
    people = _person_terms(terms)
    if not people:
        return

    conn = _connect("polls")
    if not conn:
        return
    try:
        if not _table_exists(conn, "va_legislator_sponsored_bills"):
            return

        # Resolve to EXACTLY one legislator. If the name is ambiguous (0 or >1
        # distinct matches), skip rather than guess — never fabricate a record.
        matched: set[str] = set()
        for p in people:
            p = (p or "").strip()
            if len(p) < 3:
                continue
            for r in conn.execute(
                "SELECT DISTINCT legislator_name FROM va_legislator_sponsored_bills "
                "WHERE lower(legislator_name) LIKE ?",
                (f"%{p.lower()}%",),
            ):
                matched.add(r[0])
        if len(matched) != 1:
            return
        name = next(iter(matched))
        sess = session or "2026"

        rows = conn.execute(
            "SELECT DISTINCT bill_number, status_label "
            "FROM va_legislator_sponsored_bills WHERE legislator_name = ? AND session = ?",
            (name, sess),
        ).fetchall()
        if not rows:
            return

        def _is_bill(num: str) -> bool:
            u = (num or "").upper()
            return u.startswith("HB") or u.startswith("SB")

        # Bucket by substantive bill vs ceremonial resolution, then by outcome.
        from collections import Counter
        bill_outcomes: Counter = Counter()
        res_outcomes: Counter = Counter()
        for num, status in rows:
            outcome = _SPONSOR_STATUS_OUTCOME.get(status, f"other ({status})")
            (bill_outcomes if _is_bill(num) else res_outcomes)[outcome] += 1

        n_bills = sum(bill_outcomes.values())
        n_res = sum(res_outcomes.values())
        chief_total = n_bills + n_res

        co_total = 0
        if _table_exists(conn, "va_legislator_cosponsor_bills"):
            co_total = conn.execute(
                "SELECT COUNT(DISTINCT bill_id) FROM va_legislator_cosponsor_bills "
                "WHERE legislator_name = ? AND session = ?",
                (name, sess),
            ).fetchone()[0]

        def _fmt(counter: Counter) -> str:
            order = ["passed", "did not pass (died without final action)",
                     "in progress (passed one chamber)", "vetoed"]
            parts = [f"{counter[k]} {k}" for k in order if counter.get(k)]
            parts += [f"{v} {k}" for k, v in counter.items() if k not in order]
            return " · ".join(parts) if parts else "none"

        # A few substantive example bills, passed first.
        examples = conn.execute(
            "SELECT DISTINCT bill_number, title, status_label "
            "FROM va_legislator_sponsored_bills WHERE legislator_name = ? AND session = ? "
            "AND (upper(bill_number) LIKE 'HB%' OR upper(bill_number) LIKE 'SB%') "
            "ORDER BY CASE status_label WHEN 'Passed' THEN 0 WHEN 'Engrossed' THEN 1 "
            "WHEN 'Vetoed' THEN 2 ELSE 3 END, bill_number LIMIT 6",
            (name, sess),
        ).fetchall()

        lines = [
            f"[LAYER 1 FACT — AUTHORITATIVE sponsorship record: {name}, VA {sess} session]",
            "Source: va_legislator_sponsored_bills / va_legislator_cosponsor_bills "
            "(deduplicated by bill; chief patron vs co-patron kept separate).",
            f"CHIEF PATRON (bills this legislator INTRODUCED) — {chief_total} total:",
            f"  Substantive bills (HB/SB): {n_bills} → {_fmt(bill_outcomes)}",
            f"  Ceremonial resolutions (HR/HJ/SR/SJ): {n_res} → {_fmt(res_outcomes)}",
            f"CO-PATRON (added name to another member's bill; did NOT introduce): {co_total} bills "
            "— report SEPARATELY, never as 'sponsored by' this legislator.",
        ]
        if examples:
            lines.append("Example chief-patron substantive bills:")
            for num, title, status in examples:
                outcome = _SPONSOR_STATUS_OUTCOME.get(status, status)
                lines.append(f"  {num} [{outcome}] {(title or '')[:70]}")
        lines += [
            "REPORTING RULES (MANDATORY):",
            "  • 'Sponsored' = CHIEF PATRON only. Never count co-patroned bills as sponsored.",
            "  • Always report BOTH the passed count AND the did-not-pass count. "
            "NEVER say '0 failed' — use the did-not-pass number above.",
            "  • These are authoritative totals; do not recompute or estimate sponsorship counts "
            "from any other block.",
        ]
        blocks.append("\n".join(lines))
    except Exception:
        pass
    finally:
        conn.close()


def _add_person_vote_context(blocks: list[str], query: str, terms: list[str], session: str) -> None:
    q_lower = (query or "").lower()
    if _known_federal_person(query) and _is_explicit_federal_vote_query(query):
        return
    people = _person_terms(terms)
    if not people:
        return
    has_vote_term = any(term in q_lower for term in ("vote", "votes", "voted", "voting"))
    has_record_term = "record" in q_lower and not _is_campaign_finance_query(query)
    has_person_context_term = any(
        term in q_lower
        for term in (
            "research", "overview", "profile", "public record", "report",
            "office", "status", "who is",
        )
    )
    # A bare-name query pulls the full voting record + sponsored bills. That
    # is right for "Aaron Rouse" / "tell me about X", but wrong for a pure
    # funding question ("who funds Aaron Rouse") — there the votes/bills are
    # ~70% of the context and irrelevant, while the donor-analysis block
    # already covers the answer. Don't trigger the vote dump on finance queries.
    has_name_only_query = (
        len(people) >= 2 and len(terms) <= 3
        and not _is_campaign_finance_query(query)
    )
    if not (has_vote_term or has_record_term or has_person_context_term or has_name_only_query):
        return

    conn = _connect("openstates")
    if conn:
        if _table_exists(conn, "legislators"):
            columns = _table_columns(conn, "legislators")
            search_cols = [
                col for col in (
                    "name", "full_name", "sort_name", "given_name", "family_name",
                    "party", "district", "chamber",
                )
                if col in columns
            ]
            select_cols = [
                col for col in (
                    "id", "name", "full_name", "party", "chamber", "district",
                    "email", "openstates_url",
                )
                if col in columns
            ]
            if search_cols and select_cols:
                clause, params = _like_all_text_clause(search_cols, people[:3])
                _query_rows(conn, f"""
                    SELECT {', '.join(_quote_identifier(col) for col in select_cols)}
                    FROM legislators
                    WHERE {clause}
                    LIMIT 8
                """, params, "openstates.legislators person", blocks, limit=8)

        clause, params = _like_any_clause(["voter_name", "party", "district"], people[:4])
        _query_rows(conn, f"""
            SELECT bill_id, session, vote_date, chamber, motion, result,
                   voter_name, option, party, district
            FROM votes
            WHERE id IN (
              SELECT MAX(id) FROM votes
              WHERE session=? AND ({clause})
              GROUP BY voter_name, bill_id, session, motion
            )
            ORDER BY vote_date DESC
            LIMIT 40
        """, [session, *params], "openstates.votes person", blocks, limit=40)

        clause, params = _like_any_clause(["sponsors"], people[:4])
        _query_rows(conn, f"""
            SELECT bill_id, session, title, sponsors, latest_action, latest_date,
                   result, openstates_url
            FROM bills
            WHERE session=? AND ({clause})
            ORDER BY latest_date DESC
            LIMIT 12
        """, [session, *params],
            "openstates.bills where person is patron OR co-patron — NOT authoritative "
            "for sponsorship counts; use the AUTHORITATIVE sponsorship record block instead",
            blocks, limit=12)
        conn.close()

    conn = _connect("legislative_intelligence")
    if conn:
        clause, params = _like_any_clause(["legislator_id", "bill_number", "vote"], people[:4])
        _query_rows(conn, f"""
            SELECT bill_number, session, legislator_id, vote, vote_date
            FROM va_votes
            WHERE session=? AND ({clause})
            ORDER BY vote_date DESC
            LIMIT 40
        """, [session, *params], "legislative_intelligence.va_votes person", blocks, limit=40)
        conn.close()


# ── State legislator vote × SBE donor correlation ─────────────────────────────

def _add_state_vote_donor_context(
    blocks: list[str], query: str, terms: list[str], session: str
) -> None:
    """Three-table join: va_legislator_recent_votes × va_bills × va_cf_schedule_a.

    Fires when a state legislator name resolves from the query AND the query
    contains both a vote signal and a money/donor signal.

    Output layout per legislator
    ----------------------------
    • Topic-filtered votes (va_legislator_recent_votes JOIN va_bills).
      If topic terms exist, filters va_bills.title/subjects LIKE term.
    • Top contributors (va_cf_reports JOIN va_cf_schedule_a), aggregated
      by employer, ordered by total amount — showing who funded the member.
    • Cycle alignment: contributions matched to the same session/cycle year.
    """
    q_lower = (query or "").lower()

    # Gate — need vote signal + money signal
    has_vote   = any(kw in q_lower for kw in ("vote", "votes", "voted", "voting", "record"))
    has_money  = any(kw in q_lower for kw in (
        "donor", "donors", "fund", "funded", "funds", "money", "contribut",
        "pac", "industry", "backed", "sponsor", "finance",
    ))
    if not has_vote or not has_money:
        return

    # Skip if this resolves as a known federal person (handled by PAC-vote block)
    if _known_federal_person(query):
        return

    people = _person_terms(terms)
    if not people:
        return

    conn = _connect("polls")
    if not conn:
        return

    # Topic terms: stripped of people tokens + generic vote/money words
    _state_topic_strip = {
        "vote", "votes", "voted", "voting", "donor", "donors", "fund",
        "funded", "funds", "money", "contribut", "backed", "sponsor",
        "finance", "record", "records", "bill", "bills", "legislator",
        "virginia", "general", "assembly", "session",
    }
    topic_terms_state = [
        t for t in terms
        if t not in _state_topic_strip
        and t not in {p.lower() for p in people}
        and len(t) >= 4
    ]
    # Expand through policy alias map (same as federal correlation block)
    expanded_state: list[str] = []
    _seen_st: set[str] = set()
    for t in topic_terms_state[:4]:
        for alias in _POLICY_AREA_ALIASES.get(t, (t,)):
            if alias not in _seen_st:
                _seen_st.add(alias)
                expanded_state.append(alias)

    lines = ["[Database Context - State Legislator Vote × Donor Correlation]"]
    lines.append(
        "Source: va_legislator_recent_votes JOIN va_bills (vote record) + "
        "va_cf_reports JOIN va_cf_schedule_a (SBE campaign contributions)."
    )
    lines.append(
        "How to read: compare donor employers/industries against vote positions "
        "on matching bills to assess whether money aligns with legislative behavior."
    )

    try:
        # Resolve legislator from polls.va_legislator_recent_votes
        if not _table_exists(conn, "va_legislator_recent_votes") or not _table_exists(conn, "va_bills"):
            conn.close()
            return

        name_clause, name_params = _like_any_clause(["voter_name"], people[:3])
        legislator_rows = conn.execute(
            f"""
            SELECT DISTINCT voter_name, chamber
            FROM va_legislator_recent_votes
            WHERE session = ? AND ({name_clause})
            LIMIT 4
            """,
            [session, *name_params],
        ).fetchall()

        if not legislator_rows:
            # Try recent session fallback
            legislator_rows = conn.execute(
                f"""
                SELECT DISTINCT voter_name, chamber
                FROM va_legislator_recent_votes
                WHERE ({name_clause})
                ORDER BY session DESC
                LIMIT 4
                """,
                name_params,
            ).fetchall()

        if not legislator_rows:
            conn.close()
            return

        for leg_row in legislator_rows[:2]:
            leg_name = leg_row["voter_name"]
            chamber  = leg_row["chamber"] or ""
            lines.append(f"\n=== {leg_name}  ({chamber}, session {session}) ===")

            # ── 1. Topic-filtered vote record ────────────────────────────────
            if expanded_state:
                topic_clause, topic_params = _like_any_clause(
                    ["b.title", "b.subjects"], expanded_state
                )
                voted_rows = conn.execute(
                    f"""
                    SELECT v.vote_date,
                           b.bill_number,
                           b.title,
                           b.subjects,
                           v.option        AS vote,
                           v.motion,
                           v.is_procedural,
                           b.introduced_by AS sponsor
                    FROM va_legislator_recent_votes v
                    JOIN va_bills b ON b.bill_number = v.bill_id
                                   AND b.session    = v.session
                    WHERE v.voter_name = ?
                      AND v.session    = ?
                      AND ({topic_clause})
                    ORDER BY v.vote_date DESC
                    LIMIT 12
                    """,
                    [leg_name, session, *topic_params],
                ).fetchall()
            else:
                voted_rows = []

            if not voted_rows:
                # No topic match — show recent votes
                voted_rows = conn.execute(
                    """
                    SELECT v.vote_date,
                           b.bill_number,
                           b.title,
                           b.subjects,
                           v.option        AS vote,
                           v.motion,
                           v.is_procedural,
                           b.introduced_by AS sponsor
                    FROM va_legislator_recent_votes v
                    JOIN va_bills b ON b.bill_number = v.bill_id
                                   AND b.session    = v.session
                    WHERE v.voter_name = ?
                      AND v.session    = ?
                    ORDER BY v.vote_date DESC
                    LIMIT 8
                    """,
                    (leg_name, session),
                ).fetchall()
                vote_label = (
                    f"no topic match for '{' + '.join(topic_terms_state[:2])}' — latest 8"
                    if topic_terms_state
                    else "latest 8 votes"
                )
            else:
                vote_label = " + ".join(topic_terms_state[:3]) if topic_terms_state else "all topics"

            if voted_rows:
                lines.append(f"  Vote record (filter: '{vote_label}'):")
                lines.append(
                    "  STAGE NOTE: 'Concur House Substitute/Amendment' and 'Adopt Conference Committee Report' "
                    "are concurrence/procedural motions, not original floor-passage votes. "
                    "A NO on concurrence means the senator rejected the House-amended text and sent it to conference; "
                    "it does NOT indicate opposition to the original bill."
                )
                for r in voted_rows:
                    subj = (r["subjects"] or "")[:50].rstrip()
                    motion = (r["motion"] or "").strip()
                    proc_tag = " [PROCEDURAL]" if r.get("is_procedural") else ""
                    motion_part = f" | motion={motion[:60]}{proc_tag}" if motion else ""
                    lines.append(
                        f"    {r['vote_date']} | {r['bill_number']} | "
                        f"{r['vote']}{proc_tag} | {(r['title'] or '')[:70]}"
                        + motion_part
                        + (f" [{subj}]" if subj else "")
                    )
            else:
                lines.append(f"  No vote records found for {leg_name} in session {session}.")

            # ── 2. SBE campaign contributions received ────────────────────────
            # Chain: va_cf_reports.CandidateName → report_uid → va_cf_schedule_a
            if not _table_exists(conn, "va_cf_reports") or not _table_exists(conn, "va_cf_schedule_a"):
                lines.append("  (SBE contribution tables not available)")
                continue

            name_cf_clause, name_cf_params = _like_any_clause(
                ["cr.CandidateName"], people[:2]
            )
            donor_rows = conn.execute(
                f"""
                SELECT sa.employer,
                       sa.occupation,
                       sa.first_name || ' ' || sa.last_or_company AS contributor,
                       sa.is_individual,
                       SUM(sa.amount)  AS total,
                       COUNT(*)        AS txns,
                       MIN(sa.transaction_date) AS earliest,
                       MAX(sa.transaction_date) AS latest
                FROM va_cf_reports cr
                JOIN va_cf_schedule_a sa ON sa.report_uid = cr.ReportUID
                WHERE ({name_cf_clause})
                  AND cr.IsGeneralAssembly = 1
                  AND cr.ElectionCycle     = ?
                GROUP BY sa.employer, sa.is_individual
                ORDER BY total DESC
                LIMIT 15
                """,
                [*name_cf_params, session],
            ).fetchall()

            if not donor_rows:
                # Try without cycle filter (broader match)
                donor_rows = conn.execute(
                    f"""
                    SELECT sa.employer,
                           sa.first_name || ' ' || sa.last_or_company AS contributor,
                           sa.is_individual,
                           SUM(sa.amount)  AS total,
                           COUNT(*)        AS txns,
                           MIN(sa.election_cycle) AS earliest_cycle,
                           MAX(sa.election_cycle) AS latest_cycle
                    FROM va_cf_reports cr
                    JOIN va_cf_schedule_a sa ON sa.report_uid = cr.ReportUID
                    WHERE ({name_cf_clause})
                      AND cr.IsGeneralAssembly = 1
                    GROUP BY sa.employer, sa.is_individual
                    ORDER BY total DESC
                    LIMIT 15
                    """,
                    name_cf_params,
                ).fetchall()

            if donor_rows:
                lines.append(f"\n  Top donors / employers to {leg_name}:")
                for r in donor_rows:
                    emp = (r["employer"] or "").strip() or (r["contributor"] or "").strip()[:40]
                    indiv = "individual" if r["is_individual"] else "org/PAC"
                    lines.append(
                        f"    {emp or '(no employer)'} [{indiv}] "
                        f"— ${r['total']:,.0f} ({r['txns']} contribution{'s' if r['txns'] != 1 else ''})"
                    )
            else:
                lines.append(f"\n  No SBE contribution records found for '{leg_name}' (cycle {session}).")

    except Exception as exc:
        lines.append(f"  state_vote_donor_error={type(exc).__name__}: {str(exc)[:240]}")
    finally:
        conn.close()

    if len(lines) > 4:
        blocks.append("\n".join(lines))


def _add_federal_vote_context(blocks: list[str], query: str, terms: list[str]) -> None:
    q_lower = (query or "").lower()

    person = _known_federal_person(query)
    is_active = bool(person and person.get("is_current_federal"))
    explicit_federal = _is_explicit_federal_vote_query(query)

    # Gate 1: require vote-related keyword — UNLESS it's a known active federal
    # member (profile queries like "who is Kiggans" should still show vote data).
    has_vote_keyword = any(term in q_lower for term in (
        "vote", "votes", "voted", "voting", "roll call", "roll-call",
        "correlation", "campaign data", "campaign finance",
    ))
    if not has_vote_keyword and not is_active:
        return

    # Gate 2: former federal members (e.g. Spanberger, now governor) — only
    # show federal vote context when user explicitly asks for their congressional
    # record. Active members always get their vote context.
    if person and not is_active and not explicit_federal:
        return

    conn = _connect("polls")
    if not conn:
        return

    members: list[sqlite3.Row] = []
    if terms and explicit_federal and _table_exists(conn, "congress_members"):
        member_terms = [
            term for term in terms
            if term not in {
                "vote", "votes", "voted", "voting", "campaign", "finance",
                "data", "correlation", "record", "records",
            }
        ]
        if member_terms:
            clause, params = _like_all_text_clause(["name"], member_terms[:3])
            try:
                members = conn.execute(f"""
                    SELECT bioguide_id, name, party, chamber, state, district
                    FROM congress_members
                    WHERE {clause}
                    LIMIT 5
                """, tuple(params)).fetchall()
            except Exception:
                members = []

    targets: list[dict] = []
    for row in members:
        targets.append({
            "name": row["name"],
            "bioguide_id": row["bioguide_id"],
            "party": row["party"] if "party" in row.keys() else "",
            "chamber": row["chamber"] if "chamber" in row.keys() else "",
            "state": row["state"] if "state" in row.keys() else "",
            "district": row["district"] if "district" in row.keys() else "",
            "office_note": "",
        })
    if person and not any(t["bioguide_id"] == person["bioguide_id"] for t in targets):
        targets.insert(0, {
            "name": person["name"],
            "bioguide_id": person["bioguide_id"],
            "party": "",
            "chamber": "",
            "state": "VA",
            "district": "",
            "office_note": person["office_note"],
        })
    if not targets:
        conn.close()
        return

    lines = ["[Database Context - polls federal vote lookup]"]
    lines.append("Source: Congress.gov / House Clerk roll-call tables when rows are present")
    lines.append(
        "chamber_label_rule=Every vote table must carry a chamber header: "
        "'Chamber: U.S. House of Representatives (119th Congress roll calls)' — "
        "never mix House and Senate votes in the same table"
    )

    try:
        for target in targets[:3]:
            bgid = target["bioguide_id"]
            lines.append(
                f"target_name={target['name']}; bioguide_id={bgid}"
                + (f"; chamber={target['chamber']}" if target.get("chamber") else "")
                + (f"; note={target['office_note']}" if target.get("office_note") else "")
            )

            total = 0
            if _table_exists(conn, "congress_votes"):
                rows = conn.execute(
                    """
                    SELECT
                        congress,
                        COUNT(*) AS total_votes,
                        SUM(CASE WHEN UPPER(member_vote) IN ('YEA', 'AYE', 'YES') THEN 1 ELSE 0 END) AS yea_votes,
                        SUM(CASE WHEN UPPER(member_vote) IN ('NAY', 'NO') THEN 1 ELSE 0 END) AS nay_votes,
                        SUM(CASE WHEN UPPER(member_vote) = 'NOT VOTING' THEN 1 ELSE 0 END) AS not_voting,
                        MIN(vote_date) AS first_vote_date,
                        MAX(vote_date) AS latest_vote_date
                    FROM congress_votes
                    WHERE bioguide_id = ?
                    GROUP BY congress
                    ORDER BY congress DESC
                    """,
                    (bgid,),
                ).fetchall()
                total += sum(int(row["total_votes"] or 0) for row in rows)
                for row in rows:
                    lines.append(f"- congress_votes summary: {_row_to_line(row)}")
                recent = conn.execute(
                    """
                    SELECT congress, vote_date, bill, question, member_vote, result,
                           vote_number, chamber
                    FROM congress_votes
                    WHERE bioguide_id = ?
                    ORDER BY vote_date DESC, vote_number DESC
                    LIMIT 10
                    """,
                    (bgid,),
                ).fetchall()
                for row in recent:
                    line = f"- congress_votes recent: {_row_to_line(row)}"
                    vnum = row["vote_number"]
                    ch = (row["chamber"] or "").lower()
                    vdate = (row["vote_date"] or "")[:4]
                    if vnum and vdate and "house" in ch:
                        try:
                            line += f"; source_url=https://clerk.house.gov/Votes/{vdate}/{int(vnum):04d}"
                        except (ValueError, TypeError):
                            pass
                    lines.append(line)
            else:
                lines.append("- congress_votes: table_missing")

            if _table_exists(conn, "federal_votes"):
                rows = conn.execute(
                    """
                    SELECT
                        congress,
                        COUNT(*) AS total_votes,
                        SUM(CASE WHEN UPPER(vote) IN ('YEA', 'AYE', 'YES') THEN 1 ELSE 0 END) AS yea_votes,
                        SUM(CASE WHEN UPPER(vote) IN ('NAY', 'NO') THEN 1 ELSE 0 END) AS nay_votes,
                        MIN(vote_date) AS first_vote_date,
                        MAX(vote_date) AS latest_vote_date
                    FROM federal_votes
                    WHERE bioguide_id = ?
                    GROUP BY congress
                    ORDER BY congress DESC
                    """,
                    (bgid,),
                ).fetchall()
                total += sum(int(row["total_votes"] or 0) for row in rows)
                for row in rows:
                    lines.append(f"- federal_votes summary: {_row_to_line(row)}")
            else:
                lines.append("- federal_votes: table_missing")

            if total == 0:
                lines.append(
                    f"lookup_status=zero_records; detail=no local federal vote rows found for bioguide_id={bgid}"
                )
        # ── Staleness check ───────────────────────────────────────────────────
        # Warn the LLM when the newest congress_votes row is older than 14 days
        # so it can qualify answers about recent floor behavior appropriately.
        # Nested try/except so a failure here never blocks context generation.
        if _table_exists(conn, "congress_votes"):
            try:
                from datetime import datetime as _dt, timezone as _tz
                _last = conn.execute(
                    "SELECT MAX(vote_date) AS last_vote FROM congress_votes"
                ).fetchone()
                if _last and _last["last_vote"]:
                    _last_str  = (_last["last_vote"] or "")[:10]
                    _last_date = _dt.fromisoformat(_last_str)
                    _now       = _dt.now(_tz.utc).replace(tzinfo=None)
                    _age_days  = (_now - _last_date).days
                    if _age_days > 14:
                        lines.append(
                            f"\n[DATA STALENESS WARNING] Most recent congress_votes "
                            f"entry is {_age_days} days old "
                            f"(last recorded vote: {_last_str}). "
                            f"Vote data may be outdated — the weekly ingestion job "
                            f"(scripts/ingest_votes_weekly.sh) may have been missed. "
                            f"Qualify any answer about recent floor votes accordingly."
                        )
            except Exception:
                pass  # best-effort; never block context generation

    except Exception as exc:
        lines.append(f"lookup_status=lookup_error; detail={type(exc).__name__}: {str(exc)[:240]}")
    finally:
        conn.close()

    blocks.append("\n".join(lines))


# ── shared VA federal member resolver ─────────────────────────────────────────
_FED_NAME_STOP = {
    "vote", "votes", "voted", "voting", "campaign", "finance", "data",
    "correlation", "record", "records", "floor", "statement", "statements",
    "speech", "speeches", "federal", "congress", "senate", "house",
    "virginia", "spent", "independent", "expenditure", "outside", "spending",
    "money", "paid", "super", "dark", "fund", "funded", "political",
    "action", "committee", "what", "when", "where", "which", "about",
    "with", "from", "that", "this", "have", "does", "bills", "bill",
}


def _resolve_federal_member(
    conn: sqlite3.Connection, terms: list[str]
) -> list[sqlite3.Row]:
    """Return congress_members rows matching name terms — shared by floor + IndyExp builders."""
    if not terms or not _table_exists(conn, "congress_members"):
        return []
    name_terms = [t for t in terms if t not in _FED_NAME_STOP and len(t) >= 4]
    if not name_terms:
        return []
    clause, params = _like_any_clause(["name"], name_terms[:4])
    try:
        return conn.execute(
            f"""SELECT bioguide_id, name, party, chamber, state, district
                FROM congress_members WHERE {clause} LIMIT 5""",
            tuple(params),
        ).fetchall()
    except Exception:
        return []


# ── floor statements ──────────────────────────────────────────────────────────
_FLOOR_STMT_TRIGGERS = re.compile(
    r"\b(floor\s+statement|congressional\s+record|spoke|speech|address(?:ed)?\s+congress|"
    r"floor\s+speech|said\s+on\s+(?:the\s+)?floor|remarks?\s+on)\b",
    re.I,
)


def _add_floor_statements_context(
    blocks: list[str], query: str, terms: list[str]
) -> None:
    """Inject congress_floor_statements for VA federal member + speech queries."""
    has_floor_trigger = bool(_FLOOR_STMT_TRIGGERS.search(query))

    conn = _connect("polls")
    if not conn:
        return
    if not _table_exists(conn, "congress_floor_statements"):
        conn.close()
        return

    try:
        members = _resolve_federal_member(conn, terms)

        if not members and not has_floor_trigger:
            return

        lines = ["[Database Context - congress_floor_statements]"]
        lines.append(
            "Source: Congressional Record (govinfo.gov) — floor speeches, remarks, extensions of remarks"
        )

        if members:
            for member in members[:2]:
                bgid = member["bioguide_id"]
                name = member["name"]

                # Build topic terms — strip the member's own name tokens
                name_tokens = {
                    tok.lower()
                    for part in name.replace(",", "").split()
                    for tok in [part.lower()]
                    if len(tok) >= 3
                }
                topic_terms = [
                    t for t in terms
                    if t not in name_tokens and t not in _FED_NAME_STOP
                ]

                if topic_terms:
                    kw_clause, kw_params = _like_any_clause(["title"], topic_terms[:4])
                    rows = conn.execute(
                        f"""SELECT member_name, statement_date, title, text
                            FROM congress_floor_statements
                            WHERE bioguide_id = ? AND {kw_clause}
                            ORDER BY statement_date DESC LIMIT 5""",
                        (bgid, *kw_params),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT member_name, statement_date, title, text
                           FROM congress_floor_statements
                           WHERE bioguide_id = ?
                           ORDER BY statement_date DESC LIMIT 5""",
                        (bgid,),
                    ).fetchall()

                total_count = conn.execute(
                    "SELECT COUNT(*) FROM congress_floor_statements WHERE bioguide_id = ?",
                    (bgid,),
                ).fetchone()[0]

                if rows:
                    lines.append(
                        f"\n{name} — {total_count:,} total floor statements on record; showing {len(rows)} most recent:"
                    )
                    for row in rows:
                        preview = (row["text"] or "")[:200].replace("\n", " ").strip()
                        lines.append(f"  [{row['statement_date']}] {row['title'][:120]}")
                        if preview:
                            lines.append(f"    preview: {preview}")
                else:
                    lines.append(
                        f"\n{name}: {total_count:,} statements on record; none matched topic keywords"
                    )

        else:
            # Floor trigger without named member — topic search across all VA members
            if not terms:
                return
            kw_clause, kw_params = _like_any_clause(["title"], terms[:5])
            rows = conn.execute(
                f"""SELECT member_name, statement_date, title, text
                    FROM congress_floor_statements
                    WHERE {kw_clause}
                    ORDER BY statement_date DESC LIMIT 8""",
                tuple(kw_params),
            ).fetchall()
            if not rows:
                return
            lines.append("Matching floor statements (all VA federal members):")
            for row in rows:
                preview = (row["text"] or "")[:150].replace("\n", " ").strip()
                lines.append(
                    f"  {row['member_name']} | {row['statement_date']} | {row['title'][:100]}"
                )
                if preview:
                    lines.append(f"    {preview}")

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


# ── FEC independent expenditures ──────────────────────────────────────────────
_INDY_EXP_TRIGGERS = re.compile(
    r"\b(independent\s+expenditure|outside\s+spending|super\s+pac|dark\s+money|"
    r"outside\s+money|pac\s+spent|who\s+paid|attack\s+ad|campaign\s+ad|"
    r"political\s+ad|funded\s+(?:by|against)|spent\s+against|spent\s+for)\b",
    re.I,
)


def _add_indy_exp_context(blocks: list[str], query: str, terms: list[str]) -> None:
    """Inject FEC independent expenditures for VA federal member or outside-spending queries."""
    has_indy_trigger = bool(_INDY_EXP_TRIGGERS.search(query))

    conn = _connect("polls")
    if not conn:
        return
    if not _table_exists(conn, "fec_independent_expenditures"):
        conn.close()
        return

    try:
        members = _resolve_federal_member(conn, terms)

        if not members and not has_indy_trigger:
            return

        lines = ["[Database Context - fec_independent_expenditures]"]
        lines.append(
            "Source: FEC — outside spending by Super PACs and outside groups for/against VA federal candidates"
        )
        lines.append(
            "Key: support_oppose=S = spent IN SUPPORT of candidate; O = spent AGAINST candidate"
        )

        if members:
            for member in members[:3]:
                bgid = member["bioguide_id"]
                name = member["name"]

                totals = conn.execute(
                    """SELECT
                           SUM(CASE WHEN support_oppose='S' THEN expenditure_amount ELSE 0 END) AS support_total,
                           SUM(CASE WHEN support_oppose='O' THEN expenditure_amount ELSE 0 END) AS oppose_total,
                           COUNT(*) AS txn_count,
                           MIN(cycle) AS earliest_cycle,
                           MAX(cycle) AS latest_cycle
                       FROM fec_independent_expenditures WHERE bioguide_id = ?""",
                    (bgid,),
                ).fetchone()

                if not totals or not totals["txn_count"]:
                    lines.append(f"\n{name}: no independent expenditure records in dataset")
                    continue

                lines.append(
                    f"\n{name} — outside spending summary "
                    f"(cycles {totals['earliest_cycle']}–{totals['latest_cycle']}):"
                )
                lines.append(f"  Spent IN SUPPORT: ${totals['support_total']:,.0f}")
                lines.append(f"  Spent AGAINST:    ${totals['oppose_total']:,.0f}")
                lines.append(f"  Transactions:     {totals['txn_count']}")

                top = conn.execute(
                    """SELECT committee_name, support_oppose,
                              MIN(committee_id) AS committee_id,
                              SUM(expenditure_amount) AS total, COUNT(*) AS cnt
                       FROM fec_independent_expenditures
                       WHERE bioguide_id = ?
                       GROUP BY committee_name, support_oppose
                       ORDER BY total DESC LIMIT 8""",
                    (bgid,),
                ).fetchall()

                if top:
                    short_name = name.split(",")[0]
                    lines.append("  Top outside spenders:")
                    for row in top:
                        direction = "FOR" if row["support_oppose"] == "S" else "AGAINST"
                        cid = row["committee_id"] or ""
                        fec_url = (
                            f" source_url=https://www.fec.gov/data/committee/{cid}/"
                            if cid else ""
                        )
                        lines.append(
                            f"    {direction} {short_name}: {row['committee_name']} "
                            f"— ${row['total']:,.0f} ({row['cnt']} txns){fec_url}"
                        )
        else:
            # General IndyExp query — leaderboard
            lines.append("Top VA federal independent-expenditure races (all cycles):")
            rows = conn.execute(
                """SELECT candidate_name, bioguide_id,
                          SUM(CASE WHEN support_oppose='S' THEN expenditure_amount ELSE 0 END) AS support_total,
                          SUM(CASE WHEN support_oppose='O' THEN expenditure_amount ELSE 0 END) AS oppose_total,
                          COUNT(*) AS txn_count
                   FROM fec_independent_expenditures
                   GROUP BY bioguide_id
                   ORDER BY (support_total + oppose_total) DESC LIMIT 10"""
            ).fetchall()
            for row in rows:
                total_outside = row["support_total"] + row["oppose_total"]
                lines.append(
                    f"  {row['candidate_name']}: FOR=${row['support_total']:,.0f}  "
                    f"AGAINST=${row['oppose_total']:,.0f}  total_recorded_outside=${total_outside:,.0f}"
                )

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


# ── FEC individual contributions — employer rollup ───────────────────────────

_FEC_EMPLOYER_TRIGGERS = re.compile(
    r"\b(top\s+(?:donors?|contributors?|employers?)|who\s+(?:funds?|gave|donated)|"
    r"employer.tagged|individual\s+contributions?|fec\s+donors?|"
    r"contributions?\s+(?:from|by)\s+employer)\b",
    re.I,
)


def _add_fec_employer_context(blocks: list[str], query: str, terms: list[str]) -> None:
    """Inject FEC individual contributions aggregated by employer for federal member queries."""
    if not _FEC_EMPLOYER_TRIGGERS.search(query):
        return

    conn = _connect("polls")
    if not conn:
        return
    if not _table_exists(conn, "fec_individual_contributions"):
        conn.close()
        return

    try:
        members = _resolve_federal_member(conn, terms)
        if not members:
            return

        for member in members[:2]:
            bgid = member["bioguide_id"]
            name = member["name"]

            rows = conn.execute(
                """
                SELECT
                    MIN(contributor_employer) AS employer,
                    MAX(COALESCE(employer_sector, 'Unclassified')) AS sector,
                    COUNT(*) AS contribution_count,
                    ROUND(SUM(amount), 0) AS total_amount,
                    MAX(cycle) AS latest_cycle
                FROM fec_individual_contributions
                WHERE bioguide_id = ?
                  AND amount > 0
                  AND contributor_employer IS NOT NULL
                  AND TRIM(UPPER(contributor_employer)) NOT IN
                      ('', 'N/A', 'NONE', 'SELF', 'RETIRED', 'NOT EMPLOYED',
                       'SELF-EMPLOYED', 'HOMEMAKER', 'STUDENT')
                GROUP BY LOWER(TRIM(contributor_employer))
                ORDER BY total_amount DESC
                LIMIT 15
                """,
                (bgid,),
            ).fetchall()

            if not rows:
                continue

            # Grand total: all positive-amount individual contributions for this
            # member, regardless of employer filtering, so the sector table has
            # an unambiguous denominator.
            total_row = conn.execute(
                "SELECT ROUND(SUM(amount), 0) AS grand_total, COUNT(*) AS n "
                "FROM fec_individual_contributions WHERE bioguide_id = ? AND amount > 0",
                (bgid,),
            ).fetchone()
            grand_total = total_row["grand_total"] if total_row else None
            grand_n     = total_row["n"]           if total_row else None

            lines = [
                f"[Database Context - fec_individual_contributions employer rollup]",
                f"target={name}; bioguide_id={bgid}",
                "Source: FEC Schedule A individual contributions (fec.gov)",
                "Sector classification is derived from VoteIQ taxonomy applied to FEC employer "
                "fields and bill subject metadata. This is not official federal categorization.",
                "note=Multiple employer-tagged entries may aggregate to the same parent entity. "
                "Employer strings are self-reported and not normalized — slight name variations "
                "(e.g. punctuation, abbreviations) produce separate rows. Treat totals as approximate.",
                "aggregation=GROUP BY lower(trim(contributor_employer)); amount=SUM",
            ]
            if grand_total is not None:
                lines.append(
                    f"Total individual contributions: ${grand_total:,.0f} ({grand_n:,} records) — "
                    "includes all itemized contributions across classified sectors, "
                    "Retired/Individual, and Other/Unknown categories before sector mapping. "
                    "Table below shows top 15 classified employers only and will not sum to this total."
                )
            lines += [
                "",
                "| Rank | Employer | Sector | Contributors | Total ($) | Latest Cycle |",
                "|---|---|---|---:|---:|---:|",
            ]
            for i, row in enumerate(rows, 1):
                lines.append(
                    f"| {i} | {row['employer']} | {row['sector']} "
                    f"| {row['contribution_count']} | ${row['total_amount']:,.0f} "
                    f"| {row['latest_cycle']} |"
                )
            blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


# ── PAC-vs-vote correlation ────────────────────────────────────────────────────

_CAUSATION_BANNED = re.compile(
    r"\b("
    r"voted because"                                            # "voted because of donations"
    r"|vote was because|voting because"
    r"|in exchange for|quid pro quo"
    r"|bought (?:the |her |his |their )?(?:\w+ )?vote"
    r"|influenced (?:by (?:pac|money|donor|spending|contribution)|(?:her|his|their|the) vote)"
    r"|as a result of (?:pac|money|donor|spending|donation)"
    r"|due to (?:pac|money|donor|spending|donation)"
    r"|caused by (?:pac|money|donor|spending)"
    r"|vote(?:d|s)? (?:in exchange|in return) for"
    r"|paid (?:for|off) (?:by|with|her|his|their|the)"         # "paid off her vote"
    r"|reward(?:ed|s)? (?:by|with) (?:pac|money|donor)"
    r"|pressure(?:d|s)? by (?:pac|donor)"
    r"|because of (?:pac|money|donor|spending|donation|contribution|funding)"  # "because of PAC funding"
    r")\b",
    re.I,
)


def _check_banned_causation_phrases(text: str) -> str | None:
    """Return the first causation-implying phrase found in *text*, or None if clean.

    Used to detect when LLM output may have crossed from neutral juxtaposition
    into asserting that PAC spending caused, bought, or influenced a vote.
    Call this on the LLM response before returning it to the user.
    """
    m = _CAUSATION_BANNED.search(text or "")
    return m.group(0) if m else None


# Patterns that indicate the model violated the sponsorship reporting rules injected
# by _add_sponsorship_summary_context. Two known failure modes from the Feggans case:
#
# 1. "0 failed" / "zero bills failed" — always wrong when a legislator has died bills;
#    the status_label is 'Introduced', not 'Failed', so a naive model reads 0.
# 2. Implausibly large single-session VA state sponsored count (>100). A VA delegate
#    realistically chief-patrons 15–80 items per session; 100+ signals co-patron conflation.
_SPONSOR_ZERO_FAILED = re.compile(
    r"\b(?:zero|0)\s+(?:bills?\s+)?(?:fail(?:ed)?|unsuccessful)\b",
    re.I,
)
_SPONSOR_INFLATED_COUNT = re.compile(
    r"\b(?:"
    r"([1-9]\d{2,})\s+bills?\s+(?:sponsored|introduced|patroned?)"  # "133 bills sponsored"
    r"|(?:sponsored|introduced|patroned?)\s+([1-9]\d{2,})\s+bills?"  # "sponsored 133 bills"
    r")\b",
    re.I,
)
# Presence of sponsorship language in the reply (guard only fires when on-topic).
_SPONSOR_CONTEXT_SIGNAL = re.compile(
    r"\b(?:sponsor(?:ed|ship)?|patron(?:ed)?|introduced|chief\s+patron|"
    r"bills?\s+(?:they\s+)?(?:sponsor|introduc|patron))\b",
    re.I,
)

_SPONSOR_WARNING = (
    "\n\n> **Data integrity note:** VoteIQ detected a sponsorship claim in this "
    "answer that may reflect a known counting issue — specifically, bills a "
    "legislator *co-patroned* (added their name to another member's bill) can be "
    "miscounted as bills they *sponsored*, and bills that died in committee without a "
    "floor vote are sometimes reported as '0 failed' because their status is "
    "'Introduced', not 'Failed'. For authoritative counts, check "
    "[lis.virginia.gov](https://lis.virginia.gov) or ask VoteIQ: "
    "\"What bills did [name] introduce in 2026?\""
)


def check_sponsorship_claims(reply: str) -> str:
    """Post-render guard: detect known sponsorship miscount signatures and append a
    correction note if found. Non-destructive — original text is never altered.

    Catches two failure modes documented in the Feggans verification (2026-06-21):
    - '0 failed' when bills actually died in committee (status='Introduced', not 'Failed')
    - Counts > 100 bills sponsored in a single VA session (signals co-patron conflation)

    Call this inside _with_source_line so it covers all chat endpoints uniformly.
    """
    text = reply or ""
    if not _SPONSOR_CONTEXT_SIGNAL.search(text):
        return text  # not a sponsorship answer — skip entirely

    triggered = (
        _SPONSOR_ZERO_FAILED.search(text) is not None
        or _SPONSOR_INFLATED_COUNT.search(text) is not None
    )
    if not triggered:
        return text

    # Don't double-append if the warning is already present (e.g. cached reply).
    if "Data integrity note:" in text:
        return text

    return text.rstrip() + _SPONSOR_WARNING


_PAC_VOTE_PAC_SIGNAL = {
    "pac", "pacs", "fund", "funded", "funds", "money", "outside", "donor",
    "donors", "contribut", "contributes", "contributed", "spend", "spent",
    "spending", "back", "backs", "backed", "interest", "industry", "support",
    "supported", "oppose", "opposed",
}
_PAC_VOTE_VOTE_SIGNAL = {
    "vote", "votes", "voted", "voting", "rollcall", "roll", "position",
    "record", "records", "support", "oppose", "yea", "nay", "yes", "pass",
}
# Words to strip when isolating topic terms (member resolution + generic political words)
_PAC_VOTE_TOPIC_STRIP = _FED_NAME_STOP | {
    "pac", "pacs", "fund", "funded", "funds", "money", "outside", "donor",
    "donors", "contribut", "spend", "spent", "spending", "back", "backed",
    "backs", "interest", "vote", "votes", "voted", "voting", "roll", "call",
    "position", "record", "records", "yea", "nay", "pass", "fail", "align",
    "alignment", "correlat", "match", "with", "their",
}

# Maps common user topic words → congress_bills.policy_area substrings so that
# "defense" matches "Armed Forces and National Security", etc.
_POLICY_AREA_ALIASES: dict[str, tuple[str, ...]] = {
    "defense":     ("defense", "armed forces", "national security", "military"),
    "healthcare":  ("health", "medicare", "medicaid"),
    "health":      ("health", "medicare", "medicaid"),
    "climate":     ("climate", "environment", "environmental"),
    "energy":      ("energy", "environment"),
    "gun":         ("gun", "firearm", "arms"),
    "guns":        ("gun", "firearm", "arms"),
    "immigration": ("immigration", "border", "asylum"),
    "taxes":       ("tax", "taxation", "fiscal"),
    "housing":     ("housing", "mortgage"),
    "trade":       ("trade", "tariff", "commerce"),
    "veterans":    ("veterans", "armed forces"),
    "education":   ("education", "school", "student"),
    "labor":       ("labor", "employment", "worker"),
    "finance":     ("finance", "banking", "financial"),
    "agriculture": ("agriculture", "farm"),
}


def _add_pac_vote_correlation_context(
    blocks: list[str], query: str, terms: list[str]
) -> None:
    """Inject a PAC-spending-vs-vote-record correlation block for a resolved VA
    federal member.  Fires only when both a PAC/money signal *and* a vote signal
    are present so it does not duplicate the individual _add_pac_context /
    _add_indy_exp_context blocks on unrelated queries.

    Output layout
    -------------
    For each resolved member (up to 2):
      • PAC spending summary grouped by committee + direction (FOR/AGAINST),
        with ideology from pac_ideology when available.
      • Topic-filtered vote records from congress_votes.question (primary) or
        federal_votes JOIN federal_bills (fallback), limited to 12 rows.
      • If no topic terms survive filtering, falls back to 8 most-recent votes
        so the LLM still has something to correlate against.
    """
    q_lower = (query or "").lower()

    # Gate 1 — need at least one PAC/money word
    if not any(kw in q_lower for kw in _PAC_VOTE_PAC_SIGNAL):
        return
    # Gate 2 — need at least one vote word
    if not any(kw in q_lower for kw in _PAC_VOTE_VOTE_SIGNAL):
        return

    conn = _connect("polls")
    if not conn:
        return

    members = _resolve_federal_member(conn, terms)
    if not members:
        conn.close()
        return

    # Derive topic terms: strip member name tokens + generic pac/vote words
    member_name_tokens: set[str] = set()
    for m in members:
        for tok in re.split(r"[\s,]+", (m["name"] or "").lower()):
            if len(tok) >= 3:
                member_name_tokens.add(tok)

    topic_terms = [
        t for t in terms
        if t not in _PAC_VOTE_TOPIC_STRIP
        and t not in member_name_tokens
        and len(t) >= 4
    ]

    lines = ["[Database Context - PAC vs. Vote Correlation]"]
    lines.append(
        "Source: fec_independent_expenditures (PAC outside spending) joined with "
        "congress_votes / federal_votes (member vote record)."
    )
    lines.append(
        "How to read: present PAC spending (direction, ideology, dollar totals) and "
        "the member's vote record (YES/NO on specific bills) as parallel factual "
        "records — a factual timeline only. "
        "INFERENCE RULE: Do NOT assert, imply, or suggest that PAC spending caused, "
        "influenced, bought, rewarded, or correlated with any vote. "
        "Never use causal connectors between money and votes. "
        "Report only what each record states, side by side, with no causal claim."
    )

    try:
        for member in members[:2]:
            bgid  = member["bioguide_id"]
            mname = member["name"]
            lines.append(f"\n=== {mname}  (bioguide_id={bgid}) ===")

            # ── 1. PAC spending on this member ───────────────────────────────
            pac_rows = conn.execute(
                """
                SELECT ie.committee_name,
                       ie.support_oppose,
                       COALESCE(pi.ideology, pi2.ideology)   AS ideology,
                       COALESCE(pi.alignment, pi2.alignment) AS alignment,
                       SUM(ie.expenditure_amount)            AS total,
                       COUNT(*)                              AS txns,
                       MIN(ie.cycle)                         AS earliest,
                       MAX(ie.cycle)                         AS latest
                FROM fec_independent_expenditures ie
                LEFT JOIN pac_ideology pi
                    ON upper(trim(ie.committee_name)) = upper(trim(pi.committee_name))
                LEFT JOIN pac_ideology pi2
                    ON pi.id IS NULL
                   AND upper(trim(pi2.short_name)) = upper(trim(ie.committee_name))
                WHERE ie.bioguide_id = ?
                GROUP BY ie.committee_name, ie.support_oppose
                ORDER BY total DESC
                LIMIT 12
                """,
                (bgid,),
            ).fetchall()

            if not pac_rows:
                lines.append("  No PAC spending records found — skipping correlation.")
                continue

            lines.append("  Outside-spending PACs (all cycles):")
            for r in pac_rows:
                direction = "FOR" if r["support_oppose"] == "S" else "AGAINST"
                ideo = (
                    f"  [{r['ideology']} / {r['alignment']}]"
                    if r["ideology"] else ""
                )
                lines.append(
                    f"    {direction} {mname.split(',')[0]}: "
                    f"{r['committee_name']}{ideo} "
                    f"— ${r['total']:,.0f}  "
                    f"(cycles {r['earliest']}–{r['latest']}, {r['txns']} txn{'s' if r['txns'] != 1 else ''})"
                )

            # ── 2. Topic-filtered vote record ─────────────────────────────────
            voted_rows: list = []
            topic_label = " + ".join(topic_terms[:3]) if topic_terms else ""

            # Expand topic terms through policy_area alias map so that
            # e.g. "defense" also matches "Armed Forces and National Security"
            expanded_terms: list[str] = []
            _seen_exp: set[str] = set()
            for t in topic_terms[:4]:
                for alias in _POLICY_AREA_ALIASES.get(t, (t,)):
                    if alias not in _seen_exp:
                        _seen_exp.add(alias)
                        expanded_terms.append(alias)

            # Path 1: congress_votes JOIN congress_bills via bill-number normalisation.
            # congress_bills.bill_type + bill_number normalise to the same format as
            # congress_votes.bill (e.g. "H R 2670" → upper(replace→' ','')) = "HR2670"
            # = upper("hr"||"2670")).  Only fires when congress_bills has a match for
            # the bill (VA-sponsored legislation); otherwise falls through.
            if expanded_terms and _table_exists(conn, "congress_votes") and _table_exists(conn, "congress_bills"):
                topic_clause, topic_params = _like_any_clause(
                    ["cb.title", "cb.policy_area"], expanded_terms
                )
                voted_rows = conn.execute(
                    f"""
                    SELECT cv.vote_date,
                           cv.bill,
                           COALESCE(cb.title, cv.question) AS question,
                           cv.member_vote,
                           cv.result,
                           cv.congress,
                           cb.policy_area
                    FROM congress_votes cv
                    JOIN congress_bills cb
                      ON upper(replace(cv.bill, ' ', '')) = upper(cb.bill_type || cb.bill_number)
                     AND cb.bill_number != ''
                     AND cb.bill_type   != ''
                    WHERE cv.bioguide_id = ?
                      AND ({topic_clause})
                    ORDER BY cv.vote_date DESC
                    LIMIT 12
                    """,
                    [bgid, *topic_params],
                ).fetchall()

            # Path 2: congress_votes.question LIKE — works for procedural votes whose
            # question text names the bill (e.g. "On Passage: National Defense Authoriz…")
            if not voted_rows and expanded_terms and _table_exists(conn, "congress_votes"):
                topic_clause, topic_params = _like_any_clause(
                    ["cv.question", "cv.bill"], expanded_terms
                )
                voted_rows = conn.execute(
                    f"""
                    SELECT cv.vote_date,
                           cv.bill,
                           cv.question,
                           cv.member_vote,
                           cv.result,
                           cv.congress,
                           NULL AS policy_area
                    FROM congress_votes cv
                    WHERE cv.bioguide_id = ?
                      AND ({topic_clause})
                    ORDER BY cv.vote_date DESC
                    LIMIT 12
                    """,
                    [bgid, *topic_params],
                ).fetchall()

            # Path 3: federal_votes JOIN federal_bills via the same bill-number
            # normalisation.  Excludes blank bill_type/bill_number rows that cause
            # false-positive matches.  federal_bills has subjects + policy_area.
            if not voted_rows and expanded_terms and _table_exists(conn, "federal_votes") and _table_exists(conn, "federal_bills"):
                topic_clause, topic_params = _like_any_clause(
                    ["fb.title", "fb.policy_area", "fb.subjects"], expanded_terms
                )
                voted_rows = conn.execute(
                    f"""
                    SELECT fv.vote_date,
                           fv.bill_number                     AS bill,
                           COALESCE(fb.title, fv.bill_number) AS question,
                           fv.vote                            AS member_vote,
                           NULL                               AS result,
                           fv.congress,
                           fb.policy_area
                    FROM federal_votes fv
                    JOIN federal_bills fb
                      ON upper(replace(fv.bill_number, ' ', '')) = upper(fb.bill_type || fb.bill_number)
                     AND fb.bill_number != ''
                     AND fb.bill_type   != ''
                    WHERE fv.bioguide_id = ?
                      AND ({topic_clause})
                    ORDER BY fv.vote_date DESC
                    LIMIT 12
                    """,
                    [bgid, *topic_params],
                ).fetchall()

            # Last-resort: most recent votes without topic filter.  Note in the label
            # that the bill databases only index VA-sponsored bills, so major
            # legislation like the NDAA may simply not be present.
            if not voted_rows and _table_exists(conn, "congress_votes"):
                voted_rows = conn.execute(
                    """
                    SELECT vote_date, bill, question, member_vote, result, congress,
                           NULL AS policy_area
                    FROM congress_votes
                    WHERE bioguide_id = ?
                    ORDER BY vote_date DESC
                    LIMIT 8
                    """,
                    (bgid,),
                ).fetchall()
                topic_label = (
                    (
                        f"no match for '{' + '.join(topic_terms[:2])}' "
                        f"(bill index covers VA-sponsored legislation only; "
                        f"major bills like NDAA may be absent) — latest 8 votes shown"
                    )
                    if topic_terms
                    else "latest 8 votes"
                )

            if voted_rows:
                filter_note = (
                    f"topic filter: '{topic_label}'"
                    if topic_label
                    else "no topic filter applied"
                )
                lines.append(f"\n  Vote record ({filter_note}):")
                for r in voted_rows:
                    q_text = (r["question"] or "")[:85].rstrip()
                    pa = (
                        f" [{r['policy_area']}]"
                        if r["policy_area"]
                        else ""
                    )
                    lines.append(
                        f"    {r['vote_date']} | {r['bill']} | "
                        f"{r['member_vote']}{pa} | {q_text}"
                    )
            else:
                lines.append(
                    f"\n  No vote records found in congress_votes / federal_votes "
                    f"for bioguide_id={bgid}"
                )

    except Exception as exc:
        lines.append(
            f"  correlation_error={type(exc).__name__}: {str(exc)[:240]}"
        )
    finally:
        conn.close()

    # Only append if we produced substantive output (more than the 3-line header)
    if len(lines) > 4:
        blocks.append("\n".join(lines))


# ── Federal bill context ───────────────────────────────────────────────────────
_FED_BILL_RE = re.compile(
    r"\b("
    r"H\.?J\.?Con\.?Res\.?|H\.?Con\.?Res\.?"   # HConRes (check before HRes)
    r"|H\.?J\.?Res\.?"                            # HJRes
    r"|S\.?J\.?Res\.?"                            # SJRes
    r"|H\.?Res\.?"                                # HRes
    r"|S\.?Res\.?"                                # SRes
    r"|H\.?R\.?"                                  # HR / H.R.
    r"|S(?=\.?\s*\d)"                             # S / S. followed by digit
    r")\s*\.?\s*(\d{1,5})\b",
    re.I,
)

_FED_BILL_TYPE_NORM: dict[str, str] = {
    "hr": "hr", "h.r.": "hr", "h.r": "hr",
    "hjres": "hjres", "h.j.res.": "hjres", "h.j.res": "hjres",
    "hconres": "hconres", "h.con.res.": "hconres", "hjconres": "hconres",
    "hres": "hres", "h.res.": "hres", "h.res": "hres",
    "sjres": "sjres", "s.j.res.": "sjres", "s.j.res": "sjres",
    "sres": "sres", "s.res.": "sres", "s.res": "sres",
    "s": "s",
}

_IS_FED_BILL_Q = re.compile(
    r"\b(federal\s+bill|congress(?:ional)?\s+bill|senate\s+bill|house\s+bill"
    r"|H\.R\.\s*\d|S\.\s*\d|HRes\s+\d|SRes\s+\d|HJRes\s+\d|SJRes\s+\d"
    r"|what\s+(?:is|does|did)\s+(?:H\.?R\.?|S\.?|HRes|SRes|HJRes|SJRes)\s*\d"
    r"|explain\s+(?:this\s+)?(?:federal|H\.?R\.?|S\.?)\s*\d)",
    re.I,
)


def _norm_fed_bill_type(prefix: str) -> str:
    p = re.sub(r"\s+", "", prefix.lower())
    return _FED_BILL_TYPE_NORM.get(p, p.replace(".", ""))


def _fmt_fed_bill_id(bill_type: str, bill_number: str) -> str:
    fmt = {
        "hr": f"H.R. {bill_number}",
        "s": f"S. {bill_number}",
        "hres": f"H.Res. {bill_number}",
        "sres": f"S.Res. {bill_number}",
        "hjres": f"H.J.Res. {bill_number}",
        "sjres": f"S.J.Res. {bill_number}",
        "hconres": f"H.Con.Res. {bill_number}",
    }
    return fmt.get((bill_type or "").lower(), f"{(bill_type or '').upper()} {bill_number}")


def _fed_bill_refs(query: str) -> list[tuple[str, str]]:
    """Return (bill_type, bill_number) pairs for federal bill references in query."""
    seen: set[tuple[str, str]] = set()
    results: list[tuple[str, str]] = []
    for m in _FED_BILL_RE.finditer(query or ""):
        bt = _norm_fed_bill_type(m.group(1))
        bn = m.group(2)
        key = (bt, bn)
        if key not in seen:
            seen.add(key)
            results.append(key)
    return results


def _add_federal_bill_context(
    blocks: list[str], query: str, terms: list[str]
) -> None:
    """Inject congress_bills + congress_bill_texts context for federal bill queries."""
    refs = _fed_bill_refs(query)
    is_fed_bill_q = bool(_IS_FED_BILL_Q.search(query))

    if not refs and not is_fed_bill_q:
        return

    conn = _connect("polls")
    if not conn:
        return
    if not _table_exists(conn, "congress_bills"):
        conn.close()
        return

    try:
        lines = ["[Database Context - congress_bills — federal bill lookup]"]
        lines.append(
            "Source: Congress.gov API — bills sponsored or cosponsored by VA federal members (119th Congress)"
        )
        found_any = False

        if refs:
            for bill_type, bill_number in refs[:3]:
                # Exact bill_type + bill_number match
                rows = conn.execute(
                    """SELECT cb.bill_type, cb.bill_number, cb.title, cb.introduced_date,
                              cb.latest_action, cb.latest_action_date, cb.policy_area, cb.role,
                              cm.name AS sponsor_name, cm.party AS sponsor_party,
                              cm.state AS sponsor_state
                       FROM congress_bills cb
                       LEFT JOIN congress_members cm ON cm.bioguide_id = cb.sponsor_id
                       WHERE lower(cb.bill_type) = ? AND cb.bill_number = ?
                       ORDER BY cb.introduced_date DESC LIMIT 3""",
                    (bill_type, bill_number),
                ).fetchall()

                if not rows:
                    # Fallback: number-only match across all bill types
                    rows = conn.execute(
                        """SELECT cb.bill_type, cb.bill_number, cb.title, cb.introduced_date,
                                  cb.latest_action, cb.latest_action_date, cb.policy_area, cb.role,
                                  cm.name AS sponsor_name, cm.party AS sponsor_party,
                                  cm.state AS sponsor_state
                           FROM congress_bills cb
                           LEFT JOIN congress_members cm ON cm.bioguide_id = cb.sponsor_id
                           WHERE cb.bill_number = ?
                           ORDER BY cb.introduced_date DESC LIMIT 3""",
                        (bill_number,),
                    ).fetchall()

                if rows:
                    for row in rows:
                        found_any = True
                        bt = (row["bill_type"] or "").lower()
                        bn = row["bill_number"]
                        display_id = _fmt_fed_bill_id(bt, bn)
                        lines.append(f"\n{display_id} — {row['title']}")
                        if row["sponsor_name"]:
                            pa = (
                                "R" if "Republican" in (row["sponsor_party"] or "") else
                                "D" if "Democrat" in (row["sponsor_party"] or "") else "?"
                            )
                            lines.append(
                                f"  Sponsor: {row['sponsor_name']} ({pa}-{row['sponsor_state'] or 'VA'})"
                                f" | role: {row['role'] or 'sponsor'}"
                            )
                        if row["introduced_date"]:
                            lines.append(f"  Introduced: {row['introduced_date']}")
                        if row["policy_area"]:
                            lines.append(f"  Policy area: {row['policy_area']}")
                        if row["latest_action"]:
                            lines.append(
                                f"  Latest action ({row['latest_action_date'] or '?'}): "
                                f"{row['latest_action'][:180]}"
                            )
                        # Text excerpt
                        if _table_exists(conn, "congress_bill_texts"):
                            txt_row = conn.execute(
                                """SELECT text FROM congress_bill_texts
                                   WHERE lower(bill_type) = ? AND bill_number = ?
                                   ORDER BY version_date DESC LIMIT 1""",
                                (bt, bn),
                            ).fetchone()
                            if txt_row and txt_row["text"]:
                                preview = (txt_row["text"] or "")[:300].replace("\n", " ").strip()
                                lines.append(f"  Text preview: {preview}")
                else:
                    lines.append(
                        f"\n{_fmt_fed_bill_id(bill_type, bill_number)}: "
                        f"not found in local dataset — may not be sponsored by a VA member"
                    )
                    found_any = True  # still emit the block so LLM can explain the gap

        else:
            # Keyword title search
            bill_stop = {
                "federal", "bill", "congress", "congressional", "senate", "house",
                "what", "does", "happened", "explain", "tell", "about",
            }
            search_terms = [t for t in terms if t not in bill_stop][:4]
            if search_terms:
                clause, params = _like_any_clause(["cb.title", "cb.policy_area"], search_terms)
                rows = conn.execute(
                    f"""SELECT cb.bill_type, cb.bill_number, cb.title, cb.introduced_date,
                               cb.latest_action, cb.latest_action_date, cb.policy_area,
                               cm.name AS sponsor_name
                        FROM congress_bills cb
                        LEFT JOIN congress_members cm ON cm.bioguide_id = cb.sponsor_id
                        WHERE {clause}
                        ORDER BY cb.introduced_date DESC LIMIT 6""",
                    tuple(params),
                ).fetchall()
                if rows:
                    found_any = True
                    lines.append("Matching federal bills (keyword search):")
                    for row in rows:
                        display_id = _fmt_fed_bill_id(row["bill_type"], row["bill_number"])
                        lines.append(
                            f"  {display_id} — {row['title'][:100]}"
                            + (f" | {row['sponsor_name']}" if row["sponsor_name"] else "")
                            + (f" | {row['latest_action'][:60]}" if row["latest_action"] else "")
                        )

        if not found_any:
            return

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


def _add_governor_action_context(blocks: list[str], query: str, terms: list[str], session: str) -> None:
    q_lower = (query or "").lower()
    # A race/election question is about the candidates, not the incumbent's
    # record — never inject the governor's signing/veto record there unless
    # the governor is explicitly named. ("who is running for governor" should
    # not surface Spanberger's legislative record.)
    _names_governor = "spanberger" in q_lower or "abigail" in q_lower
    _race_query = any(t in q_lower for t in (
        "running", "run for", "race", "candidate", "candidates",
        "election", "primary", "ballot", "challenger", "opponent",
    ))
    if _race_query and not _names_governor:
        return
    has_governor_action_term = any(
        term in q_lower
        for term in ("governor", "veto", "vetoed", "vetoes", "signed", "amended")
    )
    if _known_federal_person(query) and _is_explicit_federal_vote_query(query) and not has_governor_action_term:
        return
    if not any(term in q_lower for term in ("governor", "spanberger", "veto", "vetoed", "vetoes", "signed", "amended")):
        return

    conn = _connect("polls")
    if not conn:
        _append_governor_action_lookup_block(
            blocks,
            "lookup_error",
            detail="polls.db unavailable",
        )
        return
    if not _table_exists(conn, "governor_actions"):
        conn.close()
        _append_governor_action_lookup_block(
            blocks,
            "table_missing",
            detail="polls.governor_actions table not found",
        )
        return

    action_terms = [
        term for term in terms
        if term not in {"public", "record", "records", "search", "source", "sources"}
    ]
    if "veto" in q_lower:
        action_terms.append("veto")
    if "sign" in q_lower:
        action_terms.append("sign")
    if "amend" in q_lower:
        action_terms.append("amend")
    if not action_terms:
        action_terms = ["spanberger", "governor"]

    columns = _table_columns(conn, "governor_actions")
    select_candidates = [
        "bill_number", "bill_id", "session", "title", "bill_title", "description",
        "summary", "action_label", "action", "action_type", "raw_status", "status",
        "action_status", "action_date", "date", "effective_date", "governor",
        "sponsor_name", "source_url", "url",
    ]
    search_candidates = [
        "governor", "action_label", "action", "action_type", "raw_status", "status",
        "action_status", "title", "bill_title", "description", "summary",
        "bill_number", "bill_id", "action_date", "date", "effective_date",
    ]
    select_cols = [col for col in select_candidates if col in columns]
    search_cols = [col for col in search_candidates if col in columns]
    if not select_cols or not search_cols:
        conn.close()
        _append_governor_action_lookup_block(
            blocks,
            "schema_mismatch",
            detail="governor_actions has no usable select/search columns",
            columns=sorted(columns),
            search_columns=search_cols,
        )
        return

    clause, params = _like_any_text_clause(search_cols, action_terms[:6])
    where_parts = [f"({clause})"]
    query_params: list[str] = list(params)
    if "session" in columns:
        where_parts.insert(0, f"{_quote_identifier('session')}=?")
        query_params.insert(0, session)
    order_col = "action_date" if "action_date" in columns else "date" if "date" in columns else select_cols[0]
    quoted_select = ", ".join(_quote_identifier(col) for col in select_cols)
    action_summary: dict[str, int] = {}
    try:
        rows = conn.execute(f"""
            SELECT {quoted_select}
            FROM governor_actions
            WHERE {" AND ".join(where_parts)}
            ORDER BY {_quote_identifier(order_col)} DESC
            LIMIT 40
        """, tuple(query_params)).fetchall()

        # If the 40-row sample has no signed bills, explicitly fetch a signed sample
        # so the AI sees real rows for each action type, not just recent vetoes
        signed_col = next((c for c in ("action", "action_label", "action_type", "raw_status", "status", "action_status") if c in columns), None)
        if signed_col and not any("sign" in str(r[signed_col]).lower() for r in rows):
            try:
                signed_sample = conn.execute(f"""
                    SELECT {quoted_select}
                    FROM governor_actions
                    WHERE lower(CAST({_quote_identifier(signed_col)} AS TEXT)) LIKE '%sign%'
                      AND {_quote_identifier('session') if 'session' in columns else '1'}={'?' if 'session' in columns else '1'}
                    ORDER BY {_quote_identifier(order_col)} DESC
                    LIMIT 5
                """, (session,) if "session" in columns else ()).fetchall()
                rows = list(rows) + list(signed_sample)
            except Exception:
                pass

        if not rows and "veto" in q_lower:
            veto_cols = [
                col for col in (
                    "action_label", "action", "action_type", "raw_status", "status",
                    "action_status", "title", "bill_title", "description", "summary",
                )
                if col in columns
            ]
            if veto_cols:
                veto_clause, veto_params = _like_any_text_clause(veto_cols, ["veto"])
                rows = conn.execute(f"""
                    SELECT {quoted_select}
                    FROM governor_actions
                    WHERE {veto_clause}
                    ORDER BY {_quote_identifier(order_col)} DESC
                    LIMIT 40
                """, tuple(veto_params)).fetchall()
    except Exception as exc:
        conn.close()
        _append_governor_action_lookup_block(
            blocks,
            "lookup_error",
            detail=f"{type(exc).__name__}: {str(exc)[:240]}",
            columns=sorted(columns),
            search_columns=search_cols,
        )
        return

    summary_col = next(
        (
            col for col in (
                "action", "action_label", "action_type", "raw_status",
                "status", "action_status",
            )
            if col in columns
        ),
        None,
    )
    if summary_col:
        try:
            summary_where: list[str] = []
            summary_params: list[str] = []
            if "session" in columns:
                summary_where.append(f"{_quote_identifier('session')}=?")
                summary_params.append(session)
            if "governor" in columns and any(term in q_lower for term in ("spanberger", "governor")):
                summary_where.append("lower(CAST(governor AS TEXT)) LIKE ?")
                summary_params.append("%spanberger%")
            where_sql = f"WHERE {' AND '.join(summary_where)}" if summary_where else ""
            for summary_row in conn.execute(f"""
                SELECT CAST({_quote_identifier(summary_col)} AS TEXT) AS action_value,
                       COUNT(*) AS count
                FROM governor_actions
                {where_sql}
                GROUP BY CAST({_quote_identifier(summary_col)} AS TEXT)
                ORDER BY count DESC
            """, tuple(summary_params)).fetchall():
                key = str(summary_row["action_value"] or "unknown").strip() or "unknown"
                action_summary[key] = int(summary_row["count"] or 0)
        except Exception:
            action_summary = {}

    conn.close()
    _append_governor_action_lookup_block(
        blocks,
        "records_found" if rows else "zero_records",
        rows=rows,
        action_summary=action_summary,
        columns=sorted(columns),
        search_columns=search_cols,
        detail=f"searched {len(search_cols)} column(s)",
    )


def _like_any_text_clause(columns: list[str], terms: list[str]) -> tuple[str, list[str]]:
    clauses = []
    params: list[str] = []
    for term in terms:
        for col in columns:
            clauses.append(f"lower(CAST({_quote_identifier(col)} AS TEXT)) LIKE ?")
            params.append(f"%{term.lower()}%")
    return " OR ".join(clauses), params


def _like_all_text_clause(columns: list[str], terms: list[str]) -> tuple[str, list[str]]:
    clauses = []
    params: list[str] = []
    for term in terms:
        term_clauses = []
        for col in columns:
            term_clauses.append(f"lower(CAST({_quote_identifier(col)} AS TEXT)) LIKE ?")
            params.append(f"%{term.lower()}%")
        clauses.append("(" + " OR ".join(term_clauses) + ")")
    return " AND ".join(clauses), params


def _append_governor_action_lookup_block(
    blocks: list[str],
    status: str,
    *,
    rows: list[sqlite3.Row] | None = None,
    action_summary: dict[str, int] | None = None,
    columns: list[str] | None = None,
    search_columns: list[str] | None = None,
    detail: str = "",
) -> None:
    lines = ["[Database Context - polls.governor_actions lookup]"]
    lines.append(f"lookup_status={status}")
    if detail:
        lines.append(f"detail={detail}")
    if columns is not None:
        lines.append(f"available_columns={', '.join(columns) if columns else 'none'}")
    if search_columns is not None:
        lines.append(f"searched_columns={', '.join(search_columns) if search_columns else 'none'}")
    if action_summary:
        lines.append("CONFIRMED governor_actions DATABASE TOTALS (full table COUNT, not a sample):")
        for action, count in sorted(action_summary.items(), key=lambda x: -x[1]):
            lines.append(f"  {action}: {count} bills/orders IN DATABASE")
        lines.append(
            "NOTE: Rows below are a recent sample only. The action totals above are full-table counts, "
            "so action types listed above ARE present in the database even if not shown in the sample rows."
        )
        lines.append("Use the totals above for availability. Use sample rows only as examples.")
    for row in rows or []:
        lines.append(f"- {_row_to_line(row)}")
    if status == "records_found":
        lines.append("Footer: SQL governor action lookup")
    blocks.append("\n".join(lines))


def _add_schema_summary(blocks: list[str]) -> None:
    lines = ["[Database Inventory]"]
    for db_key in DB_PATHS:
        conn = _connect(db_key)
        if not conn:
            continue
        tables = []
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            name = row[0]
            if _is_internal_table(name):
                continue
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(name)}").fetchone()[0]
            except Exception:
                count = "?"
            tables.append(f"{name}({count})")
        conn.close()
        lines.append(f"- {db_key}: {', '.join(tables[:28])}")
    blocks.append("\n".join(lines))


def _add_full_schema_summary(blocks: list[str]) -> None:
    lines = ["[Admin SQL Inventory - all configured SQLite tables]"]
    for db_key in DB_PATHS:
        conn = _connect(db_key)
        if not conn:
            lines.append(_db_unavailable_line(db_key))
            continue
        lines.append(f"- {db_key}:")
        try:
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            for row in table_rows:
                table = row[0]
                if _is_internal_table(table):
                    continue
                try:
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                    ).fetchone()[0]
                except Exception:
                    count = "?"
                column_names = [info[1] for info in _table_column_info(conn, table)]
                preview = ", ".join(column_names[:14])
                if len(column_names) > 14:
                    preview += ", ..."
                lines.append(f"  - {table}: rows={count}; columns={preview or 'unknown'}")
        finally:
            conn.close()
    blocks.append("\n".join(lines))


def _add_admin_sql_coverage_summary(blocks: list[str]) -> None:
    lines = ["[Admin SQL Coverage - all configured SQLite DBs are connected read-only]"]
    for db_key in DB_PATHS:
        conn = _connect(db_key)
        if not conn:
            lines.append(_db_unavailable_line(db_key))
            continue
        try:
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            tables = [row[0] for row in table_rows if not _is_internal_table(row[0])]
            lines.append(
                f"- {db_key}: tables={len(tables)}; "
                f"table_names={', '.join(tables[:80])}"
                + (" ..." if len(tables) > 80 else "")
            )
        finally:
            conn.close()
    blocks.append("\n".join(lines))


def _generic_search_terms(query: str, terms: list[str]) -> list[str]:
    q_lower = (query or "").lower()
    result = list(terms)
    for bill in _bill_numbers(query):
        result.insert(0, bill)
    for known in ("spanberger", "rouse", "feggans"):
        if known in q_lower and known not in result:
            result.insert(0, known)
    generic = {
        "database", "databases", "table", "tables", "schema", "source",
        "sources", "record", "records", "lookup", "search", "admin",
        "chat", "voteiq", "public", "report", "analysis", "data",
        "vote", "votes", "voted", "voting",
    }
    cleaned: list[str] = []
    seen: set[str] = set()
    for term in result:
        low = term.lower()
        if low in generic or low in seen:
            continue
        seen.add(low)
        cleaned.append(term)
        if len(cleaned) >= 5:
            break
    return cleaned


def _add_generic_sql_search_context(blocks: list[str], query: str, terms: list[str]) -> None:
    search_terms = _generic_search_terms(query, terms)
    if not search_terms:
        return

    lines = ["[Admin SQL Generic Search - read-only all-table scan]"]
    lines.append(f"searched_terms={', '.join(search_terms)}")
    matches = 0
    scanned_tables = 0
    skipped_tables: list[str] = []

    for db_key in DB_PATHS:
        conn = _connect(db_key)
        if not conn:
            lines.append(_db_unavailable_line(db_key))
            continue
        try:
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            for row in table_rows:
                table = row[0]
                if _is_internal_table(table):
                    continue
                info = _table_column_info(conn, table)
                if not info:
                    continue
                columns = [
                    col[1]
                    for col in info
                    if any(kind in str(col[2] or "").upper() for kind in ("CHAR", "CLOB", "TEXT"))
                ]
                if not columns:
                    skipped_tables.append(f"{db_key}.{table}: no_text_columns")
                    continue
                scanned_tables += 1
                search_cols = columns[:8]
                select_cols = [col[1] for col in info[:10]]
                clause, params = _like_any_text_clause(search_cols, search_terms)
                try:
                    rows = conn.execute(f"""
                        SELECT {', '.join(_quote_identifier(col) for col in select_cols)}
                        FROM {_quote_identifier(table)}
                        WHERE {clause}
                        LIMIT 2
                    """, tuple(params)).fetchall()
                except Exception as exc:
                    skipped_tables.append(f"{db_key}.{table}: search_error:{type(exc).__name__}")
                    continue
                if not rows:
                    continue
                matches += len(rows)
                lines.append(
                    f"- {db_key}.{table}: matched_rows={len(rows)}; "
                    f"searched_columns={', '.join(search_cols)}"
                )
                for found in rows:
                    lines.append(f"  - {_row_to_line(found, max_value=150)}")
                # Cap the dump hard — it is a last-resort fallback, not the
                # main context. Keeping it small leaves room for the model to
                # actually read it instead of drowning in raw rows.
                if matches >= 12:
                    lines.append("search_limit_reached=true")
                    blocks.append("\n".join(lines))
                    return
        finally:
            conn.close()

    lines.append(f"summary=scanned_tables={scanned_tables}; matched_rows={matches}")
    if skipped_tables:
        lines.append("skipped=" + "; ".join(skipped_tables[:20]))
    blocks.append("\n".join(lines))


# ── Donor-Influence Analysis Cache ───────────────────────────────────────────
# Loaded once at module import; silently absent if cache not yet built.
_ANALYSIS_CACHE: dict = {}
try:
    _cache_path = BASE_DIR / "data" / "donor_map_cache.json"
    _ANALYSIS_CACHE = json.loads(_cache_path.read_text(encoding="utf-8"))
except Exception:
    pass

_DONOR_TREND_TRIGGER = re.compile(
    r'\b('
    r'spike|ramp|surge|trend|historically|multi.cycle|year.over.year|'
    r'over.time|over.the.years|donation.history|giving.history|'
    r'rate.case|regulatory.fight|invest|increas|'
    r'dominion|appalachian.power|clean.virginia|everytown|'
    r'seiu|afscme|altria|reynolds|league.of.conservation'
    r')\b',
    re.I,
)

_ANALYSIS_TRIGGER = re.compile(
    r'\b(coi|conflict.of.interest|industry.influence|donor.alignment|dominion.energy|'
    r'dominion.donor|energy.donor|donor.industry|industry.donor|top.donor.industry|'
    r'most.funded|sponsor.rate|bill.success.rate|legislative.success|'
    r'finance.donor|funded.legislator|who.funds|what.industry|alignment.score)\b',
    re.I,
)

_DOM_RECIPIENT_TRIGGER = re.compile(
    r'(?:'
    r'(?:top|most|largest|biggest|highest)\s+(?:recipient|receiver)s?'
    r'|who\s+(?:received|got|took|accepted)\s+(?:the\s+)?most'
    r'|dominion\s+(?:funding|money|donation|contribution|pac\s+money|energy\s+funding)'
    r'|received\s+(?:the\s+)?most\s+(?:from\s+)?dominion'
    r'|dominion\s+recipient'
    r'|how\s+much\s+dominion'
    r')',
    re.I,
)

_DOM_PAC_LIKE_PATTERNS = (
    "%Dominion Energy%",
    "%Dominion PAC%",
    "%Dominion Political Action%",
    "%Dominion Leadership Trust%",
    "%Dominion Resources%",
    "%Dominion Power%",
)

_DOM_PAC_EXCL_RE = re.compile(
    r'atlantic\s+dominion|new\s+dominion|old\s+dominion(?!\s+electric)',
    re.I,
)

_DOM_EMPLOYER_RE = re.compile(
    r'\bdominion\s+(energy|resources?|power|virginia|services?)\b'
    r'|\bvirginia\s+power\b'
    r'|\bdominion\s+virginia\s+power\b',
    re.I,
)
_DOM_EMPLOYER_EXCL_RE = re.compile(
    r'\b(hospital|university|college|medical|financial|bank|insurance)\b'
    r'|atlantic\s+dominion|new\s+dominion|old\s+dominion(?!\s+electric)',
    re.I,
)


def _is_dominion_employer(employer: str) -> bool:
    if not employer:
        return False
    if _DOM_EMPLOYER_EXCL_RE.search(employer):
        return False
    return bool(_DOM_EMPLOYER_RE.search(employer))


_FINANCE_METHODOLOGY_VERSION = "VoteIQ Finance Analysis v2.0"


def _dom_share_tier(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct < 1:
        return "minimal (<1%)"
    if pct < 5:
        return "modest (1–5%)"
    if pct < 15:
        return "significant (5–15%)"
    return "major funding source (>15%)"


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _degrade(current: str, to: str) -> str:
    """Return the worse of two grades (D is worst)."""
    return to if _GRADE_ORDER.get(to, 0) > _GRADE_ORDER.get(current, 0) else current


def _quality_score(
    yes_n: int,
    no_n: int,
    median_available: bool,
    ratio_medians,
    concentration_warning: bool,
    mean_skew_warning: bool,
) -> tuple[str, list[str]]:
    """Return (grade, reasons) — A=strong, B=moderate, C=limited, D=directional only."""
    grade = "A"
    reasons: list[str] = []
    min_n = min(yes_n, no_n)

    if min_n < 10:
        grade = _degrade(grade, "D")
        reasons.append(f"very small sample (min n={min_n})")
    elif min_n < 30:
        grade = _degrade(grade, "C")
        side = "NO" if no_n < yes_n else "YES"
        reasons.append(f"small {side}-vote sample (n={min_n})")

    if not median_available:
        grade = _degrade(grade, "C")
        reasons.append("median unavailable")

    r = ratio_medians or 0
    if r > 10:
        grade = _degrade(grade, "D")
        reasons.append(f"very high ratio ({r:.1f}×)")
    elif r > 5:
        grade = _degrade(grade, "C")
        reasons.append(f"high ratio ({r:.1f}×)")

    if concentration_warning:
        grade = _degrade(grade, "C")
        reasons.append("top-5 YES recipients >50% of group total")

    if mean_skew_warning:
        grade = _degrade(grade, "C")
        reasons.append("mean >3× median — outlier skew")

    if not reasons:
        reasons.append("strong sample sizes, median available, no outlier concentration")

    return grade, reasons


_CIVIC_RULES_BLOCK = """\
[Civic Analysis Rendering Rules — MANDATORY]
Methodology: {version}
Apply these rules to ALL donor, vote, and finance outputs:

TIME WINDOWS: Every metric must be labeled with one of:
  session_cycle (e.g. VA 2025-2026) | election_cycle (2/4-yr) | multi_cycle_total (cumulative)
  Never mix time windows in one table or ranking. If user requests a mixed-cycle view, split
  into separate labeled sections with a ⚠ banner: "Mixed time windows — figures are NOT directly
  comparable." Do not block the output; render it with the banner.

VOTE-DONATION COMPARISONS: Median is the PRIMARY statistic. Mean is supplemental.
  Always render:
    YES group: n | median Dominion $ | mean Dominion $ (supplemental)
    NO group:  n | median Dominion $ | mean Dominion $ (supplemental)
    Ratio of medians: X× (PRIMARY)
    Ratio of means: Y× (supplemental — label explicitly as "Ratio of means")
    Time window: [explicit]
    Statistical Confidence: [A/B/C/D with reason]
    Outlier/Distribution warning: show when triggered
    Bill context: one-line summary
  D-grade outputs must still render — append: "For background research only. Do not publish
  without independent verification."

FUNDING CONCENTRATION: For every legislator profile show:
  Dominion Contributions: $X | Total Raised: $Y | Dominion Share: Z%
  Tiers: <1% minimal · 1-5% modest · 5-15% significant · >15% major funding source

DONOR ATTRIBUTION: "Dominion Energy funding" = PAC contributions + employer-tagged individuals
  (label individual amounts as "reported affiliation, unverified"). Never use utilities sector
  rollups as a Dominion proxy.

TOP RECIPIENTS: Enforce same cycle + same donor definition + same chamber within any ranking.
  Mixed requests → split into: Current Session Leaders / Multi-Cycle Leaders / Lifetime Totals.

PROVENANCE: Every metric must show Source | Method | Window inline.

PRE-RENDER VALIDATION: Before rendering, confirm:
  ✓ Same cycle across all figures?
  ✓ Same donor definition?
  ✓ Median available?
  ✓ Sample sizes shown where n<30 or ratio>5×?
  ✓ Funding concentration calculated?
  ✓ Provenance displayed?
  ✓ Confidence score generated?
  If any check fails → prepend a ⚠ WARNING banner naming the specific gap.
  A flagged output is always better than a blank or silently incorrect one.
""".format(version=_FINANCE_METHODOLOGY_VERSION)


# Three-layer response contract. Injected whenever campaign-finance data is present
# in the assembled context so the LLM segregates finance OUT of the factual narrative
# instead of inlining it next to a committee chair, sponsor, or vote (the trust-risk
# pattern: "...died in committee, chaired by X, who raised $1.3M..."). The factual
# layer must be readable on its own with zero financial leakage.
_LAYER_CONTRACT_BLOCK = """\
[Three-Layer Response Contract — MANDATORY for THIS answer]
This answer combines verifiable facts with campaign-finance context. Render it in three
clearly separated layers. NEVER weave finance or donor data into factual sentences.

LAYER 1 — FACTS (render first; always):
  Bill identifier + title | status / legislative outcome | committees + procedural history |
  roll-call votes (if any) | sponsors / co-sponsors | official links & sources.
  HARD RULE: no dollar amounts, no "raised $X", no donor sectors, no campaign-finance of any
  kind in this layer — not even adjacent to a committee chair, sponsor, or voter's name.
  A journalist must be able to read Layer 1 alone and fully understand what happened with
  zero financial or political inference.

LAYER 2 — CONTEXT (only if finance data is present; separate "## Context — campaign finance" heading):
  Campaign-finance totals | donor-sector breakdowns | related political/historical context |
  related bills. EVERY dollar figure and donor sector lives ONLY here, never in Layer 1.

LAYER 3 — ANALYSIS (only if a correlation is being drawn; separate "## Analysis" heading):
  Neutral, non-causal language only. No implication of influence, corruption, intent, or motive.
  This layer MUST end with the literal line: "This section does not imply causation or intent."

FORBIDDEN — these leak Layer-2 data into the Layer-1 narrative:
  ❌ "...left in House Public Safety, chaired by X, who raised $1.3M..."
  ❌ "Chair X (top donor sectors: Tobacco, Wine & Spirits)"
  ✔ Layer 1: "HB7 was left in House Public Safety, chaired by X." (fact only)
  ✔ Layer 2: "X has raised $1.3M; top donor sectors: ..." (finance, separated)
"""

# Finance signals that mean "Layer 2 content exists and must be segregated."
_LAYER_FINANCE_MARKERS = re.compile(
    r"raised|donor sector|top donor|dominion|campaign[_ ]finance|\bPAC\b|\$\s?\d", re.I
)


def _add_civic_rendering_rules(blocks: list[str], query: str) -> None:
    """Inject civic analysis rendering rules when donor/vote/finance context is active."""
    if (
        _ANALYSIS_TRIGGER.search(query or "")
        or _DONOR_TREND_TRIGGER.search(query or "")
        or _is_campaign_finance_query(query)
        or "dominion" in (query or "").lower()
    ):
        blocks.append(_CIVIC_RULES_BLOCK)


def _add_layer_rendering_rules(blocks: list[str]) -> None:
    """Inject the 3-layer FACT/CONTEXT/ANALYSIS contract whenever campaign-finance data
    is present in the already-assembled context. Scanning the built blocks (rather than
    the query) means the contract fires exactly when there is finance to segregate — no
    matter which builder produced it — so finance never leaks into the factual narrative.
    Inserted at the front so it is never lost to max_chars truncation."""
    if any(_LAYER_FINANCE_MARKERS.search(b) for b in blocks):
        blocks.insert(0, _LAYER_CONTRACT_BLOCK)


def _add_donor_analysis_context(blocks: list[str], query: str) -> None:
    """Inject pre-computed donor-influence analysis into the chat context.
    Includes aggregate findings (top CoI, Dominion, sponsor outcomes) and
    per-legislator breakdown when a known name appears in the query.
    """
    state = _ANALYSIS_CACHE.get("state", {})
    if not state:
        return

    dl  = state.get("donor_legislation", [])
    dom = state.get("dominion_analysis", {})
    iss = state.get("industry_sponsor_stats", [])
    q_lower = query.lower()

    lines: list[str] = ["=== Donor-Influence Analysis (VA 2025-2026 Session) ==="]

    # ── Per-legislator lookup when a name is mentioned ──
    matched_leg = None
    for r in dl:
        name = (r.get("name") or "").lower()
        if not name:
            continue
        last = name.split()[-1]
        if len(last) >= 4 and last in q_lower:
            matched_leg = r
            break

    if matched_leg:
        r = matched_leg
        lines.append(
            f"\n-- {r['name']} — Donor-Industry Profile"
            " | Window: VA 2025-2026 session_cycle"
            " | Source: Virginia SBE | Method: SQL aggregate --"
        )
        lines.append(f"Top donor industry : {r.get('top_donor_industry', 'N/A')}")
        lines.append(f"Funds from top industry : ${r.get('top_donor_amount', 0):,.0f}")
        lines.append(f"Concentration (classified donors) : {r.get('concentration', 'N/A')}% of classified donor $")

        # Dominion-specific funding concentration (if legislator appears in Dominion data)
        leg_name_lower = (r.get("name") or "").lower()
        dom_leg = next(
            (d for d in dom.get("legislators", [])
             if (d.get("name") or "").lower() == leg_name_lower),
            None,
        )
        if dom_leg:
            dom_amt   = dom_leg.get("dom_total", 0)
            total_amt = dom_leg.get("total_raised", 0)
            dom_pct   = dom_leg.get("dom_share_pct")
            tier      = _dom_share_tier(dom_pct)
            lines.append(
                f"Dominion Contributions: ${dom_amt:,.0f}"
                + (f" | Total Raised (Schedule A): ${total_amt:,.0f}" if total_amt else "")
                + (f" | Dominion Share: {dom_pct:.1f}% — {tier}" if dom_pct is not None else "")
            )

        lines.append(f"Vote alignment in top industry : {r.get('top_alignment', 'N/A')}%")
        lines.append(f"CoI Score (0-100) : {r.get('coi_score', 'N/A')}")
        lines.append(
            f"Within-party CoI rank : "
            f"{r.get('party_rank', 'N/A')} of {r.get('party_total', 'N/A')} ({r.get('party', '?')[:3]})"
        )
        lines.append(f"Sponsored bills in top industry : {r.get('sponsored_in_top', 0)}")
        lines.append(f"Sponsor success rate (top industry) : {r.get('sponsor_success_rate', 'N/A')}%")
        for d in (r.get("donor_industries") or [])[:5]:
            lines.append(f"  {d['label']}: ${d['total']:,.0f} ({d['donors']} donors)")

    # ── Top CoI legislators (always included) ──
    top_coi = sorted(
        [r for r in dl if r.get("coi_score") is not None],
        key=lambda x: -x["coi_score"],
    )[:10]
    if top_coi:
        lines.append(
            "\n-- Top 10 Legislators by Conflict-of-Interest Score"
            " | Window: VA 2025-2026 session_cycle"
            " | Source: VoteIQ (derived) | Method: SQL aggregate --"
        )
        lines.append("CoI Score = (% YES votes in top donor industry) × (log-scaled donor $)")
        for r in top_coi:
            lines.append(
                f"  {r['name']} ({r.get('party','?')[:3]}, {r.get('chamber','?')}): "
                f"CoI={r['coi_score']}  |  {r.get('top_donor_industry','?')} "
                f"${r.get('top_donor_amount',0):,.0f}  |  alignment {r.get('top_alignment','?')}%"
            )

    # ── Dominion headline ──
    dom_stats = dom.get("stats", {})
    if dom_stats:
        lines.append(
            "\n-- Dominion Energy Influence"
            " | Window: VA 2025-2026 session_cycle"
            " | Source: Virginia SBE | Method: SQL aggregate --"
        )
        lines.append(
            "Donor attribution: PAC contributions + individuals where employer is explicitly"
            " reported in SBE Schedule A (individual amounts labeled"
            " 'reported affiliation, unverified'). Utilities sector rollups are NOT used"
            " as a Dominion proxy."
        )
        lines.append(
            f"Total Dominion donations (session_cycle 2025-2026):"
            f" ${dom_stats.get('total_dominion_dollars',0):,.0f}"
        )
        lines.append(
            f"Legislators with Dominion funding:"
            f" {dom_stats.get('legislators_funded',0)} of {dom_stats.get('legislators_analyzed',0)}"
        )
        lines.append(
            f"Top Dominion recipient: {dom_stats.get('top_recipient','')} "
            f"(${dom_stats.get('top_recipient_amount',0):,.0f})"
        )
        # Prefer the bill with the highest ratio of medians; fall back to ratio of means
        per_bill_list = dom.get("per_bill", [])
        top_bill = max(
            (b for b in per_bill_list if b.get("ratio_medians") or b.get("ratio")),
            key=lambda x: x.get("ratio_medians") or x.get("ratio") or 0,
            default=None,
        )
        if top_bill:
            yes_n         = top_bill.get("yes_count", 0)
            no_n          = top_bill.get("no_count", 0)
            yes_median    = top_bill.get("median_yes_dom")
            no_median     = top_bill.get("median_no_dom")
            yes_mean      = top_bill.get("avg_yes_dom")
            no_mean       = top_bill.get("avg_no_dom")
            ratio_med     = top_bill.get("ratio_medians")
            ratio_means   = top_bill.get("ratio")
            conc_warn     = top_bill.get("concentration_warning", False)
            skew_warn     = top_bill.get("mean_skew_warning", False)
            top5_yes      = top_bill.get("top5_share_yes_pct")
            bill_ctx      = top_bill.get("description") or top_bill.get("title") or ""
            median_avail  = yes_median is not None and no_median is not None
            primary_ratio = ratio_med if ratio_med is not None else ratio_means

            grade, grade_reasons = _quality_score(
                yes_n, no_n, median_avail,
                ratio_med, conc_warn, skew_warn,
            )

            gap_lines = [
                f"Largest YES/NO Dominion funding gap | Bill: {top_bill['bill_id']}"
                f" | Time window: VA 2025-2026 session_cycle",
            ]
            if bill_ctx:
                gap_lines.append(f"  Bill context: {bill_ctx[:120]}")
            gap_lines.append(
                f"  YES group : n={yes_n}"
                + (f" | median Dominion $={yes_median:,.0f}" if yes_median is not None else " | median: n/a")
                + (f" | mean Dominion $={yes_mean:,.0f} (supplemental)" if yes_mean is not None else "")
            )
            gap_lines.append(
                f"  NO group  : n={no_n}"
                + (f" | median Dominion $={no_median:,.0f}" if no_median is not None else " | median: n/a")
                + (f" | mean Dominion $={no_mean:,.0f} (supplemental)" if no_mean is not None else "")
            )
            if ratio_med is not None:
                gap_lines.append(f"  Ratio of medians: {ratio_med:.1f}× (PRIMARY)")
            if ratio_means is not None:
                gap_lines.append(f"  Ratio of means: {ratio_means:.1f}× (supplemental)")
            gap_lines.append(
                f"  Statistical Confidence: {grade} — {'; '.join(grade_reasons)}"
            )
            if grade == "D":
                gap_lines.append(
                    "  ⚠ D-grade: For background research only."
                    " Do not publish without independent verification."
                )
            if conc_warn or skew_warn:
                gap_lines.append("  ⚠ Distribution Warning:")
                if conc_warn:
                    top5_s = f" ({top5_yes:.0f}% of YES-group total)" if top5_yes else ""
                    gap_lines.append(
                        f"    Funding is highly concentrated among top recipients{top5_s}."
                        " Median values better represent the typical legislator."
                    )
                if skew_warn:
                    gap_lines.append(
                        "    Mean >3× median — outlier skew detected."
                        " Cite median, not mean, as primary statistic."
                    )
            gap_lines.append(
                f"  Methodology: {_FINANCE_METHODOLOGY_VERSION}"
            )
            lines.extend(gap_lines)

    # ── Sponsored-bill outcome rates ──
    if iss:
        lines.append(
            "\n-- Sponsored-Bill Pass Rate by Donor Industry"
            " | Window: VA 2025-2026 session_cycle"
            " | Source: VoteIQ (derived) | Method: SQL aggregate --"
        )
        lines.append("Gap = high-funded legislator pass rate minus low-funded legislator pass rate")
        for r in iss:
            gap = r.get("high_vs_low_gap")
            gap_str = (f"+{gap}pp" if gap >= 0 else f"{gap}pp") if gap is not None else "n/a"
            lines.append(
                f"  {r['industry']}: {r['n']} legislators, avg donor ${r['avg_donor']:,.0f}, "
                f"avg pass rate {r['avg_success']}%, gap {gap_str}"
            )

    blocks.append("\n".join(lines))


def _add_donor_trend_context(blocks: list[str], query: str) -> None:
    """
    Add multi-cycle donor trend data to the retrieval context.
    Searches donor_cycle_trends, donor_entity_map, and cycle_context in polls.db.
    Triggered by spike/trend/ramp keywords or named major donors.
    Available to all tiers (this is SQLite retrieval, not analyst overlay).
    """
    if not (_DONOR_TREND_TRIGGER.search(query) or _is_campaign_finance_query(query)):
        return

    conn = _connect("polls")
    if not conn:
        return

    if not _table_exists(conn, "donor_cycle_trends"):
        conn.close()
        return

    q_lower = (query or "").lower()
    has_money = any(m in q_lower for m in ("donat", "contribut", "money", "fund", "gave", "giving"))

    # ── Named donor lookup ────────────────────────────────────────────────────
    if _table_exists(conn, "donor_entity_map"):
        # Try to match a canonical donor name from the query
        try:
            entity_rows = conn.execute(
                "SELECT donor_key, canonical_name, total_all_cycles, first_cycle, last_cycle "
                "FROM donor_entity_map WHERE total_all_cycles >= 100000 "
                "ORDER BY total_all_cycles DESC LIMIT 300"
            ).fetchall()

            matched_key = matched_name = None
            for e in entity_rows:
                cname = (e["canonical_name"] or "").lower()
                dkey  = (e["donor_key"] or "").replace("_", " ")
                if cname in q_lower or dkey in q_lower or any(
                    word in q_lower for word in cname.split() if len(word) > 4
                ):
                    matched_key  = e["donor_key"]
                    matched_name = e["canonical_name"]
                    total        = e["total_all_cycles"]
                    first        = e["first_cycle"]
                    last         = e["last_cycle"]
                    break

            if matched_key:
                cycles = conn.execute(
                    "SELECT election_cycle, cycle_parity, total_amount, "
                    "pct_change_prior, ratio_vs_mean, is_spike "
                    "FROM donor_cycle_trends WHERE donor_key = ? "
                    "ORDER BY election_cycle",
                    (matched_key,),
                ).fetchall()

                if cycles:
                    odd_amts  = [c["total_amount"] for c in cycles if c["cycle_parity"] == "odd"]
                    even_amts = [c["total_amount"] for c in cycles if c["cycle_parity"] == "even"]
                    odd_mean  = sum(odd_amts)  / len(odd_amts)  if odd_amts  else 0
                    even_mean = sum(even_amts) / len(even_amts) if even_amts else 0

                    # Get legislative context for elevated cycles
                    elevated = [c["election_cycle"] for c in cycles if c["is_spike"]]
                    ctx_map: dict[str, list[str]] = {}
                    if elevated and _table_exists(conn, "cycle_context"):
                        ph = ",".join("?" for _ in elevated)
                        for row in conn.execute(
                            f"SELECT election_cycle, title, donor_sector FROM cycle_context "
                            f"WHERE election_cycle IN ({ph}) AND significance = 'high' "
                            f"ORDER BY election_cycle, donor_sector",
                            elevated,
                        ):
                            ctx_map.setdefault(row["election_cycle"], []).append(
                                f"{row['title'][:60]} [{row['donor_sector'] or 'general'}]"
                            )

                    lines = [
                        f"[RETRIEVED RECORD - Virginia Campaign Finance: Multi-Cycle Donor Trend]",
                        f"Donor: {matched_name}",
                        f"Source: Virginia SBE campaign finance public records, 2012-2026",
                        f"Total donated ({first}-{last}): ${total:,.0f}",
                        f"Typical giving: ${odd_mean:,.0f} in state-election years (odd), "
                        f"${even_mean:,.0f} in federal-election years (even)",
                        "Note: Virginia holds state elections in odd years; odd-year giving is "
                        "typically 5-50x higher than even years. Spikes measured against this "
                        "donor's own same-parity average.",
                        "Giving history:",
                    ]
                    for c in cycles:
                        base  = odd_mean if c["cycle_parity"] == "odd" else even_mean
                        ratio = c["total_amount"] / base if base else 0
                        pct_s = f", up {c['pct_change_prior']:.0f}% from prior comparable cycle" \
                                if c["pct_change_prior"] else ""
                        yr_label = "state-election yr" if c["cycle_parity"] == "odd" else "federal-election yr"

                        if c["is_spike"]:
                            ctx_str = ""
                            if c["election_cycle"] in ctx_map:
                                ctx_str = " | Active legislation: " + "; ".join(ctx_map[c["election_cycle"]][:2])
                            lines.append(
                                f"  {c['election_cycle']} ({yr_label}): ${c['total_amount']:,.0f} "
                                f"[NOTABLY ELEVATED: {ratio:.1f}x typical{pct_s}]{ctx_str}"
                            )
                        elif ratio >= 1.5:
                            lines.append(
                                f"  {c['election_cycle']} ({yr_label}): ${c['total_amount']:,.0f} "
                                f"[elevated: {ratio:.1f}x typical{pct_s}]"
                            )
                        else:
                            lines.append(
                                f"  {c['election_cycle']} ({yr_label}): ${c['total_amount']:,.0f}"
                            )
                    blocks.append("\n".join(lines))

        except Exception:
            pass

    # ── Top institutional spikes (always show when trend query) ───────────────
    if _DONOR_TREND_TRIGGER.search(query):
        try:
            spikes = conn.execute("""
                SELECT canonical_name, election_cycle, total_amount,
                       pct_change_prior, ratio_vs_mean, cycle_parity
                FROM donor_cycle_trends
                WHERE is_spike = 1 AND is_individual = 0 AND total_amount >= 500000
                ORDER BY zscore DESC LIMIT 8
            """).fetchall()

            if spikes:
                lines = [
                    "[RETRIEVED RECORD - Virginia Campaign Finance: Top Institutional Donation Spikes 2012-2026]",
                    "Source: Virginia SBE campaign finance public records",
                    "The following donors gave notably more than their own historical average in these cycles:",
                ]
                for s in spikes:
                    yr_type = "state-election yr" if s["cycle_parity"] == "odd" else "federal-election yr"
                    ratio_s = f"{s['ratio_vs_mean']:.1f}x their typical {yr_type} spending" \
                              if s["ratio_vs_mean"] else ""
                    pct_s   = f"up {s['pct_change_prior']:.0f}% from prior comparable cycle" \
                              if s["pct_change_prior"] else "first cycle on record"
                    lines.append(
                        f"- {s['canonical_name']} gave ${s['total_amount']:,.0f} in "
                        f"{s['election_cycle']} ({yr_type}): {ratio_s}, {pct_s}"
                    )
                blocks.append("\n".join(lines))
        except Exception:
            pass

    conn.close()


_FTM_MONEY = re.compile(
    r"\b(donor|donat|fund|money|contribut|who paid|who funded|follow the money|"
    r"sponsor.*donor|donor.*sponsor|lobbyi|finance|financ)\b",
    re.I,
)
_FTM_BILL = re.compile(r"\b(HB|SB|HJ|SJ|HR|SR)\s*-?\s*(\d{1,5})\b", re.I)
# VA reuses bill numbers every session — extract an explicit 4-digit year from the
# query so we never silently return the wrong session's bill.
_FTM_YEAR = re.compile(r"\b(20\d{2})\b")


def _add_follow_the_money_context(blocks: list[str], query: str) -> None:
    """
    When a bill number + money/donor terms appear in the query, inject a
    Follow-the-Money summary: bill → sponsors → top donors → lobbyist count.
    """
    bill_match = _FTM_BILL.search(query or "")
    if not bill_match:
        return
    if not _FTM_MONEY.search(query or ""):
        # Also trigger on explicit "follow the money" without separate money keyword
        if "follow" not in (query or "").lower():
            return

    bill_number = (bill_match.group(1).upper() + bill_match.group(2)).replace(" ", "")

    # Extract an explicit year hint from the query (e.g. "HB1373 (2023)" or "2023").
    # VA reuses bill numbers every session, so without a year we must not silently
    # return the most-recent session's bill — it is almost certainly the wrong bill.
    year_hits = _FTM_YEAR.findall(query or "")
    session_hint = year_hits[0] if year_hits else None

    conn = _connect("polls")
    li   = _connect("legislative_intelligence")
    if not conn:
        return

    try:
        # Resolve bill — always scope to a known session to prevent cross-session mismatch
        if session_hint:
            bill_row = conn.execute(
                "SELECT bill_id, session, title, status_label, primary_sponsor "
                "FROM legiscan_va_bills WHERE bill_number = ? AND session = ? LIMIT 1",
                (bill_number, session_hint),
            ).fetchone()
        else:
            # No year given — check whether this bill number is unique across sessions
            all_sessions = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT session FROM legiscan_va_bills "
                    "WHERE bill_number = ? ORDER BY session DESC",
                    (bill_number,),
                ).fetchall()
            ]
            if len(all_sessions) > 1:
                # Ambiguous: same number maps to different bills in different years.
                # Surface a disambiguation prompt rather than silently returning wrong data.
                blocks.append(
                    f"[Follow-the-Money: {bill_number} — Session Required]\n"
                    f"{bill_number} appears in multiple VA sessions: "
                    f"{', '.join(all_sessions)}.\n"
                    f"Virginia reuses bill numbers each session — "
                    f"{bill_number} is a different bill in every year listed above.\n"
                    f"Please include the year in your question "
                    f"(e.g. '{bill_number} 2023') so the correct bill is retrieved."
                )
                conn.close()
                if li:
                    li.close()
                return
            bill_row = conn.execute(
                "SELECT bill_id, session, title, status_label, primary_sponsor "
                "FROM legiscan_va_bills WHERE bill_number = ? LIMIT 1",
                (bill_number,),
            ).fetchone() if all_sessions else None
        if not bill_row:
            return

        session = bill_row["session"]
        title   = bill_row["title"] or ""
        status  = bill_row["status_label"] or ""

        # Get sponsors
        sponsor_names: list[str] = []
        if li:
            sp_rows = li.execute(
                """SELECT bs.legislator_name FROM va_bill_sponsors bs
                   JOIN va_bills vb ON bs.bill_id = vb.bill_id
                   WHERE vb.bill_number = ? AND vb.session = ?
                     AND bs.legislator_name != ''
                   ORDER BY bs.sponsor_order LIMIT 4""",
                (bill_number, session),
            ).fetchall()
            sponsor_names = [r["legislator_name"] for r in sp_rows]
        if not sponsor_names and bill_row["primary_sponsor"]:
            sponsor_names = [bill_row["primary_sponsor"]]

        lines = [
            f"[RETRIEVED RECORD - Follow the Money: {bill_number} ({session})]",
            f"Bill: {title[:80]}",
            f"Status: {status}",
            f"Sponsor(s): {', '.join(sponsor_names) if sponsor_names else 'unknown'}",
            "",
        ]

        # For each sponsor, pull top donors + lobbyist count
        for sp_name in sponsor_names[:3]:
            last = sp_name.split()[-1] if sp_name.split() else ""
            fin = conn.execute(
                "SELECT total_raised, top_sector, top_donors_json, by_sector_json "
                "FROM campaign_finance_summary "
                "WHERE name LIKE ? AND source = 'va_sbe' "
                "ORDER BY total_raised DESC LIMIT 1",
                (f"%{last}%",),
            ).fetchone()
            if not fin:
                continue

            try:
                top_donors = json.loads(fin["top_donors_json"] or "[]")
            except Exception:
                top_donors = []
            try:
                by_sector = json.loads(fin["by_sector_json"] or "[]")
            except Exception:
                by_sector = []

            # Skip vague top sector
            _SKIP = {"ideological", "individual/other"}
            top_sector = fin["top_sector"] or ""
            if top_sector.lower() in _SKIP:
                for s in by_sector[1:]:
                    if s.get("sector", "").lower() not in _SKIP:
                        top_sector = s["sector"]
                        break

            lines.append(f"{sp_name} — raised ${(fin['total_raised'] or 0):,.0f} total | "
                         f"top sector: {top_sector}")

            for d in top_donors[:4]:
                dname = d.get("contributor_name") or d.get("employer") or ""
                damt  = d.get("total", 0)
                if not dname or not damt:
                    continue
                # Lobbyist count
                words = [w for w in dname.lower().split() if len(w) >= 4][:2]
                n_lob = 0
                if words:
                    cl = " AND ".join(f"lower(principal_name) LIKE ?" for _ in words)
                    lrow = conn.execute(
                        f"SELECT COUNT(DISTINCT lobbyist_name) FROM lobbyist_registrations WHERE {cl}",
                        [f"%{w}%" for w in words],
                    ).fetchone()
                    n_lob = lrow[0] if lrow else 0
                lob_s = f" | {n_lob} registered lobbyist{'s' if n_lob != 1 else ''}" if n_lob else ""
                lines.append(f"  Donor: {dname[:45]} — ${damt:,.0f}{lob_s}")

            lines.append("")

        lines.append(
            "Virginia SBE campaign finance public records. "
            "Correlation does not imply causation."
        )

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()
        if li:
            li.close()


_TESTIMONY_TRIGGERS = re.compile(
    r"\b(lobby|lobbied|lobbying|lobbyist|testified|testimony|testif|"
    r"who was behind|interest group|organizations? behind|who opposed|"
    r"who supported|who pushed|in the room)\b",
    re.I,
)


def _add_testimony_proxy_context(blocks: list[str], query: str) -> None:
    """
    When a bill number + lobbying/testimony terms appear in the query,
    inject a summary of registered lobbyist principals likely present
    at committee hearings, with donation-backed support signals.
    """
    bill_match = _FTM_BILL.search(query or "")
    if not bill_match:
        return
    if not _TESTIMONY_TRIGGERS.search(query or ""):
        return

    bill_number = (bill_match.group(1).upper() + bill_match.group(2)).replace(" ", "")

    conn = _connect("polls")
    if not conn:
        return

    try:
        # Check table exists
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='committee_testimony_proxy'"
        ).fetchone()
        if not tbl:
            return

        # Get most recent session for this bill
        session_row = conn.execute(
            "SELECT session FROM committee_testimony_proxy WHERE upper(bill_number)=? "
            "ORDER BY session DESC LIMIT 1",
            (bill_number.upper(),),
        ).fetchone()
        if not session_row:
            return
        session = session_row[0]

        # Pull top principals for this bill
        rows = conn.execute("""
            SELECT principal_name, principal_sector, lobbyist_count,
                   likely_position, confidence, total_donated_to_sponsors,
                   sponsor_names
            FROM committee_testimony_proxy
            WHERE upper(bill_number) = ? AND session = ?
            ORDER BY
                CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                lobbyist_count DESC
            LIMIT 12
        """, (bill_number.upper(), session)).fetchall()

        if not rows:
            return

        # Bill title
        bill_row = conn.execute(
            "SELECT title FROM legiscan_va_bills WHERE bill_number = ? AND session = ? LIMIT 1",
            (bill_number, session),
        ).fetchone()
        title = bill_row["title"][:80] if bill_row else ""

        total = conn.execute(
            "SELECT COUNT(*) FROM committee_testimony_proxy WHERE upper(bill_number)=? AND session=?",
            (bill_number.upper(), session),
        ).fetchone()[0]

        supporters = sum(1 for r in rows if r["likely_position"] == "support")

        lines = [
            f"[Committee Testimony Proxy — {bill_number} ({session})]",
            f'Bill: "{title}"',
            f"Registered lobbying organizations active this session in the same sector: {total}",
            f"With donation-backed support signal: {supporters} of top {len(rows)} shown",
            "",
        ]

        for r in rows:
            donated = r["total_donated_to_sponsors"]
            donated_str = f", donated ${donated:,.0f} to sponsors" if donated and donated > 0 else ""
            pos_str = f"[{r['likely_position'].upper()}]" if r["likely_position"] != "unknown" else ""
            lines.append(
                f"- {r['principal_name']} | {r['lobbyist_count']} lobbyists | "
                f"{r['principal_sector']}{donated_str} {pos_str} (confidence: {r['confidence']})"
            )

        lines += [
            "",
            "NOTE: This is a proxy — Virginia does not require bill-specific lobbying "
            "disclosure. These organizations were registered in the same sector during "
            "this session. 'SUPPORT' indicates a donation record to bill sponsors.",
        ]

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


_GATEKEEPER_TRIGGERS = re.compile(
    r"\b(kills?\s+bills?|kill(?:ing|ed)\s+bills?|gatekeeper|"
    r"who\s+(?:kill|block|stop|bury|buried|suppress)(?:s|ed|ing)?\b|"
    r"blocked?\s+in\s+committee|died?\s+in\s+committee|left\s+in\s+committee|"
    r"killed?\s+in\s+committee|tabled\s+in\s+committee|"
    r"passed\s+by\s+indefinitely|stricken\s+from\s+docket|"
    r"failed\s+to\s+report|committee\s+chair(?:man|woman|person)?s?|"
    r"who\s+controls?\s+(?:the\s+)?committee|"
    r"bills?\s+(?:buried|killed|blocked|died|tabled|stopped)\s+in\s+committee)\b",
    re.I,
)

_GK_DEATH_RE = re.compile(
    r"(?:"
    r"Left in Committee\s+"
    r"|Left in\s+"
    r"|Continued to next session in\s+"
    r"|Tabled in\s+"
    r"|Passed by indefinitely in\s+"
    r"|Stricken from docket by\s+"
    r"|Stricken at request of Patron in\s+"
    r"|Failed to report \(defeated\) in\s+"
    r")",
    re.IGNORECASE,
)
_GK_VOTE_SUFFIX = re.compile(r"\s*\(\d+[-–].*$")


def _gk_parse_committee(status: str) -> str | None:
    """Extract committee name from a 'died in committee' status string."""
    m = _GK_DEATH_RE.search(status or "")
    if not m:
        return None
    remainder = status[m.end():].strip()
    remainder = _GK_VOTE_SUFFIX.sub("", remainder).strip()
    return remainder if remainder else None


_COMMITTEE_CHAIR_TRIGGERS = re.compile(
    r"\b(who\s+chairs?|committee\s+chair(?:man|woman|person)?|chair\s+of\s+(?:the\s+)?"
    r"|who\s+(?:leads?|heads?|runs?)\s+(?:the\s+)?committee"
    r"|(?:labor\s+and\s+commerce|finance\s+and\s+appropriations|courts\s+of\s+justice"
    r"|health\s+and\s+human|appropriations|commerce\s+and\s+labor|agriculture"
    r"|education\s+and\s+health|transportation|general\s+laws?|privileges\s+and\s+elections"
    r"|public\s+safety|housing|rules\s+committee)\b.*chair"
    r"|chair.*\b(labor\s+and\s+commerce|finance|health|appropriations|education|transportation"
    r"|agriculture|courts\s+of\s+justice|general\s+laws?|public\s+safety|rules))\b",
    re.I,
)

# Also fire when a known committee name appears with person/leadership context
_COMMITTEE_NAME_RE = re.compile(
    r"\b(labor\s+and\s+commerce|finance\s+and\s+appropriations|courts\s+of\s+justice|"
    r"health\s+and\s+human\s+services|appropriations|commerce\s+and\s+labor|"
    r"agriculture,?\s+chesapeake|education\s+and\s+health|transportation|"
    r"general\s+laws?\s+and\s+technology|general\s+laws?|privileges\s+and\s+elections|"
    r"public\s+safety|housing|labor\s+and\s+commerce|finance\b)\b",
    re.I,
)


def _add_committee_chair_context(blocks: list[str], query: str) -> None:
    """
    When query asks who chairs a committee (or names a committee with chair context),
    inject chair name, donor profile, and kill counts from va_committee_assignments
    and campaign_finance_summary.
    """
    q = query or ""
    # Fire on explicit chair questions OR committee name + leadership/money context
    has_chair_question = bool(_COMMITTEE_CHAIR_TRIGGERS.search(q))
    has_committee_name = bool(_COMMITTEE_NAME_RE.search(q))
    has_money_context = bool(re.search(
        r"\b(donat|fund|money|donor|raised|contribut|sector|lobbied|who\s+chair|chair)\b", q, re.I
    ))
    if not has_chair_question and not (has_committee_name and has_money_context):
        return

    conn = _connect("polls")
    if not conn:
        return

    try:
        # Get all chairs
        chairs = conn.execute(
            "SELECT DISTINCT committee, chamber, member_name "
            "FROM va_committee_assignments WHERE role = 'chair' "
            "ORDER BY chamber DESC, committee"
        ).fetchall()
        if not chairs:
            return

        # Filter to committees mentioned in query if a name is present
        mentioned = _COMMITTEE_NAME_RE.findall(q)
        if mentioned:
            norm_q = q.lower()
            matching = [
                r for r in chairs
                if any(m.lower().replace(",", "") in (r[0] or "").lower() for m in mentioned)
            ]
            if not matching:
                matching = chairs  # fall back to all if no specific match
        else:
            matching = chairs

        # Load campaign finance lookup
        cfs_rows = conn.execute(
            "SELECT name, party, total_raised, by_sector_json "
            "FROM campaign_finance_summary WHERE source = 'va_sbe'"
        ).fetchall()
        suffixes = {"jr", "sr", "ii", "iii", "iv"}

        def _norm(s):
            parts = re.sub(r"[^a-z ]", "", s.lower()).split()
            while parts and parts[-1] in suffixes:
                parts.pop()
            return (parts[0], parts[-1]) if len(parts) >= 2 else ("", parts[-1] if parts else "")

        cfs_map = {}
        for row in cfs_rows:
            n = row[0] if hasattr(row, "__getitem__") else row[0]
            cfs_map[_norm(n)] = row

        def _find_cfs(name):
            f, l = _norm(name)
            hit = cfs_map.get((f, l))
            if hit:
                return hit
            cands = [v for (ff, ll), v in cfs_map.items() if ll == l and ff.startswith(f[:4])]
            if len(cands) == 1:
                return cands[0]
            cands = [v for (ff, ll), v in cfs_map.items() if ll == l]
            return cands[0] if len(cands) == 1 else None

        import json as _json
        # Layer-separated emission: chair assignments are FACTS; chair finance is
        # CONTEXT (Layer 2) and is kept in a distinct block so it is never inlined
        # next to the chair's name in the factual narrative.
        fact_lines = ["[LAYER 1 FACT — Committee Chair Assignments, Virginia General Assembly]"]
        ctx_lines = [
            "[LAYER 2 CONTEXT — campaign finance of the committee chair(s) above; "
            "do NOT inline into the factual narrative]"
        ]

        for row in matching[:8]:  # cap at 8 to avoid bloating context
            committee = row[0]
            chamber = row[1]
            chair_name = row[2]

            cfs = _find_cfs(chair_name)
            raised = 0
            top_sectors = ""
            party = "?"
            if cfs:
                party = ((cfs[1] if hasattr(cfs, "__getitem__") else cfs[1]) or "?")[0].upper()
                raised = (cfs[2] if hasattr(cfs, "__getitem__") else cfs[2]) or 0
                raw = (cfs[3] if hasattr(cfs, "__getitem__") else cfs[3]) or "[]"
                try:
                    slist = _json.loads(raw)
                    top = sorted(slist, key=lambda x: -(x.get("total") or 0))[:3]
                    top_sectors = ", ".join(s.get("sector", "") for s in top if s.get("sector"))
                except Exception:
                    pass

            raised_str = f"${raised/1_000_000:.1f}M raised" if raised >= 1_000_000 else (
                f"${raised/1000:.0f}k raised" if raised else "no finance data"
            )
            fact_lines.append(f"  {chamber} | {committee} — Chair: {chair_name} ({party})")
            ctx_lines.append(
                f"  {chair_name} ({party}): {raised_str}; "
                f"top donor sectors: {top_sectors or '—'}"
            )

        blocks.append("\n".join(fact_lines))
        if len(ctx_lines) > 1:
            blocks.append("\n".join(ctx_lines))

    except Exception:
        pass
    finally:
        conn.close()


def _add_legislator_committee_membership_context(blocks: list[str], query: str) -> None:
    """When a legislator name + 'committee' appears in the query, inject their
    full committee membership list from va_committee_assignments."""
    q_lower = (query or "").lower()
    if "committee" not in q_lower:
        return

    conn = _connect("polls")
    if not conn:
        return

    try:
        if not _table_exists(conn, "va_committee_assignments"):
            return

        # Score all distinct member names against the query tokens
        names = conn.execute(
            "SELECT DISTINCT member_name FROM va_committee_assignments WHERE member_name IS NOT NULL"
        ).fetchall()
        _HONORIFICS = frozenset({"jr.", "jr", "sr.", "sr", "iii", "ii", "iv"})
        best_name, best_score = None, 0
        for (name,) in names:
            tokens = [
                t for t in name.lower().split()
                if len(t) >= 3 and not t.endswith(".") and t not in _HONORIFICS
            ]
            score = sum(1 for t in tokens if t in q_lower)
            if score > best_score:
                best_name, best_score = name, score

        if not best_name or best_score == 0:
            return

        rows = conn.execute(
            "SELECT committee, chamber, role "
            "FROM va_committee_assignments WHERE member_name=? ORDER BY role DESC, committee",
            (best_name,),
        ).fetchall()
        if not rows:
            return

        lines = [f"[Database Context - committee memberships: {best_name}]"]
        for r in rows:
            role_tag = " (Chair)" if r["role"] == "chair" else ""
            lines.append(f"- {r['committee']} ({r['chamber']}){role_tag}")
        lines.append("\nSource: VoteIQ SQL (va_committee_assignments, scraped 2026-06-02).")
        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


_COMMITTEE_FROM_STATUS_RE = re.compile(
    r"left in(?:\s+(?:committee|the))?\s+([A-Za-z ,&/]+?)(?:\.|$)", re.I
)


def _add_bill_committee_chair_context(blocks: list[str], bills: list[str], session: str, pro: bool = False) -> None:
    """
    For any bill that was 'Left in <Committee>', look up who chairs that
    committee and inject their top donor sectors — the "who funds whom"
    crosswalk that connects a killed bill to the chair's industry backers.
    Pro/newsroom only — free users are not shown the donor pattern unprompted.
    """
    if not pro:
        return
    if not bills:
        return

    conn = _connect("polls")
    if not conn:
        return

    import json as _json

    try:
        # Pre-load finance data for fast lookup
        cfs_rows = conn.execute(
            "SELECT name, party, total_raised, by_sector_json "
            "FROM campaign_finance_summary WHERE source = 'va_sbe'"
        ).fetchall()
        suffixes = {"jr", "sr", "ii", "iii", "iv"}

        def _norm(s):
            parts = re.sub(r"[^a-z ]", "", (s or "").lower()).split()
            while parts and parts[-1] in suffixes:
                parts.pop()
            return (parts[0], parts[-1]) if len(parts) >= 2 else ("", parts[-1] if parts else "")

        cfs_map = {_norm(r[0]): r for r in cfs_rows}

        def _find_cfs(name):
            key = _norm(name)
            hit = cfs_map.get(key)
            if hit:
                return hit
            _, l = key
            cands = [v for (f, ll), v in cfs_map.items() if ll == l]
            return cands[0] if len(cands) == 1 else None

        for bill in bills:
            # Get the bill status to find the committee it was left in
            bill_row = conn.execute(
                "SELECT status FROM va_bills WHERE bill_number=? AND session=? LIMIT 1",
                (bill, session),
            ).fetchone()
            if not bill_row or not bill_row[0]:
                continue

            status = bill_row[0]
            m = _COMMITTEE_FROM_STATUS_RE.search(status)
            if not m:
                continue  # bill not killed in committee, skip

            committee_fragment = m.group(1).strip().rstrip(",")

            # Find matching chair(s) — committee names can be partial
            chair_rows = conn.execute(
                "SELECT DISTINCT committee, chamber, member_name "
                "FROM va_committee_assignments WHERE role = 'chair' "
                "AND lower(committee) LIKE lower(?)",
                (f"%{committee_fragment}%",),
            ).fetchall()

            # Deduplicate (table has "First Last" and "Last, First" entries)
            seen_chairs = set()
            unique_chairs = []
            for r in chair_rows:
                key = _norm(r[2] or "")
                if key not in seen_chairs:
                    seen_chairs.add(key)
                    unique_chairs.append(r)

            if not unique_chairs:
                continue

            # Layer-separated emission: the committee/chair disposition is a FACT and
            # must be readable without finance; the chair's finance is CONTEXT (Layer 2)
            # and is emitted as a distinct block so it never inlines next to the kill.
            fact_lines = [f"[LAYER 1 FACT — {bill} committee disposition]"]
            ctx_lines = [
                f"[LAYER 2 CONTEXT — campaign finance of chair(s) of the committee "
                f"that held {bill}; do NOT inline into the factual narrative]"
            ]
            for row in unique_chairs[:3]:
                committee = row[0]
                chamber = row[1]
                chair_name = row[2]

                fact_lines.append(
                    f"  {bill} left in {chamber} {committee} — Chair: {chair_name}"
                )

                cfs = _find_cfs(chair_name)
                if cfs:
                    party = (cfs[1] or "?")[0].upper()
                    raised = cfs[2] or 0
                    raw = cfs[3] or "[]"
                    try:
                        slist = _json.loads(raw)
                        top = sorted(slist, key=lambda x: -(x.get("total") or 0))[:4]
                        top_sectors = ", ".join(s["sector"] for s in top if s.get("sector"))
                    except Exception:
                        top_sectors = ""
                    raised_str = (
                        f"${raised/1_000_000:.1f}M raised"
                        if raised >= 1_000_000
                        else f"${raised/1000:.0f}K raised" if raised else "no finance data"
                    )
                    ctx_lines.append(
                        f"  {chair_name} ({party}): {raised_str}; "
                        f"top donor sectors: {top_sectors or '—'}"
                    )
                else:
                    ctx_lines.append(f"  {chair_name}: no finance data")

            if len(fact_lines) > 1:
                blocks.append("\n".join(fact_lines))
            if len(ctx_lines) > 1:
                blocks.append("\n".join(ctx_lines))

    except Exception:
        pass
    finally:
        conn.close()


def _add_gatekeeper_context(blocks: list[str], query: str) -> None:
    """
    When query asks about committee gatekeepers / who kills bills,
    inject a leaderboard of committee chairs by kill count (2024-2026)
    with their top donor sectors — highlighting jurisdiction conflicts.

    Designed for pro/journalist use: surfaces the gatekeeper pattern
    (chair funded by industry whose bills they kill or protect).
    """
    if not _GATEKEEPER_TRIGGERS.search(query or ""):
        return

    conn = _connect("polls")
    if not conn:
        return

    try:
        # ── 1. Load committee chairs ──────────────────────────────────────────
        chair_rows = conn.execute(
            "SELECT DISTINCT committee, chamber, member_name "
            "FROM va_committee_assignments WHERE role = 'chair' "
            "ORDER BY chamber DESC, committee"
        ).fetchall()
        if not chair_rows:
            return

        # ── 2. Load killed bills 2024-2026 ───────────────────────────────────
        bill_rows = conn.execute(
            """
            SELECT bill_number, title, status, session
            FROM va_bills
            WHERE session IN ('2026', '2025', '2024')
              AND (
                   status LIKE 'Left in%'
                OR status LIKE 'Continued to next session in%'
                OR status LIKE 'Tabled in%'
                OR status LIKE 'Passed by indefinitely in%'
                OR status LIKE 'Stricken from docket by%'
                OR status LIKE 'Stricken at request of Patron in%'
                OR status LIKE 'Failed to report%in%'
              )
            """
        ).fetchall()

        # ── 3. Aggregate kill counts per (chamber_prefix, norm_committee) ────
        from collections import defaultdict
        kills_by_yr: dict = defaultdict(lambda: {"2024": 0, "2025": 0, "2026": 0})
        for br in bill_rows:
            cmt = _gk_parse_committee(br["status"] if hasattr(br, "__getitem__") else br[2])
            if not cmt:
                continue
            status_val = br["status"] if hasattr(br, "__getitem__") else br[2]
            bill_num = br["bill_number"] if hasattr(br, "__getitem__") else br[0]
            session_val = br["session"] if hasattr(br, "__getitem__") else br[3]
            prefix = "House" if str(bill_num).upper().startswith("H") else "Senate"
            norm = re.sub(r"\s+", " ", cmt.lower().strip())
            key = (prefix, norm)
            kills_by_yr[key][session_val] = kills_by_yr[key].get(session_val, 0) + 1

        # ── 4. Load campaign finance for donor sector lookup ──────────────────
        cfs_rows = conn.execute(
            "SELECT name, party, total_raised, top_sector, by_sector_json "
            "FROM campaign_finance_summary WHERE source = 'va_sbe'"
        ).fetchall()

        suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}

        def _norm(s: str):
            parts = re.sub(r"[^a-z ]", "", s.lower()).split()
            while parts and parts[-1] in suffixes:
                parts.pop()
            return (parts[0], parts[-1]) if parts else ("", "")

        cfs_lookup = {}
        for row in cfs_rows:
            name_val = row["name"] if hasattr(row, "__getitem__") else row[0]
            cfs_lookup[_norm(name_val)] = row

        def _find_cfs(chair_name):
            f, l = _norm(chair_name)
            hit = cfs_lookup.get((f, l))
            if hit:
                return hit
            cands = [v for (ff, ll), v in cfs_lookup.items() if ll == l and ff.startswith(f[:4])]
            if len(cands) == 1:
                return cands[0]
            cands = [v for (ff, ll), v in cfs_lookup.items() if ll == l]
            return cands[0] if len(cands) == 1 else None

        # ── 5. Build leaderboard cards ────────────────────────────────────────
        cards = []
        for row in chair_rows:
            committee = row["committee"] if hasattr(row, "__getitem__") else row[0]
            chamber = row["chamber"] if hasattr(row, "__getitem__") else row[1]
            chair_name = row["member_name"] if hasattr(row, "__getitem__") else row[2]

            norm_cmt = re.sub(r"\s+", " ", committee.lower().strip())
            key = (chamber, norm_cmt)
            alt_key = (chamber, re.sub(r"\s+", " ", committee.replace(",", "").lower().strip()))
            yr_counts = kills_by_yr.get(key) or kills_by_yr.get(alt_key) or {"2024": 0, "2025": 0, "2026": 0}
            total_kills = sum(yr_counts.values())

            cfs = _find_cfs(chair_name)
            top_sectors_str = ""
            total_raised = 0
            party = "?"
            if cfs:
                party = ((cfs["party"] if hasattr(cfs, "__getitem__") else cfs[1]) or "?")[0].upper()
                total_raised = (cfs["total_raised"] if hasattr(cfs, "__getitem__") else cfs[2]) or 0
                raw = (cfs["by_sector_json"] if hasattr(cfs, "__getitem__") else cfs[4]) or "[]"
                try:
                    import json as _json
                    slist = _json.loads(raw)
                    if isinstance(slist, list):
                        top = sorted(slist, key=lambda x: -(x.get("total") or 0))[:3]
                        top_sectors_str = ", ".join(s.get("sector", "") for s in top if s.get("sector"))
                except Exception:
                    pass

            cards.append({
                "committee": committee,
                "chamber": chamber,
                "chair": chair_name,
                "party": party,
                "total_raised": total_raised,
                "top_sectors": top_sectors_str,
                "kills_2024": yr_counts.get("2024", 0),
                "kills_2025": yr_counts.get("2025", 0),
                "kills_2026": yr_counts.get("2026", 0),
                "total_kills": total_kills,
            })

        # Sort by total kills desc, then by total_raised
        cards.sort(key=lambda c: (-c["total_kills"], -(c["total_raised"] or 0)))

        # Filter to chairs with at least 1 kill (or keep top 12 if all are 0)
        active = [c for c in cards if c["total_kills"] > 0]
        if not active:
            active = cards[:12]
        else:
            active = active[:15]

        if not active:
            return

        total_dead = sum(c["total_kills"] for c in active)
        lines = [
            "[Committee Gatekeeper Analysis — Virginia General Assembly 2024–2026]",
            f"Bills killed in committee across all tracked sessions: {total_dead:,}",
            f"Chairs shown: {len(active)} (ranked by bills killed)",
            "",
            f"{'Chair':<26} {'(P)'} {'Committee':<38} {'Chamber':<7} "
            f"{'2024':>5} {'2025':>5} {'2026':>5} {'Total':>6}  Top Donor Sectors",
        ]
        lines.append("-" * 140)

        for c in active:
            raised_str = f"${c['total_raised']/1000:.0f}k" if c["total_raised"] else "no data"
            lines.append(
                f"{c['chair']:<26} ({c['party']}) "
                f"{c['committee'][:38]:<38} {c['chamber']:<7} "
                f"{c['kills_2024']:>5} {c['kills_2025']:>5} {c['kills_2026']:>5} "
                f"{c['total_kills']:>6}  {c['top_sectors'] or '—'} [{raised_str}]"
            )

        lines += [
            "",
            "NOTE: 'Killed' = bill received status: Left in Committee, Tabled, Passed by Indefinitely, "
            "Stricken from Docket, or Failed to Report. Top Donor Sectors = industries that gave most "
            "to this chair's campaign fund. Cross-reference sector vs. committee jurisdiction to identify "
            "potential gatekeeper conflicts of interest.",
        ]

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


def _add_legislator_narrative_context(blocks: list[str], query: str) -> None:
    """
    When a specific legislator name appears in the query, inject their
    pre-generated civic profile narrative from legislator_narratives.
    Falls back to narrative_short for adjacent name matches.
    """
    conn = _connect("polls")
    if not conn:
        return
    if not _table_exists(conn, "legislator_narratives"):
        conn.close()
        return

    q_lower = (query or "").lower()

    # Try to find a legislator whose name appears in the query
    # Load all names once (small table, ~200 rows)
    try:
        rows = conn.execute(
            "SELECT legislator_id, name, narrative, narrative_short "
            "FROM legislator_narratives WHERE name IS NOT NULL"
        ).fetchall()
    except Exception:
        conn.close()
        return

    _HONORIFICS = frozenset({"jr.", "jr", "sr.", "sr", "iii", "ii", "iv"})
    matched = None
    matched_len = 0
    best_score = 0
    for row in rows:
        name = (row["name"] or "").lower()
        name_tokens = name.split()
        # Significant tokens: not an initial (ends with "."), not an honorific, len >= 3.
        # Score = count of significant tokens that appear in the query.
        # Requiring the first name to match (score >= 2) breaks "Phillip A. Scott"
        # matching a "Don Scott" query — both share "scott" but only Don shares "don".
        sig_tokens = [
            t for t in name_tokens
            if len(t) >= 3 and not t.endswith(".") and t not in _HONORIFICS
        ]
        score = sum(1 for t in sig_tokens if t in q_lower)
        if score == 0:
            continue
        if score > best_score or (score == best_score and len(name) > matched_len):
            matched = row
            matched_len = len(name)
            best_score = score

    if not matched:
        conn.close()
        return

    narrative = matched["narrative"] or matched["narrative_short"] or ""
    if not narrative:
        conn.close()
        return

    blocks.append(
        f"[RETRIEVED RECORD - Legislator Civic Profile: {matched['name']}]\n"
        f"{narrative}"
    )
    conn.close()


# ── VEC Financial Disclosures ─────────────────────────────────────────────────
_DISCLOSURES_TRIGGERS = re.compile(
    r"\b(financial\s+disclosures?|soei|statement\s+of\s+economic\s+interest|"
    r"conflict\s+of\s+interest|what\s+did\s+\w+\s+disclos|income\s+disclos|"
    r"disclos(?:es?|ed|ures?)|outside\s+income|financial\s+interest|"
    r"business\s+interest|investment\s+disclos|vec\s+disclos)\b",
    re.I,
)


def _add_disclosures_context(blocks: list[str], query: str) -> None:
    """
    Fires on financial disclosure / SOEI / conflict-of-interest queries.
    Pulls from legislator_financial_disclosures (VEC SOEI filings).
    If a legislator name is detected, returns their disclosures.
    Otherwise returns top 10 legislators by disclosure count.
    """
    if not _DISCLOSURES_TRIGGERS.search(query or ""):
        return

    conn = _connect("polls")
    if not conn:
        return

    try:
        if not _table_exists(conn, "legislator_financial_disclosures"):
            return

        q_lower = (query or "").lower()

        # Try to match a legislator name
        all_names = conn.execute(
            "SELECT DISTINCT legislator_name FROM legislator_financial_disclosures"
        ).fetchall()
        matched_name = None
        for (name,) in all_names:
            last = (name or "").split()[-1].lower() if name else ""
            if len(last) >= 4 and last in q_lower:
                if matched_name is None or len(name) > len(matched_name):
                    matched_name = name

        if matched_name:
            rows = conn.execute("""
                SELECT filing_year, part, entity_name, role_or_type,
                       sector, amount_range
                FROM legislator_financial_disclosures
                WHERE legislator_name = ?
                ORDER BY filing_year DESC, part, entity_name
                LIMIT 20
            """, (matched_name,)).fetchall()

            if not rows:
                return

            lines = [
                f"[VEC Financial Disclosures — {matched_name}]",
                "Source: Virginia Ethics Commission SOEI filings (public record)",
                "",
            ]
            for r in rows:
                amt = f" | {r[5]}" if r[5] else ""
                lines.append(
                    f"  {r[0]} | Part {r[1]} | {r[2]} | {r[3] or ''} | "
                    f"sector: {r[4] or 'unclassified'}{amt}"
                )
            blocks.append("\n".join(lines))

        else:
            # No name — return legislators with most disclosures
            rows = conn.execute("""
                SELECT legislator_name,
                       COUNT(*) as entries,
                       COUNT(DISTINCT filing_year) as years,
                       GROUP_CONCAT(DISTINCT sector) as sectors
                FROM legislator_financial_disclosures
                GROUP BY legislator_name
                ORDER BY entries DESC
                LIMIT 12
            """).fetchall()

            if not rows:
                return

            lines = [
                "[VEC Financial Disclosures — Top Filers]",
                "Source: Virginia Ethics Commission SOEI filings",
                "",
                f"{'Legislator':<30} {'Entries':>7} {'Years':>6}  Sectors",
            ]
            lines.append("-" * 90)
            for r in rows:
                sectors = (r[3] or "")[:50]
                lines.append(f"  {r[0]:<30} {r[1]:>7} {r[2]:>6}  {sectors}")

            blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


# ── Direct Lobbyist / Principal Queries ───────────────────────────────────────
_LOBBYIST_DIRECT_TRIGGERS = re.compile(
    r"\b(who\s+lobbies?\s+for|lobbyist\s+for|principal\s+name|"
    r"registered\s+lobbyist|lobbying\s+firm|lobbying\s+registr|"
    r"who\s+(?:is\s+)?registered\s+to\s+lobby|which\s+(?:companies|organizations?|groups?)\s+lobby|"
    r"top\s+lobbying|most\s+lobbyists?|how\s+many\s+lobbyists?)\b",
    re.I,
)


def _add_lobbyist_direct_context(blocks: list[str], query: str) -> None:
    """
    Fires on direct lobbyist/principal queries (not bill-specific).
    Pulls from lobbyist_registrations — top principals by lobbyist count
    or filtered by organization name if mentioned.
    """
    if not _LOBBYIST_DIRECT_TRIGGERS.search(query or ""):
        return

    conn = _connect("polls")
    if not conn:
        return

    try:
        if not _table_exists(conn, "lobbyist_registrations"):
            return

        q_lower = (query or "").lower()

        # Detect org name mention (3+ word match against principal names)
        q_words = set(w for w in re.split(r"\W+", q_lower) if len(w) > 4)

        # Most recent sessions
        rows = conn.execute("""
            SELECT principal_name,
                   COUNT(DISTINCT lobbyist_name) as lobbyist_count,
                   GROUP_CONCAT(DISTINCT year_range) as sessions
            FROM lobbyist_registrations
            WHERE status = 'Approved'
            GROUP BY principal_name
            ORDER BY lobbyist_count DESC
            LIMIT 20
        """).fetchall()

        if not rows:
            return

        # Filter to matching org if query words overlap principal name
        filtered = [
            r for r in rows
            if any(w in (r[0] or "").lower() for w in q_words)
        ]
        display = filtered[:8] if filtered else rows[:12]

        label = "Matching" if filtered else "Top"
        lines = [
            f"[Virginia Lobbyist Registrations — {label} Principals]",
            "Source: Virginia DLS lobbyist.dls.virginia.gov (legally required filings)",
            "",
            f"{'Principal':<45} {'Lobbyists':>9}  Sessions",
        ]
        lines.append("-" * 80)
        for r in display:
            lines.append(f"  {(r[0] or '')[:45]:<45} {r[1]:>9}  {r[2] or ''}")

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


# ── Sponsor–Donor Sector Correlation ─────────────────────────────────────────
_SPONSOR_CORR_TRIGGERS = re.compile(
    r"\b(sponsor.*donor|donor.*sponsor|who\s+funds?\s+(?:bill\s+)?sponsors?|"
    r"sector.*sponsors?|sponsors?.*sector|correlation|which\s+sectors?\s+fund|"
    r"industry\s+(?:that\s+)?funds?|donor\s+pattern)\b",
    re.I,
)


def _add_sponsor_correlation_context(blocks: list[str], query: str) -> None:
    """
    Fires on sponsor–donor sector correlation queries.
    Pulls from sponsor_donor_correlation — which donor sectors fund
    legislators who sponsor bills in each policy area.
    """
    if not _SPONSOR_CORR_TRIGGERS.search(query or ""):
        return

    conn = _connect("polls")
    if not conn:
        return

    try:
        if not _table_exists(conn, "sponsor_donor_correlation"):
            return

        # Get top correlations — most bills sponsored per legislator+sector
        rows = conn.execute("""
            SELECT legislator_name, sector, bills_sponsored,
                   total_donated, avg_donated_per_bill
            FROM sponsor_donor_correlation
            ORDER BY bills_sponsored DESC, total_donated DESC
            LIMIT 20
        """).fetchall()

        if not rows:
            return

        lines = [
            "[Sponsor–Donor Sector Correlation]",
            "Shows which donor sectors fund legislators who sponsor bills in each policy area.",
            "",
            f"{'Legislator':<28} {'Donor Sector':<26} {'Bills':>6} {'Total Donated':>14}",
        ]
        lines.append("-" * 80)
        for r in rows:
            donated = f"${r[3]:,.0f}" if r[3] else "—"
            lines.append(
                f"  {(r[0] or ''):<28} {(r[1] or ''):<26} {r[2]:>6} {donated:>14}"
            )

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


# ── Spike alert context ──────────────────────────────────────────────────────

_SPIKE_TRIGGERS = re.compile(
    r"\b(spike[sd]?|donation\s+spike|spending\s+spike|unusual\s+don(?:or|ation)|"
    r"watchlist\s+alert|trending\s+don(?:or|ation)|surge[sd]?|outlier)\b",
    re.I,
)


def _add_spike_alerts_context(blocks: list[str], query: str) -> None:
    """Inject spike-alert context when query asks about donation spikes or a flagged donor."""
    triggered = bool(_SPIKE_TRIGGERS.search(query))
    q_lower = query.lower()

    try:
        conn = _connect("polls")
        if conn is None:
            return

        # Also fire when any known flagged canonical_name appears in the query
        matched_name: str | None = None
        if not triggered:
            names = conn.execute(
                "SELECT DISTINCT canonical_name FROM spike_alerts WHERE seen = 0"
            ).fetchall()
            for row in names:
                name: str = row[0]
                # Require at least one word longer than 4 chars to match
                words = [w for w in name.lower().split() if len(w) > 4]
                if words and any(w in q_lower for w in words):
                    matched_name = name
                    triggered = True
                    break

        if not triggered:
            conn.close()
            return

        if matched_name:
            rows = conn.execute(
                """
                SELECT canonical_name, watch_label, election_cycle, cycle_parity,
                       total_amount, ratio_vs_mean, pct_change_prior, context_bills
                FROM spike_alerts
                WHERE canonical_name = ? AND seen = 0
                ORDER BY ratio_vs_mean DESC, watchlist_id ASC LIMIT 1
                """,
                (matched_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT canonical_name, watch_label, election_cycle, cycle_parity,
                       total_amount, ratio_vs_mean, pct_change_prior, context_bills
                FROM spike_alerts WHERE seen = 0
                ORDER BY ratio_vs_mean DESC LIMIT 6
                """
            ).fetchall()

        conn.close()
        if not rows:
            return

        lines = [
            "[Database Context - spike_alerts]",
            "Donors with statistically unusual contribution spikes flagged by the VoteIQ watchlist:",
        ]
        for r in rows:
            name, label, cycle, parity, amt, ratio, pct, ctx_raw = (
                r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
            )
            yr_type = "state-election year" if parity == "odd" else "federal-election year"
            pct_s = f", up {pct:.0f}% from prior {yr_type}" if pct else ""
            try:
                bills = json.loads(ctx_raw or "[]")
            except Exception:
                bills = []
            bill_s = (
                f" Related legislation this cycle: {', '.join(str(b) for b in bills[:3])}."
                if bills
                else ""
            )
            lines.append(
                f"• {name}: ${amt:,.0f} in {cycle} — {ratio:.1f}× their typical "
                f"{yr_type} giving{pct_s}. Trigger: {label}.{bill_s}"
            )
        blocks.append("\n".join(lines))
    except Exception:
        pass


def _add_dominion_recipient_ranking_context(blocks: list[str], query: str) -> None:
    """SQL-first ranked list of who received the most Dominion Energy money.

    Combines PAC contributions (is_individual=0, last_or_company pattern)
    and employer-tagged individual contributions (is_individual=1).
    Fires on queries asking about top recipients / Dominion funding / rankings.
    """
    if "dominion" not in (query or "").lower():
        return
    if not _DOM_RECIPIENT_TRIGGER.search(query or ""):
        return

    conn = _connect("polls")
    if conn is None:
        return

    try:
        like_clause = " OR ".join(
            "last_or_company LIKE ?" for _ in _DOM_PAC_LIKE_PATTERNS
        )
        pac_rows = conn.execute(
            f"""
            SELECT candidate_name, last_or_company, SUM(amount) as total
            FROM va_cf_schedule_a
            WHERE election_cycle = '2025'
              AND is_individual = 0
              AND ({like_clause})
            GROUP BY candidate_name, last_or_company
            """,
            _DOM_PAC_LIKE_PATTERNS,
        ).fetchall()

        emp_rows = conn.execute(
            """
            SELECT candidate_name, employer, SUM(amount) as total
            FROM va_cf_schedule_a
            WHERE election_cycle = '2025'
              AND is_individual = 1
              AND (employer LIKE '%ominion%' OR employer LIKE '%irginia Power%')
            GROUP BY candidate_name, employer
            """,
        ).fetchall()

        totals: dict[str, float] = {}
        pac_detail: dict[str, float] = {}
        ind_detail: dict[str, float] = {}

        for r in pac_rows:
            entity = r["last_or_company"] or ""
            if _DOM_PAC_EXCL_RE.search(entity):
                continue
            name = r["candidate_name"] or "Unknown"
            amt = float(r["total"] or 0)
            totals[name] = totals.get(name, 0) + amt
            pac_detail[name] = pac_detail.get(name, 0) + amt

        for r in emp_rows:
            if not _is_dominion_employer(r["employer"] or ""):
                continue
            name = r["candidate_name"] or "Unknown"
            amt = float(r["total"] or 0)
            totals[name] = totals.get(name, 0) + amt
            ind_detail[name] = ind_detail.get(name, 0) + amt

        if not totals:
            return

        candidates = list(totals.keys())
        placeholders = ",".join("?" * len(candidates))
        total_raised: dict[str, float] = {}
        for r in conn.execute(
            f"""
            SELECT candidate_name, SUM(amount) as total
            FROM va_cf_schedule_a
            WHERE election_cycle = '2025'
              AND candidate_name IN ({placeholders})
            GROUP BY candidate_name
            """,
            candidates,
        ).fetchall():
            total_raised[r["candidate_name"]] = float(r["total"] or 0)

        ranked = sorted(totals.items(), key=lambda x: -x[1])
        grand_total = sum(totals.values())

        out: list[str] = [
            "[Database Context - Dominion Recipient Ranking"
            " | Window: VA 2025 election_cycle"
            " | Source: SBE Schedule A"
            " | Method: SQL direct — not RAG]",
            "Donor definition: Dominion Energy PAC (is_individual=0, last_or_company"
            " matching Dominion utility entities) + employer-tagged individuals"
            " (is_individual=1). Not a sector rollup.",
            f"Total Dominion contributions (2025): ${grand_total:,.0f}"
            f" across {len(totals)} recipients",
            "",
            f"{'Rank':<5} {'Legislator':<42} {'Dominion $':>12} {'PAC $':>12}"
            f" {'Indiv $':>10} {'Total Raised':>13} {'Dom%':>6}",
            f"{'─'*5} {'─'*42} {'─'*12} {'─'*12} {'─'*10} {'─'*13} {'─'*6}",
        ]
        for rank, (name, dom_total) in enumerate(ranked[:30], 1):
            pac_amt = pac_detail.get(name, 0)
            ind_amt = ind_detail.get(name, 0)
            raised = total_raised.get(name, 0)
            if raised > 0:
                share_pct = dom_total / raised * 100
                if share_pct > 15:
                    tier = "[major]"
                elif share_pct >= 5:
                    tier = "[significant]"
                elif share_pct >= 1:
                    tier = "[modest]"
                else:
                    tier = "[minimal]"
                share_str = f"{share_pct:.1f}% {tier}"
            else:
                share_str = "N/A"
            out.append(
                f"{rank:<5} {name[:42]:<42} ${dom_total:>11,.0f}"
                f" ${pac_amt:>11,.0f} ${ind_amt:>9,.0f}"
                f" ${raised:>12,.0f} {share_str}"
            )
        if len(ranked) > 30:
            out.append(f"  … and {len(ranked) - 30} more recipients")
        out.append("")
        out.append(
            "Note: Dominion Share = Dominion $ ÷ Total Raised (all sources, 2025)."
            " Use median-primary statistics when comparing YES-voter vs NO-voter funding."
            " Do not assert causation."
        )
        blocks.append("\n".join(out))
    except Exception:
        pass
    finally:
        conn.close()


# ── Donor-vote alignment, electoral races, congressional hearings ─────────────
# Dedicated structured builders for analytically rich tables that the generic
# all-table keyword scan serves poorly: JSON-packed metrics, numeric rankings,
# and parent/child joins that a 3-row LIKE dump cannot surface usefully.

_ALIGNMENT_TRIGGER = re.compile(
    r"\b(alignment|aligned|vote[s]?\s+with\s+(?:their\s+)?donor|donor[\s-]*align|"
    r"sector\s+yes[\s-]*rate|voting\s+with\s+(?:the\s+)?money|"
    r"pay[\s-]*to[\s-]*vote|bought\s+vote)\b",
    re.I,
)

_RACE_TRIGGER = re.compile(
    r"\b(race|races|running\s+for|who\s+(?:is|are)\s+running|candidate[s]?|"
    r"election|primary|on\s+the\s+ballot|governor['’]?s?\s+race|"
    r"senate\s+race|house\s+race|attorney\s+general|lieutenant\s+governor|"
    r"lt\.?\s+governor)\b",
    re.I,
)

_HEARING_TRIGGER = re.compile(
    r"\b(hearing[s]?|testified|testimony\s+before|committee\s+hearing)\b",
    re.I,
)


def _add_donor_vote_alignment_context(blocks: list[str], query: str, terms: list[str]) -> None:
    """Per-legislator donor-sector vs vote alignment (donor_vote_alignment).

    Surfaces the structured alignment metrics (sector yes-rate vs other yes-rate,
    alignment delta) that the generic keyword scan can't read out of the packed
    by_sector_json column. Fires on alignment keywords or when a named legislator
    appears with alignment intent.
    """
    names = _person_terms(terms)
    triggered = bool(_ALIGNMENT_TRIGGER.search(query or ""))
    # A bare name (esp. a common first name like "Mark") must not pull alignment
    # rows on its own — require donor/vote intent alongside the name.
    has_money_or_vote = (
        _is_campaign_finance_query(query)
        or bool(re.search(r"\bvot(?:e|es|ed|ing)\b", query or "", re.I))
    )
    if not triggered and not (names and has_money_or_vote):
        return
    try:
        conn = _connect("polls")
        if conn is None:
            return
        if not _table_exists(conn, "donor_vote_alignment"):
            conn.close()
            return
        # The rebuilt table carries honest-framing columns (share of total +
        # any larger non-industry source). They are absent until the seed is
        # refreshed, so include them only when present.
        have = {c[1] for c in conn.execute(
            "PRAGMA table_info(donor_vote_alignment)"
        ).fetchall()}
        rich = {"top_sector_share", "nonindustry_dominant", "nonindustry_amt"} <= have
        base = (
            "name, party, chamber, top_donor_sector, top_sector_amt, "
            "sector_yes_rate, other_yes_rate, alignment_delta, "
            "sector_vote_count, other_vote_count"
        )
        cols = base + (
            ", top_sector_share, nonindustry_dominant, nonindustry_amt"
            if rich else ""
        )
        rows: list = []
        if names:
            clause, params = _like_any_clause(["name"], names)
            rows = conn.execute(
                f"SELECT {cols} FROM donor_vote_alignment WHERE {clause} "
                "ORDER BY ABS(alignment_delta) DESC LIMIT 4",
                tuple(params),
            ).fetchall()
        if not rows and triggered:
            rows = conn.execute(
                f"SELECT {cols} FROM donor_vote_alignment "
                "ORDER BY ABS(alignment_delta) DESC LIMIT 8"
            ).fetchall()
        conn.close()
        if not rows:
            return
        def _conf(n) -> str:
            if n is None or n < 10:  return "LOW"
            if n <= 30:              return "MEDIUM"
            return "HIGH"

        lines = [
            "[Database Context - donor_vote_alignment"
            " | Source: Virginia SBE (donor) + LegiScan VA (votes)"
            " | Method: SQL aggregate | Window: VA 2025-2026 session_cycle]",
            "How often a legislator votes YES on bills touching their largest INDUSTRY "
            "donor sector vs. their YES rate on all other bills. alignment_delta = "
            "sector_rate - other_rate (positive = votes more favorably on bills "
            "affecting their biggest industry funders). Correlation, not proof of causation.",
            "Sector classification is derived from VoteIQ taxonomy applied to FEC employer "
            "fields and bill subject metadata. This is not official federal categorization.",
        ]
        for r in rows:
            name, party, chamber, sector, amt, syr, oyr, delta, sc, oc = r[:10]
            amt_s   = f"${amt:,.0f}" if amt is not None else "n/a"
            syr_s   = f"{syr:.0f}%" if syr is not None else "n/a"
            oyr_s   = f"{oyr:.0f}%" if oyr is not None else "n/a"
            delta_s = f"{delta:+.1f}" if delta is not None else "n/a"
            sc_conf = _conf(sc)
            oc_conf = _conf(oc)
            extra = ""
            if rich:
                share, nonind, nonind_amt = r[10], r[11], r[12]
                if share is not None:
                    amt_s += f" / {share:.0f}% of total raised"
                if nonind:
                    extra = (
                        f" Their single largest source is {nonind} "
                        f"(${(nonind_amt or 0):,.0f}, non-industry party/leadership money)."
                    )
            # Determine whether the delta note should flag low confidence
            min_conf = sc_conf if (sc or 0) <= (oc or 0) else oc_conf
            if min_conf == "LOW":
                delta_note = f" — treat as directional signal only, not statistically meaningful given small {sector} sample."
            elif min_conf == "MEDIUM":
                delta_note = " — treat as directional signal; sample sizes are moderate."
            else:
                delta_note = "."
            lines.append(
                f"• {name} ({party}, {chamber}) — top industry donor sector: {sector} ({amt_s}).{extra}\n"
                f"  YES rate on {sector}-tagged bills: {syr_s} (n={sc}) — Confidence: {sc_conf}\n"
                f"  YES rate on all other bills: {oyr_s} (n={oc}) — Confidence: {oc_conf}\n"
                f"  Alignment delta: {delta_s} pp{delta_note}"
            )
        blocks.append("\n".join(lines))
    except Exception:
        pass


def _add_vpap_race_context(blocks: list[str], query: str) -> None:
    """Virginia electoral races and their candidates' fundraising.

    Joins vpap_races to vpap_candidates so 'who is running for X' / 'the YEAR
    governor race' questions get the field plus money raised/spent/cash-on-hand,
    instead of a keyword dump that can't relate candidates to their race.
    """
    if not _RACE_TRIGGER.search(query or ""):
        return
    try:
        conn = _connect("polls")
        if conn is None:
            return
        if not _table_exists(conn, "vpap_races"):
            conn.close()
            return
        q_lower = (query or "").lower()
        years = set(YEAR_RE.findall(query or ""))
        office_kw = [
            kw for kw in (
                "governor", "senate", "house", "attorney general",
                "lieutenant", "congress", "delegate",
            )
            if kw in q_lower
        ]
        race_rows = conn.execute(
            "SELECT race_key, office, district, year, election_date "
            "FROM vpap_races ORDER BY year DESC, office"
        ).fetchall()
        selected = []
        for r in race_rows:
            _rk, office, _district, year, _edate = r
            office_l = (office or "").lower()
            if years and str(year) not in years:
                continue
            if office_kw and not any(k in office_l for k in office_kw):
                continue
            selected.append(r)
        if not selected:
            selected = race_rows[:6]
        if not selected:
            conn.close()
            return
        lines = [
            "[Database Context - vpap_races / vpap_candidates]",
            "Virginia electoral races tracked by VoteIQ, with candidate fundraising "
            "(VPAP/FEC):",
        ]
        for r in selected[:6]:
            rk, office, district, year, edate = r
            dist_s = f" district {district}" if district else ""
            lines.append(f"\n{office}{dist_s} ({year}) — election {edate or 'TBD'}:")
            cands = conn.execute(
                "SELECT name, party, incumbent, money_raised, cash_on_hand "
                "FROM vpap_candidates WHERE race_key=? ORDER BY money_raised DESC",
                (rk,),
            ).fetchall()
            if not cands:
                lines.append("  (no candidates recorded yet)")
            for c in cands[:8]:
                nm, party, inc, raised, coh = c
                inc_s = " (incumbent)" if inc else ""
                raised_s = f"${raised:,.0f}" if raised else "$0"
                coh_s = f"${coh:,.0f}" if coh else "$0"
                lines.append(
                    f"  • {nm} ({party}){inc_s} — raised {raised_s}, "
                    f"cash on hand {coh_s}"
                )
        conn.close()
        blocks.append("\n".join(lines))
    except Exception:
        pass


def _add_congress_hearings_context(blocks: list[str], query: str, terms: list[str]) -> None:
    """Congressional hearing records for VA's federal delegation (congress_hearings).

    Matches by member name first, then by topic against the hearing title/text/
    committee, returning the most recent matches.
    """
    if not _HEARING_TRIGGER.search(query or ""):
        return
    try:
        conn = _connect("polls")
        if conn is None:
            return
        if not _table_exists(conn, "congress_hearings"):
            conn.close()
            return
        names = _person_terms(terms)
        rows: list = []
        if names:
            clause, params = _like_any_clause(["member_name"], names)
            rows = conn.execute(
                "SELECT member_name, chamber, committee, hearing_date, title "
                f"FROM congress_hearings WHERE {clause} "
                "ORDER BY hearing_date DESC LIMIT 6",
                tuple(params),
            ).fetchall()
        if not rows:
            topic = _generic_search_terms(query, terms)
            if topic:
                clause, params = _like_any_clause(["title", "text", "committee"], topic)
                rows = conn.execute(
                    "SELECT member_name, chamber, committee, hearing_date, title "
                    f"FROM congress_hearings WHERE {clause} "
                    "ORDER BY hearing_date DESC LIMIT 6",
                    tuple(params),
                ).fetchall()
        conn.close()
        if not rows:
            return
        lines = [
            "[Database Context - congress_hearings]",
            "Congressional hearing records involving Virginia's federal delegation:",
        ]
        for r in rows:
            nm, chamber, committee, hdate, title = r
            comm_s = f" — {committee}" if committee else ""
            lines.append(f"• {hdate}: {title} ({nm}, {chamber}{comm_s})")
        blocks.append("\n".join(lines))
    except Exception:
        pass


# ── Vote similarity ────────────────────────────────────────────────────────────
_VOTE_SIMILARITY_RE = re.compile(
    r"(?:"
    r"vote[sd]?\s+(?:most\s+)?similar(?:ly)?(?:\s+(?:to|with))?"
    r"|similar\s+voting\s+record"
    r"|most\s+(?:aligned|similar)\s+(?:with|to)"
    r"|votes?\s+(?:the\s+same|alike)\s+(?:as|with)"
    r"|voting\s+record\s+similar\s+to"
    r"|who\s+(?:votes?|voted)\s+(?:most\s+)?(?:with|like)\s"
    r"|most\s+similar\s+legislat"
    r"|voting\s+alli(?:es|y)"
    r")",
    re.I,
)


def _add_vote_similarity_context(blocks: list[str], query: str) -> None:
    """Compute legislator vote-agreement rates for similarity queries.

    Joins va_legislator_recent_votes on bill_id+session across all available
    sessions (2025+2026) so cross-session variation surfaces intra-party
    divergences masked by end-of-session caucus discipline.

    Output splits into:
      - Same-party allies (top 5): for intra-caucus comparison
      - Cross-party bipartisan allies (top 10): higher signal for coalition work

    When all top same-party scores are ≥95%, an explanatory note is added
    warning that same-caucus lockstep voting inflates these scores.
    """
    if not _VOTE_SIMILARITY_RE.search(query):
        return

    name_candidates = re.findall(
        r"\b([A-Z][a-zA-Z]+(?:\.?\s+[A-Z][a-zA-Z]+)+)\b", query
    )
    if not name_candidates:
        return

    MIN_SHARED = 10
    TOP_SAME   = 5
    TOP_CROSS  = 10

    try:
        conn = sqlite3.connect(_POLLS_DB, timeout=10)
        conn.row_factory = sqlite3.Row

        # Resolve the target legislator name against the DB (all sessions)
        target_name: str | None = None
        for candidate in name_candidates:
            row = conn.execute(
                "SELECT DISTINCT voter_name FROM va_legislator_recent_votes "
                "WHERE voter_name = ? LIMIT 1",
                (candidate,),
            ).fetchone()
            if not row:
                last = candidate.strip().split()[-1]
                if len(last) >= 4:
                    row = conn.execute(
                        "SELECT DISTINCT voter_name FROM va_legislator_recent_votes "
                        "WHERE lower(voter_name) LIKE ? LIMIT 1",
                        (f"%{last.lower()}%",),
                    ).fetchone()
            if row:
                target_name = row["voter_name"]
                break

        if not target_name:
            conn.close()
            return

        # Look up target legislator's party/chamber (prefer 2026, fall back to any session)
        meta_row = conn.execute(
            "SELECT party, chamber FROM va_legislator_vote_summary "
            "WHERE voter_name = ? ORDER BY session DESC LIMIT 1",
            (target_name,),
        ).fetchone()
        target_party   = (meta_row["party"]   if meta_row else "") or ""
        target_chamber = (meta_row["chamber"] if meta_row else "") or ""

        rows = conn.execute(
            """
            WITH target AS (
                SELECT DISTINCT bill_id, session, option
                FROM va_legislator_recent_votes
                WHERE voter_name = ?
                  AND option IN ('yes', 'no')
            ),
            others AS (
                SELECT DISTINCT voter_name, bill_id, session, option
                FROM va_legislator_recent_votes
                WHERE option IN ('yes', 'no')
                  AND voter_name != ?
            )
            SELECT
                o.voter_name,
                COALESCE(v.chamber, '') AS chamber,
                COALESCE(v.party,   '') AS party,
                COUNT(*)                                                        AS shared_votes,
                SUM(CASE WHEN t.option = o.option THEN 1 ELSE 0 END)           AS agreed,
                ROUND(
                    100.0 * SUM(CASE WHEN t.option = o.option THEN 1 ELSE 0 END)
                    / COUNT(*), 1
                )                                                               AS agreement_pct
            FROM target t
            JOIN others o ON t.bill_id = o.bill_id AND t.session = o.session
            LEFT JOIN va_legislator_vote_summary v
                   ON v.voter_name = o.voter_name AND v.session = '2026'
            GROUP BY o.voter_name
            HAVING COUNT(*) >= ?
            ORDER BY agreement_pct DESC
            """,
            (target_name, target_name, MIN_SHARED),
        ).fetchall()

        conn.close()

        if not rows:
            return

        # Require a known (non-empty) party on both sides to avoid misclassifying
        # legislators whose party field is missing in the summary table.
        same_party  = [r for r in rows if target_party and r["party"] == target_party][:TOP_SAME]
        cross_party = [r for r in rows if r["party"] and r["party"] != target_party][:TOP_CROSS]

        lines = [
            "[Database Context - vote_similarity]",
            f"target_legislator={target_name}",
            f"target_party={target_party or 'unknown'}",
            f"target_chamber={target_chamber or 'unknown'}",
            "sessions=2025-2026",
            "method=bill_vote_agreement_rate — option match on shared bill_id+session",
            f"minimum_shared_votes={MIN_SHARED}",
            "source=va_legislator_recent_votes",
            "note=Only yes/no votes compared. not-voting and abstain excluded. "
            "DISTINCT applied to deduplicate source rows.",
            "",
        ]

        # ── Same-party section ────────────────────────────────────────────────
        if same_party:
            all_high = all(r["agreement_pct"] >= 95.0 for r in same_party)
            lines += [
                f"## Same-party allies ({target_party or 'unknown'}, top {TOP_SAME})",
                f"Legislators with most similar voting records to {target_name} "
                f"within {target_party or 'their'} caucus (2025-2026 sessions):",
                "| Rank | Legislator | Chamber | Shared Votes | Agreement % |",
                "|---|---|---|---:|---:|",
            ]
            for i, row in enumerate(same_party, 1):
                lines.append(
                    f"| {i} | {row['voter_name']} | {row['chamber'] or '—'} "
                    f"| {row['shared_votes']} | {row['agreement_pct']}% |"
                )
            if all_high:
                lines += [
                    "",
                    "NOTE: All top same-caucus scores are ≥95%. Same-caucus, same-chamber "
                    "legislators typically vote together on 90–100% of floor votes due to "
                    "party discipline. Cross-party allies below are more informative for "
                    "bipartisan coalition signals.",
                ]
        else:
            lines.append(f"(No same-party allies found with ≥{MIN_SHARED} shared votes.)")

        lines.append("")

        # ── Cross-party section ───────────────────────────────────────────────
        if cross_party:
            lines += [
                f"## Cross-party bipartisan allies (top {TOP_CROSS})",
                f"Legislators from other parties with most similar voting records "
                f"to {target_name} (2025-2026 sessions):",
                "| Rank | Legislator | Chamber | Party | Shared Votes | Agreement % |",
                "|---|---|---|---|---:|---:|",
            ]
            for i, row in enumerate(cross_party, 1):
                lines.append(
                    f"| {i} | {row['voter_name']} | {row['chamber'] or '—'} "
                    f"| {row['party'] or '—'} | {row['shared_votes']} "
                    f"| {row['agreement_pct']}% |"
                )
        else:
            lines.append(f"(No cross-party allies found with ≥{MIN_SHARED} shared votes.)")

        lines += [
            "",
            f"Agreement % = votes where both legislators cast the same option (yes/no) "
            f"on the same bill in the same session, divided by total shared votes "
            f"across 2025-2026 sessions. "
            f"Legislators with fewer than {MIN_SHARED} shared votes are excluded to "
            f"prevent misleading scores from small samples.",
        ]
        blocks.append("\n".join(lines))

    except Exception:
        pass


_NORFOLK_COUNCIL_TRIGGER = re.compile(
    r"\b("
    r"norfolk\s+(city\s+)?council"
    r"|city\s+council\s+vote"
    r"|norfolk\s+vote"
    r"|council\s+member\s+(alexander|clanton|doyle|johnson|mcgee|paige|smigiel|thomas)"
    r"|norfolk\s+(mayor|councilmember|councilman|councilwoman)"
    r"|norfolk\s+(passed|voted|approved|denied|tabled)"
    r")\b",
    re.I,
)
_NORFOLK_MEMBER_NAMES = {
    "alexander", "clanton", "doyle", "johnson", "mcgee",
    "paige", "smigiel", "thomas", "royster", "mcclellan",
    "riddick",   # on council 2022–2023, before Paige/Clanton/McGee joined
}

# ── Norfolk factsheet + lint helpers ─────────────────────────────────────────

CONTESTED_VOTE_MIN = 100  # body-level minimum for comparative member differentiation

_NORFOLK_BANNED_OUTPUT_TERMS = frozenset([
    # Causal / intent (from donor-vote schema rework)
    "captured", "market protection", "bought", "because donors",
    "in exchange", "rewarded", "benefit directly",
    "paid to vote", "quid pro quo", "builders fund him",
    "because he opposes", "because she", "hotels benefit",
    # Ideological / motive (factsheet addition — per Step 5)
    "ideology", "ideological", "liberal", "conservative",
    "progressive", "believes", " stance", "is pro-", "is anti-",
    "aligns with the", "represents a", "characterized as",
])


def _lint_norfolk_output(text: str) -> str:
    """Raise ValueError if any banned causal or ideological term appears in output."""
    low = text.lower()
    for term in _NORFOLK_BANNED_OUTPUT_TERMS:
        if term in low:
            raise ValueError(f"[NORFOLK-LINT FAIL] Banned term: {repr(term)}")
    return text


def _norfolk_body_summary(conn: "sqlite3.Connection") -> list[str]:
    """Body-level consensus summary with methodology gate."""
    body = conn.execute("""
        SELECT COUNT(*) total,
            SUM(CASE WHEN vote_count IN ('8-0','7-0','6-0','5-0','0-8','0-7','0-6','0-5')
                     THEN 1 ELSE 0 END) unani
        FROM norfolk_council_votes WHERE vote_count != ''
    """).fetchone()
    if not body or not body[0]:
        return []

    total, unani = body[0], body[1] or 0
    contested = total - unani
    consensus_pct = round(100 * unani / total, 1)

    dates = conn.execute(
        "SELECT MIN(meeting_date), MAX(meeting_date) FROM norfolk_council_votes"
    ).fetchone()
    date_range = f"{dates[0]} – {dates[1]}" if dates and dates[0] else "2024–2026"

    lines: list[str] = [
        f"### Norfolk City Council — Body Summary ({date_range})",
        f"Recorded votes: {total}  |  Consensus rate: {consensus_pct}% "
        f"({unani} unanimous or near-unanimous)  |  "
        f"Contested: {contested} ({round(100*contested/total, 1)}%)",
    ]

    member_rows = conn.execute("""
        SELECT
            CASE WHEN member_name LIKE 'Smigiel%' THEN 'Smigiel Jr.'
                 WHEN member_name LIKE 'Thomas%'  THEN 'Thomas Jr.'
                 ELSE member_name END AS nm,
            COUNT(*) total,
            SUM(CASE WHEN vote='yes'    THEN 1 ELSE 0 END) yes_v,
            SUM(CASE WHEN vote='no'     THEN 1 ELSE 0 END) no_v,
            SUM(CASE WHEN vote='absent' THEN 1 ELSE 0 END) absent_v
        FROM norfolk_council_member_votes
        GROUP BY nm ORDER BY nm
    """).fetchall()

    if member_rows:
        lines += [
            "",
            f"{'Member':<20} {'Votes':>6} {'YES':>5} {'NO':>5} "
            f"{'Abs':>5} {'YES%':>6} {'NO%':>5}",
            "-" * 62,
        ]
        for r in member_rows:
            tot = r[1] or 1
            lines.append(
                f"{r[0]:<20} {r[1]:>6} {r[2]:>5} {r[3]:>5} {r[4]:>5} "
                f"{100*r[2]/tot:>5.1f}% {100*r[3]/tot:>4.1f}%"
            )

    gate_pass = contested >= CONTESTED_VOTE_MIN
    lines += [
        "",
        "**Methodology gate**",
        f"Contested vote count: {contested}  |  Threshold (CONTESTED_VOTE_MIN): {CONTESTED_VOTE_MIN}",
        (
            f"GATE NOT MET — comparative member rankings, agreement-score clustering, "
            f"and ideal-point analysis are suppressed. "
            f"This body decides by consensus ({consensus_pct}% of votes unanimous or near-unanimous); "
            f"the contested-vote sample ({contested} votes) is below the minimum required "
            f"for individual differentiation. Descriptive factsheets are available per member."
        ) if not gate_pass else (
            f"GATE MET — {contested} contested votes exceed threshold; "
            f"comparative analysis may proceed."
        ),
    ]
    return lines


def _norfolk_member_factsheet(
    conn: "sqlite3.Connection", member_term: str
) -> list[str]:
    """Per-member descriptive voting factsheet (no comparative ranking)."""
    display = member_term.title()
    if member_term == "smigiel":
        display = "Smigiel Jr."
    elif member_term == "thomas":
        display = "Thomas Jr."
    like = f"%{member_term}%"

    raw = conn.execute("""
        SELECT COUNT(*) total,
            SUM(CASE WHEN vote='yes'     THEN 1 ELSE 0 END) yes_v,
            SUM(CASE WHEN vote='no'      THEN 1 ELSE 0 END) no_v,
            SUM(CASE WHEN vote='abstain' THEN 1 ELSE 0 END) abs_v,
            SUM(CASE WHEN vote='absent'  THEN 1 ELSE 0 END) absent_v
        FROM norfolk_council_member_votes WHERE LOWER(member_name) LIKE ?
    """, (like,)).fetchone()
    if not raw or not raw[0]:
        return [f"No vote records found for '{member_term}'."]

    total, yes_v, no_v, abs_v, absent_v = raw
    participated = total - (absent_v or 0)
    yes_rate = round(100 * yes_v / participated, 1) if participated else 0
    no_rate  = round(100 * no_v  / participated, 1) if participated else 0

    dissents = conn.execute("""
        SELECT mv.meeting_date, mv.agenda_item, mv.vote, v.result, v.vote_count,
               v.title, e.plain_english
        FROM norfolk_council_member_votes mv
        JOIN norfolk_council_votes v ON mv.vote_id = v.id
        LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
        WHERE LOWER(mv.member_name) LIKE ?
          AND ((mv.vote = 'no' AND v.result = 'passed')
            OR (mv.vote = 'yes' AND v.result = 'failed'))
        ORDER BY mv.meeting_date DESC
    """, (like,)).fetchall()

    contested_body = conn.execute("""
        SELECT COUNT(*) FROM norfolk_council_votes
        WHERE vote_count != ''
          AND vote_count NOT IN ('8-0','7-0','6-0','5-0','0-8','0-7','0-6','0-5')
    """).fetchone()[0]
    contested_member = conn.execute("""
        SELECT COUNT(*) FROM norfolk_council_member_votes mv
        JOIN norfolk_council_votes v ON mv.vote_id = v.id
        WHERE LOWER(mv.member_name) LIKE ?
          AND mv.vote IN ('yes','no','abstain')
          AND v.vote_count NOT IN ('8-0','7-0','6-0','5-0','0-8','0-7','0-6','0-5','')
    """, (like,)).fetchone()[0]

    topic_rows = conn.execute("""
        SELECT COALESCE(e.topic, v.category, 'other') AS topic,
               COUNT(*) total,
               SUM(CASE WHEN mv.vote='yes' THEN 1 ELSE 0 END) yes_v,
               SUM(CASE WHEN mv.vote='no'  THEN 1 ELSE 0 END) no_v
        FROM norfolk_council_member_votes mv
        JOIN norfolk_council_votes v ON mv.vote_id = v.id
        LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
        WHERE LOWER(mv.member_name) LIKE ?
          AND mv.vote IN ('yes','no')
        GROUP BY COALESCE(e.topic, v.category, 'other')
        ORDER BY total DESC LIMIT 12
    """, (like,)).fetchall()

    dates = conn.execute(
        "SELECT MIN(meeting_date), MAX(meeting_date) FROM norfolk_council_member_votes "
        "WHERE LOWER(member_name) LIKE ?", (like,)
    ).fetchone()
    date_range = f"{dates[0]} – {dates[1]}" if dates and dates[0] else "2024–2026"

    dissent_pct = round(100 * len(dissents) / participated, 2) if participated else 0
    gate_pass = contested_body >= CONTESTED_VOTE_MIN

    # Sole-dissent detection
    sole_dissents = []
    for r in dissents:
        tally = r[4] or ""
        if tally and "-" in tally:
            parts = tally.split("-")
            if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
                if min(int(parts[0]), int(parts[1])) == 1:
                    sole_dissents.append(r)

    lines: list[str] = [
        f"### {display} — Voting Factsheet (Norfolk City Council, {date_range})",
        "",
        "**Raw facts**",
        f"Votes recorded: {total}  |  Participated (non-absent): {participated}",
        f"YES: {yes_v} ({yes_rate}%)  |  NO: {no_v} ({no_rate}%)"
        f"  |  Abstain: {abs_v or 0}  |  Absent: {absent_v or 0}",
        "",
        "**Derived metrics**",
        f"YES rate: {yes_rate}%  |  NO rate: {no_rate}%",
        f"Dissent rate (voted against majority outcome): "
        f"{dissent_pct:.2f}% ({len(dissents)} of {participated})",
        f"Contested-vote participation: {contested_member} of {contested_body} "
        f"contested votes in dataset",
    ]

    if topic_rows:
        lines += ["", "**Per-topic record**"]
        lines.append(f"  {'Topic':<28} {'Total':>6}  {'YES':>5}  {'NO':>5}")
        for r in topic_rows:
            if r[1] < 3:
                continue
            lines.append(
                f"  {r[0]:<28} {r[1]:>6}  {r[2]:>5}  {r[3]:>5}"
            )

    if dissents:
        lines += ["", "**Notable dissents (voted against majority outcome)**"]
        for i, r in enumerate(dissents, 1):
            tally = r[4] or ""
            sole = False
            if tally and "-" in tally:
                parts = tally.split("-")
                if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
                    sole = min(int(parts[0]), int(parts[1])) == 1
            flag = " [sole dissent]" if sole else ""
            desc = (r[6] or r[5] or "")[:72]
            lines.append(
                f"  {i}. {r[0]} | {r[1]} | {r[2].upper()} | {r[4]} {r[3]}{flag}"
            )
            if desc:
                lines.append(f"     {desc}")
    else:
        lines.append("**Notable dissents**: none — all votes aligned with majority outcome")

    # Interpretive signal (neutral behavioral flag, no motive)
    sole_note = (
        f"Anomalous: {len(sole_dissents)} sole-dissent vote(s) against majority. "
        if sole_dissents else ""
    )
    lines += [
        "",
        "**Interpretive signal**",
        f"{sole_note}Dissent rate: {dissent_pct:.2f}% ({len(dissents)} dissents / "
        f"{participated} participated). No motive or characterization is inferred.",
        "",
        "**Methodology note**",
        f"Body contested votes: {contested_body}  |  CONTESTED_VOTE_MIN: {CONTESTED_VOTE_MIN}",
        "Below threshold — comparative rankings suppressed; descriptive record only."
        if not gate_pass else
        "Threshold met — comparative analysis available.",
    ]

    # Lint interpretive lines (skip raw vote-data lines starting with numbering/spaces)
    linted: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        is_raw_data = stripped[:3].replace(".", "").replace(" ", "").isdigit()
        if is_raw_data:
            linted.append(ln)
        else:
            try:
                linted.append(_lint_norfolk_output(ln))
            except ValueError as e:
                linted.append(f"  [SUPPRESSED — {e}]")
    return linted


def _add_norfolk_council_context(blocks: list[str], query: str, terms: list[str]) -> None:
    """Inject Norfolk City Council vote records.

    Fires when the query mentions Norfolk city council, a council member by
    last name with vote intent, or Norfolk local government actions.
    """
    q_lower = (query or "").lower()
    named_members = [n for n in _NORFOLK_MEMBER_NAMES if n in q_lower]
    triggered = bool(_NORFOLK_COUNCIL_TRIGGER.search(query or ""))

    # Also fire when a council member name appears with vote/council/donor/testimony intent
    if not triggered:
        member_hit = bool(named_members)
        action_intent = any(
            w in q_lower
            for w in ("vote", "voted", "votes", "voting", "council", "norfolk",
                      "donor", "fund", "money", "contribut", "financ", "receiv",
                      "paid", "backed", "hotel", "pathway", "developer", "corrupt",
                      "align", "interest",
                      "testif", "testimony", "constituent", "resident", "public",
                      "spoke", "said", "comment", "hearing", "oppos", "support",
                      "dissent", "pattern", "differently", "profile", "outlier",
                      "contrarian", "disagree", "record")
        )
        triggered = member_hit and action_intent

    # Also fire for Norfolk + testimony/donor-adjacency intent (no member name needed)
    if not triggered and "norfolk" in q_lower:
        triggered = any(w in q_lower for w in (
            "testif", "testimony", "public comment", "public hearing",
            "who spoke", "spoke against", "spoke in support",
            "public oppos", "public sent", "residents oppos",
            "voted against public", "against the public", "public accountability",
            "donor", "overlap", "adjacen", "align", "sector", "who fund",
            "member", "council member",
            "sentiment", "opinion", "oppose most", "most opposed",
            "unpopular", "public feel", "community feel", "public mood",
            "what do people", "what do residents", "what does the public",
            "resistance", "pushback", "public resist", "public push",
            "norfolk oppos",
            "bloc", "coalition", "vote with", "votes with",
            "vote together", "votes together", "who votes with",
            "voting pattern", "most independent", "vote alike",
            "swing vote", "agree with", "alignment",
            "ward", "overrul", "overrod", "ignored", "which community",
            "split vote", "split the council", "votes split", "council split",
            "close vote", "contested", "most divided", "divided vote",
            "fault line", "controversial", "divided council",
            "most contentious", "tight vote", "most disputed",
            "who represents ward", "represents ward", "ward rep",
            "ward representative", "council member for ward",
            "who is ward", "who holds ward", "who sits on",
            "attend", "absence", "absent", "missed", "who misses",
            "most absent", "participation", "never absent",
            "listen", "responsive", "scorecard", "accountability score",
            "who listens", "who ignores", "votes with constituents",
            "last meeting", "recent meeting", "latest meeting",
            "what happened", "meeting recap", "council meeting",
            "trend", "over time", "growing", "increasing",
            "opposition trend", "more opposed", "getting worse",
            "superward", "super ward",
            "upcoming", "next meeting", "next council",
            "next session", "future meeting", "agenda",
        ))

    if not triggered:
        return

    try:
        conn = _connect("polls")
        if conn is None:
            return
        if not _table_exists(conn, "norfolk_council_votes"):
            conn.close()
            return

        lines: list[str] = ["## Norfolk City Council Vote Records"]

        # ── Routing: factsheet and body-summary modes ─────────────────────────
        _has_record_kw = any(kw in q_lower for kw in (
            "factsheet", "voting record", "full record",
        ))
        _has_show_kw = (
            any(kw in q_lower for kw in ("show me", "tell me about")) and "record" in q_lower
        )
        factsheet_mode = bool(named_members) and (_has_record_kw or _has_show_kw)

        body_summary_mode = (
            not named_members
            and any(kw in q_lower for kw in (
                "how does", "body summary", "consensus", "voting pattern",
                "overall voting", "as a body", "whole council",
            ))
        )

        if factsheet_mode:
            fs_lines: list[str] = ["## Norfolk City Council Vote Records", ""]
            for m in named_members[:2]:
                fs_lines += _norfolk_member_factsheet(conn, m)
                fs_lines.append("")
            if len(fs_lines) > 3:
                blocks.append("\n".join(fs_lines))
            conn.close()
            return

        if body_summary_mode:
            bs_lines = (
                ["## Norfolk City Council Vote Records", ""]
                + _norfolk_body_summary(conn)
            )
            blocks.append("\n".join(bs_lines))
            conn.close()
            return

        # ── Per-member vote summary ───────────────────────────────────────────
        # Normalize variant name forms across meetings (e.g. "Smigiel" vs "Smigiel Jr.")
        member_rows = conn.execute("""
            SELECT
                CASE
                    WHEN member_name LIKE 'Smigiel%' THEN 'Smigiel Jr.'
                    WHEN member_name LIKE 'Thomas%'  THEN 'Thomas Jr.'
                    ELSE member_name
                END AS norm_name,
                COUNT(*) AS total,
                SUM(CASE WHEN vote = 'yes'     THEN 1 ELSE 0 END) AS yes_v,
                SUM(CASE WHEN vote = 'no'      THEN 1 ELSE 0 END) AS no_v,
                SUM(CASE WHEN vote = 'abstain' THEN 1 ELSE 0 END) AS abstain_v,
                SUM(CASE WHEN vote = 'absent'  THEN 1 ELSE 0 END) AS absent_v
            FROM norfolk_council_member_votes
            GROUP BY norm_name
            ORDER BY no_v DESC, norm_name
        """).fetchall()

        if member_rows:
            lines += ["", "### Council Member Vote Summary"]
            lines.append(
                f"{'Member':<20} {'Total':>5} {'Yes':>5} {'No':>5} "
                f"{'Abstain':>8} {'Absent':>7}"
            )
            lines.append("-" * 58)
            for r in member_rows:
                lines.append(
                    f"{r[0]:<20} {r[1]:>5} {r[2]:>5} {r[3]:>5} {r[4]:>8} {r[5]:>7}"
                )


        # ── Recent votes matching query terms ─────────────────────────────────
        _NORFOLK_STOP_WORDS = {
            "norfolk", "city", "council", "vote", "votes", "voted", "voting",
            "how", "does", "what", "when", "who", "did", "member", "members",
        }
        topic_terms = [
            t for t in terms
            if t.lower() not in _NORFOLK_MEMBER_NAMES
            and t.lower() not in _NORFOLK_STOP_WORDS
            and len(t) > 3
        ]
        if topic_terms:
            like_clauses = " OR ".join(
                "LOWER(v.title) LIKE ?" for _ in topic_terms
            )
            params = [f"%{t.lower()}%" for t in topic_terms]
            topic_rows = conn.execute(f"""
                SELECT v.meeting_date, v.agenda_item, v.title, v.vote_count,
                       v.result, v.votes_json, e.plain_english, e.topic,
                       e.state_tags, e.federal_tags
                FROM norfolk_council_votes v
                LEFT JOIN norfolk_vote_enrichment e ON v.title = e.title
                WHERE ({like_clauses})
                ORDER BY v.meeting_date DESC
                LIMIT 10
            """, params).fetchall()

            if topic_rows:
                lines += ["", f"### Votes matching query ({', '.join(topic_terms[:3])})"]
                for r in topic_rows:
                    member_votes = json.loads(r[5] or "{}")
                    vote_str = ", ".join(
                        f"{m}: {v}" for m, v in sorted(member_votes.items())
                    )
                    topic_tag = f" [{r[7]}]" if r[7] else ""
                    desc = r[6] or r[2][:80]
                    lines.append(f"{r[0]} | {r[1]} | {r[3]} {r[4]}{topic_tag}")
                    lines.append(f"  {desc}")
                    # Show state/federal cross-references when enriched
                    state_tags = json.loads(r[8] or "[]")
                    fed_tags   = json.loads(r[9] or "[]")
                    if state_tags:
                        lines.append(f"  State law context: {', '.join(state_tags)}")
                    if fed_tags:
                        lines.append(f"  Federal context: {', '.join(fed_tags)}")
                    if vote_str:
                        lines.append(f"  Votes: {vote_str}")

        # ── Member-specific voting history ────────────────────────────────────
        # named_members derived at function entry before trigger check
        if named_members:
            for member in named_members[:2]:
                member_votes_rows = conn.execute("""
                    SELECT mv.meeting_date, mv.agenda_item, mv.title, mv.vote, mv.result
                    FROM norfolk_council_member_votes mv
                    WHERE LOWER(mv.member_name) LIKE ?
                    ORDER BY mv.meeting_date DESC
                    LIMIT 20
                """, (f"%{member}%",)).fetchall()

                if member_votes_rows:
                    lines += ["", f"### {member.title()} — Recent Votes"]
                    for r in member_votes_rows:
                        lines.append(
                            f"  {r[0]} | {r[1]} | {r[3].upper():>7} | {r[4]} | {r[2][:65]}"
                        )

        # ── Donor × vote cross-reference ─────────────────────────────────────
        # Map council member last names to their SBE campaign finance records.
        # Shows top donor employer categories alongside their dissent pattern.
        _SBE_NAME_MAP = {
            "alexander": "Alexander", "clanton": "Clanton",
            "doyle": "Doyle",        "johnson": "Johnson",
            "mcgee": "McGee",        "paige": "Paige",
            "smigiel": "Smigiel",    "thomas": "Thomas",
            "royster": "Royster",    "mcclellan": "McClellan",
        }

        # Ward / Superward → current council representative (public record, Norfolk City Council)
        # Transitions: Riddick→Paige (Jan 2023, SW7), McClellan→Clanton + Royster→McGee (Jan 2025, W5/SW6)
        _WARD_REP: dict[int, str] = {
            1: "Smigiel Jr.",
            2: "Doyle",
            3: "Johnson",
            4: "Thomas Jr.",
            5: "Clanton",    # replaced McClellan Jan 2025
            6: "McGee",      # replaced Royster Jan 2025
            7: "Paige",      # replaced Riddick Jan 2023
        }
        _SEAT_LABEL: dict[int, str] = {
            1: "Ward 1", 2: "Ward 2", 3: "Ward 3",
            4: "Ward 4", 5: "Ward 5",
            6: "Superward 6", 7: "Superward 7",
        }
        # Reverse: vote-data name → (ward_num, seat_label)  — includes former reps
        _REP_WARD: dict[str, tuple[int, str]] = {
            "Smigiel Jr.": (1, "Ward 1"),
            "Doyle":        (2, "Ward 2"),
            "Johnson":      (3, "Ward 3"),
            "Thomas Jr.":   (4, "Ward 4"),
            "Clanton":      (5, "Ward 5"),
            "McClellan":    (5, "Ward 5"),      # former (–Dec 2024)
            "McGee":        (6, "Superward 6"),
            "Royster":      (6, "Superward 6"), # former (–Dec 2024)
            "Paige":        (7, "Superward 7"),
            "Riddick":      (7, "Superward 7"), # former (–Dec 2022)
            "Alexander":    (0, "Mayor (at-large)"),
        }

        donor_finance_trigger = (
            any(w in q_lower for w in ("donor", "fund", "money", "contribut", "financ",
                                       "who pays", "paid by", "backed by", "receiv"))
            or named_members
        )
        if donor_finance_trigger and _table_exists(conn, "norfolk_finance_summary"):
            # Use precomputed sector totals (name-disambiguated via build_norfolk_finance.py)
            target_members = named_members if named_members else list(_SBE_NAME_MAP.keys())
            donor_lines: list[str] = []
            for m in target_members[:4]:
                display = _SBE_NAME_MAP.get(m, m.title())
                summary = conn.execute(
                    "SELECT total_raised, top_sector, top_sector_pct, sector_json "
                    "FROM norfolk_finance_summary WHERE member_name=?",
                    (display,)
                ).fetchone()
                sectors = conn.execute(
                    "SELECT sector, total_amount, pct_of_total, donor_count "
                    "FROM norfolk_finance_totals WHERE member_name=? "
                    "ORDER BY total_amount DESC LIMIT 5",
                    (display,)
                ).fetchall()
                no_votes = conn.execute("""
                    SELECT mv.title, mv.meeting_date
                    FROM norfolk_council_member_votes mv
                    JOIN norfolk_council_votes v ON mv.vote_id = v.id
                    WHERE LOWER(mv.member_name) LIKE ?
                      AND mv.vote = 'no'
                      AND v.category = 'substantive'
                    ORDER BY mv.meeting_date DESC
                    LIMIT 8
                """, (f"%{m}%",)).fetchall()

                if summary or no_votes:
                    donor_lines.append(f"\n#### {display} — Donors & Dissent")
                    if summary and sectors:
                        total_raised = summary[0]
                        donor_lines.append(
                            f"Total raised: ${total_raised:,.0f}  "
                            f"(top sector: {summary[1]}, {summary[2]:.1f}%)"
                        )
                        donor_lines.append("Donor sectors:")
                        for s in sectors:
                            donor_lines.append(
                                f"  {s[0]:<18} ${s[1]:>9,.0f}  ({s[2]:>5.1f}%,  {s[3]} donors)"
                            )
                    if no_votes:
                        donor_lines.append("Dissenting (No) votes on substantive items:")
                        for r in no_votes:
                            donor_lines.append(f"  {r[1]} — {r[0][:70]}")

            if donor_lines:
                lines += ["", "### Campaign Finance × Voting Pattern"] + donor_lines

        # ── Donor-sector ↔ vote-topic adjacency (facts only, no causal inference) ──
        _adj_trigger = donor_finance_trigger or any(
            w in q_lower for w in ("align", "adjacen", "sector", "pattern", "overlap")
        )
        if _adj_trigger and _table_exists(conn, "norfolk_donor_vote_adjacency"):
            target_adj = named_members if named_members else list(_SBE_NAME_MAP.keys())
            adj_lines: list[str] = []
            for m in target_adj[:6]:
                display = _SBE_NAME_MAP.get(m, m.title())
                rows_adj = conn.execute("""
                    SELECT sector, sector_pct, top_topic, top_topic_delta,
                           top_topic_yes_pct, council_yes_pct, topic_vote_count
                    FROM norfolk_donor_vote_summary
                    WHERE member_name = ?
                    ORDER BY ABS(top_topic_delta) DESC
                    LIMIT 4
                """, (display,)).fetchall()
                if not rows_adj:
                    continue
                adj_lines.append(f"\n#### {display} — Donor Sector / Vote Topic Adjacency")
                adj_lines.append(
                    "Source: SBE contributions + council member votes joined on enriched topic. "
                    "Adjacency only — no causal inference drawn."
                )
                for r in rows_adj:
                    sector, sec_pct, topic, delta, m_yes, c_yes, n = r
                    sign = "+" if delta >= 0 else ""
                    adj_lines.append(
                        f"  {sec_pct:4.0f}% from {sector:<15s} | "
                        f"votes YES on {topic:<22s} {m_yes:.0f}% "
                        f"(council avg {c_yes:.0f}%, delta {sign}{delta:.0f}pp, n={n})"
                    )
            if adj_lines:
                lines += ["", "### Donor-Sector / Vote-Topic Adjacency"] + adj_lines

        # ── Per-member dissent profile (No-rate vs council avg by topic) ──────
        _dissent_trigger = (
            named_members
            or any(w in q_lower for w in (
                "dissent", "differently", "pattern", "contrarian",
                "disagree", "against", "oppose", "no vote", "outlier",
                "bloc", "coalition", "votes with", "votes together",
                "who votes with", "aligned with", "voting pattern",
                "most independent", "who disagrees", "lone vote",
            ))
        )
        if _dissent_trigger and _table_exists(conn, "norfolk_member_dissent"):
            target_dis = named_members if named_members else list(_SBE_NAME_MAP.keys())
            dis_lines: list[str] = []
            for m in target_dis[:6]:
                display = _SBE_NAME_MAP.get(m, m.title())
                vote_name = {"Smigiel": "Smigiel Jr.", "Thomas": "Thomas Jr."}.get(display, display)
                rows_dis = conn.execute("""
                    SELECT topic, member_no_pct, council_no_pct, delta_pp, member_no_count
                    FROM norfolk_member_dissent
                    WHERE member_name = ? AND ABS(delta_pp) >= 2
                    ORDER BY ABS(delta_pp) DESC LIMIT 5
                """, (vote_name,)).fetchall()
                if not rows_dis:
                    continue
                dis_lines.append(f"\n#### {display} — Dissent Profile (No-vote rate vs council avg)")
                top_topic = None
                for topic, m_no, c_no, delta, nos in rows_dis:
                    sign = "+" if delta >= 0 else ""
                    direction = "more" if delta > 0 else "less"
                    dis_lines.append(
                        f"  {topic:<22s}  {m_no:.1f}% No vs council {c_no:.1f}%  "
                        f"({sign}{delta:.1f}pp — votes No {direction} than peers, {nos} No votes)"
                    )
                    if top_topic is None and delta > 0:
                        top_topic = topic
                # Signature No votes: 3 actual bills from the most distinctive topic
                if top_topic:
                    sig_rows = conn.execute("""
                        SELECT mv.meeting_date, mv.agenda_item,
                               cv.vote_count, e.plain_english, mv.title
                        FROM norfolk_council_member_votes mv
                        JOIN norfolk_vote_enrichment e ON e.title = mv.title
                        JOIN norfolk_council_votes cv
                          ON cv.agenda_item = mv.agenda_item
                         AND cv.meeting_date = mv.meeting_date
                        WHERE mv.member_name = ?
                          AND LOWER(mv.vote) = 'no'
                          AND e.topic = ?
                          AND mv.category = 'substantive'
                        ORDER BY mv.meeting_date DESC
                        LIMIT 3
                    """, (vote_name, top_topic)).fetchall()
                    if sig_rows:
                        dis_lines.append(f"  Signature No votes on {top_topic}:")
                        for date, item, vc, plain, title in sig_rows:
                            desc = (plain or title or "")[:80]
                            dis_lines.append(f"    {date} | {item} | {vc} — {desc}")
            if dis_lines:
                lines += ["", "### Member Dissent Profiles"] + dis_lines

        # ── Voting blocs (pairwise agreement; independent of dissent table) ───
        _bloc_trigger = any(w in q_lower for w in (
            "bloc", "coalition", "vote with", "votes with",
            "vote together", "votes together", "who votes with",
            "aligned with", "alignment", "agree with", "agreement",
            "voting pattern", "most independent", "who disagrees",
            "swing vote", "lone vote", "vote alike", "vote the same",
        ))
        if _bloc_trigger and _table_exists(conn, "norfolk_voting_blocs"):
            if named_members:
                for m in named_members[:2]:
                    display = _SBE_NAME_MAP.get(m, m.title())
                    vote_name = {"Smigiel": "Smigiel Jr.", "Thomas": "Thomas Jr."}.get(display, display)
                    allies = conn.execute("""
                        SELECT member_b, agreement_pct, shared_votes
                        FROM norfolk_voting_blocs WHERE member_a = ?
                        ORDER BY agreement_pct DESC LIMIT 4
                    """, (vote_name,)).fetchall()
                    contrarians = conn.execute("""
                        SELECT member_b, agreement_pct, shared_votes
                        FROM norfolk_voting_blocs WHERE member_a = ?
                        ORDER BY agreement_pct ASC LIMIT 3
                    """, (vote_name,)).fetchall()
                    if allies:
                        lines.append(f"\n#### {display} — Voting Alignment (substantive votes)")
                        lines.append("  Closest allies:")
                        for b, pct, n in allies:
                            lines.append(f"    {b:<22s}  {pct:.1f}% agreement ({n} shared votes)")
                        if contrarians:
                            lines.append("  Most frequent disagreements:")
                            for b, pct, n in contrarians:
                                lines.append(f"    {b:<22s}  {pct:.1f}% agreement ({n} shared votes)")
            else:
                tight = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM norfolk_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct DESC LIMIT 5
                """).fetchall()
                diverge = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM norfolk_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct ASC LIMIT 5
                """).fetchall()
                if tight:
                    lines.append("\n#### Norfolk Council — Voting Bloc Analysis (substantive votes)")
                    lines.append("  Tightest pairs (most aligned):")
                    for a, b, pct, n in tight:
                        lines.append(f"    {a} + {b}: {pct:.1f}% agreement ({n} votes)")
                    if diverge:
                        lines.append("  Most divergent pairs:")
                        for a, b, pct, n in diverge:
                            lines.append(f"    {a} + {b}: {pct:.1f}% agreement ({n} votes)")

        # ── Split-vote / contested-issue index ───────────────────────────────
        _split_trigger = any(w in q_lower for w in (
            "split vote", "split the council", "votes split", "council split",
            "close vote", "contested", "most divided", "divided vote",
            "fault line", "controversial", "5-3", "4-4", "6-2", "3-5", "2-6",
            "divided council", "most contentious", "tight vote",
            "most disputed", "biggest disagreement",
        ))
        if _split_trigger and _table_exists(conn, "norfolk_council_votes"):
            # Detect optional topic filter
            _SPLIT_TOPIC_MAP = [
                ("budget",               ["budget", "fiscal", "spending", "tax"]),
                ("short-term-rental",    ["short-term", "short term", "str", "airbnb", "vrbo", "rental"]),
                ("rezoning",             ["rezoning", "zoning", "rezone"]),
                ("housing",              ["housing", "affordable"]),
                ("public-safety",        ["public safety", "police", "fire"]),
                ("economic-development", ["economic development", "econ dev"]),
                ("schools",              ["school", "education"]),
                ("infrastructure",       ["infrastructure", "road", "water"]),
                ("environment",          ["environment", "environmental"]),
            ]
            _split_topic: str | None = None
            for _st, _skws in _SPLIT_TOPIC_MAP:
                if any(kw in q_lower for kw in _skws):
                    _split_topic = _st
                    break

            topic_clause = "AND e.topic = ?" if _split_topic else ""
            params_split: list = [_split_topic] if _split_topic else []
            split_rows = conn.execute(f"""
                SELECT cv.meeting_date, cv.agenda_item, cv.vote_count,
                       cv.result, e.plain_english, e.topic,
                       MIN(
                           CAST(SUBSTR(cv.vote_count, 1,
                               INSTR(cv.vote_count,'-')-1) AS INTEGER),
                           CAST(SUBSTR(cv.vote_count,
                               INSTR(cv.vote_count,'-')+1) AS INTEGER)
                       ) minority
                FROM norfolk_council_votes cv
                LEFT JOIN norfolk_vote_enrichment e ON e.title = cv.title
                WHERE cv.category IN ('substantive','consent')
                  AND INSTR(cv.vote_count,'-') > 0
                  AND MIN(
                      CAST(SUBSTR(cv.vote_count, 1,
                          INSTR(cv.vote_count,'-')-1) AS INTEGER),
                      CAST(SUBSTR(cv.vote_count,
                          INSTR(cv.vote_count,'-')+1) AS INTEGER)
                  ) >= 2
                  {topic_clause}
                ORDER BY minority DESC, cv.meeting_date DESC
                LIMIT 15
            """, params_split).fetchall()
            if split_rows:
                label = f" on {_split_topic.replace('-',' ').title()}" if _split_topic else ""
                lines.append(f"\n#### Most Contested Council Votes{label}")
                lines.append("  (minority dissent >= 2; ordered by closeness)")
                for date, item, vc, result, plain, topic, minority in split_rows:
                    desc = (plain or "")[:70]
                    topic_tag = f" [{topic}]" if topic and not _split_topic else ""
                    lines.append(f"  {date} | {item} | {vc} {result.upper()}{topic_tag}")
                    if desc:
                        lines.append(f"    {desc}")

        # ── Named-member No-vote history (from precomputed norfolk_split_votes) ──
        _named_split_trigger = (
            bool(named_members)
            and any(w in q_lower for w in (
                "voted against", "voted no", "no vote", "voted down", "split",
                "contested", "controversial", "opposed", "specific vote",
                "which vote", "example", "minority", "dissent", "record",
                "disagree", "against", "no on",
            ))
        )
        if _named_split_trigger and _table_exists(conn, "norfolk_split_votes"):
            _nm = named_members[0]
            _nm_rows = conn.execute("""
                SELECT meeting_date, topic, yes_count, no_count, no_voters, title
                FROM norfolk_split_votes
                WHERE no_voters LIKE ?
                ORDER BY meeting_date DESC
                LIMIT 8
            """, (f"%{_nm}%",)).fetchall()
            if _nm_rows:
                lines.append(f"\n#### {_nm.title()} — No votes on contested resolutions")
                for dt, topic, yes, no, voters, title in _nm_rows:
                    t = f"[{topic}]" if topic else "[other]"
                    lines.append(
                        f"  {dt}  {t:22s}  {yes}Y/{no}N  ({voters})  {title[:65]}"
                    )

        # ── Attendance / absence tracker ──────────────────────────────────────
        _attend_trigger = any(w in q_lower for w in (
            "attend", "absence", "absent", "miss", "missed", "show up",
            "skip", "skipped", "present", "participation",
            "who misses", "most absent", "never absent",
        ))
        if _attend_trigger and _table_exists(conn, "norfolk_council_member_votes"):
            att_rows = conn.execute("""
                SELECT member_name,
                       COUNT(*) total,
                       SUM(CASE WHEN LOWER(vote)='absent' THEN 1 ELSE 0 END) absent,
                       SUM(CASE WHEN LOWER(vote)='abstain' THEN 1 ELSE 0 END) abstain
                FROM norfolk_council_member_votes
                GROUP BY member_name
                ORDER BY absent DESC
            """).fetchall()
            if att_rows:
                lines.append("\n#### Council Attendance Record")
                lines.append("  Member               Total   Absent   Rate   Abstain")
                lines.append("  " + "-" * 55)
                for name, total, absent, abstain in att_rows:
                    rate = round(100 * absent / total, 1) if total else 0
                    ward_info = _REP_WARD.get(name, (None, None))
                    seat_tag = f"  ({ward_info[1]})" if ward_info[0] else ""
                    lines.append(
                        f"  {name:20s}  {total:5d}  {absent:6d}  {rate:5.1f}%  {abstain:7d}{seat_tag}"
                    )

        # ── Member-topic voting profile ───────────────────────────────────────
        _mtopic_trigger = (
            named_members
            and topic_terms
            and any(w in q_lower for w in (
                "vote on", "votes on", "voting on", "position on",
                "stance on", "record on", "how does", "how did",
            ))
        )
        if not _mtopic_trigger and named_members and topic_terms:
            _mtopic_trigger = True
        if _mtopic_trigger and _table_exists(conn, "norfolk_vote_enrichment"):
            for m in named_members[:2]:
                display = _SBE_NAME_MAP.get(m, m.title())
                vote_name = {"Smigiel": "Smigiel Jr.", "Thomas": "Thomas Jr."}.get(display, display)
                for topic_kw in topic_terms[:2]:
                    topic_label = topic_kw.replace("-", " ").title()
                    tp_rows = conn.execute("""
                        SELECT mv.vote, COUNT(*) n
                        FROM norfolk_council_member_votes mv
                        JOIN norfolk_vote_enrichment e ON e.title = mv.title
                        WHERE mv.category = 'substantive'
                          AND mv.member_name = ?
                          AND LOWER(e.topic) = LOWER(?)
                        GROUP BY mv.vote
                        ORDER BY n DESC
                    """, (vote_name, topic_kw)).fetchall()
                    if tp_rows:
                        total = sum(r[1] for r in tp_rows)
                        breakdown = ", ".join(f"{r[0]}={r[1]}" for r in tp_rows)
                        yes_n = next((r[1] for r in tp_rows if r[0].lower() == 'yes'), 0)
                        no_n = next((r[1] for r in tp_rows if r[0].lower() == 'no'), 0)
                        lines.append(
                            f"\n#### {vote_name} — {topic_label} Voting Record\n"
                            f"  {total} substantive {topic_label.lower()} votes: {breakdown}\n"
                            f"  YES rate: {round(100*yes_n/total,1)}% | NO rate: {round(100*no_n/total,1)}%"
                        )

        # ── Defensible donor-vote signals (schema-conformant) ────────────────
        # Schema: raw facts → derived metrics → at most one neutral signal.
        # NO causal/intent language. Banned: captured, bought, because donors,
        # in exchange, rewarded, influence, benefit directly, market protection.
        _BANNED_DONOR_TERMS = frozenset([
            "captured", "market protection", "bought", "because donors",
            "in exchange", "rewarded", "benefit directly",
            "paid to vote", "quid pro quo", "builders fund him",
            "because he opposes", "because she", "hotels benefit",
        ])

        def _lint_donor_signal(text: str) -> str:
            return _lint_norfolk_output(text)  # delegates to module-level extended lint

        # Build per-member baselines from member_rows (already queried above)
        _baselines: dict[str, dict] = {}
        for _br in (member_rows or []):
            _nm = str(_br[0]).lower()
            _tot = _br[1] or 1
            _baselines[_nm] = {
                "yes_rate": round(100.0 * _br[2] / _tot, 1),
                "no_rate":  round(100.0 * _br[3] / _tot, 1),
                "total":    _br[1],
            }

        def _b(name: str, field: str, default: str = "?") -> str:
            return str(_baselines.get(name, {}).get(field, default))

        # STR bloc check — are STR votes a council-wide pattern?
        _str_rows = conn.execute("""
            SELECT vote_count FROM norfolk_council_votes
            WHERE LOWER(title) LIKE '%short-term%' OR LOWER(title) LIKE '%short term%'
        """).fetchall()
        _str_total = len(_str_rows)
        _str_unanimous = sum(
            1 for r in _str_rows
            if r[0] and r[0].startswith(("8-0","0-8","7-0","0-7","6-0","0-6"))
        )
        _str_bloc_pct = round(100 * _str_unanimous / _str_total) if _str_total else 0
        _str_bloc_label = "LOW-SIGNAL" if _str_bloc_pct >= 85 else "INFORMATIVE"

        _DONOR_SIGNALS = [
            {
                "members": {"paige", "alexander"},
                "heading": "PAIGE + ALEXANDER — Hotel Industry Donations",
                "lines": [
                    _lint_donor_signal(
                        f"Raw facts: Paige — $5,500 from hotel operators (Norfolk Hotel Associates LLC). "
                        f"Alexander — $47,500 from hotel operators (Norfolk Hotel Associates LLC $25,000; "
                        f"Gold Key Resorts $15,000; Shamin Hotels $7,500)."
                    ),
                    f"Derived metrics: Paige baseline {_b('paige','yes_rate')}% YES "
                    f"({_b('paige','total')} votes); "
                    f"Alexander baseline {_b('alexander','yes_rate')}% YES "
                    f"({_b('alexander','total')} votes).",
                    f"STR vote record (2024–2026): {_str_total} STR-related votes; "
                    f"{_str_unanimous}/{_str_total} unanimous ({_str_bloc_pct}%). "
                    f"Feb 25 2025 zoning amendment removing STR as permitted use: 8-0 (whole council).",
                    f"Interpretive signal: {_str_bloc_label} — STR vote pattern is council-wide bloc "
                    f"({_str_bloc_pct}% unanimous). Hotel donations are adjacent fact; "
                    f"no discriminating deviation from majority or baseline in this dataset.",
                ],
            },
            {
                "members": {"clanton"},
                "heading": "CLANTON — Pathway Realty Group Donation",
                "lines": [
                    _lint_donor_signal(
                        "Raw facts: $2,500 from Pathway RG / Managing Member (Pathway Realty Group)."
                    ),
                    "Vote record: MARCH 25, 2025 PH-1 — Approved 2.28-acre city-owned land "
                    "purchase-and-development agreement. Clanton voted YES. Result: 7-1 passed "
                    "(87.5% council approval). Sole NO: Doyle (no Pathway donations on record).",
                    f"Derived metrics: Clanton baseline {_b('clanton','yes_rate')}% YES across "
                    f"{_b('clanton','total')} votes.",
                    "Interpretive signal: LOW-SIGNAL — Clanton's YES is consistent with baseline; "
                    "87.5% of council approved. No deviation from majority or baseline detected.",
                ],
            },
            {
                "members": {"doyle"},
                "heading": "DOYLE — Pathway NO Vote (no donor link; anomalous dissent)",
                "lines": [
                    "Raw facts: No Pathway Realty Group donations on record for Doyle.",
                    "Vote record: MARCH 25, 2025 PH-1 — Doyle voted NO (sole dissent in 7-1 vote).",
                    f"Derived metrics: Doyle baseline {_b('doyle','yes_rate')}% YES, "
                    f"{_b('doyle','no_rate')}% dissent rate.",
                    "Interpretive signal: ANOMALOUS — Doyle broke from her YES baseline and the "
                    "87.5% council majority on a split vote; no donor link to Pathway detected. "
                    "Ward 2 constituency context may be relevant (testimony records show Ward 2 "
                    "opposition on development items).",
                ],
            },
            {
                "members": {"alexander"},
                "heading": "ALEXANDER — Bonaventure Developer Donation",
                "lines": [
                    _lint_donor_signal(
                        "Raw facts: John Hyland / Bonaventure gave $105,000 to Alexander "
                        "(14% of $750,807 total raised; 3 contributions on Feb 15, 2023). "
                        "Donor base also includes Tidewater Builders Association PAC, "
                        "hotel operators, and real estate law firms."
                    ),
                    f"Derived metrics: Alexander baseline {_b('alexander','yes_rate')}% YES across "
                    f"{_b('alexander','total')} votes.",
                    "Interpretive signal: LOW-SIGNAL — Alexander's near-100% baseline makes any YES "
                    "vote indistinguishable from general pattern. Donation amount is notable; "
                    "no Bonaventure-specific vote items identified to assess deviation.",
                ],
            },
            {
                "members": {"royster"},
                "heading": "ROYSTER — Franklin Johnston Group Donation",
                "lines": [
                    "Raw facts: $24,500 from The Franklin Johnston Group (single largest donor, "
                    "~9% of total raised). Alexander also received $30,000 from Franklin Johnston.",
                    f"Derived metrics: Royster baseline {_b('royster','yes_rate')}% YES, "
                    f"{_b('royster','no_rate')}% dissent rate ({_b('royster','total')} votes; "
                    f"8 contested votes — marginal sample).",
                    "Interpretive signal: LOW-SIGNAL — vote sample (8 contested votes) is "
                    "insufficient for pattern claim; dissent rate makes any individual YES "
                    "indistinguishable from baseline. Developer donation is adjacent fact; "
                    "no discriminating vote pattern identified.",
                ],
            },
            {
                "members": {"smigiel"},
                "heading": "SMIGIEL JR. — Construction / Builder Donations",
                "lines": [
                    _lint_donor_signal(
                        "Raw facts: $14,500 combined from construction and homebuilder employers "
                        "(2021–2023) — $6,000 Equity Development Corporation, $3,000 Elysion, "
                        "$3,000 ConstructTech, $2,500 Breeden Company."
                    ),
                    f"Derived metrics: Smigiel Jr. baseline {_b('smigiel jr.','yes_rate')}% YES, "
                    f"{_b('smigiel jr.','no_rate')}% dissent rate "
                    f"({_b('smigiel jr.','total')} votes; 30 contested votes, all NO vs majority YES).",
                    "STR vote record: Smigiel Jr. cast 11 NO votes on STR permit items across "
                    "117 STR-related votes. Thomas Jr. — $0 construction donations on record — "
                    "cast 14 NO votes on the same category. Doyle, Paige, and Alexander each "
                    "cast 10 NO votes with no construction donors on record.",
                    "Interpretive signal: LOW-SIGNAL — dissent pattern (anti-spending, "
                    "limited-government) is council-wide and predates the donation period. "
                    "STR opposition is not unique to members with construction donors. "
                    "No discriminating deviation from baseline identified.",
                ],
            },
        ]

        corr_trigger = donor_finance_trigger or any(
            w in q_lower for w in (
                "corrupt", "align", "conflict", "interest",
                "who funds", "who paid", "hotel", "pathway",
                "bonaventure", "franklin johnston", "airbnb", "str",
                "short-term", "short term", "rental", "vacation rental", "vrbo",
            )
        )
        if corr_trigger:
            active_members = set(named_members) if named_members else set(_SBE_NAME_MAP.keys())
            corr_lines: list[str] = []
            for sig in _DONOR_SIGNALS:
                if sig["members"] & active_members:
                    corr_lines.append(f"\n**{sig['heading']}**")
                    for ln in sig["lines"]:
                        try:
                            corr_lines.append(f"  {_lint_donor_signal(ln)}")
                        except ValueError as _e:
                            corr_lines.append(f"  [SUPPRESSED: {_e}]")
            if corr_lines:
                lines += ["", "### Donor-Vote Adjacency Record (schema v2 — facts only, no causal inference)"]
                lines += corr_lines

        # ── Testimony / public comment lookup ────────────────────────────────
        import re as _re
        _ward_m = _re.search(r'\bward\s*([1-5])\b', q_lower)
        query_ward = int(_ward_m.group(1)) if _ward_m else None

        testimony_trigger = any(
            w in q_lower for w in (
                "testif", "oppos", "support", "spoke", "comment", "public",
                "who opposed", "who supported", "neighbor", "resident",
                "community", "hearing", "public hearing", "ward",
            )
        ) or topic_terms or named_members or query_ward
        if testimony_trigger and _table_exists(conn, "norfolk_council_testimony"):
            test_lines: list[str] = []

            _sentiment_intent = any(w in q_lower for w in (
                "sentiment", "oppose most", "public opinion", "community opinion",
                "what does norfolk oppose", "what do residents", "what does the public",
                "how does public", "community feel", "public feel",
                "most opposed", "least supported", "most unpopular",
                "public against", "what do people", "public mood",
                "resistance", "pushback", "public resist", "public push",
                "most oppos", "public oppos", "residents oppos", "norfolk oppos",
                "most public", "had the most",
            ))

            # Accountability mode — takes priority; no member/topic needed
            _acct_intent = any(w in q_lower for w in (
                "voted against", "ignored", "overrode", "passed despite",
                "accountability", "went against", "public say", "public sentiment",
                "vs outcome", "outcome vs", "public vs", "against the public",
                "council vs", "overruled",
            ))
            if _acct_intent:
                _opp_pass = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                           AND LOWER(v.result) LIKE '%pass%'
                    )
                """).fetchone()[0]
                _opp_total = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                    )
                """).fetchone()[0]
                _pct = round(100 * _opp_pass / _opp_total) if _opp_total else 0
                acc_rows = conn.execute("""
                    SELECT t.meeting_date, t.agenda_item, v.title,
                        SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) oppose,
                        SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END) support,
                        v.result, v.vote_count, e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                      ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                    LEFT JOIN norfolk_vote_enrichment e ON e.title=v.title
                    GROUP BY t.meeting_date, t.agenda_item, v.title, v.result, v.vote_count
                    HAVING oppose > support AND LOWER(v.result) LIKE '%pass%'
                    ORDER BY oppose DESC
                    LIMIT 15
                """).fetchall()
                test_lines.append(
                    f"\n#### Public Opposition vs. Council Outcome\n"
                    f"Stat: {_opp_pass}/{_opp_total} items where public opposition exceeded "
                    f"support still passed ({_pct}%)"
                )
                for r in acc_rows:
                    desc = (r[7] or r[2] or "")[:70]
                    test_lines.append(
                        f"  {r[0]} | {r[1]} | {r[3]} oppose / {r[4]} support "
                        f"-> {r[5].upper()} {r[6]}"
                    )
                    if desc:
                        test_lines.append(f"    {desc}")

            # Public sentiment index — topic drill-down or overall rollup
            elif _sentiment_intent:
                # Detect if query targets a specific topic
                _TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
                    ("budget",               ["budget", "fiscal", "spending", "tax", "fy20"]),
                    ("schools",              ["school", "education", "student", "teacher", "classroom"]),
                    ("short-term-rental",    ["short-term", "short term", "airbnb", "vrbo",
                                              "vacation rental", "rental permit", " str "]),
                    ("housing",              ["housing", "affordable housing", "affordable home"]),
                    ("public-safety",        ["public safety", "police", "fire department",
                                              "crime", "911", "emergency service"]),
                    ("personnel",            ["personnel", "staff", "salary", "employee",
                                              "hiring", "firing", "layoff"]),
                    ("infrastructure",       ["infrastructure", "road", "water", "sewer",
                                              "stormwater", "sidewalk", "bridge"]),
                    ("rezoning",             ["rezoning", "zoning", "rezone", "land use"]),
                    ("economic-development", ["economic development", "econ dev", "developer",
                                              "development project", "redevelopment"]),
                    ("environment",          ["environment", "environmental", "climate",
                                              "flood", "green", "sustainability"]),
                ]
                _drill_topic: str | None = None
                for _t, _kws in _TOPIC_KEYWORDS:
                    if any(kw in q_lower for kw in _kws):
                        _drill_topic = _t
                        break

                if _drill_topic:
                    topic_label = _drill_topic.replace("-", " ").title()
                    drill_rows = conn.execute("""
                        SELECT e.plain_english, cv.agenda_item, cv.meeting_date,
                               SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) opp,
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END) sup,
                               COUNT(*) total, cv.result
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes cv
                          ON cv.agenda_item = t.agenda_item
                         AND cv.meeting_date = t.meeting_date
                        JOIN norfolk_vote_enrichment e ON e.title = cv.title
                        WHERE t.stance IN ('oppose','support')
                          AND e.topic = ?
                        GROUP BY cv.agenda_item, cv.meeting_date
                        HAVING total >= 2
                        ORDER BY opp DESC
                        LIMIT 12
                    """, (_drill_topic,)).fetchall()
                    if drill_rows:
                        test_lines.append(
                            f"\n#### {topic_label} Items — Public Opposition Ranking\n"
                            "(oppose/support testimony only; 'comment' stance excluded)"
                        )
                        for plain, item, date, opp, sup, total, result in drill_rows:
                            pct = round(100 * opp / total) if total else 0
                            test_lines.append(
                                f"  {date} | {item} | {opp} opp / {sup} sup "
                                f"({pct}% oppose) -> {(result or '').upper()}"
                            )
                            if plain:
                                test_lines.append(f"    {plain[:75]}")
                    else:
                        test_lines.append(
                            f"\n#### {topic_label} Items — Public Opposition Ranking\n"
                            f"No {topic_label.lower()} testimony records with "
                            "opposition/support stances found."
                        )
                else:
                    # No specific topic — show rollup across all categories
                    sent_rows = conn.execute("""
                        SELECT e.topic,
                               SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) opp,
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END) sup,
                               COUNT(*) total
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes cv
                          ON cv.agenda_item = t.agenda_item
                         AND cv.meeting_date = t.meeting_date
                        JOIN norfolk_vote_enrichment e ON e.title = cv.title
                        WHERE t.stance IN ('oppose','support')
                        GROUP BY e.topic
                        HAVING total >= 3
                        ORDER BY CAST(opp AS REAL) / total DESC
                    """).fetchall()
                    if sent_rows:
                        total_stance = conn.execute(
                            "SELECT COUNT(*) FROM norfolk_council_testimony "
                            "WHERE stance IN ('oppose','support')"
                        ).fetchone()[0]
                        test_lines.append(
                            f"\n#### Public Sentiment by Topic\n"
                            f"Based on {total_stance} stance-bearing testimony records "
                            f"(oppose/support only; 'comment' stance excluded as neutral)."
                        )
                        for topic, opp, sup, total in sent_rows:
                            pct = round(100 * opp / total) if total else 0
                            bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
                            test_lines.append(
                                f"  {topic:<22s}  {bar}  {pct:3d}% oppose "
                                f"({opp} opp / {sup} sup, n={total})"
                            )

            # Ward-filtered query — fires when "ward N" appears in query
            elif query_ward:
                ward_stance_filter = ""
                ward_params: list = [query_ward]
                # Narrow by oppose/support if query signals a stance
                if any(w in q_lower for w in ("oppos", "against", "no vote", "fought")):
                    ward_stance_filter = "AND t.stance = 'oppose'"
                elif any(w in q_lower for w in ("support", "favor", "pro", "backed")):
                    ward_stance_filter = "AND t.stance = 'support'"
                else:
                    ward_stance_filter = "AND t.stance IN ('oppose','support')"
                # Add topic filter if present
                topic_ward_clause = ""
                if topic_terms:
                    topic_ward_clause = "AND (" + " OR ".join(
                        "LOWER(v.title) LIKE ? OR LOWER(e.plain_english) LIKE ?"
                        for _ in topic_terms
                    ) + ")"
                    ward_params += [p for t in topic_terms for p in (f"%{t.lower()}%", f"%{t.lower()}%")]
                ward_rows = conn.execute(f"""
                    SELECT t.speaker_name, t.stance, t.meeting_date,
                           t.agenda_item, t.ward, v.result, v.vote_count,
                           e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                        ON v.meeting_date = t.meeting_date
                       AND v.agenda_item  = t.agenda_item
                    LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
                    WHERE t.ward = ?
                      {ward_stance_filter}
                      {topic_ward_clause}
                    ORDER BY t.meeting_date DESC
                    LIMIT 20
                """, ward_params).fetchall()
                if ward_rows:
                    label = f"Ward {query_ward}"
                    test_lines.append(f"\n#### Testimony from {label} residents")
                    for r in ward_rows:
                        desc = (r["plain_english"] or "")[:70]
                        test_lines.append(
                            f"  [{r['stance'].upper():>7}] {r['meeting_date']} "
                            f"{r['agenda_item']} ({r['vote_count']} {r['result']}) "
                            f"— {r['speaker_name']} ({r['speaker_addr'] if 'speaker_addr' in r.keys() else ''})"
                        )
                        if desc:
                            test_lines.append(f"           {desc}")
                else:
                    test_lines.append(f"\n#### No Ward {query_ward} testimony matched those criteria")

            # Topic-matched testimony
            elif topic_terms:
                like_clauses = " OR ".join(
                    "LOWER(v.title) LIKE ? OR LOWER(e.plain_english) LIKE ?"
                    for _ in topic_terms
                )
                params = [p for t in topic_terms for p in (f"%{t.lower()}%", f"%{t.lower()}%")]
                test_rows = conn.execute(f"""
                    SELECT t.speaker_name, t.stance, t.meeting_date,
                           t.agenda_item, t.ward, v.result, v.vote_count,
                           e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                        ON v.meeting_date = t.meeting_date
                       AND v.agenda_item  = t.agenda_item
                    LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
                    WHERE ({like_clauses})
                      AND t.stance IN ('oppose','support','present')
                    ORDER BY t.meeting_date DESC
                    LIMIT 15
                """, params).fetchall()
                if test_rows:
                    test_lines.append("\n#### Public Testimony on matched items")
                    for r in test_rows:
                        desc = (r["plain_english"] or "")[:70]
                        ward_tag = f" Ward {r['ward']}" if r["ward"] else ""
                        test_lines.append(
                            f"  [{r['stance'].upper():>7}] {r['meeting_date']} "
                            f"{r['agenda_item']} ({r['vote_count']} {r['result']}) "
                            f"— {r['speaker_name']}{ward_tag}"
                        )
                        if desc:
                            test_lines.append(f"           {desc}")

            # Named-member query — show recent contested testimony
            elif named_members:
                mem_test = conn.execute("""
                    SELECT t.speaker_name, t.stance, t.meeting_date,
                           t.agenda_item, t.ward, v.result, v.vote_count,
                           e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                        ON v.meeting_date = t.meeting_date
                       AND v.agenda_item  = t.agenda_item
                    LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
                    WHERE t.stance IN ('oppose','support')
                    ORDER BY t.meeting_date DESC
                    LIMIT 10
                """).fetchall()
                if mem_test:
                    test_lines.append("\n#### Recent contested testimony")
                    for r in mem_test:
                        desc = (r["plain_english"] or "")[:65]
                        ward_tag = f" Ward {r['ward']}" if r["ward"] else ""
                        test_lines.append(
                            f"  [{r['stance'].upper():>7}] {r['meeting_date']} "
                            f"{r['agenda_item']} ({r['vote_count']} {r['result']}) "
                            f"— {r['speaker_name']}{ward_tag}"
                        )
                        if desc:
                            test_lines.append(f"           {desc}")

            # ── Who represents ward X? ───────────────────────────────────────
            _ward_rep_query = any(w in q_lower for w in (
                "who represents ward", "represents ward", "who is ward",
                "council member for ward", "rep for ward", "ward rep",
                "ward representative", "representative for ward",
                "who sits on", "who holds ward", "who holds superward",
                "who is the rep", "who is my council",
            ))
            if _ward_rep_query:
                import re as _re
                _wm = _re.search(r'\b(?:super\s*ward|ward)\s*([1-7])\b', q_lower)
                if _wm:
                    _wn = int(_wm.group(1))
                    _wrep = _WARD_REP.get(_wn)
                    _wseat = _SEAT_LABEL.get(_wn, f"Ward {_wn}")
                    if _wrep:
                        test_lines.append(f"\n#### Norfolk {_wseat} Council Seat")
                        test_lines.append(f"  Current representative: **{_wrep}** ({_wseat})")
                        if _wn == 5:
                            test_lines.append("  (Clanton replaced Andria McClellan in January 2025)")
                        elif _wn == 6:
                            test_lines.append("  (McGee replaced Danica Royster in January 2025)")
                        elif _wn == 7:
                            test_lines.append("  (Paige replaced Paul Riddick in January 2023)")
                else:
                    # No specific ward number — show full map
                    test_lines.append("\n#### Norfolk City Council — Seat Assignments")
                    test_lines.append("  Mayor (at-large): Alexander")
                    for _wn, _wrep in _WARD_REP.items():
                        _wseat = _SEAT_LABEL.get(_wn, f"Ward {_wn}")
                        test_lines.append(f"  {_wseat}: {_wrep}")

            # Ward vs. council outcome — which wards get overruled most
            _ward_outcome_trigger = (
                ("ward" in q_lower and any(w in q_lower for w in (
                    "overrul", "overrod", "ignored", "ignore",
                    "outcome", "listened", "least listen",
                )))
                or any(w in q_lower for w in (
                    "which ward", "which community", "community ignored",
                    "most overruled", "ignored the most",
                ))
            )
            if _ward_outcome_trigger and _table_exists(conn, "norfolk_council_testimony"):
                ward_out = conn.execute("""
                    SELECT ward,
                           COUNT(*) items_with_opp,
                           SUM(passed) passed_despite,
                           SUM(oppose_n) total_opp
                    FROM (
                        SELECT t.ward, t.meeting_date, t.agenda_item,
                               MAX(CASE WHEN LOWER(v.result) LIKE '%pass%' THEN 1 ELSE 0 END) passed,
                               COUNT(*) oppose_n
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item = t.agenda_item
                         AND v.meeting_date = t.meeting_date
                        WHERE t.ward IS NOT NULL AND t.stance = 'oppose'
                        GROUP BY t.ward, t.meeting_date, t.agenda_item
                    )
                    GROUP BY ward
                    HAVING items_with_opp >= 2
                    ORDER BY CAST(passed_despite AS REAL) / items_with_opp DESC
                """).fetchall()
                # Superward-level outcome (wards 6/7 via superward column)
                sw_out = conn.execute("""
                    SELECT superward,
                           COUNT(*) items_with_opp,
                           SUM(passed) passed_despite,
                           SUM(oppose_n) total_opp
                    FROM (
                        SELECT t.superward, t.meeting_date, t.agenda_item,
                               MAX(CASE WHEN LOWER(v.result) LIKE '%pass%' THEN 1 ELSE 0 END) passed,
                               COUNT(*) oppose_n
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item = t.agenda_item
                         AND v.meeting_date = t.meeting_date
                        WHERE t.superward IS NOT NULL AND t.stance = 'oppose'
                        GROUP BY t.superward, t.meeting_date, t.agenda_item
                    )
                    GROUP BY superward
                    HAVING items_with_opp >= 2
                    ORDER BY CAST(passed_despite AS REAL) / items_with_opp DESC
                """).fetchall()
                all_out = [(w, c, p, o) for w, c, p, o in ward_out] + \
                          [(w, c, p, o) for w, c, p, o in sw_out]
                all_out.sort(key=lambda x: x[2] / x[1] if x[1] else 0, reverse=True)
                if all_out:
                    test_lines.append(
                        "\n#### Ward vs. Council Outcome\n"
                        "(Items where ward/superward residents testified in opposition — % that still passed)"
                    )
                    for ward, contested, passed, opp_n in all_out:
                        pct = round(100 * passed / contested) if contested else 0
                        bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
                        seat = _SEAT_LABEL.get(ward, f"Ward {ward}")
                        rep  = _WARD_REP.get(ward)
                        rep_tag = f"  [{rep} — {seat} rep]" if rep else ""
                        test_lines.append(
                            f"  {seat:<14}  {bar}  {pct:3d}% overruled "
                            f"({passed}/{contested} items passed | {opp_n} oppose testimonies){rep_tag}"
                        )

            # ── Testimony-to-vote alignment per member ─────────────────────────
            _align_trigger = any(w in q_lower for w in (
                "listen", "responsive", "responsiveness", "represent",
                "votes with constituents", "votes with ward",
                "votes against constituents", "ignores ward",
                "alignment score", "scorecard", "accountability score",
                "who listens", "who ignores", "responsive to",
            ))
            if _align_trigger and _table_exists(conn, "norfolk_council_testimony"):
                _ward_to_rep_sql = {1: "Smigiel Jr.", 2: "Doyle", 3: "Johnson",
                                    4: "Thomas Jr.", 5: "Clanton"}
                _sw_to_rep_sql = {6: "McGee", 7: "Paige"}
                _align_parts: dict[str, list[int]] = {}
                for _aw, _arep in _ward_to_rep_sql.items():
                    rows_a = conn.execute("""
                        SELECT t.stance, mv.vote
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_member_votes mv
                          ON mv.agenda_item = t.agenda_item
                         AND mv.meeting_date = t.meeting_date
                        WHERE t.ward = ? AND t.stance IN ('support','oppose')
                          AND mv.member_name = ?
                    """, (_aw, _arep)).fetchall()
                    if rows_a:
                        aligned = sum(
                            1 for stance, vote in rows_a
                            if (stance == 'oppose' and vote.lower() == 'no')
                            or (stance == 'support' and vote.lower() == 'yes')
                        )
                        _align_parts[_arep] = [aligned, len(rows_a)]
                for _asw, _arep in _sw_to_rep_sql.items():
                    rows_a = conn.execute("""
                        SELECT t.stance, mv.vote
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_member_votes mv
                          ON mv.agenda_item = t.agenda_item
                         AND mv.meeting_date = t.meeting_date
                        WHERE t.superward = ? AND t.stance IN ('support','oppose')
                          AND mv.member_name = ?
                    """, (_asw, _arep)).fetchall()
                    if rows_a:
                        aligned = sum(
                            1 for stance, vote in rows_a
                            if (stance == 'oppose' and vote.lower() == 'no')
                            or (stance == 'support' and vote.lower() == 'yes')
                        )
                        _align_parts[_arep] = [aligned, len(rows_a)]
                align_rows = sorted(
                    [(k, v[0], v[1]) for k, v in _align_parts.items() if v[1] >= 3],
                    key=lambda x: x[1] / x[2], reverse=True,
                )
                if align_rows:
                    test_lines.append(
                        "\n#### Constituency Alignment Scorecard\n"
                        "(How often each ward/superward rep votes with their constituents' testimony stance)"
                    )
                    for name, aligned, total in align_rows:
                        pct = round(100 * aligned / total) if total else 0
                        ward_info = _REP_WARD.get(name, (None, None))
                        seat_tag = f" ({ward_info[1]})" if ward_info[0] else ""
                        bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
                        test_lines.append(
                            f"  {name:16s}  {bar}  {pct:3d}% aligned "
                            f"({aligned}/{total} items){seat_tag}"
                        )

            # ── Meeting recap ─────────────────────────────────────────────────
            import re as _re2
            _meeting_trigger = any(w in q_lower for w in (
                "last meeting", "recent meeting", "latest meeting",
                "what happened", "meeting recap", "meeting summary",
                "council meeting", "agenda recap",
            ))
            _date_match = _re2.search(
                r'(january|february|march|april|may|june|july|august|'
                r'september|october|november|december)\s+\d{1,2},?\s*\d{4}',
                q_lower,
            )
            if (_meeting_trigger or _date_match) and _table_exists(conn, "norfolk_council_votes"):
                if _date_match:
                    target_date = _date_match.group(0).upper()
                    if "," not in target_date:
                        parts = target_date.rsplit(" ", 1)
                        target_date = parts[0] + ", " + parts[1] if len(parts) == 2 else target_date
                    mtg_rows = conn.execute("""
                        SELECT cv.agenda_item, cv.vote_count, cv.result,
                               e.plain_english, e.topic, cv.category
                        FROM norfolk_council_votes cv
                        LEFT JOIN norfolk_vote_enrichment e ON e.title = cv.title
                        WHERE cv.meeting_date = ?
                        ORDER BY cv.agenda_item
                    """, (target_date,)).fetchall()
                else:
                    latest = conn.execute("""
                        SELECT meeting_date FROM norfolk_council_votes
                        ORDER BY ROWID DESC LIMIT 1
                    """).fetchone()
                    if latest:
                        target_date = latest[0]
                        mtg_rows = conn.execute("""
                            SELECT cv.agenda_item, cv.vote_count, cv.result,
                                   e.plain_english, e.topic, cv.category
                            FROM norfolk_council_votes cv
                            LEFT JOIN norfolk_vote_enrichment e ON e.title = cv.title
                            WHERE cv.meeting_date = ?
                            ORDER BY cv.agenda_item
                        """, (target_date,)).fetchall()
                    else:
                        mtg_rows = []
                if mtg_rows:
                    sub = [r for r in mtg_rows if r[5] == 'substantive']
                    consent = [r for r in mtg_rows if r[5] == 'consent']
                    test_lines.append(f"\n#### Meeting Recap — {target_date}")
                    test_lines.append(
                        f"  {len(mtg_rows)} items total "
                        f"({len(sub)} substantive, {len(consent)} consent)"
                    )
                    for item, vc, result, plain, topic, cat in mtg_rows:
                        if cat == 'substantive':
                            desc = (plain or "")[:80]
                            topic_tag = f" [{topic}]" if topic else ""
                            test_lines.append(f"  {item}  {vc} {result}{topic_tag}")
                            if desc:
                                test_lines.append(f"    {desc}")

            # ── Upcoming agenda ────────────────────────────────────────────────
            _upcoming_trigger = any(w in q_lower for w in (
                "upcoming", "next meeting", "next council", "what's coming",
                "what is coming", "agenda", "scheduled", "future meeting",
                "when is the next", "next session",
            ))
            if _upcoming_trigger and _table_exists(conn, "norfolk_upcoming_agenda"):
                up_rows = conn.execute("""
                    SELECT meeting_date, item_ref, title, status, agenda_url
                    FROM norfolk_upcoming_agenda
                    ORDER BY meeting_date, item_ref
                """).fetchall()
                if up_rows:
                    from itertools import groupby as _groupby
                    test_lines.append("\n#### Upcoming Norfolk City Council Meetings")
                    by_date: dict[str, list] = {}
                    for date_s, ref, title, status, aurl in up_rows:
                        by_date.setdefault(date_s, []).append((ref, title, status, aurl))
                    for mtg_date, items in by_date.items():
                        pending = items[0][2] == 'pending'
                        if pending:
                            test_lines.append(f"  {mtg_date} — Formal Session (agenda not yet posted)")
                        else:
                            aurl = items[0][3] or ""
                            url_tag = f"  [agenda PDF]({aurl})" if aurl else ""
                            test_lines.append(f"  {mtg_date} — Formal Session{url_tag}")
                            for ref, title, _, _ in items:
                                ref_tag = f"{ref}: " if ref and ref != "AGENDA" else ""
                                test_lines.append(f"    {ref_tag}{title}")

            # ── Opposition trend over time ─────────────────────────────────────
            _trend_trigger = any(w in q_lower for w in (
                "trend", "over time", "growing", "increasing", "declining",
                "more opposed", "less opposed", "opposition trend",
                "changing", "shifted", "shift", "year over year",
                "historically", "compared to", "getting worse",
                "getting better", "more resistance", "less resistance",
            ))
            if _trend_trigger and _table_exists(conn, "norfolk_council_testimony"):
                trend_rows = conn.execute("""
                    SELECT SUBSTR(meeting_date, -4) yr,
                           SUM(CASE WHEN stance='oppose' THEN 1 ELSE 0 END) oppose,
                           SUM(CASE WHEN stance='support' THEN 1 ELSE 0 END) support,
                           SUM(CASE WHEN stance='comment' THEN 1 ELSE 0 END) comment,
                           COUNT(*) total
                    FROM norfolk_council_testimony
                    GROUP BY SUBSTR(meeting_date, -4)
                    ORDER BY yr
                """).fetchall()
                if trend_rows and len(trend_rows) >= 2:
                    test_lines.append("\n#### Public Testimony Trend by Year")
                    test_lines.append("  Year   Oppose  Support  Comment  Total  Opp%")
                    test_lines.append("  " + "-" * 50)
                    for yr, opp, sup, com, tot in trend_rows:
                        opp_pct = round(100 * opp / tot) if tot else 0
                        test_lines.append(
                            f"  {yr}   {opp:6d}  {sup:7d}  {com:7d}  {tot:5d}  {opp_pct:3d}%"
                        )
                    first_opp_pct = round(100 * trend_rows[0][1] / trend_rows[0][4]) if trend_rows[0][4] else 0
                    last_opp_pct = round(100 * trend_rows[-1][1] / trend_rows[-1][4]) if trend_rows[-1][4] else 0
                    direction = "rising" if last_opp_pct > first_opp_pct else "declining" if last_opp_pct < first_opp_pct else "stable"
                    test_lines.append(
                        f"  Trend: opposition share {direction} "
                        f"({first_opp_pct}% in {trend_rows[0][0]} → {last_opp_pct}% in {trend_rows[-1][0]})"
                    )

            # Influence summary note — dynamic stat replaces hardcoded version
            if test_lines:
                _inf_opp_pass = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                           AND LOWER(v.result) LIKE '%pass%'
                    )
                """).fetchone()[0]
                _inf_opp_total = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                    )
                """).fetchone()[0]
                _inf_pct = round(100 * _inf_opp_pass / _inf_opp_total) if _inf_opp_total else 96
                test_lines.insert(0, (
                    f"\n#### Testimony influence note: "
                    f"{_inf_pct}% of items where public opposition exceeded support still passed. "
                    "Doyle is the only member whose NO votes consistently match "
                    "opposition testimony from Ward 2 constituents."
                ))
                lines += ["", "### Public Testimony Records"] + test_lines

        conn.close()
        if len(lines) > 3:
            blocks.append("\n".join(lines))

    except Exception:
        pass


# ── Virginia Beach City Council context ──────────────────────────────────────

_VB_TRIGGER_RE = re.compile(
    r"virginia\s+beach.*(council|member|district|representative|agenda|meeting|upcoming|vote|voted|votes|mayor|appoint|ordinance|resolution)"
    r"|vb\s+(city\s+)?council"
    r"|(council|member|district|representative|agenda|upcoming|ordinance|resolution).*virginia\s+beach"
    r"|berlucchi|hutcheson|cummings|jackson.green|remick|ross.hammond|schulman"
    r"|(henley|rouse|wilson).*(vote|council|district|virginia)",
    re.IGNORECASE,
)


def _add_vb_council_context(blocks: list[str], query: str, terms: list[str]) -> None:
    q_lower = query.lower()

    if not _VB_TRIGGER_RE.search(query):
        return

    try:
        conn = sqlite3.connect(_POLLS_DB, timeout=10)
        lines: list[str] = ["## Virginia Beach City Council"]

        # ── member roster ──────────────────────────────────────────────────
        member_trigger = any(w in q_lower for w in (
            "member", "council", "who", "roster", "list", "all", "district",
            "represent", "mayor", "contact", "email",
        ))

        members: list[tuple] = []
        if _table_exists(conn, "vb_council_members"):
            members = conn.execute("""
                SELECT name, district, district_num, email
                FROM vb_council_members
                ORDER BY district_num
            """).fetchall()

        if members and member_trigger:
            # Specific district lookup
            _dist_m = re.search(r"\bdistrict\s+(\d+)\b", q_lower)
            if _dist_m:
                _dn = int(_dist_m.group(1))
                _match = [(n, d, dn, e) for n, d, dn, e in members if dn == _dn]
                if _match:
                    n, d, dn, e = _match[0]
                    lines.append(f"\n#### Virginia Beach {d}")
                    lines.append(f"  Current representative: **{n}**")
                    if e:
                        lines.append(f"  Email: {e}")
                else:
                    lines.append(f"\n  No member found for District {_dn}")
            else:
                lines.append("\n#### Virginia Beach City Council — Member Roster")
                for name, district, dnum, email in members:
                    label = f"{district:<12}" if district != "Mayor" else "Mayor (at-large)"
                    e_str = f"  ({email})" if email else ""
                    lines.append(f"  {label}  {name}{e_str}")

        # ── upcoming agenda ────────────────────────────────────────────────
        agenda_trigger = any(w in q_lower for w in (
            "upcoming", "next meeting", "next council", "agenda",
            "coming up", "scheduled", "vote on", "ordinance", "resolution",
        ))

        if agenda_trigger and _table_exists(conn, "vb_upcoming_agenda"):
            ag_rows = conn.execute("""
                SELECT meeting_date, item_ref, category, title
                FROM vb_upcoming_agenda
                WHERE meeting_date >= date('now')
                ORDER BY meeting_date, item_ref
                LIMIT 30
            """).fetchall()

            if ag_rows:
                lines.append("\n#### Virginia Beach City Council — Upcoming Agenda Items")
                cur_date = ""
                for mdate, ref, cat, title in ag_rows:
                    if mdate != cur_date:
                        cur_date = mdate
                        lines.append(f"\n  {mdate} (Formal Session)")
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    lines.append(f"    {ref:<6}  [{cat}] {short_title}")
            else:
                lines.append("\n  No upcoming agenda items posted yet — agendas typically appear 1-2 weeks before each meeting.")

        # ── vote data availability note ────────────────────────────────────
        vote_trigger = any(w in q_lower for w in (
            "vote", "voted", "votes", "voting", "yes", "no", "passed", "failed",
            "dissent", "opposed", "against", "absent", "record", "history",
        ))
        has_vote_table = _table_exists(conn, "vb_council_member_votes")

        if vote_trigger and has_vote_table:
            # Named member query
            _vb_member_names = {
                "berlucchi": "Michael F. Berlucchi",
                "dyer": 'Robert M. "Bobby" Dyer',
                "cummings": "Stacy Cummings",
                "henley": "Barbara M. Henley",
                "hutcheson": "David Hutcheson",
                "jackson": "Cal",
                "jackson-green": "Cal",
                "remick": "Robert W.",
                "ross-hammond": "Dr. Amelia",
                "ross hammond": "Dr. Amelia",
                "rouse": "Jennifer Rouse",
                "schulman": "Joashua",
                "wilson": "Rosemary C. Wilson",
            }
            matched_member = None
            for key, partial in _vb_member_names.items():
                if key in q_lower:
                    row = conn.execute(
                        "SELECT DISTINCT member_name, district FROM vb_council_member_votes "
                        "WHERE member_name LIKE ?", (f"%{partial}%",)
                    ).fetchone()
                    if row:
                        matched_member = row[0]
                        break

            # Title / subject keyword search
            _kw_excl = {"virginia", "beach", "council", "vote", "voted", "how", "did",
                        "what", "the", "on", "at", "in", "of", "is", "are", "was"}
            search_terms = [t for t in terms if t.lower() not in _kw_excl and len(t) > 3]

            if matched_member:
                # Member voting record summary
                stats = conn.execute("""
                    SELECT COUNT(*) total,
                           SUM(CASE WHEN vote='Yes/Aye' THEN 1 ELSE 0 END) yes_v,
                           SUM(CASE WHEN vote='No/Nay'  THEN 1 ELSE 0 END) no_v,
                           SUM(CASE WHEN vote='Absent'  THEN 1 ELSE 0 END) absent,
                           MIN(meeting_date), MAX(meeting_date)
                    FROM vb_council_member_votes WHERE member_name=?
                """, (matched_member,)).fetchone()
                if stats and stats[0]:
                    total_v, yes_v, no_v, absent, d_min, d_max = stats
                    lines.append(f"\n#### VB Council — {matched_member} Vote Summary")
                    lines.append(f"  Period: {d_min} to {d_max}  ({total_v} recorded votes)")
                    lines.append(f"  Yes/Aye: {yes_v}   No/Nay: {no_v}   Absent: {absent}")
                    if total_v:
                        lines.append(f"  Dissent rate: {round(100*no_v/(yes_v+no_v) if yes_v+no_v else 0)}%  "
                                     f"Absence rate: {round(100*absent/total_v)}%")

                # Show their No votes
                no_rows = conn.execute("""
                    SELECT meeting_date, title, vote_yes, vote_no
                    FROM vb_council_member_votes
                    WHERE member_name=? AND vote='No/Nay'
                    ORDER BY meeting_date DESC LIMIT 10
                """, (matched_member,)).fetchall()
                if no_rows:
                    lines.append(f"\n  Recent No/Nay votes ({len(no_rows)} shown):")
                    for mdate, title, vy, vn in no_rows:
                        lines.append(f"    {mdate}  [{vy}Y/{vn}N]  {title[:80]}")

            elif search_terms:
                # Search by topic keywords
                like = "%" + "%".join(search_terms[:3]) + "%"
                item_rows = conn.execute("""
                    SELECT title, meeting_date, vote_yes, vote_no,
                           GROUP_CONCAT(member_name || '=' || vote, ' | ') votes_detail
                    FROM vb_council_member_votes
                    WHERE title LIKE ?
                    GROUP BY resolution_id
                    ORDER BY meeting_date DESC LIMIT 5
                """, (like,)).fetchall()
                if item_rows:
                    lines.append(f"\n#### VB Council — Vote Search: {' '.join(search_terms[:3])!r}")
                    for title, mdate, vy, vn, detail in item_rows:
                        lines.append(f"\n  {mdate}  [{vy}Y/{vn}N]  {title[:90]}")
                        if detail:
                            # Show No votes only
                            no_detail = [p for p in detail.split(" | ") if "No/Nay" in p]
                            if no_detail:
                                lines.append(f"    Dissent: {', '.join(no_detail)}")
                else:
                    lines.append(f"\n  No VB vote records found matching: {' '.join(search_terms[:3])}")
            else:
                # General summary — most contested votes
                lines.append("\n#### VB Council — Most Contested Votes (most No votes)")
                contested = conn.execute("""
                    SELECT title, meeting_date, vote_yes, vote_no
                    FROM vb_council_member_votes
                    GROUP BY resolution_id
                    HAVING SUM(CASE WHEN vote='No/Nay' THEN 1 ELSE 0 END) >= 3
                    ORDER BY SUM(CASE WHEN vote='No/Nay' THEN 1 ELSE 0 END) DESC
                    LIMIT 8
                """).fetchall()
                for title, mdate, vy, vn in contested:
                    lines.append(f"  {mdate}  [{vy}Y/{vn}N]  {title[:80]}")

        # ── campaign finance ──────────────────────────────────────────────
        _VB_FINANCE_KEY_MAP = {
            "berlucchi": "Berlucchi", "dyer": "Dyer", "henley": "Henley",
            "hutcheson": "Hutcheson", "wilson": "Wilson", "remick": "Remick",
            "ross-hammond": "Ross-Hammond", "ross hammond": "Ross-Hammond",
            "schulman": "Schulman", "rouse": "Rouse", "cummings": "Cummings",
            "jackson-green": "Jackson-Green", "jackson green": "Jackson-Green",
        }
        _ALL_VB_FINANCE = ["Berlucchi", "Dyer", "Henley", "Hutcheson", "Wilson",
                           "Remick", "Ross-Hammond", "Schulman", "Rouse", "Cummings",
                           "Jackson-Green"]
        _vb_finance_trigger = any(w in q_lower for w in (
            "donor", "fund", "money", "contribut", "financ",
            "who pays", "paid by", "backed by", "receiv", "campaign", "raise",
        ))
        _named_vb_finance = list(dict.fromkeys(
            v for k, v in _VB_FINANCE_KEY_MAP.items() if k in q_lower
        ))
        if _vb_finance_trigger and _table_exists(conn, "vb_finance_summary"):
            _finance_targets = _named_vb_finance if _named_vb_finance else _ALL_VB_FINANCE
            _vb_donor_lines: list[str] = []
            for db_key in _finance_targets[:4]:
                summary = conn.execute(
                    "SELECT total_raised, top_sector, top_sector_pct, "
                    "undisclosed_count, undisclosed_amt, undisclosed_pct "
                    "FROM vb_finance_summary WHERE member_name=?", (db_key,)
                ).fetchone()
                sectors = conn.execute(
                    "SELECT sector, total_amount, pct_of_total, donor_count "
                    "FROM vb_finance_totals WHERE member_name=? "
                    "ORDER BY total_amount DESC LIMIT 5", (db_key,)
                ).fetchall()
                if summary or sectors:
                    _vb_donor_lines.append(f"\n#### {db_key} — Campaign Finance (SBE contributions)")
                    if summary and sectors:
                        _vb_donor_lines.append(
                            f"Total raised: ${summary[0]:,.0f}  "
                            f"(top sector: {summary[1]}, {summary[2]:.1f}%)"
                        )
                        _vb_donor_lines.append("Donor sectors:")
                        for s in sectors:
                            _vb_donor_lines.append(
                                f"  {s[0]:<18} ${s[1]:>9,.0f}  ({s[2]:>5.1f}%,  {s[3]} donors)"
                            )
                        unk_pct = summary[5] or 0
                        if unk_pct >= 1.0:
                            _vb_donor_lines.append(
                                f"  Transparency note: {unk_pct:.1f}% of contributions "
                                f"(${summary[4]:,.0f}, {summary[3]} records) list no employer "
                                f"or occupation — Virginia SBE does not verify or enforce "
                                f"disclosure of this field."
                            )
            if _vb_donor_lines:
                lines += ["", "### VB Council — Campaign Finance by Sector"] + _vb_donor_lines

        # ── Donor-sector ↔ vote-topic adjacency (facts only, no causal inference) ──
        _vb_adj_trigger = _vb_finance_trigger or any(
            w in q_lower for w in ("align", "adjacen", "sector", "pattern", "overlap",
                                   "topic", "interest", "conflict")
        )
        if _vb_adj_trigger and _table_exists(conn, "vb_donor_vote_summary"):
            _adj_targets = _named_vb_finance if _named_vb_finance else _ALL_VB_FINANCE
            _adj_lines: list[str] = []
            for db_key in _adj_targets[:4]:
                rows_adj = conn.execute("""
                    SELECT sector, sector_pct, top_topic, top_topic_delta,
                           top_topic_yes_pct, council_yes_pct, topic_vote_count
                    FROM vb_donor_vote_summary
                    WHERE member_name = ?
                    ORDER BY ABS(top_topic_delta) DESC
                    LIMIT 4
                """, (db_key,)).fetchall()
                if not rows_adj:
                    continue
                _adj_lines.append(f"\n#### {db_key} — Donor Sector / Vote Topic Adjacency")
                _adj_lines.append(
                    "Source: SBE contributions + VB council votes joined on topic. "
                    "Adjacency only — no causal inference drawn."
                )
                for r in rows_adj:
                    sector, sec_pct, topic, delta, m_yes, c_yes, n = r
                    sign = "+" if delta >= 0 else ""
                    _adj_lines.append(
                        f"  {sec_pct:4.0f}% from {sector:<15s} | "
                        f"votes YES on {topic:<20s} {m_yes:.0f}% "
                        f"(council avg {c_yes:.0f}%, delta {sign}{delta:.0f}pp, n={n})"
                    )
            if _adj_lines:
                lines += ["", "### VB Council — Donor Sector / Vote Topic Adjacency"] + _adj_lines

        # ── Per-member dissent profile (No-rate vs council avg by topic) ──
        _vb_dissent_trigger = (
            bool(_named_vb_finance)
            or any(w in q_lower for w in (
                "dissent", "pattern", "contrarian", "disagree", "against",
                "oppose", "no vote", "outlier", "independently", "bloc",
                "who votes against", "votes no",
            ))
        )
        if _vb_dissent_trigger and _table_exists(conn, "vb_member_dissent"):
            _dis_targets = _named_vb_finance if _named_vb_finance else _ALL_VB_FINANCE
            _dis_lines: list[str] = []
            for db_key in _dis_targets[:4]:
                # Resolve to full vote-table name via partial match
                full_name = conn.execute(
                    "SELECT DISTINCT member_name FROM vb_member_dissent "
                    "WHERE member_name LIKE ? LIMIT 1", (f"%{db_key.split('-')[0]}%",)
                ).fetchone()
                if not full_name:
                    continue
                vote_name = full_name[0]
                rows_dis = conn.execute("""
                    SELECT topic, member_no_pct, council_no_pct, delta_pp, member_no_count
                    FROM vb_member_dissent
                    WHERE member_name = ? AND ABS(delta_pp) >= 1 AND member_no_count >= 1
                    ORDER BY ABS(delta_pp) DESC LIMIT 5
                """, (vote_name,)).fetchall()
                if not rows_dis:
                    continue
                _dis_lines.append(f"\n#### {db_key} — Dissent Profile (No-vote rate vs council avg)")
                for topic, m_no, c_no, delta, nos in rows_dis:
                    sign = "+" if delta >= 0 else ""
                    direction = "more" if delta > 0 else "less"
                    _dis_lines.append(
                        f"  {topic:<22s}  {m_no:.1f}% No vs council {c_no:.1f}%  "
                        f"({sign}{delta:.1f}pp — votes No {direction} than peers, {nos} No votes)"
                    )
            if _dis_lines:
                lines += ["", "### VB Council — Dissent Profiles"] + _dis_lines

        # ── voting bloc / alignment ────────────────────────────────────────
        _bloc_trigger = any(w in q_lower for w in (
            "bloc", "align", "coalition", "faction", "together", "swing",
            "who votes with", "voting pattern", "pair", "outlier", "isolated",
            "agree", "disagree", "divide", "divided", "split",
        ))
        if _bloc_trigger and _table_exists(conn, "vb_voting_blocs"):
            # Detect named member for targeted lookup
            _bloc_member: str | None = None
            for _bk, _bpartial in {
                "berlucchi": "Berlucchi", "dyer": "Dyer", "henley": "Henley",
                "hutcheson": "Hutcheson", "wilson": "Wilson", "remick": "Remick",
                "ross-hammond": "Ross-Hammond", "schulman": "Schulman",
                "rouse": "Rouse", "cummings": "Cummings",
                "jackson-green": "Jackson-Green",
            }.items():
                if _bk in q_lower:
                    _brow = conn.execute(
                        "SELECT DISTINCT member_a FROM vb_voting_blocs WHERE member_a LIKE ?",
                        (f"%{_bpartial}%",)
                    ).fetchone()
                    if _brow:
                        _bloc_member = _brow[0]
                        break

            if _bloc_member:
                allies = conn.execute("""
                    SELECT member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a = ?
                    ORDER BY agreement_pct DESC LIMIT 4
                """, (_bloc_member,)).fetchall()
                contrarians = conn.execute("""
                    SELECT member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a = ?
                    ORDER BY agreement_pct ASC LIMIT 3
                """, (_bloc_member,)).fetchall()
                if allies:
                    lines.append(f"\n#### {_bloc_member} — Voting Alignment")
                    lines.append("  Closest allies:")
                    for b, pct, n in allies:
                        lines.append(f"    {b:<36s}  {pct:.1f}% agreement ({n} shared votes)")
                    if contrarians:
                        lines.append("  Most frequent disagreements:")
                        for b, pct, n in contrarians:
                            lines.append(f"    {b:<36s}  {pct:.1f}% agreement ({n} shared votes)")
            else:
                diverge = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct ASC LIMIT 5
                """).fetchall()
                aligned = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct DESC LIMIT 5
                """).fetchall()
                if diverge:
                    lines.append("\n#### VB Council — Voting Alignment (precomputed, all recorded votes)")
                    lines.append("  Most divergent pairs:")
                    for a, b, pct, n in diverge:
                        al = a.split()[-1].rstrip(",")
                        bl = b.split()[-1].rstrip(",")
                        lines.append(f"    {al} + {bl}: {pct:.1f}% agreement ({n} shared votes)")
                    if aligned:
                        lines.append("  Most aligned pairs:")
                        for a, b, pct, n in aligned:
                            al = a.split()[-1].rstrip(",")
                            bl = b.split()[-1].rstrip(",")
                            lines.append(f"    {al} + {bl}: {pct:.1f}% agreement ({n} shared votes)")

        # ── Split votes — specific non-unanimous examples ─────────────────────
        _split_trigger = (
            bool(_named_vb_finance)
            or any(w in q_lower for w in (
                "voted against", "voted no", "no vote", "voted down", "split",
                "contested", "controversial", "opposed", "specific vote",
                "which vote", "example", "minority",
            ))
        )
        if _split_trigger and _table_exists(conn, "vb_split_votes"):
            _split_lines: list[str] = []
            if _named_vb_finance:
                for _sv_key in _named_vb_finance[:2]:
                    _sv_last = _sv_key.split()[-1]
                    _sv_rows = conn.execute("""
                        SELECT meeting_date, topic, yes_count, no_count, no_voters, title
                        FROM vb_split_votes
                        WHERE no_voters LIKE ?
                        ORDER BY meeting_date DESC LIMIT 8
                    """, (f"%{_sv_last}%",)).fetchall()
                    if not _sv_rows:
                        continue
                    _sv_total = conn.execute(
                        "SELECT COUNT(*) FROM vb_split_votes WHERE no_voters LIKE ?",
                        (f"%{_sv_last}%",)
                    ).fetchone()[0]
                    _split_lines.append(
                        f"\n#### {_sv_key} — No votes on contested resolutions ({_sv_total} total)"
                    )
                    for _dt, _tp, _yes, _no, _voters, _title in _sv_rows:
                        _t = f"[{_tp}]" if _tp else "[other]"
                        _split_lines.append(
                            f"  {_dt}  {_t:18s}  {_yes}Y/{_no}N  ({_voters})  {_title[:72]}"
                        )
            else:
                _split_lines.append("\n#### Most contested votes (highest No count):")
                for _dt, _tp, _yes, _no, _voters, _title in conn.execute("""
                    SELECT meeting_date, topic, yes_count, no_count, no_voters, title
                    FROM vb_split_votes ORDER BY no_count DESC, meeting_date DESC LIMIT 8
                """).fetchall():
                    _t = f"[{_tp}]" if _tp else "[other]"
                    _split_lines.append(
                        f"  {_dt}  {_t:18s}  {_yes}Y/{_no}N  ({_voters})  {_title[:72]}"
                    )
                _split_lines.append("\n#### Most recent split votes:")
                for _dt, _tp, _yes, _no, _voters, _title in conn.execute("""
                    SELECT meeting_date, topic, yes_count, no_count, no_voters, title
                    FROM vb_split_votes ORDER BY meeting_date DESC LIMIT 6
                """).fetchall():
                    _t = f"[{_tp}]" if _tp else "[other]"
                    _split_lines.append(
                        f"  {_dt}  {_t:18s}  {_yes}Y/{_no}N  ({_voters})  {_title[:72]}"
                    )
            if _split_lines:
                lines += ["", "### VB Council — Split Votes (Specific Examples)"] + _split_lines

        conn.close()
        if len(lines) > 1:
            blocks.append("\n".join(lines))

    except Exception:
        pass


def build_database_context(query: str, max_chars: int = 22000, pro: bool = False) -> str:
    with _request_connection_cache():
        return _build_database_context_inner(query, max_chars, pro)


def _build_database_context_inner(query: str, max_chars: int = 22000, pro: bool = False) -> str:
    q = query or ""
    blocks: list[str] = []
    q_lower = q.lower()
    if (
        re.search(r"\b(database|databases|tables|schema)\b", q_lower)
        or "data do you have" in q_lower
        or "what data" in q_lower
    ):
        _add_schema_summary(blocks)

    bills = _bill_numbers(q)
    session = _session_year(q)
    terms = _keywords(q)
    _add_known_person_scope_context(blocks, q)
    _add_city_district_context(blocks, q)
    # Pre-generated civic profile — injected early so it always fits within max_chars
    _add_legislator_narrative_context(blocks, q)
    # Follow-the-money chain — fires when a bill number + money terms appear
    _add_follow_the_money_context(blocks, q)
    # Testimony proxy — fires when a bill number + lobbying/testimony terms appear
    _add_testimony_proxy_context(blocks, q)
    # Committee chair lookup — fires on "who chairs X" or committee name + money context
    _add_committee_chair_context(blocks, q)
    # Legislator committee memberships — fires on "[name] + committee" queries
    _add_legislator_committee_membership_context(blocks, q)
    # Vote similarity — fires on "votes most similarly", "most aligned with", etc.
    _add_vote_similarity_context(blocks, q)
    # Gatekeeper — fires when query asks who kills/blocks bills in committee
    _add_gatekeeper_context(blocks, q)
    # VEC financial disclosures — fires on SOEI / conflict-of-interest queries
    _add_disclosures_context(blocks, q)
    # Direct lobbyist/principal queries — fires without requiring a bill number
    _add_lobbyist_direct_context(blocks, q)
    # Sponsor–donor sector correlation
    _add_sponsor_correlation_context(blocks, q)
    _add_bill_context(blocks, bills, session, pro=pro)
    _add_bill_committee_chair_context(blocks, bills, session, pro=pro)
    _add_pac_context(blocks, q, terms)
    _add_federal_bill_context(blocks, q, terms)
    _add_federal_vote_context(blocks, q, terms)
    _add_floor_statements_context(blocks, q, terms)
    _add_indy_exp_context(blocks, q, terms)
    _add_fec_employer_context(blocks, q, terms)
    _add_pac_vote_correlation_context(blocks, q, terms)
    _add_spike_alerts_context(blocks, q)
    # Structured access to analytically rich tables the generic scan serves poorly
    _add_donor_vote_alignment_context(blocks, q, terms)
    _add_norfolk_council_context(blocks, q, terms)
    _add_vb_council_context(blocks, q, terms)
    _add_vpap_race_context(blocks, q)
    _add_congress_hearings_context(blocks, q, terms)
    if _is_campaign_finance_query(q):
        _add_campaign_finance_context(blocks, q, terms)
        _add_governor_action_context(blocks, q, terms, session)
    else:
        _add_governor_action_context(blocks, q, terms, session)
        _add_campaign_finance_context(blocks, q, terms)
    _add_targeted_vote_lookup(blocks, q, bills, terms, session)
    # Authoritative chief-patron sponsorship counts — injected before the generic
    # person-vote sweep so the model anchors on structured totals (chief vs co-patron,
    # real did-not-pass count) instead of inferring them from the ambiguous LIKE sweep.
    _add_sponsorship_summary_context(blocks, q, terms, session)
    _add_person_vote_context(blocks, q, terms, session)
    _add_state_vote_donor_context(blocks, q, terms, session)
    _add_keyword_context(blocks, q, terms, session)

    # Multi-cycle donor trend / investment-return signal — run FIRST so it
    # is never truncated by the generic SQL dump that comes later
    _add_donor_trend_context(blocks, q)

    # Civic analysis rendering rules — injected before donor/finance context so
    # the LLM sees the formatting contract before the data it must render.
    _add_civic_rendering_rules(blocks, q)

    # Donor-influence analysis for CoI / Dominion / industry sponsor queries
    # Also triggered by any campaign-finance query so named-legislator lookups
    # can return their donor-industry breakdown.
    if _ANALYSIS_TRIGGER.search(q) or _is_campaign_finance_query(q):
        _add_donor_analysis_context(blocks, q)
    _add_dominion_recipient_ranking_context(blocks, q)

    # ── Known data-gap signals ─────────────────────────────────────────────────
    # Emit explicit out_of_scope blocks for categories the DB layer cannot satisfy,
    # so the LLM declines rather than fabricating from unrelated records.

    # Historical years (19xx) — VA state bills/votes/finance start at 2018
    # Insert at position 0 so it is never truncated by max_chars even when
    # governor_actions or other builders produce large volumes of content.
    hist_years = _HIST_YEAR_RE.findall(q)
    if hist_years:
        yr = hist_years[0]
        blocks.insert(
            0,
            f"[Database Context - scope]\n"
            f"lookup_status=out_of_scope\n"
            f"detail=VoteIQ data coverage starts at 2018 for Virginia state bills, votes, and campaign finance. "
            f"The year {yr} is before coverage — no records are available for this period. "
            f"Do NOT fabricate historical results.",
        )

    # Federal committee assignments — not in dataset
    if re.search(r"\bcommittee\s+assignment", q_lower):
        blocks.append(
            "[Database Context - scope]\n"
            "lookup_status=out_of_scope\n"
            "detail=Federal committee assignments are not in the VoteIQ dataset. "
            "The dataset covers roll-call votes (congress_votes), sponsored bills (congress_bills), "
            "and campaign finance — not committee membership rosters. "
            "Do NOT fabricate committee assignments."
        )

    # Polling and public opinion surveys — explicitly excluded
    if re.search(r"\bpoll(?:ing|s|ster)?\b|\bsurvey\b|\bapproval\s+rating\b", q_lower):
        blocks.append(
            "[Database Context - scope]\n"
            "SCOPE LIMIT: polling_and_surveys_excluded\n"
            "lookup_status=out_of_scope\n"
            "detail=Polling data and public opinion surveys are not in the VoteIQ dataset. "
            "VoteIQ covers votes, bills, campaign finance, and donor patterns only. "
            "Do NOT fabricate poll numbers or approval ratings."
        )

    # Outgoing donations FROM legislators — not tracked (SBE tracks contributions RECEIVED)
    if re.search(
        r"\b(?:did|does|do)\s+[\w\s]{1,25}\s+donat(?:e|ed)\s+to\b"
        r"|\bdonated\s+to\s+(?:the\s+)?(?:dccc|rncc|nrcc|dscc|gop|dnc|rnc|dcc|rcc|\bpac\b)",
        q_lower,
    ):
        blocks.append(
            "[Database Context - scope]\n"
            "lookup_status=out_of_scope\n"
            "detail=VoteIQ tracks campaign contributions RECEIVED by Virginia candidates and committees "
            "(source: Virginia SBE Schedule A). Outgoing donations FROM legislators or candidates "
            "TO other committees or party organizations are not in the dataset. "
            "Do NOT fabricate an outgoing donation amount."
        )

    # No targeted builder matched. The generic all-table scan used to run
    # here, but it returns spurious keyword matches — a "school board"
    # question pulls a "school crossing" bill, a "women in boards of..."
    # resolution, and a disaster-relief hearing — which the model can stitch
    # into a confident, fabricated answer on data we don't actually have.
    # Instead, signal the gap explicitly so the chat admits the topic isn't
    # covered rather than inventing one. (Admin chat keeps the full scan via
    # build_admin_database_context.)
    if not blocks:
        blocks.append(
            "[No matching VoteIQ data]\n"
            "No structured records in the VoteIQ database match this question. "
            "Do NOT infer or fabricate an answer from unrelated records. Tell the "
            "user this topic is not in the dataset yet — VoteIQ currently covers "
            "Virginia state legislators and their votes/bills, the governor's "
            "actions, the federal delegation, campaign finance, and donor-vote "
            "alignment — and suggest a question within that scope."
        )

    # Enforce the 3-layer FACT/CONTEXT/ANALYSIS contract when any finance data is
    # present, so campaign-finance content is segregated out of the factual narrative.
    _add_layer_rendering_rules(blocks)

    context = "\n\n---\n\n".join(blocks)
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n\n[Database Context truncated to fit model context.]"
    return context


def build_admin_database_context(query: str, max_chars: int = 28000) -> str:
    """Build read-only SQL context for admin chat, including generic all-table search."""
    with _request_connection_cache():
        q = query or ""
        q_lower = q.lower()
        terms = _keywords(q)
        blocks: list[str] = []
        _add_admin_sql_coverage_summary(blocks)
        base = build_database_context(q, max_chars=max_chars)
        if base:
            blocks.append(base)
        _add_generic_sql_search_context(blocks, q, terms)
        if re.search(r"\b(database|databases|tables|schema|columns)\b", q_lower):
            _add_full_schema_summary(blocks)
        context = "\n\n---\n\n".join(blocks)
        if len(context) > max_chars:
            context = context[:max_chars].rstrip() + "\n\n[Admin Database Context truncated to fit model context.]"
        return context
