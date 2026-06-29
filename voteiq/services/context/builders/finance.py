"""Campaign-finance, PAC, and donor-analysis context builders."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from voteiq.services.context._db import (
    BASE_DIR,
    DB_PATHS,
    _connect,
    _query_rows,
    _quote_identifier,
    _row_to_line,
    _table_columns,
    _table_exists,
)
from voteiq.services.context._parsing import YEAR_RE  # noqa: F401

# ── Name-stop set for federal-member resolution ──────────────────────────────
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


# ── Text-clause helpers ───────────────────────────────────────────────────────

def _like_any_clause(columns: list[str], terms: list[str]) -> tuple[str, list[str]]:
    clauses = []
    params: list[str] = []
    for term in terms:
        for col in columns:
            clauses.append(f"lower({col}) LIKE ?")
            params.append(f"%{term.lower()}%")
    return " OR ".join(clauses), params


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


# ── Campaign-finance query detection ─────────────────────────────────────────

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
    r"|vb\s+(city\s+)?council"
    # VB council members whose names would otherwise match GA legislators —
    # first-name-qualified to avoid misrouting to state finance tables.
    r"|jennifer\s+rouse"        # VB District 10 (vs. Aaron Rouse, GA)
    r"|berlucchi"               # VB District 3 (no GA counterpart)
    r"|ross.hammond"            # VB District 4
    r"|joashua|schulman"        # VB District 9
    r"|jackson.green",          # VB District 7
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


# ── Campaign finance context builder ─────────────────────────────────────────

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


# ── PAC context builder ───────────────────────────────────────────────────────

def _add_pac_context(blocks: list[str], query: str, terms: list[str]) -> None:
    q_lower = (query or "").lower()
    if not any(term in q_lower for term in (
        "pac", "political action", "committee money", "outside money",
        "outside spending", "independent expenditure", "super pac",
        "who funds", "who funded", "who backs", "special interest",
        "nra", "national rifle", "gun owners", "gun lobby",
        "donation", "donations", "contributor", "contributions",
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

        # NRA / state-level gun contributions from Virginia SBE (va_cf_schedule_a)
        if any(t in q_lower for t in ("nra", "national rifle", "gun owners", "gun lobby")):
            try:
                nra_rows = conn.execute("""
                    SELECT last_or_company, candidate_name, SUM(amount) as total,
                           COUNT(*) as n, MAX(election_cycle) as cycle
                    FROM va_cf_schedule_a
                    WHERE lower(last_or_company) LIKE 'nra%'
                       OR lower(last_or_company) LIKE '% nra%'
                       OR lower(last_or_company) LIKE '%national rifle%'
                    GROUP BY last_or_company, candidate_name
                    ORDER BY total DESC
                    LIMIT 20
                """).fetchall()
                if nra_rows:
                    lines = [
                        "[Database Context - polls.va_cf_schedule_a]",
                        "SCOPE: Virginia SBE Schedule A filings — NRA contributions to VA state legislators.",
                        "Source: Virginia State Board of Elections campaign finance filings.",
                        "NOTE: Amounts are SBE-reported totals; cycle reflects most recent filing year.",
                    ]
                    for r in nra_rows:
                        lines.append(
                            f"- {r[0]} → {r[1]}: ${r[2]:,.0f} ({r[3]} transactions, cycle {r[4]})"
                        )
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


# ── FEC independent expenditures ─────────────────────────────────────────────

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


# ── Dominion helpers ──────────────────────────────────────────────────────────

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
    ratio_medians: Any,
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


# ── Sponsor–donor correlation ─────────────────────────────────────────────────

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
    r"election|primary|on\s+the\s+ballot|governor['']?s?\s+race|"
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
