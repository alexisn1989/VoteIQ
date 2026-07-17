"""Tests for _add_donor_vote_alignment_context.

Fires on _ALIGNMENT_TRIGGER keywords:
  "alignment", "aligned", "votes with donors", "donor-align",
  "sector yes-rate", "voting with the money", "pay-to-vote", "bought vote"
  — OR on a named legislator + money/vote intent.

Primary table: donor_vote_alignment (polls DB)
  Base columns: name, party, chamber, top_donor_sector, top_sector_amt,
                sector_yes_rate, other_yes_rate, alignment_delta,
                sector_vote_count, other_vote_count
  Rich columns (optional, detected via PRAGMA):
                top_sector_share, nonindustry_dominant, nonindustry_amt

When triggered by name: WHERE name LIKE any(_person_terms(terms)) LIMIT 4
When triggered by keyword only: ORDER BY ABS(alignment_delta) DESC LIMIT 8

Block marker prefix: "[Database Context - donor_vote_alignment"
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_DVA_DDL = (
    "CREATE TABLE donor_vote_alignment "
    "(name TEXT, party TEXT, chamber TEXT, top_donor_sector TEXT, "
    "top_sector_amt REAL, sector_yes_rate REAL, other_yes_rate REAL, "
    "alignment_delta REAL, sector_vote_count INTEGER, other_vote_count INTEGER)"
)


def _seed(db) -> None:
    db.polls(
        _DVA_DDL,
        "INSERT INTO donor_vote_alignment VALUES "
        "('Jane Smith','D','Delegate','Real Estate',85000.0,75.0,60.0,15.0,12,45)",
        "INSERT INTO donor_vote_alignment VALUES "
        "('Tom Baker','R','Senator','Energy',200000.0,90.0,55.0,35.0,30,80)",
    )


def test_alignment_keyword_returns_block(db):
    _seed(db)
    ctx = dc.build_database_context("show donor alignment scores for Virginia delegates")
    assert "[Database Context - donor_vote_alignment" in ctx


def test_votes_with_money_trigger(db):
    _seed(db)
    ctx = dc.build_database_context("who votes with their donor money?")
    assert "[Database Context - donor_vote_alignment" in ctx


def test_pay_to_vote_trigger(db):
    _seed(db)
    ctx = dc.build_database_context("is there pay-to-vote in Virginia?")
    assert "[Database Context - donor_vote_alignment" in ctx


def test_all_rows_in_top8_global_query(db):
    _seed(db)
    ctx = dc.build_database_context("show me donor alignment scores")
    assert "Jane Smith" in ctx
    assert "Tom Baker" in ctx


def test_name_filter_returns_matching_row(db):
    _seed(db)
    ctx = dc.build_database_context("what is Jane Smith's donor alignment and voting record?")
    assert "Jane Smith" in ctx


def test_alignment_delta_in_output(db):
    _seed(db)
    ctx = dc.build_database_context("show donor alignment scores")
    # Tom Baker has higher delta (35.0), should appear; values formatted as "+35.0" or "+15.0"
    assert "+35.0" in ctx or "+15.0" in ctx


def test_sector_name_in_output(db):
    _seed(db)
    ctx = dc.build_database_context("donor alignment for all delegates")
    assert "Real Estate" in ctx or "Energy" in ctx


def test_missing_table_no_block(db):
    # No table created — _table_exists check exits early
    ctx = dc.build_database_context("show donor alignment scores")
    assert "[Database Context - donor_vote_alignment" not in ctx


def test_bare_name_without_vote_intent_no_block(db):
    _seed(db)
    # No money/vote intent and no alignment trigger — should not fire
    ctx = dc.build_database_context("tell me about Jane Smith")
    assert "[Database Context - donor_vote_alignment" not in ctx


def test_rich_columns_included_when_present(db):
    db.polls(
        "CREATE TABLE donor_vote_alignment "
        "(name TEXT, party TEXT, chamber TEXT, top_donor_sector TEXT, "
        "top_sector_amt REAL, sector_yes_rate REAL, other_yes_rate REAL, "
        "alignment_delta REAL, sector_vote_count INTEGER, other_vote_count INTEGER, "
        "top_sector_share REAL, nonindustry_dominant TEXT, nonindustry_amt REAL)",
        "INSERT INTO donor_vote_alignment VALUES "
        "('Carol Tran','D','Delegate','Finance',50000.0,80.0,65.0,15.0,20,60,"
        "0.25,'Small Donors',30000.0)",
    )
    ctx = dc.build_database_context("show donor alignment scores")
    assert "Carol Tran" in ctx
    assert "[Database Context - donor_vote_alignment" in ctx
