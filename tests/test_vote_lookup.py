"""Tests for _add_targeted_vote_lookup — the single-legislator × single-bill path.

The ROUTING RULE (CLAUDE.md): a query containing one legislator name + one bill
number must use name-filtered SQL, never a full roll-call blob.  Full roll-calls
are alphabetical; a 666-row A→Z list truncates before names starting with 'H',
causing false "vote not found" answers.
"""
from __future__ import annotations

import pytest
from voteiq.services import database_context as dc

_OPENSTATES_VOTES_DDL = """
    CREATE TABLE votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id TEXT, session TEXT, vote_date TEXT, chamber TEXT,
        motion TEXT, result TEXT, voter_name TEXT, option TEXT,
        party TEXT, district TEXT
    )
"""

_POLLS_BILLS_DDL = """
    CREATE TABLE va_bills (
        bill_number TEXT, session TEXT, title TEXT, status TEXT,
        description TEXT, introduced_by TEXT, introduced_date TEXT
    )
"""

_POLLS_RECENT_VOTES_DDL = """
    CREATE TABLE va_legislator_recent_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        voter_name TEXT, bill_id TEXT, session TEXT,
        vote_date TEXT, option TEXT, motion TEXT, is_procedural INTEGER
    )
"""


# ── openstates path ────────────────────────────────────────────────────────────

def test_targeted_lookup_returns_named_legislator_vote(db):
    db.openstates(
        _OPENSTATES_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('HB84', '2026', '2026-02-10', 'House', 'Passage', 'pass', 'Aaron Rouse', 'yes', 'Democratic', '22')""",
    )
    context = dc.build_database_context("How did Aaron Rouse vote on HB84?")
    assert "voter=Aaron Rouse" in context
    assert "vote=yes" in context
    assert "targeted_lookup=true" in context


def test_targeted_lookup_filters_to_named_legislator_only(db):
    """Regression: other legislators' rows must not appear in a name-targeted query."""
    db.openstates(
        _OPENSTATES_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('HB84', '2026', '2026-02-10', 'House', 'Passage', 'pass', 'Glenn Youngkin', 'no', 'Republican', '1')""",
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('HB84', '2026', '2026-02-10', 'House', 'Passage', 'pass', 'Aaron Rouse', 'yes', 'Democratic', '22')""",
    )
    context = dc.build_database_context("How did Aaron Rouse vote on HB84?")
    assert "voter=Aaron Rouse" in context
    assert "voter=Glenn Youngkin" not in context


def test_no_bill_number_skips_targeted_lookup(db):
    db.openstates(
        _OPENSTATES_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('HB84', '2026', '2026-02-10', 'House', 'Passage', 'pass', 'Aaron Rouse', 'yes', 'Democratic', '22')""",
    )
    # "education bill" has no bill number → targeted lookup must not fire
    context = dc.build_database_context("How did Aaron Rouse vote on the education bill?")
    assert "targeted_lookup=true" not in context


def test_no_vote_signal_skips_targeted_lookup(db):
    db.openstates(
        _OPENSTATES_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('HB84', '2026', '2026-02-10', 'House', 'Passage', 'pass', 'Aaron Rouse', 'yes', 'Democratic', '22')""",
    )
    # No vote-signal keyword → lookup must not fire
    context = dc.build_database_context("Aaron Rouse HB84 sponsor text")
    assert "targeted_lookup=true" not in context


def test_miss_in_db_produces_no_targeted_block(db):
    db.openstates(
        _OPENSTATES_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('HB100', '2026', '2026-02-10', 'House', 'Passage', 'pass', 'Other Person', 'yes', 'Republican', '1')""",
    )
    context = dc.build_database_context("How did Aaron Rouse vote on HB84?")
    assert "targeted_lookup=true" not in context
    assert "voter=Aaron Rouse" not in context


def test_nay_vote_captured_correctly(db):
    db.openstates(
        _OPENSTATES_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('SB100', '2026', '2026-03-01', 'Senate', 'Passage', 'fail', 'Louise Lucas', 'no', 'Democratic', '18')""",
    )
    context = dc.build_database_context("How did Louise Lucas vote on SB100?")
    assert "voter=Louise Lucas" in context
    assert "vote=no" in context
    assert "targeted_lookup=true" in context


# ── polls path (va_legislator_recent_votes JOIN va_bills) ─────────────────────

def test_polls_path_fires_when_both_tables_exist(db):
    db.polls(
        _POLLS_BILLS_DDL,
        """INSERT INTO va_bills VALUES ('HB84', '2026', 'Education funding', 'passed', NULL, NULL, NULL)""",
        _POLLS_RECENT_VOTES_DDL,
        """INSERT INTO va_legislator_recent_votes (voter_name, bill_id, session, vote_date, option, motion, is_procedural)
           VALUES ('Marcus Simon', 'HB84', '2026', '2026-02-15', 'yes', 'Passage', 0)""",
    )
    context = dc.build_database_context("How did Marcus Simon vote on HB84?")
    assert "voter=Marcus Simon" in context
    assert "targeted_lookup=true" in context


def test_polls_path_marks_procedural_votes(db):
    db.polls(
        _POLLS_BILLS_DDL,
        """INSERT INTO va_bills VALUES ('SB10', '2026', 'Climate bill', 'passed', NULL, NULL, NULL)""",
        _POLLS_RECENT_VOTES_DDL,
        """INSERT INTO va_legislator_recent_votes (voter_name, bill_id, session, vote_date, option, motion, is_procedural)
           VALUES ('Dawn Adams', 'SB10', '2026', '2026-02-15', 'yes', 'Concur House Substitute', 1)""",
    )
    context = dc.build_database_context("How did Dawn Adams vote on SB10?")
    assert "[PROCEDURAL]" in context


# ── routing marker deduplication ──────────────────────────────────────────────

def test_targeted_lookup_marker_appears_once_when_both_dbs_hit(db):
    db.polls(
        _POLLS_BILLS_DDL,
        """INSERT INTO va_bills VALUES ('SB10', '2026', 'Climate bill', 'passed', NULL, NULL, NULL)""",
        _POLLS_RECENT_VOTES_DDL,
        """INSERT INTO va_legislator_recent_votes (voter_name, bill_id, session, vote_date, option, motion, is_procedural)
           VALUES ('Louise Lucas', 'SB10', '2026', '2026-02-15', 'yes', 'Passage', 0)""",
    )
    db.openstates(
        _OPENSTATES_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option, party, district)
           VALUES ('SB10', '2026', '2026-02-15', 'Senate', 'Passage', 'pass', 'Louise Lucas', 'yes', 'Democratic', '18')""",
    )
    context = dc.build_database_context("How did Louise Lucas vote on SB10?")
    assert context.count("targeted_lookup=true") == 1
