"""Tests for _add_norfolk_council_context and _add_vb_council_context.

Both builders use _connect("polls") and respect the db fixture.

Norfolk routing modes:
  - body_summary_mode: triggers when query contains "how does", "consensus",
    "voting pattern", etc. (no named member required). Returns early before
    any column that our minimal DDL doesn't have.
  - factsheet_mode: named member + "voting record" / "show me" + "record".
  - default: member vote summary table + recent vote queries (needs mv.title
    column which we avoid by using body_summary_mode queries in tests).

Block markers:
  Norfolk: "## Norfolk City Council Vote Records"
  VB:      "## Virginia Beach City Council"
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

# ── Norfolk fixtures ──────────────────────────────────────────────────────────

_VOTES_DDL = (
    "CREATE TABLE norfolk_council_votes "
    "(vote_count TEXT, meeting_date TEXT, agenda_item TEXT, result TEXT)"
)
_MV_DDL = (
    "CREATE TABLE norfolk_council_member_votes "
    "(member_name TEXT, vote TEXT, meeting_date TEXT, agenda_item TEXT)"
)


def _seed_norfolk(db) -> None:
    db.polls(
        _VOTES_DDL,
        "INSERT INTO norfolk_council_votes VALUES "
        "('5-3','2025-01-15','Hotel Rezoning','PASSED')",
        "INSERT INTO norfolk_council_votes VALUES "
        "('8-0','2025-02-01','Budget Amendment','PASSED')",
        _MV_DDL,
        "INSERT INTO norfolk_council_member_votes VALUES "
        "('Doyle','yes','2025-01-15','Hotel Rezoning')",
        "INSERT INTO norfolk_council_member_votes VALUES "
        "('Alexander','no','2025-01-15','Hotel Rezoning')",
        "INSERT INTO norfolk_council_member_votes VALUES "
        "('Doyle','yes','2025-02-01','Budget Amendment')",
        "INSERT INTO norfolk_council_member_votes VALUES "
        "('Alexander','yes','2025-02-01','Budget Amendment')",
    )


def test_norfolk_fires_on_city_council_body_summary_query(db):
    _seed_norfolk(db)
    # "how does" triggers body_summary_mode — early return, no title column needed
    ctx = dc.build_database_context("how does the norfolk city council vote?")
    assert "## Norfolk City Council Vote Records" in ctx


def test_norfolk_fires_on_voting_pattern_query(db):
    _seed_norfolk(db)
    ctx = dc.build_database_context("norfolk council voting pattern summary")
    assert "## Norfolk City Council Vote Records" in ctx


def test_norfolk_consensus_rate_shown(db):
    _seed_norfolk(db)
    ctx = dc.build_database_context("what is the norfolk council consensus rate?")
    assert "## Norfolk City Council Vote Records" in ctx
    assert "Recorded votes: 2" in ctx


def test_norfolk_no_trigger_no_block(db):
    _seed_norfolk(db)
    # No Norfolk-related keywords → builder short-circuits
    ctx = dc.build_database_context("How did the House vote on HB100?")
    assert "## Norfolk City Council Vote Records" not in ctx


def test_norfolk_missing_table_silent(db):
    # Table not created → builder exits after _table_exists check
    ctx = dc.build_database_context("how does the norfolk city council vote?")
    assert "## Norfolk City Council Vote Records" not in ctx


def test_norfolk_norfolk_vote_trigger(db):
    _seed_norfolk(db)
    ctx = dc.build_database_context("norfolk vote consensus as a body")
    assert "## Norfolk City Council Vote Records" in ctx


# ── VB fixtures ───────────────────────────────────────────────────────────────

_VB_MEMBERS_DDL = (
    "CREATE TABLE vb_council_members "
    "(name TEXT, district TEXT, district_num INTEGER, email TEXT)"
)


def _seed_vb(db) -> None:
    db.polls(
        _VB_MEMBERS_DDL,
        "INSERT INTO vb_council_members VALUES "
        "('Mike Berlucchi','Beach',1,'berlucchi@vb.gov')",
        "INSERT INTO vb_council_members VALUES "
        "('Barbara Henley','Princess Anne',2,'henley@vb.gov')",
    )


def test_vb_fires_on_berlucchi_name(db):
    _seed_vb(db)
    ctx = dc.build_database_context(
        "What did Berlucchi vote on at the Virginia Beach council?"
    )
    assert "## Virginia Beach City Council" in ctx


def test_vb_fires_on_virginia_beach_council_keyword(db):
    _seed_vb(db)
    ctx = dc.build_database_context("Virginia Beach council member roster")
    assert "## Virginia Beach City Council" in ctx


def test_vb_unrelated_query_no_block(db):
    _seed_vb(db)
    ctx = dc.build_database_context("How did Del. Jones vote on HB100?")
    assert "## Virginia Beach City Council" not in ctx
