"""Tests for _add_testimony_proxy_context.

Requires BOTH a bill number (HB/SB/etc + digits) AND a testimony/lobbying
keyword.  Pulls from committee_testimony_proxy (with optional bill title
from legiscan_va_bills).

Block marker: "[Committee Testimony Proxy — {bill_number} ({session})]"
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_PROXY_DDL = (
    "CREATE TABLE committee_testimony_proxy "
    "(bill_number TEXT, session TEXT, principal_name TEXT, principal_sector TEXT, "
    "lobbyist_count INTEGER, likely_position TEXT, confidence TEXT, "
    "total_donated_to_sponsors REAL, sponsor_names TEXT)"
)
_BILLS_DDL = (
    "CREATE TABLE legiscan_va_bills "
    "(bill_number TEXT, session TEXT, title TEXT)"
)


def _seed_proxy(db, *, with_bills: bool = True) -> None:
    stmts = [
        _PROXY_DDL,
        "INSERT INTO committee_testimony_proxy VALUES "
        "('HB500','2026','Acme Corp','Real Estate',3,'support','high',75000.0,'Del. Smith')",
        "INSERT INTO committee_testimony_proxy VALUES "
        "('HB500','2026','Tech Alliance','Technology',2,'oppose','medium',0.0,NULL)",
    ]
    if with_bills:
        stmts += [
            _BILLS_DDL,
            "INSERT INTO legiscan_va_bills VALUES ('HB500','2026','Short-Term Rental Regulation Act')",
        ]
    db.polls(*stmts)


def test_testimony_fires_on_bill_and_keyword(db):
    _seed_proxy(db)
    ctx = dc.build_database_context("Who lobbied on HB500?")
    assert "[Committee Testimony Proxy — HB500 (2026)]" in ctx
    assert "Acme Corp" in ctx
    assert "Tech Alliance" in ctx


def test_testimony_includes_bill_title_from_legiscan(db):
    _seed_proxy(db, with_bills=True)
    ctx = dc.build_database_context("Who testified on HB500?")
    assert "Short-Term Rental Regulation Act" in ctx


def test_testimony_works_without_matching_bill_title(db):
    # legiscan table exists but has no row for HB500 → title = "" → block still emitted
    _seed_proxy(db, with_bills=False)
    db.polls(
        _BILLS_DDL,
        # Seed a different bill so the table exists but HB500 has no title
        "INSERT INTO legiscan_va_bills VALUES ('HB999','2026','Some Other Bill')",
    )
    ctx = dc.build_database_context("Who lobbied on HB500?")
    assert "[Committee Testimony Proxy — HB500 (2026)]" in ctx
    assert "Acme Corp" in ctx


def test_testimony_no_bill_number_no_block(db):
    _seed_proxy(db)
    ctx = dc.build_database_context("Who testified on this bill?")
    assert "[Committee Testimony Proxy" not in ctx


def test_testimony_no_keyword_no_block(db):
    _seed_proxy(db)
    ctx = dc.build_database_context("What does HB500 do?")
    assert "[Committee Testimony Proxy" not in ctx


def test_testimony_missing_table_silent(db):
    ctx = dc.build_database_context("Who lobbied on HB500?")
    assert "[Committee Testimony Proxy" not in ctx


def test_testimony_no_rows_for_bill_no_block(db):
    db.polls(
        _PROXY_DDL,
        # Only HB999, not HB500
        "INSERT INTO committee_testimony_proxy VALUES "
        "('HB999','2026','Other Corp','Tech',1,'support','low',0.0,NULL)",
    )
    ctx = dc.build_database_context("Who lobbied on HB500?")
    assert "[Committee Testimony Proxy" not in ctx


def test_testimony_support_count_shown(db):
    _seed_proxy(db)
    ctx = dc.build_database_context("What interest groups lobbied on HB500?")
    # Block shows "With donation-backed support signal: N of top N shown"
    assert "donation-backed support signal" in ctx


def test_testimony_senate_bill_number(db):
    db.polls(
        _PROXY_DDL,
        "INSERT INTO committee_testimony_proxy VALUES "
        "('SB200','2026','Health Alliance','Healthcare',4,'support','high',50000.0,NULL)",
        _BILLS_DDL,  # table must exist for the title query to succeed
    )
    ctx = dc.build_database_context("who testified on SB200?")
    assert "[Committee Testimony Proxy — SB200 (2026)]" in ctx
