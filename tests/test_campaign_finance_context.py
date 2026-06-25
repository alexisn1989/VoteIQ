"""Tests for _add_campaign_finance_context.

Fires on any _FINANCE_QUERY_TERMS hit (donor, campaign, finance, raised, etc.).
Skips for municipal council queries (handled by Norfolk/VB builders).
Emits zero_records inventory when no rows match.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_CF_REPORTS_DDL = """
    CREATE TABLE va_cf_reports (
        CandidateName TEXT, CommitteeName TEXT,
        OfficeSought TEXT, Party TEXT, ElectionCycle TEXT, ReportYear TEXT,
        FilingType TEXT, FilingDate TEXT, StartDate TEXT, EndDate TEXT,
        NoActivity INTEGER, BalanceLastReportingPeriod REAL, source_period TEXT
    )
"""

_CF_SCHED_A_DDL = """
    CREATE TABLE va_cf_schedule_a (
        report_uid TEXT, candidate_name TEXT, election_cycle TEXT,
        amount REAL, transaction_date TEXT,
        is_individual INTEGER, first_name TEXT, last_or_company TEXT,
        employer TEXT, occupation TEXT
    )
"""

_VA_FINANCE_PEOPLE_DDL = """
    CREATE TABLE va_finance_people (
        person_name TEXT, office TEXT, district TEXT, party TEXT, role TEXT,
        committee_name TEXT, finance_url TEXT, source_url TEXT,
        data_confidence TEXT, fetched_at TEXT
    )
"""


def test_cf_reports_match_returns_filings_block(db):
    db.polls(
        _CF_REPORTS_DDL,
        "INSERT INTO va_cf_reports VALUES "
        "('Aaron Rouse','Friends of Aaron Rouse','Senate','D','2021',NULL,NULL,NULL,NULL,NULL,0,NULL,NULL)",
    )
    context = dc.build_database_context("Aaron Rouse campaign finance")
    assert "polls.va_cf_reports campaign finance filings" in context
    assert "Aaron Rouse" in context


def test_schedule_a_match_returns_totals_block(db):
    db.polls(
        _CF_SCHED_A_DDL,
        "INSERT INTO va_cf_schedule_a VALUES "
        "('R001','Aaron Rouse','2021',1000.0,'2021-06-01',0,NULL,'Dominion Energy','Dominion Energy','Lobbyist')",
        "INSERT INTO va_cf_schedule_a VALUES "
        "('R001','Aaron Rouse','2021',500.0,'2021-07-01',0,NULL,'Tech Corp','Tech Corp','Engineer')",
    )
    context = dc.build_database_context("Aaron Rouse campaign donations")
    assert "polls.va_cf_schedule_a campaign finance totals" in context


def test_va_finance_people_match_returns_profile_block(db):
    db.polls(
        _VA_FINANCE_PEOPLE_DDL,
        "INSERT INTO va_finance_people VALUES "
        "('Louise Lucas','Senate','18','D','candidate','Lucas for Senate',NULL,NULL,'high','2026-01-01')",
    )
    context = dc.build_database_context("Louise Lucas fundraising donors")
    assert "polls.va_finance_people campaign finance profile" in context
    assert "Louise Lucas" in context


def test_finance_query_with_no_match_emits_zero_records(db):
    """Finance query with no rows → zero_records inventory block appended."""
    db.polls(_CF_REPORTS_DDL)
    context = dc.build_database_context("Zachary Nobody campaign finance")
    assert "lookup_status=zero_records" in context


def test_non_finance_query_skips_builder(db):
    db.polls(_CF_REPORTS_DDL)
    context = dc.build_database_context("How did Aaron Rouse vote on HB84?")
    assert "polls.va_cf_reports campaign finance filings" not in context


def test_municipal_council_query_skips_ga_finance_builder(db):
    """Norfolk council finance is handled by _add_norfolk_council_context — skip GA builder."""
    db.polls(
        _CF_REPORTS_DDL,
        "INSERT INTO va_cf_reports VALUES "
        "('Joe Council','Council Committee','Council','N','2024',NULL,NULL,NULL,NULL,NULL,0,NULL,NULL)",
    )
    context = dc.build_database_context("norfolk council campaign donations")
    assert "polls.va_cf_reports campaign finance filings" not in context
