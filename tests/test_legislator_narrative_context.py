"""Tests for _add_legislator_narrative_context.

The builder score-matches legislator names against the query without a
keyword trigger — any significant token (≥3 chars, no dot suffix, not an
honorific) shared between the name and the query fires the block.
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_DDL = (
    "CREATE TABLE legislator_narratives "
    "(legislator_id INTEGER, name TEXT, narrative TEXT, narrative_short TEXT)"
)


def test_name_match_returns_narrative(db):
    db.polls(
        _DDL,
        "INSERT INTO legislator_narratives VALUES "
        "(1, 'Marcus Jones', 'Marcus Jones is a delegate from Richmond.', 'Del. Marcus Jones.')",
    )
    ctx = dc.build_database_context("What is Marcus Jones known for?")
    assert "[RETRIEVED RECORD - Legislator Civic Profile: Marcus Jones]" in ctx
    assert "Marcus Jones is a delegate from Richmond." in ctx


def test_no_name_in_query_returns_nothing(db):
    db.polls(
        _DDL,
        "INSERT INTO legislator_narratives VALUES "
        "(1, 'Marcus Jones', 'Marcus Jones narrative.', 'Short.')",
    )
    ctx = dc.build_database_context("How did HB100 pass the committee?")
    assert "[RETRIEVED RECORD - Legislator Civic Profile:" not in ctx


def test_falls_back_to_narrative_short_when_narrative_null(db):
    db.polls(
        _DDL,
        "INSERT INTO legislator_narratives VALUES "
        "(1, 'Sara Lopez', NULL, 'Sen. Lopez represents Fairfax.')",
    )
    ctx = dc.build_database_context("What does Sara Lopez do?")
    assert "[RETRIEVED RECORD - Legislator Civic Profile: Sara Lopez]" in ctx
    assert "Sen. Lopez represents Fairfax." in ctx


def test_both_narrative_columns_null_returns_nothing(db):
    db.polls(
        _DDL,
        "INSERT INTO legislator_narratives VALUES (1, 'Sara Lopez', NULL, NULL)",
    )
    ctx = dc.build_database_context("What does Sara Lopez do?")
    assert "[RETRIEVED RECORD - Legislator Civic Profile:" not in ctx


def test_best_scoring_match_wins_over_partial_match(db):
    db.polls(
        _DDL,
        "INSERT INTO legislator_narratives VALUES "
        "(1, 'John Scott', 'John Scott delegate.', 'Delegate John Scott.')",
        "INSERT INTO legislator_narratives VALUES "
        "(2, 'Donald Scott', 'Donald Scott senator.', 'Senator Donald Scott.')",
    )
    # "donald" and "scott" both appear → score=2 for Donald Scott; score=1 for John Scott
    ctx = dc.build_database_context("How did Donald Scott vote on the budget?")
    assert "Donald Scott senator." in ctx
    assert "John Scott delegate." not in ctx


def test_missing_table_returns_nothing(db):
    # Table not created — builder exits silently
    ctx = dc.build_database_context("What is Marcus Jones known for?")
    assert "[RETRIEVED RECORD - Legislator Civic Profile:" not in ctx


def test_short_name_tokens_under_three_chars_ignored(db):
    db.polls(
        _DDL,
        # Name "Ed Wu" — tokens "ed" (2 chars, ignored) and "wu" (2 chars, ignored)
        "INSERT INTO legislator_narratives VALUES (1, 'Ed Wu', 'Ed Wu narrative.', 'Short.')",
    )
    # Neither token is ≥3 chars so score is always 0 → no match
    ctx = dc.build_database_context("What did Ed Wu vote on?")
    assert "[RETRIEVED RECORD - Legislator Civic Profile:" not in ctx
