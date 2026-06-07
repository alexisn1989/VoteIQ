from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DB_PATHS = {
    "polls": BASE_DIR / "polls.db",
    "openstates": BASE_DIR / "openstates_va.db",
    "legislative_intelligence": BASE_DIR / "legislative_intelligence.db",
    "virginia_legislature": BASE_DIR / "virginia_legislature.db",
}

DATA_DIR_ENV_VARS = ("DATA_DIR", "VOTEIQ_DATA_DIR", "RENDER_DISK_MOUNT_PATH")
COMMON_DATA_DIRS = (Path("/data"), Path("/var/data"))

BILL_RE = re.compile(r"\b(HB|SB|HJ|SJ|HR|SR|HJR|SJR)\s*-?\s*(\d{1,5})\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")

KNOWN_FEDERAL_PEOPLE = {
    "spanberger": {
        "name": "Abigail Spanberger",
        "bioguide_id": "S001209",
        "fec_candidate_ids": ["H8VA07094"],
        "office_note": "Former U.S. Representative for Virginia; current Virginia governor in VoteIQ records.",
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
    path = _resolve_db_path(db_key)
    if path is None:
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
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
    "donation", "donations", "campaign", "campaing", "campain",
    "filing", "filings", "sbe", "vpap", "funding", "funded",
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
        "vpap", "funding", "funded", "public", "source", "sources",
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


def _add_known_person_scope_context(blocks: list[str], query: str) -> None:
    q_lower = (query or "").lower()
    if "spanberger" not in q_lower and "spanberge" not in q_lower:
        return
    if _is_explicit_federal_vote_query(query):
        blocks.append(
            "[Database Context - person scope]\n"
            "person=Abigail Spanberger\n"
            "requested_scope=former federal/congressional record\n"
            "lookup_policy=Use federal roll-call tables only because the query explicitly requests federal or congressional records."
        )
        return
    blocks.append(
        "[Database Context - person scope]\n"
        "person=Abigail Spanberger\n"
        "current_voteiq_scope=Virginia state governor\n"
        "lookup_policy=For current state-governor questions, use governor bill-action records "
        "(signed, vetoed, amended/returned) and Virginia state campaign-finance tables. "
        "Do not require federal roll-call vote records unless the user explicitly asks for her former congressional/federal record."
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
                        {latest_date_expr} AS latest_transaction_date
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
                    lines.append("Source: Virginia SBE Campaign Finance Schedule A itemized contribution records")
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
                    lines.append("Source: Virginia SBE Campaign Finance Schedule A itemized contribution records")
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
            _query_rows(conn, """
                SELECT bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district
                FROM votes
                WHERE bill_id=? AND session=?
                ORDER BY vote_date DESC
                LIMIT 30
            """, (bill, session), f"openstates.votes {bill}", blocks, limit=30)
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
            _query_rows(conn, f"""
                SELECT candidate_name, sector, total_amount, donor_count, cycle
                FROM candidate_sector_totals
                WHERE {clause}
                ORDER BY total_amount DESC
                LIMIT 8
            """, params, "polls.candidate_sector_totals keyword", blocks, limit=8)
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
        _query_rows(conn, f"""
            SELECT member_name, cycle, industry, total_amount,
                   contributor_count, top_donors
            FROM fec_industry_totals
            WHERE {clause}
            ORDER BY cycle DESC, total_amount DESC
            LIMIT 12
        """, params, "polls.fec_industry_totals PAC keyword", blocks, limit=12)
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
    }
    return [term for term in terms if term not in generic]


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
    has_name_only_query = len(people) >= 2 and len(terms) <= 3
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
            WHERE session=? AND ({clause})
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
        """, [session, *params], "openstates.bills sponsored by person", blocks, limit=12)
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


def _add_federal_vote_context(blocks: list[str], query: str, terms: list[str]) -> None:
    q_lower = (query or "").lower()
    if not any(term in q_lower for term in (
        "vote", "votes", "voted", "voting", "roll call", "roll-call",
        "correlation", "campaign data", "campaign finance",
    )):
        return

    person = _known_federal_person(query)
    explicit_federal = _is_explicit_federal_vote_query(query)
    if person and not explicit_federal:
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

    try:
        for target in targets[:3]:
            bgid = target["bioguide_id"]
            lines.append(
                f"target_name={target['name']}; bioguide_id={bgid}"
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
                    SELECT congress, vote_date, bill, question, member_vote, result
                    FROM congress_votes
                    WHERE bioguide_id = ?
                    ORDER BY vote_date DESC, vote_number DESC
                    LIMIT 10
                    """,
                    (bgid,),
                ).fetchall()
                for row in recent:
                    lines.append(f"- congress_votes recent: {_row_to_line(row)}")
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
                        lines.append(
                            f"    {direction} {short_name}: {row['committee_name']} "
                            f"— ${row['total']:,.0f} ({row['cnt']} txns)"
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
                net = row["support_total"] + row["oppose_total"]
                lines.append(
                    f"  {row['candidate_name']}: support=${row['support_total']:,.0f}  "
                    f"oppose=${row['oppose_total']:,.0f}  total outside=${net:,.0f}"
                )

        blocks.append("\n".join(lines))

    except Exception:
        pass
    finally:
        conn.close()


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
                        LIMIT 3
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
                    lines.append(f"  - {_row_to_line(found, max_value=220)}")
                if matches >= 45:
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
        lines.append(f"\n-- {r['name']} — Donor-Industry Profile --")
        lines.append(f"Top donor industry : {r.get('top_donor_industry', 'N/A')}")
        lines.append(f"Funds from top industry : ${r.get('top_donor_amount', 0):,.0f}")
        lines.append(f"Concentration : {r.get('concentration', 'N/A')}% of classified donor $")
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
        lines.append("\n-- Top 10 Legislators by Conflict-of-Interest Score --")
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
        lines.append("\n-- Dominion Energy Influence --")
        lines.append(f"Total Dominion utility donations : ${dom_stats.get('total_dominion_dollars',0):,.0f}")
        lines.append(
            f"Legislators with Dominion funding : "
            f"{dom_stats.get('legislators_funded',0)} of {dom_stats.get('legislators_analyzed',0)}"
        )
        lines.append(
            f"Top Dominion recipient : {dom_stats.get('top_recipient','')} "
            f"(${dom_stats.get('top_recipient_amount',0):,.0f})"
        )
        top_bill = max(
            (b for b in dom.get("per_bill", []) if b.get("ratio")),
            key=lambda x: x["ratio"],
            default=None,
        )
        if top_bill:
            lines.append(
                f"Largest YES/NO Dominion funding gap : {top_bill['bill_id']} — "
                f"YES voters received {top_bill['ratio']}x more Dominion $ than NO voters"
            )

    # ── Sponsored-bill outcome rates ──
    if iss:
        lines.append("\n-- Sponsored-Bill Pass Rate by Donor Industry --")
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

    conn = _connect("polls")
    li   = _connect("legislative_intelligence")
    if not conn:
        return

    try:
        # Resolve bill
        bill_row = conn.execute(
            "SELECT bill_id, session, title, status_label, primary_sponsor "
            "FROM legiscan_va_bills WHERE bill_number = ? "
            "ORDER BY session DESC LIMIT 1",
            (bill_number,),
        ).fetchone()
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
        lines = ["[Committee Chair Assignments — Virginia General Assembly]"]

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
            lines.append(
                f"  {chamber} | {committee}\n"
                f"    Chair: {chair_name} ({party}) — {raised_str}\n"
                f"    Top donor sectors: {top_sectors or '—'}"
            )

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

            lines = [
                f"[Committee Chair — {bill} killed in {committee_fragment}]",
            ]
            for row in unique_chairs[:3]:
                committee = row[0]
                chamber = row[1]
                chair_name = row[2]

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
                    lines.append(
                        f"  {chamber} {committee}\n"
                        f"    Chair: {chair_name} ({party}) — {raised_str}\n"
                        f"    Top donor sectors: {top_sectors or '—'}"
                    )
                else:
                    lines.append(f"  {chamber} {committee}\n    Chair: {chair_name} (no finance data)")

            if len(lines) > 1:
                blocks.append("\n".join(lines))

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

    matched = None
    matched_len = 0
    for row in rows:
        name = (row["name"] or "").lower()
        last = name.split()[-1] if name.split() else ""
        # Match on full name or last name (at least 4 chars to avoid false matches)
        if (name in q_lower or (len(last) >= 4 and last in q_lower)):
            if len(name) > matched_len:
                matched = row
                matched_len = len(name)

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
                ORDER BY ratio_vs_mean DESC LIMIT 1
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


def build_database_context(query: str, max_chars: int = 22000, pro: bool = False) -> str:
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
    # Pre-generated civic profile — injected early so it always fits within max_chars
    _add_legislator_narrative_context(blocks, q)
    # Follow-the-money chain — fires when a bill number + money terms appear
    _add_follow_the_money_context(blocks, q)
    # Testimony proxy — fires when a bill number + lobbying/testimony terms appear
    _add_testimony_proxy_context(blocks, q)
    # Committee chair lookup — fires on "who chairs X" or committee name + money context
    _add_committee_chair_context(blocks, q)
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
    _add_spike_alerts_context(blocks, q)
    if _is_campaign_finance_query(q):
        _add_campaign_finance_context(blocks, q, terms)
        _add_governor_action_context(blocks, q, terms, session)
    else:
        _add_governor_action_context(blocks, q, terms, session)
        _add_campaign_finance_context(blocks, q, terms)
    _add_person_vote_context(blocks, q, terms, session)
    _add_keyword_context(blocks, q, terms, session)

    # Multi-cycle donor trend / investment-return signal — run FIRST so it
    # is never truncated by the generic SQL dump that comes later
    _add_donor_trend_context(blocks, q)

    # Donor-influence analysis for CoI / Dominion / industry sponsor queries
    # Also triggered by any campaign-finance query so named-legislator lookups
    # can return their donor-industry breakdown.
    if _ANALYSIS_TRIGGER.search(q) or _is_campaign_finance_query(q):
        _add_donor_analysis_context(blocks, q)

    # Add comprehensive SQL table search as fallback for any unmatched keywords
    # This ensures all SQL tables are searched before RAG context
    _add_generic_sql_search_context(blocks, q, terms)

    context = "\n\n---\n\n".join(blocks)
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n\n[Database Context truncated to fit model context.]"
    return context


def build_admin_database_context(query: str, max_chars: int = 28000) -> str:
    """Build read-only SQL context for admin chat, including generic all-table search."""
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
