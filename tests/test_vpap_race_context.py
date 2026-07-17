"""Tests for _add_vpap_race_context.

Fires on election/race keywords ("election", "running for", "candidates",
"race", "primary", …) and queries vpap_races joined to vpap_candidates.

Block marker: "[Database Context - vpap_races / vpap_candidates]"
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_RACES_DDL = (
    "CREATE TABLE vpap_races "
    "(race_key TEXT, office TEXT, district TEXT, year INTEGER, election_date TEXT)"
)
_CANDS_DDL = (
    "CREATE TABLE vpap_candidates "
    "(race_key TEXT, name TEXT, party TEXT, incumbent INTEGER, "
    "money_raised REAL, cash_on_hand REAL)"
)


def _seed(db) -> None:
    db.polls(
        _RACES_DDL,
        "INSERT INTO vpap_races VALUES ('GOV2025','Governor',NULL,2025,'2025-11-04')",
        "INSERT INTO vpap_races VALUES ('HD042-2025','House of Delegates',42,2025,'2025-11-04')",
        _CANDS_DDL,
        "INSERT INTO vpap_candidates VALUES ('GOV2025','Alice Warren','Democrat',0,4200000.0,1500000.0)",
        "INSERT INTO vpap_candidates VALUES ('GOV2025','Bob Hollins','Republican',1,3100000.0,900000.0)",
        "INSERT INTO vpap_candidates VALUES ('HD042-2025','Carol Tran','Democrat',1,125000.0,45000.0)",
    )


def test_race_block_on_election_keyword(db):
    _seed(db)
    ctx = dc.build_database_context("Who is running in the 2025 election?")
    assert "[Database Context - vpap_races / vpap_candidates]" in ctx


def test_governor_race_shows_candidates(db):
    _seed(db)
    ctx = dc.build_database_context("Who is running for governor in 2025?")
    assert "Alice Warren" in ctx
    assert "Bob Hollins" in ctx


def test_year_filter_narrows_results(db):
    db.polls(
        _RACES_DDL,
        "INSERT INTO vpap_races VALUES ('GOV2025','Governor',NULL,2025,'2025-11-04')",
        "INSERT INTO vpap_races VALUES ('SEN2026','Senate',NULL,2026,'2026-11-03')",
        _CANDS_DDL,
        "INSERT INTO vpap_candidates VALUES ('SEN2026','Dana Cruz','Democrat',0,200000.0,80000.0)",
    )
    ctx = dc.build_database_context("Who is running in the 2026 senate race?")
    assert "Dana Cruz" in ctx
    # 2025 governor race should not appear when year filter is 2026
    assert "GOV2025" not in ctx


def test_office_keyword_filters_to_matching_race(db):
    _seed(db)
    ctx = dc.build_database_context("Who is running for House of Delegates in 2025?")
    assert "Carol Tran" in ctx


def test_no_candidates_shows_placeholder(db):
    db.polls(
        _RACES_DDL,
        "INSERT INTO vpap_races VALUES ('SEN2025','Senate',NULL,2025,'2025-11-04')",
        _CANDS_DDL,
        # No candidates seeded for SEN2025
    )
    ctx = dc.build_database_context("Who is running in the 2025 senate race?")
    assert "[Database Context - vpap_races / vpap_candidates]" in ctx
    assert "no candidates recorded yet" in ctx


def test_no_trigger_keyword_no_block(db):
    _seed(db)
    ctx = dc.build_database_context("How did the governor sign HB100?")
    assert "[Database Context - vpap_races / vpap_candidates]" not in ctx


def test_missing_table_silent(db):
    ctx = dc.build_database_context("Who is running for governor?")
    assert "[Database Context - vpap_races / vpap_candidates]" not in ctx


def test_fundraising_amounts_in_output(db):
    _seed(db)
    ctx = dc.build_database_context("candidates running for governor in 2025")
    assert "4,200,000" in ctx or "4200000" in ctx  # Alice Warren raised $4.2M
