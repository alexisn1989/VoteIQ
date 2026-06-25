"""Tests for _add_federal_bill_context.

Fires on explicit federal bill references (H.R. 100, S. 5) or the
_IS_FED_BILL_Q pattern ("federal bill", "congressional bill", etc.).
Falls back to keyword title search when no bill number is present.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_BILLS_DDL = """
    CREATE TABLE congress_bills (
        bill_type TEXT, bill_number TEXT, title TEXT, introduced_date TEXT,
        latest_action TEXT, latest_action_date TEXT, policy_area TEXT,
        role TEXT, sponsor_id TEXT
    )
"""

_MEMBERS_DDL = """
    CREATE TABLE congress_members (
        bioguide_id TEXT, name TEXT, party TEXT, chamber TEXT, state TEXT, district TEXT
    )
"""

_TEXTS_DDL = """
    CREATE TABLE congress_bill_texts (
        bill_type TEXT, bill_number TEXT, text TEXT, version_date TEXT
    )
"""


def test_exact_bill_ref_returns_bill_block(db):
    """H.R. 100 in query → exact match on congress_bills."""
    db.polls(
        _BILLS_DDL,
        "INSERT INTO congress_bills VALUES "
        "('hr','100','Coastal Infrastructure Act','2025-01-15','Referred to committee','2025-01-20','Transportation',NULL,NULL)",
        _MEMBERS_DDL,
    )
    context = dc.build_database_context("What is H.R. 100?")
    assert "congress_bills" in context
    assert "Coastal Infrastructure Act" in context


def test_bill_with_sponsor_includes_sponsor_name(db):
    db.polls(
        _BILLS_DDL,
        "INSERT INTO congress_bills VALUES "
        "('hr','200','Defense Spending Act','2025-02-01','Passed House','2025-02-10','Defense','sponsor','K000399')",
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES ('K000399','Jennifer A. Kiggans','Republican','House','VA','2')",
    )
    context = dc.build_database_context("Tell me about H.R. 200")
    assert "Kiggans" in context
    assert "Defense Spending Act" in context


def test_bill_not_in_db_emits_not_found_notice(db):
    """Bill reference present but no matching row → 'not found' notice prevents hallucination."""
    db.polls(
        _BILLS_DDL,
        _MEMBERS_DDL,
    )
    context = dc.build_database_context("What is H.R. 9999?")
    assert "not found in local dataset" in context


def test_keyword_search_path_returns_matching_bills(db):
    """'federal bill' phrase + no bill number → keyword title search."""
    db.polls(
        _BILLS_DDL,
        "INSERT INTO congress_bills VALUES "
        "('hr','300','Climate Resilience Act','2025-03-01',NULL,NULL,'Environment',NULL,NULL)",
        _MEMBERS_DDL,
    )
    context = dc.build_database_context("What federal bills address climate resilience?")
    assert "congress_bills" in context
    assert "Climate Resilience Act" in context


def test_text_preview_included_when_bill_texts_seeded(db):
    db.polls(
        _BILLS_DDL,
        "INSERT INTO congress_bills VALUES "
        "('s','5','Clean Oceans Act','2025-01-10','Referred to committee','2025-01-15','Environment',NULL,NULL)",
        _MEMBERS_DDL,
        _TEXTS_DDL,
        "INSERT INTO congress_bill_texts VALUES "
        "('s','5','This act establishes standards for ocean pollution control.','2025-01-10')",
    )
    context = dc.build_database_context("What does S. 5 do?")
    assert "Clean Oceans Act" in context
    assert "Text preview" in context
    assert "ocean pollution" in context


def test_state_bill_query_does_not_trigger_federal_bill_builder(db):
    """HB84 (state bill) must not fire the federal bill context builder."""
    db.polls(
        _BILLS_DDL,
        "INSERT INTO congress_bills VALUES "
        "('hr','84','Some Federal Bill','2025-01-01',NULL,NULL,NULL,NULL,NULL)",
        _MEMBERS_DDL,
    )
    context = dc.build_database_context("How did Aaron Rouse vote on HB84?")
    assert "congress_bills — federal bill lookup" not in context
