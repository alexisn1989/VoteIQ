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
    "campaign", "filing", "filings", "sbe", "vpap", "funding", "funded",
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
        "research", "overview", "with",
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
    if not finance_terms:
        return

    conn = _connect("polls")
    if not conn:
        blocks.append(
            "[Database Context - campaign finance lookup]\n"
            "lookup_status=lookup_error\n"
            "detail=polls.db unavailable"
        )
        return

    found_any = False
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
                clause, params = _like_any_text_clause(search_cols, finance_terms)
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
                clause, params = _like_any_text_clause(search_cols, finance_terms)
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
                clause, params = _like_any_text_clause(search_cols, finance_terms)
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
                clause, params = _like_any_text_clause(["candidate_name"], finance_terms)
                rows = conn.execute(f"""
                    SELECT
                        candidate_name,
                        election_cycle,
                        COUNT(*) AS contribution_records,
                        ROUND(SUM(amount), 2) AS total_amount,
                        MIN(transaction_date) AS first_transaction_date,
                        MAX(transaction_date) AS latest_transaction_date
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
        blocks.append(
            "[Database Context - campaign finance lookup]\n"
            "lookup_status=lookup_error\n"
            f"detail={type(exc).__name__}: {str(exc)[:240]}\n"
            f"searched_tables={', '.join(searched_tables) if searched_tables else 'none'}"
        )
    finally:
        conn.close()

    if not found_any:
        blocks.append(
            "[Database Context - campaign finance lookup]\n"
            "lookup_status=zero_records\n"
            f"searched_terms={', '.join(finance_terms)}\n"
            f"searched_tables={', '.join(searched_tables) if searched_tables else 'none'}"
        )


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
        finance_generic = {"campaign", "finance", "financial", "donor", "donors", "money", "contribution", "contributions", "pac"}
        finance_terms = [term for term in terms if term not in finance_generic] or terms[:4]
        clause, params = _like_any_clause(["person_name", "committee_name", "office", "party"], finance_terms[:4])
        _query_rows(conn, f"""
            SELECT person_name, office, district, party, committee_name, finance_url, source_url
            FROM va_finance_people
            WHERE {clause}
            LIMIT 8
        """, params, "polls.va_finance_people keyword", blocks, limit=8)
        clause, params = _like_any_clause(["candidate_name", "sector"], finance_terms[:4])
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
        "public", "search",
    }
    return [term for term in terms if term not in generic]


def _add_person_vote_context(blocks: list[str], query: str, terms: list[str], session: str) -> None:
    q_lower = (query or "").lower()
    if not any(term in q_lower for term in ("vote", "votes", "voted", "voting", "record")):
        return

    people = _person_terms(terms)
    if not people:
        return

    conn = _connect("openstates")
    if conn:
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


def _add_governor_action_context(blocks: list[str], query: str, terms: list[str], session: str) -> None:
    q_lower = (query or "").lower()
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
            if name.startswith("sqlite_") or name.endswith("_data") or name.endswith("_idx"):
                continue
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            except Exception:
                count = "?"
            tables.append(f"{name}({count})")
        conn.close()
        lines.append(f"- {db_key}: {', '.join(tables[:28])}")
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
    _add_bill_context(blocks, bills, session)
    _add_pac_context(blocks, q, _keywords(q))
    _add_campaign_finance_context(blocks, q, _keywords(q))
    _add_person_vote_context(blocks, q, _keywords(q), session)
    _add_governor_action_context(blocks, q, _keywords(q), session)
    _add_keyword_context(blocks, q, _keywords(q), session)

    context = "\n\n---\n\n".join(blocks)
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n\n[Database Context truncated to fit model context.]"
    return context
