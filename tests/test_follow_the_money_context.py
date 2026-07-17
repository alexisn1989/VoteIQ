"""Tests for _add_follow_the_money_context.

Fires when a bill number (_FTM_BILL) is present AND either a money keyword
(_FTM_MONEY) or "follow" appears in the query.

Primary table: legiscan_va_bills (polls DB)
  columns: bill_id, session, title, status_label, primary_sponsor, bill_number

Optional sponsor finance: campaign_finance_summary (polls DB)
  columns: name, party, total_raised, top_sector, top_donors_json, by_sector_json, source
  Only queried when sponsor_names is non-empty. Must exist or builder fails silently.

Lobbyist count: lobbyist_registrations (polls DB)
  Only queried when top_donors list has entries. Seed top_donors_json='[]' to skip.

Legislative intelligence (va_bill_sponsors + va_bills) used only when the
li connection is available.  The db fixture does NOT call db.legislative(),
so the li path returns None and primary_sponsor fallback is used instead.

Block markers:
  "[RETRIEVED RECORD - Follow the Money: {bill_number} ({session})]"
  "[Follow-the-Money: {bill_number} — Session Required]"  ← disambiguation
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_BILLS_DDL = (
    "CREATE TABLE legiscan_va_bills "
    "(bill_id INTEGER, session TEXT, title TEXT, status_label TEXT, "
    "primary_sponsor TEXT, bill_number TEXT)"
)
_CFS_DDL = (
    "CREATE TABLE campaign_finance_summary "
    "(name TEXT, party TEXT, total_raised REAL, top_sector TEXT, "
    "top_donors_json TEXT, by_sector_json TEXT, source TEXT)"
)
_LOB_DDL = (
    "CREATE TABLE lobbyist_registrations "
    "(principal_name TEXT, lobbyist_name TEXT, status TEXT, year_range TEXT)"
)


def _seed_bill(db, *, session="2026", sponsor="", bill_number="HB100"):
    """Seed a single bill with empty sponsor so CFS loop is skipped."""
    db.polls(
        _BILLS_DDL,
        f"INSERT INTO legiscan_va_bills VALUES "
        f"(1,'{session}','Clean Energy Act','Enrolled','{sponsor}','{bill_number}')",
    )


def test_basic_block_with_money_keyword(db):
    _seed_bill(db)
    # "who funded" is an explicit phrase in _FTM_MONEY; "funds" is not (\bfund\b)
    ctx = dc.build_database_context("who funded HB100 2026?")
    assert "[RETRIEVED RECORD - Follow the Money: HB100 (2026)]" in ctx


def test_follow_keyword_without_money_term(db):
    _seed_bill(db)
    ctx = dc.build_database_context("follow the money on HB100 2026")
    assert "[RETRIEVED RECORD - Follow the Money: HB100 (2026)]" in ctx


def test_bill_title_in_block(db):
    _seed_bill(db)
    # "donor" is a standalone word → matches \bdonor\b
    ctx = dc.build_database_context("who is the donor behind HB100 2026?")
    assert "Clean Energy Act" in ctx


def test_session_shown_in_block(db):
    _seed_bill(db, session="2025")
    ctx = dc.build_database_context("who funded HB100 2025?")
    assert "HB100 (2025)" in ctx


def test_disambiguation_block_when_multi_session(db):
    # Same bill number in two sessions → disambiguation prompt, no data
    db.polls(
        _BILLS_DDL,
        "INSERT INTO legiscan_va_bills VALUES (1,'2025','Old Bill','Passed','','HB100')",
        "INSERT INTO legiscan_va_bills VALUES (2,'2026','New Bill','Enrolled','','HB100')",
    )
    # No year hint → multiple sessions found → disambiguation block
    ctx = dc.build_database_context("who funded HB100?")
    assert "[Follow-the-Money: HB100 — Session Required]" in ctx
    assert "2025" in ctx
    assert "2026" in ctx


def test_sponsor_finance_data_shown(db):
    db.polls(
        _BILLS_DDL,
        "INSERT INTO legiscan_va_bills VALUES "
        "(1,'2026','Clean Energy Act','Enrolled','Del. Jones','HB100')",
        _CFS_DDL,
        "INSERT INTO campaign_finance_summary VALUES "
        "('Del. Jones','D',125000.0,'Energy','[]','[]','va_sbe')",
        _LOB_DDL,
    )
    ctx = dc.build_database_context("who funded HB100 2026?")
    assert "Del. Jones" in ctx
    assert "125,000" in ctx or "125000" in ctx


def test_sponsor_unknown_when_no_primary_sponsor(db):
    _seed_bill(db, sponsor="")
    ctx = dc.build_database_context("who funded HB100 2026?")
    assert "Sponsor(s): unknown" in ctx


def test_no_bill_number_no_block(db):
    _seed_bill(db)
    ctx = dc.build_database_context("who donates to energy bills?")
    assert "[RETRIEVED RECORD - Follow the Money" not in ctx
    assert "[Follow-the-Money" not in ctx


def test_no_money_keyword_no_block(db):
    _seed_bill(db)
    ctx = dc.build_database_context("what does HB100 2026 say?")
    assert "[RETRIEVED RECORD - Follow the Money" not in ctx


def test_bill_not_found_no_block(db):
    _seed_bill(db, bill_number="HB999")
    ctx = dc.build_database_context("who funds HB100 2026?")
    assert "[RETRIEVED RECORD - Follow the Money" not in ctx


def test_senate_bill_number(db):
    _seed_bill(db, bill_number="SB50", session="2026")
    ctx = dc.build_database_context("who funded SB50 2026?")
    assert "[RETRIEVED RECORD - Follow the Money: SB50 (2026)]" in ctx
