"""Tests for _add_person_vote_context and _add_known_person_scope_context."""
from __future__ import annotations

from voteiq.services import database_context as dc

_LEGISLATORS_DDL = """
    CREATE TABLE legislators (
        id TEXT, name TEXT, party TEXT, chamber TEXT, district TEXT, openstates_url TEXT
    )
"""

_VOTES_DDL = """
    CREATE TABLE votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id TEXT, session TEXT, vote_date TEXT, chamber TEXT,
        motion TEXT, result TEXT, voter_name TEXT, option TEXT,
        party TEXT, district TEXT
    )
"""

_BILLS_DDL = """
    CREATE TABLE bills (
        bill_id TEXT, session TEXT, title TEXT, sponsors TEXT,
        latest_action TEXT, latest_date TEXT, result TEXT, openstates_url TEXT
    )
"""


# ── scope routing (no DB required) ────────────────────────────────────────────

def test_spanberger_state_query_gets_dual_scope_hint(db):
    context = dc.build_database_context("What has Spanberger done as governor?")
    assert "current_voteiq_scope=Virginia state governor" in context
    assert "person=Abigail Spanberger" in context


def test_spanberger_explicit_federal_query_gets_congressional_scope(db):
    context = dc.build_database_context("What was Spanberger congressional voting record?")
    assert "requested_scope=former federal/congressional record" in context


def test_kiggans_query_gets_federal_member_scope(db):
    context = dc.build_database_context("How did Kiggans vote?")
    assert "person=Jennifer A. Kiggans" in context
    assert "lookup_policy=Use congress_votes" in context


def test_unknown_person_produces_no_scope_block(db):
    context = dc.build_database_context("How did Aaron Rouse vote on SB100?")
    assert "[Database Context - person scope]" not in context


# ── person vote context ────────────────────────────────────────────────────────

def test_vote_term_triggers_legislator_and_vote_lookup(db):
    db.openstates(
        _LEGISLATORS_DDL,
        """INSERT INTO legislators VALUES
           ('ocd-person/rouse', 'Aaron Rouse', 'Democratic', 'Senate', '22', NULL)""",
        _VOTES_DDL,
        """INSERT INTO votes
               (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES
               ('SB200', '2026', '2026-03-10', 'Senate', 'Passage', 'pass',
                'Aaron Rouse', 'yes', 'Democratic', '22')""",
        _BILLS_DDL,
    )
    context = dc.build_database_context("How did Aaron Rouse vote in 2026?")
    assert "openstates.legislators person" in context
    assert "openstates.votes person" in context


def test_profile_term_triggers_legislator_lookup(db):
    db.openstates(
        _LEGISLATORS_DDL,
        """INSERT INTO legislators VALUES
           ('ocd-person/lucas', 'Louise Lucas', 'Democratic', 'Senate', '18', NULL)""",
        _VOTES_DDL,
        _BILLS_DDL,
    )
    context = dc.build_database_context("Give me an overview of Louise Lucas")
    assert "openstates.legislators person" in context


def test_session_year_filters_votes_correctly(db):
    """Votes for a different session must not bleed into a year-specific query."""
    db.openstates(
        _VOTES_DDL,
        """INSERT INTO votes
               (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES
               ('HB1', '2025', '2025-02-01', 'House', 'Passage', 'pass',
                'Marcus Simon', 'yes', 'Democratic', '53')""",
        """INSERT INTO votes
               (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES
               ('HB9', '2026', '2026-02-01', 'House', 'Passage', 'pass',
                'Marcus Simon', 'yes', 'Democratic', '53')""",
        _LEGISLATORS_DDL,
        _BILLS_DDL,
    )
    context = dc.build_database_context("Marcus Simon voting record 2025")
    assert "bill_id=HB1" in context
    assert "bill_id=HB9" not in context


def test_finance_only_query_does_not_trigger_vote_dump(db):
    """A pure donor/funding question must not pull vote history — irrelevant and wastes context."""
    db.openstates(_VOTES_DDL, _LEGISLATORS_DDL, _BILLS_DDL)
    context = dc.build_database_context("Who funds Aaron Rouse campaign donations?")
    assert "openstates.votes person" not in context


def test_known_federal_with_congressional_term_defers_to_federal_builder(db):
    """Kiggans + 'congressional' → _add_federal_vote_context handles it; person builder skips."""
    db.openstates(_VOTES_DDL, _LEGISLATORS_DDL, _BILLS_DDL)
    context = dc.build_database_context("Show Kiggans congressional voting record")
    assert "openstates.votes person" not in context


def test_legislator_found_in_legislative_intelligence_va_votes(db):
    db.legislative(
        """CREATE TABLE va_votes (
            bill_number TEXT, session TEXT, legislator_id TEXT, vote TEXT, vote_date TEXT
        )""",
        """INSERT INTO va_votes VALUES
           ('HB50', '2026', 'Dawn Adams', 'yes', '2026-02-20')""",
    )
    db.openstates(_VOTES_DDL, _LEGISLATORS_DDL, _BILLS_DDL)
    context = dc.build_database_context("Dawn Adams voting record 2026")
    assert "legislative_intelligence.va_votes person" in context
