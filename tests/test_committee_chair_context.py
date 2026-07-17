"""Tests for _add_committee_chair_context.

Fires when query contains a chair question ("who chairs", "committee chair")
OR a known committee name combined with money/leadership context.

Emits two separated blocks:
  Layer 1: "[LAYER 1 FACT — Committee Chair Assignments, Virginia General Assembly]"
  Layer 2: "[LAYER 2 CONTEXT — campaign finance of the committee chair(s) above; ...]"
"""
from __future__ import annotations

import json

import pytest

from voteiq.services import database_context as dc

_ASSIGN_DDL = (
    "CREATE TABLE va_committee_assignments "
    "(committee TEXT, chamber TEXT, member_name TEXT, role TEXT)"
)
_CFS_DDL = (
    "CREATE TABLE campaign_finance_summary "
    "(name TEXT, party TEXT, total_raised REAL, by_sector_json TEXT, source TEXT)"
)


def _seed(db, *, with_finance: bool = True) -> None:
    stmts = [
        _ASSIGN_DDL,
        "INSERT INTO va_committee_assignments VALUES "
        "('Finance and Appropriations','Senate','Patricia Gould','chair')",
        "INSERT INTO va_committee_assignments VALUES "
        "('Courts of Justice','House','Derek Carr','chair')",
        "INSERT INTO va_committee_assignments VALUES "
        "('Finance and Appropriations','Senate','Reggie Brown','member')",
    ]
    if with_finance:
        sectors = json.dumps([
            {"sector": "Real Estate", "total": 85000},
            {"sector": "Finance", "total": 62000},
        ])
        stmts += [
            _CFS_DDL,
            f"INSERT INTO campaign_finance_summary VALUES "
            f"('Patricia Gould','Democrat',250000.0,'{sectors}','va_sbe')",
            f"INSERT INTO campaign_finance_summary VALUES "
            f"('Derek Carr','Republican',180000.0,'[]','va_sbe')",
        ]
    else:
        # Table must exist so the SELECT doesn't throw OperationalError
        stmts.append(_CFS_DDL)
    db.polls(*stmts)


def test_chair_question_returns_layer1_block(db):
    _seed(db)
    ctx = dc.build_database_context("who chairs the Finance and Appropriations committee?")
    assert "[LAYER 1 FACT — Committee Chair Assignments, Virginia General Assembly]" in ctx
    assert "Finance and Appropriations" in ctx
    assert "Patricia Gould" in ctx


def test_chair_question_returns_layer2_finance_block(db):
    _seed(db)
    ctx = dc.build_database_context("who chairs the Finance and Appropriations committee?")
    assert "[LAYER 2 CONTEXT — campaign finance of the committee chair(s) above;" in ctx
    assert "Patricia Gould" in ctx
    assert "Real Estate" in ctx


def test_only_chairs_shown_not_members(db):
    _seed(db)
    ctx = dc.build_database_context("who chairs committees in the Virginia Senate?")
    # Reggie Brown is only a member, not a chair
    assert "Reggie Brown" not in ctx


def test_missing_table_silent(db):
    ctx = dc.build_database_context("who chairs the Finance committee?")
    assert "[LAYER 1 FACT" not in ctx


def test_committee_name_plus_money_context_fires(db):
    _seed(db)
    ctx = dc.build_database_context("who funds the finance committee chair?")
    assert "[LAYER 1 FACT — Committee Chair Assignments, Virginia General Assembly]" in ctx


def test_explicit_chair_trigger_words(db):
    _seed(db)
    ctx = dc.build_database_context("who chairs the Courts of Justice committee?")
    assert "Derek Carr" in ctx


def test_finance_without_cfs_still_emits_layer1(db):
    _seed(db, with_finance=False)
    ctx = dc.build_database_context("who chairs the Finance committee?")
    assert "[LAYER 1 FACT — Committee Chair Assignments, Virginia General Assembly]" in ctx
    # Layer 2 still emitted but with "no finance data"
    assert "[LAYER 2 CONTEXT" in ctx


def test_no_chair_question_and_no_money_context_no_block(db):
    _seed(db)
    # Mentions committee name but no chair/money context words
    ctx = dc.build_database_context("what bills did Courts of Justice consider?")
    assert "[LAYER 1 FACT" not in ctx
