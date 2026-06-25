"""Tests for _add_federal_vote_context.

Fires for known VA federal members (KNOWN_FEDERAL_PEOPLE) when the query
contains a vote keyword.  Former members only get federal context when the
query explicitly asks for their congressional record.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_CONGRESS_VOTES_DDL = """
    CREATE TABLE congress_votes (
        bioguide_id TEXT, congress INTEGER, vote_date TEXT, bill TEXT,
        question TEXT, member_vote TEXT, result TEXT, vote_number INTEGER, chamber TEXT
    )
"""

_FEDERAL_VOTES_DDL = """
    CREATE TABLE federal_votes (
        bioguide_id TEXT, congress INTEGER, vote_date TEXT, vote TEXT
    )
"""


def test_active_federal_member_vote_returns_summary(db):
    """Kiggans + vote keyword → congress_votes summary block appended."""
    db.polls(
        _CONGRESS_VOTES_DDL,
        "INSERT INTO congress_votes VALUES "
        "('K000399',119,'2025-03-01','HR100','Passage','Yea','Passed',42,'House')",
        _FEDERAL_VOTES_DDL,
    )
    context = dc.build_database_context("How did Kiggans vote in 2025?")
    assert "polls federal vote lookup" in context
    assert "congress_votes summary" in context


def test_missing_congress_votes_table_emits_zero_records(db):
    """congress_votes absent → zero_records prevents hallucination.
    polls.db must exist so _connect returns a real connection."""
    db.polls("CREATE TABLE _stub (x INTEGER)")
    context = dc.build_database_context("How did Kiggans vote?")
    assert "polls federal vote lookup" in context
    assert "zero_records" in context


def test_former_member_explicit_congressional_query_fires_builder(db):
    """Spanberger + 'congressional' → explicit_federal=True → federal context returned."""
    db.polls(
        _CONGRESS_VOTES_DDL,
        "INSERT INTO congress_votes VALUES "
        "('S001209',117,'2022-06-01','HR500','Passage','Yea','Passed',120,'House')",
        _FEDERAL_VOTES_DDL,
    )
    context = dc.build_database_context("What was Spanberger congressional voting record?")
    assert "polls federal vote lookup" in context
    assert "S001209" in context


def test_non_federal_person_does_not_get_federal_vote_block(db):
    """State legislators must not trigger the federal vote builder."""
    context = dc.build_database_context("Aaron Rouse voting record 2026")
    assert "polls federal vote lookup" not in context
