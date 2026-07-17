"""Tests for _add_pac_context.

Fires when query contains PAC-related keywords ("pac", "super pac",
"who funds", "special interest", etc.).  Non-generic terms from
_keywords(query) drive per-table keyword searches; generic words
(pac, political, money, committee, …) are stripped first.
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_IDEOLOGY_DDL = (
    "CREATE TABLE pac_ideology "
    "(committee_id TEXT, committee_name TEXT, short_name TEXT, ideology TEXT, "
    "alignment TEXT, network TEXT, issue_focus TEXT, description TEXT, "
    "source_url TEXT, verified TEXT, foreign_alignment TEXT, foreign_country TEXT)"
)
_LOOKUP_DDL = (
    "CREATE TABLE pac_name_lookup "
    "(pac_name TEXT, industry TEXT, source TEXT, confidence REAL, created_at TEXT)"
)
_IE_DDL = (
    "CREATE TABLE fec_independent_expenditures "
    "(candidate_name TEXT, committee_name TEXT, support_oppose TEXT, "
    "expenditure_amount REAL, expenditure_date TEXT, cycle TEXT, office TEXT, state TEXT)"
)
_FIT_DDL = (
    "CREATE TABLE fec_industry_totals "
    "(member_name TEXT, cycle TEXT, industry TEXT, "
    "total_amount REAL, contributor_count INTEGER, top_donors TEXT)"
)


def _seed_all(db) -> None:
    db.polls(
        _IDEOLOGY_DDL,
        "INSERT INTO pac_ideology VALUES "
        "('C001','Dominion Victory Fund','DVF','Pro-energy','right',"
        "'energy','utility','Dominion-aligned PAC',NULL,'1',NULL,NULL)",
        _LOOKUP_DDL,
        "INSERT INTO pac_name_lookup VALUES "
        "('Dominion Victory Fund','Utility','FEC',0.9,'2024-01-01')",
        _IE_DDL,
        "INSERT INTO fec_independent_expenditures VALUES "
        "('Jane Smith','Dominion Victory Fund','S',50000.0,'2024-10-01','2024','Senate','VA')",
        _FIT_DDL,
        "INSERT INTO fec_industry_totals VALUES "
        "('Bob Jones','2024','Dominion/Electric Utilities',125000.0,45,'Dominion Energy')",
    )


def test_pac_keyword_search_hits_ideology_table(db):
    _seed_all(db)
    ctx = dc.build_database_context("who funds Dominion super pac")
    assert "[Database Context - polls.pac_ideology keyword]" in ctx
    assert "Dominion Victory Fund" in ctx


def test_pac_keyword_search_hits_name_lookup(db):
    _seed_all(db)
    ctx = dc.build_database_context("who funds Dominion super pac")
    assert "[Database Context - polls.pac_name_lookup keyword]" in ctx


def test_pac_keyword_search_hits_independent_expenditures(db):
    _seed_all(db)
    ctx = dc.build_database_context("who funds Dominion super pac")
    assert "[Database Context - polls.fec_independent_expenditures keyword]" in ctx


def test_pac_keyword_search_hits_industry_totals(db):
    _seed_all(db)
    ctx = dc.build_database_context("who funds Dominion super pac")
    # fec_industry_totals block uses a distinct header line
    assert "polls.fec_industry_totals" in ctx


def test_pac_no_trigger_no_blocks(db):
    _seed_all(db)
    ctx = dc.build_database_context("How did Jane Smith vote on HB50?")
    assert "polls.pac_ideology" not in ctx
    assert "polls.pac_name_lookup" not in ctx


def test_pac_missing_tables_silent(db):
    # Tables not created → builder exits silently
    ctx = dc.build_database_context("who funds Dominion super pac")
    assert "[Database Context - polls.pac_ideology keyword]" not in ctx


def test_pac_generic_only_terms_run_sample_query(db):
    db.polls(
        _IDEOLOGY_DDL,
        "INSERT INTO pac_ideology VALUES "
        "('C001','Alpha Fund','AF','neutral','center',"
        "'general','misc','A fund',NULL,'1',NULL,NULL)",
        _LOOKUP_DDL,
        "INSERT INTO pac_name_lookup VALUES "
        "('Alpha Fund','General','FEC',0.8,'2024-01-01')",
        _IE_DDL,
        _FIT_DDL,
    )
    # All query terms are generic ("pac", "political", "action") → sample path
    ctx = dc.build_database_context("list all political action committee pac spending")
    # Sample path uses "polls.pac_ideology sample" label
    assert "polls.pac_ideology sample" in ctx
