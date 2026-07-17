"""Tests for _add_gatekeeper_context.

Fires on _GATEKEEPER_TRIGGERS: "kills bills", "gatekeeper", "who kills",
"blocked in committee", "died in committee", "committee chairs", etc.

Required tables (all polls DB):
  va_committee_assignments (committee, chamber, member_name, role)
  va_bills (bill_number, title, status, session)
  campaign_finance_summary (name, party, total_raised, top_sector, by_sector_json, source)

Status pattern for "killed": "Left in <committee> (vote)" matched by _GK_DEATH_RE.
e.g. "Left in Courts of Justice (12-0)" → committee "Courts of Justice"

The chamber prefix for bill attribution: "H..." → "House", "S..." → "Senate".
The key match: (chamber_prefix_from_bill, norm_committee) == (chair_row.chamber, norm_chair_row.committee)

Block marker: "[Committee Gatekeeper Analysis — Virginia General Assembly 2024–2026]"
No-kill path: shows top 12 chairs with 0 kills (cards[:12]).
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_ASSIGN_DDL = (
    "CREATE TABLE va_committee_assignments "
    "(committee TEXT, chamber TEXT, member_name TEXT, role TEXT)"
)
_BILLS_DDL = (
    "CREATE TABLE va_bills "
    "(bill_number TEXT, title TEXT, status TEXT, session TEXT)"
)
_CFS_DDL = (
    "CREATE TABLE campaign_finance_summary "
    "(name TEXT, party TEXT, total_raised REAL, top_sector TEXT, "
    "by_sector_json TEXT, source TEXT)"
)


def _seed(db, *, with_kill: bool = True, with_finance: bool = False) -> None:
    stmts = [
        _ASSIGN_DDL,
        "INSERT INTO va_committee_assignments VALUES "
        "('Courts of Justice','House','Derek Carr','chair')",
        "INSERT INTO va_committee_assignments VALUES "
        "('Finance and Appropriations','Senate','Patricia Gould','chair')",
        "INSERT INTO va_committee_assignments VALUES "
        "('Courts of Justice','House','Reggie Brown','member')",  # not a chair
        _BILLS_DDL,
    ]
    if with_kill:
        stmts.append(
            "INSERT INTO va_bills VALUES "
            "('HB50','Some Bill','Left in Courts of Justice (12-0)','2026')"
        )
    stmts.append(_CFS_DDL)
    if with_finance:
        # by_sector_json drives the display; top_sector column is not rendered
        stmts.append(
            "INSERT INTO campaign_finance_summary VALUES "
            "('Derek Carr','R',180000.0,'Real Estate',"
            "'[{\"sector\": \"Real Estate\", \"total\": 80000}]','va_sbe')"
        )
    db.polls(*stmts)


def test_gatekeeper_block_on_kills_bills_query(db):
    _seed(db)
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "[Committee Gatekeeper Analysis — Virginia General Assembly 2024–2026]" in ctx


def test_gatekeeper_block_on_died_in_committee_query(db):
    _seed(db)
    ctx = dc.build_database_context("which bills died in committee this session?")
    assert "[Committee Gatekeeper Analysis — Virginia General Assembly 2024–2026]" in ctx


def test_gatekeeper_block_on_explicit_keyword(db):
    _seed(db)
    ctx = dc.build_database_context("who is the gatekeeper in the House?")
    assert "[Committee Gatekeeper Analysis — Virginia General Assembly 2024–2026]" in ctx


def test_killed_chair_shown_with_kill_count(db):
    _seed(db, with_kill=True)
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "Derek Carr" in ctx
    assert "1" in ctx  # total_kills = 1


def test_non_chair_member_not_in_block(db):
    _seed(db, with_kill=True)
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "Reggie Brown" not in ctx


def test_no_chairs_no_block(db):
    db.polls(
        _ASSIGN_DDL,
        # Only members, no chairs
        "INSERT INTO va_committee_assignments VALUES "
        "('Courts of Justice','House','John Doe','member')",
        _BILLS_DDL,
        _CFS_DDL,
    )
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "[Committee Gatekeeper Analysis" not in ctx


def test_no_kills_shows_top_chairs_anyway(db):
    # No bills with kill status → all chairs shown with 0 kills (cards[:12] path)
    _seed(db, with_kill=False)
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "[Committee Gatekeeper Analysis" in ctx
    assert "Derek Carr" in ctx


def test_donor_sector_shown_when_finance_present(db):
    _seed(db, with_kill=True, with_finance=True)
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "Real Estate" in ctx


def test_no_trigger_no_block(db):
    _seed(db)
    ctx = dc.build_database_context("how did HB100 pass?")
    assert "[Committee Gatekeeper Analysis" not in ctx


def test_missing_table_silent(db):
    # No tables created — builder should fail silently
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "[Committee Gatekeeper Analysis" not in ctx


def test_senate_killed_bill_attributed_to_senate_chair(db):
    db.polls(
        _ASSIGN_DDL,
        "INSERT INTO va_committee_assignments VALUES "
        "('Finance and Appropriations','Senate','Patricia Gould','chair')",
        _BILLS_DDL,
        "INSERT INTO va_bills VALUES "
        "('SB20','Tax Bill','Left in Finance and Appropriations (8-4)','2026')",
        _CFS_DDL,
    )
    ctx = dc.build_database_context("who kills bills in committee?")
    assert "Patricia Gould" in ctx
