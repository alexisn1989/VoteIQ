from __future__ import annotations

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


def _connect(db_key: str) -> sqlite3.Connection | None:
    path = DB_PATHS[db_key]
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        alt = Path(data_dir) / path.name
        if alt.exists():
            path = alt
    if not path.exists():
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
    "finance", "financial", "finicial", "fundraising", "fundraiser",
    "raised", "donor", "donors", "money", "contribution", "contributions",
    "donation", "donations", "campaign", "filing", "filings", "sbe",
    "vpap", "funding", "funded",
)


def _is_campaign_finance_query(query: str) -> bool:
    q_lower = (query or "").lower()
    return any(term in q_lower for term in _FINANCE_QUERY_TERMS)


def _campaign_finance_terms(query: str, terms: list[str]) -> list[str]:
    generic = {
        "campaign", "finance", "financial", "finicial", "fundraising",
        "fundraiser", "raised", "donor", "donors", "money", "contribution",
        "contributions", "record", "records", "filing", "filings", "sbe",
        "vpap", "funding", "funded", "public", "source", "sources",
        "research", "report", "overview", "with", "why", "return", "returned", "data",
        "issue", "problem", "lookup", "retrieval", "debug", "debugger",
        "bill", "bills", "action", "actions", "veto", "vetoes", "vetoed",
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


def _add_bill_context(blocks: list[str], bills: list[str], session: str) -> None:
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
            _query_rows(conn, f"""
                SELECT source, title, published_at, url, gemini_json
                FROM va_news
                WHERE {clause}
                ORDER BY COALESCE(published_at, fetched_at) DESC
                LIMIT 6
            """, params, "polls.va_news keyword", blocks, limit=6)

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
    try:
        rows = conn.execute(f"""
            SELECT {quoted_select}
            FROM governor_actions
            WHERE {" AND ".join(where_parts)}
            ORDER BY {_quote_identifier(order_col)} DESC
            LIMIT 40
        """, tuple(query_params)).fetchall()

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

    conn.close()
    _append_governor_action_lookup_block(
        blocks,
        "records_found" if rows else "zero_records",
        rows=rows,
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
            lines.append(f"- {db_key}: database_unavailable")
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
            lines.append(f"- {db_key}: database_unavailable")
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
            lines.append(f"- {db_key}: database_unavailable")
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


def build_database_context(query: str, max_chars: int = 22000) -> str:
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
    _add_bill_context(blocks, bills, session)
    _add_pac_context(blocks, q, terms)
    _add_governor_action_context(blocks, q, terms, session)
    _add_federal_vote_context(blocks, q, terms)
    _add_campaign_finance_context(blocks, q, terms)
    _add_person_vote_context(blocks, q, terms, session)
    _add_keyword_context(blocks, q, terms, session)

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
