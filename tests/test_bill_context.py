"""Tests for _add_bill_context — bill query retrieval from polls and openstates."""
from __future__ import annotations

import pytest
from voteiq.services import database_context as dc

_VA_BILLS_DDL = """
    CREATE TABLE va_bills (
        bill_number TEXT, session TEXT, title TEXT, status TEXT,
        description TEXT, introduced_by TEXT, introduced_date TEXT
    )
"""

_GOV_ACTIONS_DDL = """
    CREATE TABLE governor_actions (
        bill_number TEXT, session TEXT, title TEXT, action_label TEXT,
        raw_status TEXT, action_date TEXT, governor TEXT,
        sponsor_name TEXT, source_url TEXT
    )
"""

_OS_BILLS_DDL = """
    CREATE TABLE bills (
        bill_id TEXT, session TEXT, title TEXT, sponsors TEXT,
        latest_action TEXT, latest_date TEXT, result TEXT, openstates_url TEXT
    )
"""

_OS_VOTES_DDL = """
    CREATE TABLE votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id TEXT, session TEXT, vote_date TEXT, chamber TEXT,
        motion TEXT, result TEXT, voter_name TEXT, option TEXT,
        party TEXT, district TEXT
    )
"""


# ── polls.va_bills ─────────────────────────────────────────────────────────────

def test_bill_found_in_va_bills(db):
    db.polls(
        _VA_BILLS_DDL,
        """INSERT INTO va_bills VALUES
           ('HB84', '2026', 'Water quality standards', 'passed', NULL, 'Del. Smith', '2026-01-10')""",
    )
    context = dc.build_database_context("What is HB84?")
    assert "polls.va_bills HB84" in context
    assert "bill_number=HB84" in context
    assert "title=Water quality standards" in context


def test_bill_with_governor_action_includes_both_blocks(db):
    db.polls(
        _VA_BILLS_DDL,
        """INSERT INTO va_bills VALUES ('HB84', '2026', 'Education reform', 'signed', NULL, NULL, NULL)""",
        _GOV_ACTIONS_DDL,
        """INSERT INTO governor_actions VALUES
           ('HB84', '2026', 'Education reform', 'Signed', 'signed',
            '2026-04-01', 'Abigail Spanberger', 'Del. Smith', NULL)""",
    )
    context = dc.build_database_context("Did HB84 pass?")
    assert "polls.va_bills HB84" in context
    assert "polls.governor_actions HB84" in context
    assert "governor=Abigail Spanberger" in context


def test_bill_not_in_any_db_emits_anti_hallucination_guard(db):
    """Missing bill must emit a zero_records guard, not silently return nothing.
    The guard prevents the LLM from fabricating a vote or outcome."""
    context = dc.build_database_context("What is HB9999?")
    assert "lookup_status=zero_records" in context
    assert "Do NOT fabricate" in context
    # No actual data rows — bill title and introduced_by must not appear
    assert "title=" not in context
    assert "introduced_by=" not in context


# ── openstates.bills ───────────────────────────────────────────────────────────

def test_bill_found_in_openstates(db):
    db.openstates(
        _OS_BILLS_DDL,
        """INSERT INTO bills VALUES
           ('SB249', '2026', 'Transportation safety', 'Sen. Jones',
            'Signed by governor', '2026-04-05', 'pass',
            'https://openstates.org/SB249')""",
    )
    context = dc.build_database_context("What happened with SB249?")
    assert "openstates.bills SB249" in context
    assert "bill_id=SB249" in context
    assert "result=pass" in context


def test_bill_found_in_both_dbs_returns_both_blocks(db):
    db.polls(
        _VA_BILLS_DDL,
        """INSERT INTO va_bills VALUES
           ('HB200', '2026', 'Housing bill', 'enrolled', NULL, NULL, NULL)""",
    )
    db.openstates(
        _OS_BILLS_DDL,
        """INSERT INTO bills VALUES
           ('HB200', '2026', 'Housing bill', 'Del. Jones',
            'Signed', '2026-04-01', 'pass', NULL)""",
    )
    context = dc.build_database_context("What is HB200?")
    assert "polls.va_bills HB200" in context
    assert "openstates.bills HB200" in context


# ── vote aggregates (not raw rows) ────────────────────────────────────────────

def test_full_rollcall_query_returns_aggregated_totals_not_raw_rows(db):
    """Roll-call queries must pre-aggregate Y/N counts; raw voter rows are too
    large for the context window and cause LLM miscounting."""
    db.openstates(
        _OS_BILLS_DDL,
        """INSERT INTO bills VALUES
           ('HB500', '2026', 'Budget bill', NULL, NULL, NULL, 'pass', NULL)""",
        _OS_VOTES_DDL,
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option)
           VALUES ('HB500', '2026', '2026-02-20', 'House', 'Passage', 'pass', 'Alice A', 'yes')""",
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option)
           VALUES ('HB500', '2026', '2026-02-20', 'House', 'Passage', 'pass', 'Bob B', 'yes')""",
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option)
           VALUES ('HB500', '2026', '2026-02-20', 'House', 'Passage', 'pass', 'Carol C', 'yes')""",
        """INSERT INTO votes (bill_id, session, vote_date, chamber, motion, result, voter_name, option)
           VALUES ('HB500', '2026', '2026-02-20', 'House', 'Passage', 'pass', 'Dave D', 'no')""",
    )
    context = dc.build_database_context("How did the House vote on HB500?")
    assert "3-Y" in context
    assert "1-N" in context
    # Raw voter rows must NOT appear
    assert "voter_name=Alice A" not in context
    assert "voter_name=Dave D" not in context


def test_multiple_bills_in_query_all_retrieved(db):
    db.openstates(
        _OS_BILLS_DDL,
        """INSERT INTO bills VALUES
           ('HB1', '2026', 'First bill', NULL, NULL, NULL, 'pass', NULL)""",
        """INSERT INTO bills VALUES
           ('SB2', '2026', 'Second bill', NULL, NULL, NULL, 'fail', NULL)""",
    )
    context = dc.build_database_context("Compare HB1 and SB2")
    assert "openstates.bills HB1" in context
    assert "openstates.bills SB2" in context
