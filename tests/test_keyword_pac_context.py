"""Tests for _add_keyword_context and _add_pac_context.

keyword context: fallback bill search when no specific entity is resolved.
pac context: PAC ideology, independent-expenditure, and FEC industry lookups.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_OS_BILLS_DDL = """
    CREATE TABLE bills (
        bill_id TEXT, session TEXT, title TEXT, sponsors TEXT,
        latest_action TEXT, latest_date TEXT, result TEXT, openstates_url TEXT,
        subjects TEXT
    )
"""

_VA_BILLS_DDL = """
    CREATE TABLE va_bills (
        bill_number TEXT, session TEXT, title TEXT, status TEXT,
        description TEXT, introduced_by TEXT, introduced_date TEXT
    )
"""

_PAC_IDEOLOGY_DDL = """
    CREATE TABLE pac_ideology (
        committee_id TEXT, committee_name TEXT, short_name TEXT,
        ideology TEXT, alignment TEXT, network TEXT, issue_focus TEXT,
        description TEXT, source_url TEXT, verified INTEGER,
        foreign_alignment INTEGER, foreign_country TEXT
    )
"""

_FEC_IE_DDL = """
    CREATE TABLE fec_independent_expenditures (
        candidate_name TEXT, committee_name TEXT, support_oppose TEXT,
        expenditure_amount REAL, expenditure_date TEXT, cycle TEXT,
        office TEXT, state TEXT
    )
"""


# ── keyword bill search ────────────────────────────────────────────────────────

def test_keyword_matches_openstates_bills_by_title(db):
    db.openstates(
        _OS_BILLS_DDL,
        "INSERT INTO bills VALUES ('HB400','2026','Clean Water Protection Act','Del. Smith','Signed','2026-04-01','pass',NULL,NULL)",
    )
    # terms: "clean", "water", "protection" — all present in the title
    context = dc.build_database_context("clean water protection 2026")
    assert "openstates.bills keyword" in context
    assert "bill_id=HB400" in context


def test_keyword_matches_polls_va_bills_by_title(db):
    db.polls(
        _VA_BILLS_DDL,
        """INSERT INTO va_bills VALUES
               ('HB777', '2026', 'Renewable Energy Standards', 'enrolled',
                'Establishes renewable energy goals', NULL, NULL)""",
    )
    context = dc.build_database_context("renewable energy standards 2026")
    assert "polls.va_bills keyword" in context
    assert "bill_number=HB777" in context


def test_keyword_respects_session_year_in_query(db):
    db.openstates(
        _OS_BILLS_DDL,
        "INSERT INTO bills VALUES ('HB1','2025','Solar Energy Standards',NULL,NULL,NULL,'pass',NULL,NULL)",
        "INSERT INTO bills VALUES ('HB2','2026','Solar Energy Standards',NULL,NULL,NULL,'pass',NULL,NULL)",
    )
    # terms: "solar", "energy", "standards" — all in the title; session filters to 2025
    context = dc.build_database_context("solar energy standards 2025")
    assert "bill_id=HB1" in context
    assert "bill_id=HB2" not in context


def test_pure_finance_query_without_bill_terms_skips_keyword_bill_search(db):
    """Finance-only query must not search the bill tables — saves context budget."""
    db.openstates(
        _OS_BILLS_DDL,
        "INSERT INTO bills VALUES ('HB1','2026','Finance bill',NULL,NULL,NULL,'pass',NULL,NULL)",
    )
    context = dc.build_database_context("who donated money to Aaron Rouse campaign finance")
    assert "openstates.bills keyword" not in context


def test_finance_query_with_legislation_term_still_searches_bills(db):
    """Finance query containing 'legislation' must not short-circuit the bill keyword search."""
    db.openstates(
        _OS_BILLS_DDL,
        "INSERT INTO bills VALUES ('HB99','2026','Campaign Finance Legislation Reform',NULL,NULL,'2026-01-01','pass',NULL,NULL)",
    )
    # terms: "campaign", "finance", "legislation" — all in title; is_finance=True but
    # "legislation" in query prevents the early-return short-circuit
    context = dc.build_database_context("campaign finance legislation 2026")
    assert "openstates.bills keyword" in context
    assert "bill_id=HB99" in context


# ── PAC context ────────────────────────────────────────────────────────────────

def test_pac_keyword_returns_ideology_block(db):
    db.polls(
        _PAC_IDEOLOGY_DDL,
        """INSERT INTO pac_ideology VALUES
               ('C001', 'Clean Virginia Fund', 'CVF', 'progressive', 'Democratic',
                'environment', 'environment',
                'Environmental advocacy PAC', NULL, 1, 0, NULL)""",
    )
    context = dc.build_database_context("What does Clean Virginia PAC fund?")
    assert "polls.pac_ideology keyword" in context
    assert "committee_name=Clean Virginia Fund" in context


def test_pac_keyword_returns_independent_expenditures(db):
    db.polls(
        _FEC_IE_DDL,
        """INSERT INTO fec_independent_expenditures VALUES
               ('Aaron Rouse', 'Clean Virginia Fund', 'SUPPORT',
                50000.0, '2021-10-01', '2021', 'State Senate', 'VA')""",
    )
    context = dc.build_database_context("PAC spending for Aaron Rouse")
    assert "polls.fec_independent_expenditures keyword" in context
    assert "candidate_name=Aaron Rouse" in context


def test_non_pac_query_skips_pac_builder(db):
    db.polls(
        _PAC_IDEOLOGY_DDL,
        """INSERT INTO pac_ideology VALUES
               ('C001', 'Clean Virginia Fund', NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, 1, 0, NULL)""",
    )
    context = dc.build_database_context("How did Aaron Rouse vote on SB100?")
    assert "polls.pac_ideology keyword" not in context


def test_super_pac_trigger_phrase_fires_pac_builder(db):
    db.polls(
        _PAC_IDEOLOGY_DDL,
        """INSERT INTO pac_ideology VALUES
               ('C002', 'Virginia Jobs Coalition', 'VJC', 'conservative', 'Republican',
                'business', 'jobs', 'Business advocacy', NULL, 1, 0, NULL)""",
    )
    context = dc.build_database_context("super PAC outside spending Virginia Jobs Coalition")
    assert "polls.pac_ideology keyword" in context
