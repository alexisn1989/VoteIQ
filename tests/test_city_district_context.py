"""Tests for _add_city_district_context.

Fires when query matches _REPRESENTS_ME_RE ("who represents me", "my delegate",
"who represents [city], VA", etc.) AND a known city name appears in the query.

Known cities: portsmouth, virginia beach, norfolk, chesapeake, hampton.

Portsmouth has state_reps defined; the others only have a federal_district.
The LEFT JOIN on legislator_narratives must exist even when empty.

Block marker: "[Database Context - representatives for {city}]"
Gate: len(lines) > 2 (lines starts with [note, ""], needs ≥1 added line)
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_LEG_DDL = (
    "CREATE TABLE legislators "
    "(name TEXT, party TEXT, chamber TEXT, district TEXT, active INTEGER)"
)
_NARR_DDL = (
    "CREATE TABLE legislator_narratives "
    "(name TEXT, narrative_short TEXT)"
)
_CM_DDL = (
    "CREATE TABLE congress_members "
    "(name TEXT, party TEXT, district TEXT, state TEXT, chamber TEXT)"
)


def test_norfolk_federal_rep_returned(db):
    # Norfolk has no state_reps, only federal_district='2'
    db.polls(
        _CM_DDL,
        "INSERT INTO congress_members VALUES "
        "('Jennifer Smith','Democrat','2','Virginia','House of Representatives')",
    )
    ctx = dc.build_database_context("who represents me in norfolk?")
    assert "[Database Context - representatives for Norfolk, VA]" in ctx
    assert "Jennifer Smith" in ctx


def test_portsmouth_state_delegate_returned(db):
    # Portsmouth has state_reps [("Delegate","88"), ("Senator","18")]
    # LEFT JOIN on legislator_narratives must exist
    db.polls(
        _LEG_DDL,
        "INSERT INTO legislators VALUES ('Robert Hughes','R','Delegate','88',1)",
        _NARR_DDL,   # empty — LEFT JOIN must not fail
        _CM_DDL,
    )
    ctx = dc.build_database_context("who is my representative in portsmouth?")
    assert "[Database Context - representatives for Portsmouth, VA]" in ctx
    assert "Robert Hughes" in ctx


def test_portsmouth_narrative_short_included(db):
    db.polls(
        _LEG_DDL,
        "INSERT INTO legislators VALUES ('Robert Hughes','R','Delegate','88',1)",
        _NARR_DDL,
        "INSERT INTO legislator_narratives VALUES ('Robert Hughes','Delegate Hughes serves District 88.')",
        _CM_DDL,
    )
    ctx = dc.build_database_context("who represents me in portsmouth virginia?")
    assert "Delegate Hughes serves District 88." in ctx


def test_virginia_beach_federal_rep_returned(db):
    db.polls(
        _CM_DDL,
        "INSERT INTO congress_members VALUES "
        "('Maria Lopez','Republican','2','Virginia','House of Representatives')",
    )
    ctx = dc.build_database_context("who is my congressman in virginia beach?")
    assert "[Database Context - representatives for Virginia Beach, VA]" in ctx
    assert "Maria Lopez" in ctx


def test_no_represents_me_trigger_no_block(db):
    db.polls(
        _CM_DDL,
        "INSERT INTO congress_members VALUES "
        "('Jennifer Smith','Democrat','2','Virginia','House of Representatives')",
    )
    # No "represents me" phrasing
    ctx = dc.build_database_context("what did the norfolk council vote on?")
    assert "[Database Context - representatives for" not in ctx


def test_unknown_city_no_block(db):
    db.polls(
        _CM_DDL,
        "INSERT INTO congress_members VALUES "
        "('Jennifer Smith','Democrat','2','Virginia','House of Representatives')",
    )
    # City "richmond" is not in _CITY_REPRESENTATIVES
    ctx = dc.build_database_context("who represents me in richmond?")
    assert "[Database Context - representatives for" not in ctx


def test_no_matching_row_no_block(db):
    # Table exists but no matching legislator → lines stays at [note, ""] = 2 items → not > 2
    db.polls(
        _CM_DDL,
        # No district 2 row in Virginia
        "INSERT INTO congress_members VALUES "
        "('Someone Else','D','5','Virginia','House of Representatives')",
    )
    ctx = dc.build_database_context("who represents me in norfolk?")
    assert "[Database Context - representatives for Norfolk, VA]" not in ctx


def test_my_delegate_phrasing(db):
    db.polls(
        _LEG_DDL,
        "INSERT INTO legislators VALUES ('Carol Tran','D','Delegate','88',1)",
        _NARR_DDL,
        _CM_DDL,
    )
    ctx = dc.build_database_context("who is my delegate in portsmouth?")
    assert "[Database Context - representatives for Portsmouth, VA]" in ctx
