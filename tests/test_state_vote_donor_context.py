"""Tests for _add_state_vote_donor_context.

Fires when a query has BOTH a vote signal AND a money/donor signal
for a resolvable non-federal state legislator.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_VOTES_DDL = """
    CREATE TABLE va_legislator_recent_votes (
        voter_name TEXT, chamber TEXT, session TEXT,
        bill_id TEXT, option TEXT, motion TEXT, is_procedural INTEGER, vote_date TEXT
    )
"""

_BILLS_DDL = """
    CREATE TABLE va_bills (
        bill_number TEXT, session TEXT, title TEXT, subjects TEXT, introduced_by TEXT
    )
"""

_CF_REPORTS_DDL = """
    CREATE TABLE va_cf_reports (
        CandidateName TEXT, ReportUID TEXT, IsGeneralAssembly INTEGER, ElectionCycle TEXT
    )
"""

_CF_SCHED_A_DDL = """
    CREATE TABLE va_cf_schedule_a (
        report_uid TEXT, employer TEXT, occupation TEXT,
        first_name TEXT, last_or_company TEXT, is_individual INTEGER,
        amount REAL, transaction_date TEXT
    )
"""

# Unique text in the block header (avoids the × Unicode char)
_BLOCK_SRC = "va_legislator_recent_votes JOIN va_bills"


def test_vote_and_money_query_returns_vote_record(db):
    db.polls(
        _VOTES_DDL,
        "INSERT INTO va_legislator_recent_votes VALUES "
        "('Aaron Rouse','Senate','2026','SB10','yes','Passage',0,'2026-03-01')",
        _BILLS_DDL,
        "INSERT INTO va_bills VALUES ('SB10','2026','Energy Standards','energy','Del. Smith')",
    )
    context = dc.build_database_context("Aaron Rouse voting record and donors 2026")
    assert _BLOCK_SRC in context
    assert "Aaron Rouse" in context


def test_vote_and_money_query_includes_donor_block(db):
    db.polls(
        _VOTES_DDL,
        "INSERT INTO va_legislator_recent_votes VALUES "
        "('Aaron Rouse','Senate','2026','SB10','yes','Passage',0,'2026-03-01')",
        _BILLS_DDL,
        "INSERT INTO va_bills VALUES ('SB10','2026','Clean Energy','energy','Del. Smith')",
        _CF_REPORTS_DDL,
        "INSERT INTO va_cf_reports VALUES ('Aaron Rouse','UID001',1,'2026')",
        _CF_SCHED_A_DDL,
        "INSERT INTO va_cf_schedule_a VALUES "
        "('UID001','Dominion Energy','Lobbyist','John','Donor',0,5000.0,'2026-01-01')",
    )
    context = dc.build_database_context("Aaron Rouse voting record and donors 2026")
    assert "Dominion Energy" in context


def test_vote_only_query_skips_state_donor_builder(db):
    """No money keyword → builder must not fire."""
    db.polls(
        _VOTES_DDL,
        "INSERT INTO va_legislator_recent_votes VALUES "
        "('Aaron Rouse','Senate','2026','SB10','yes','Passage',0,'2026-03-01')",
        _BILLS_DDL,
        "INSERT INTO va_bills VALUES ('SB10','2026','Energy bill','energy','Del. Smith')",
    )
    context = dc.build_database_context("How did Aaron Rouse vote in 2026?")
    assert _BLOCK_SRC not in context


def test_money_only_query_skips_state_donor_builder(db):
    """No vote keyword → builder must not fire."""
    db.polls(
        _CF_REPORTS_DDL,
        _CF_SCHED_A_DDL,
    )
    context = dc.build_database_context("Who donates to Aaron Rouse campaign finance?")
    assert _BLOCK_SRC not in context


def test_known_federal_person_skips_state_donor_builder(db):
    """Federal members are routed to the PAC-vote block — skip this builder."""
    db.polls(
        _VOTES_DDL,
        _BILLS_DDL,
    )
    context = dc.build_database_context("Kiggans voting record and donors 2026")
    assert _BLOCK_SRC not in context
