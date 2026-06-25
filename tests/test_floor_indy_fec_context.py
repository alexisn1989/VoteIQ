"""Tests for _add_floor_statements_context, _add_indy_exp_context, and _add_fec_employer_context."""
from __future__ import annotations

from voteiq.services import database_context as dc

_MEMBERS_DDL = """
    CREATE TABLE congress_members (
        bioguide_id TEXT, name TEXT, party TEXT, chamber TEXT, state TEXT, district TEXT
    )
"""

_FLOOR_DDL = """
    CREATE TABLE congress_floor_statements (
        bioguide_id TEXT, member_name TEXT, statement_date TEXT, title TEXT, text TEXT
    )
"""

_INDY_EXP_DDL = """
    CREATE TABLE fec_independent_expenditures (
        bioguide_id TEXT, candidate_name TEXT, committee_name TEXT,
        committee_id TEXT, support_oppose TEXT, expenditure_amount REAL,
        expenditure_date TEXT, cycle TEXT
    )
"""

_FEC_INDIV_DDL = """
    CREATE TABLE fec_individual_contributions (
        bioguide_id TEXT, contributor_employer TEXT, employer_sector TEXT,
        amount REAL, cycle TEXT
    )
"""


# ── floor statements ──────────────────────────────────────────────────────────

def test_floor_trigger_with_named_member_returns_statements(db):
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES ('W000804','Rob Wittman','R','House','VA','1')",
        _FLOOR_DDL,
        "INSERT INTO congress_floor_statements VALUES "
        "('W000804','Rob Wittman','2025-04-10','Energy Policy Remarks','We must expand offshore energy.')",
    )
    context = dc.build_database_context("Wittman floor speech on energy")
    assert "congress_floor_statements" in context
    assert "Energy Policy Remarks" in context


def test_floor_trigger_without_named_member_does_topic_search(db):
    """'congressional record' phrase alone → topic search across all VA members."""
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES ('K000399','Jennifer A. Kiggans','R','House','VA','2')",
        _FLOOR_DDL,
        "INSERT INTO congress_floor_statements VALUES "
        "('K000399','Jennifer A. Kiggans','2025-05-01','Coastal Defense Act','Remarks on defense.')",
    )
    context = dc.build_database_context(
        "What did anyone say in the congressional record about defense?"
    )
    assert "congress_floor_statements" in context
    assert "Coastal Defense Act" in context


def test_no_floor_trigger_no_member_skips_floor_builder(db):
    db.polls(
        _MEMBERS_DDL,
        _FLOOR_DDL,
        "INSERT INTO congress_floor_statements VALUES "
        "('W000804','Rob Wittman','2025-04-10','Energy bill','Some text.')",
    )
    context = dc.build_database_context("What is HB84?")
    assert "[Database Context - congress_floor_statements]" not in context


# ── independent expenditures ──────────────────────────────────────────────────

def test_indy_exp_with_named_member_returns_support_oppose_totals(db):
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES ('K000399','Jennifer A. Kiggans','R','House','VA','2')",
        _INDY_EXP_DDL,
        "INSERT INTO fec_independent_expenditures VALUES "
        "('K000399','Jennifer Kiggans','Clean Ocean PAC','C00123','S',75000.0,'2024-09-01','2024')",
        "INSERT INTO fec_independent_expenditures VALUES "
        "('K000399','Jennifer Kiggans','Oppose Kiggans Fund','C00456','O',20000.0,'2024-10-01','2024')",
    )
    context = dc.build_database_context("outside spending for Kiggans 2024")
    assert "fec_independent_expenditures" in context
    assert "Clean Ocean PAC" in context


def test_indy_exp_general_trigger_returns_leaderboard(db):
    """Indy trigger without a specific member → leaderboard of top outside-money races."""
    db.polls(
        _MEMBERS_DDL,
        _INDY_EXP_DDL,
        "INSERT INTO fec_independent_expenditures VALUES "
        "('W000804','Rob Wittman','Jobs PAC','C00789','S',50000.0,'2024-08-01','2024')",
    )
    context = dc.build_database_context(
        "How much dark money was spent in Virginia federal races?"
    )
    assert "fec_independent_expenditures" in context
    assert "Rob Wittman" in context


def test_non_indy_query_skips_indy_exp_builder(db):
    db.polls(
        _MEMBERS_DDL,
        _INDY_EXP_DDL,
    )
    context = dc.build_database_context("How did Aaron Rouse vote on SB100?")
    assert "[Database Context - fec_independent_expenditures]" not in context


# ── FEC individual contributions / employer rollup ────────────────────────────

def test_fec_employer_trigger_returns_employer_table(db):
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES ('K000399','Jennifer A. Kiggans','R','House','VA','2')",
        _FEC_INDIV_DDL,
        "INSERT INTO fec_individual_contributions VALUES "
        "('K000399','Huntington Ingalls Industries','Defense',2500.0,'2024')",
        "INSERT INTO fec_individual_contributions VALUES "
        "('K000399','Huntington Ingalls Industries','Defense',1500.0,'2024')",
    )
    context = dc.build_database_context("top donors for Kiggans from employers")
    assert "fec_individual_contributions employer rollup" in context
    assert "Huntington Ingalls Industries" in context


def test_no_trigger_skips_fec_employer_builder(db):
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES ('K000399','Jennifer A. Kiggans','R','House','VA','2')",
        _FEC_INDIV_DDL,
        "INSERT INTO fec_individual_contributions VALUES "
        "('K000399','Some Corp','Tech',1000.0,'2024')",
    )
    context = dc.build_database_context("How did Kiggans vote on defense bills?")
    assert "fec_individual_contributions employer rollup" not in context


def test_trigger_without_member_skips_fec_employer_builder(db):
    """Trigger present but congress_members has no matching name → builder skips."""
    db.polls(
        _MEMBERS_DDL,
        _FEC_INDIV_DDL,
        "INSERT INTO fec_individual_contributions VALUES "
        "('K000399','Some Corp','Tech',1000.0,'2024')",
    )
    context = dc.build_database_context("top donors for Virginia federal races")
    assert "fec_individual_contributions employer rollup" not in context
