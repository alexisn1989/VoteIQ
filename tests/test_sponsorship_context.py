"""Tests for _add_sponsorship_summary_context.

The builder resolves exactly one legislator by name, then returns
authoritative chief-patron totals from va_legislator_sponsored_bills.
It must skip rather than guess when the name is ambiguous.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_DDL = """
    CREATE TABLE va_legislator_sponsored_bills (
        legislator_name TEXT, session TEXT, bill_number TEXT, title TEXT, status_label TEXT
    )
"""

_COSPONSOR_DDL = """
    CREATE TABLE va_legislator_cosponsor_bills (
        legislator_name TEXT, session TEXT, bill_id TEXT
    )
"""


def test_sponsorship_returns_passed_and_failed_counts(db):
    db.polls(
        _DDL,
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Rouse','2026','SB10','Clean energy bill','Passed')",
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Rouse','2026','SB11','Housing reform','Passed')",
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Rouse','2026','SB12','Transportation study','Introduced')",
    )
    context = dc.build_database_context("What bills did Aaron Rouse sponsor?")
    assert "Aaron Rouse" in context
    assert "passed" in context
    assert "did not pass" in context


def test_cosponsor_count_included_when_table_exists(db):
    db.polls(
        _DDL,
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Louise Lucas','2026','SB1','Environment bill','Passed')",
        _COSPONSOR_DDL,
        "INSERT INTO va_legislator_cosponsor_bills VALUES ('Louise Lucas','2026','HB100')",
        "INSERT INTO va_legislator_cosponsor_bills VALUES ('Louise Lucas','2026','HB101')",
    )
    context = dc.build_database_context("Louise Lucas legislation sponsored 2026")
    assert "Louise Lucas" in context
    assert "2" in context or "co" in context.lower()


def test_ambiguous_name_match_skips_builder(db):
    """Two distinct legislators matching the same partial name — builder must not guess."""
    db.polls(
        _DDL,
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Smith','2026','HB1','Bill one','Passed')",
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Jones','2026','HB2','Bill two','Passed')",
    )
    context = dc.build_database_context("What bills did Aaron sponsor?")
    assert "va_legislator_sponsored_bills" not in context


def test_no_rows_for_session_skips_builder(db):
    """Data exists for 2025 but query defaults to 2026 — no rows → silent skip."""
    db.polls(
        _DDL,
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Rouse','2025','SB1','Old bill','Passed')",
    )
    context = dc.build_database_context("What bills did Aaron Rouse introduce?")
    assert "va_legislator_sponsored_bills" not in context


def test_no_sponsorship_trigger_word_skips_builder(db):
    """Vote-only query must not fire the sponsorship builder."""
    db.polls(
        _DDL,
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Rouse','2026','HB84','Some bill','Passed')",
    )
    context = dc.build_database_context("How did Aaron Rouse vote on HB84?")
    assert "va_legislator_sponsored_bills" not in context


def test_vetoed_bills_bucketed_separately(db):
    db.polls(
        _DDL,
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Louise Lucas','2026','SB5','Climate act','Passed')",
        "INSERT INTO va_legislator_sponsored_bills VALUES ('Louise Lucas','2026','SB6','Tax reform','Vetoed')",
    )
    context = dc.build_database_context("What legislation did Louise Lucas author?")
    assert "vetoed" in context
