"""Tests for _add_disclosures_context and _add_lobbyist_direct_context.

Disclosures: fires on SOEI / financial disclosure keywords; matches by
legislator last name (≥4 chars) when one is mentioned, otherwise returns
top filers.

Lobbyist: fires on "registered lobbyist" / "who lobbies for" / similar;
only shows status='Approved' rows; filters to matching org when query
words (>4 chars) overlap a principal name.
"""
from __future__ import annotations

import pytest

from voteiq.services import database_context as dc

_DISC_DDL = (
    "CREATE TABLE legislator_financial_disclosures "
    "(legislator_name TEXT, filing_year INTEGER, part TEXT, "
    "entity_name TEXT, role_or_type TEXT, sector TEXT, amount_range TEXT)"
)
_LOB_DDL = (
    "CREATE TABLE lobbyist_registrations "
    "(principal_name TEXT, lobbyist_name TEXT, status TEXT, year_range TEXT)"
)


# ── Disclosures ────────────────────────────────────────────────────────────────

def test_disclosures_named_legislator(db):
    db.polls(
        _DISC_DDL,
        "INSERT INTO legislator_financial_disclosures VALUES "
        "('Jennifer Hughes', 2024, 'A', 'Hughes LLC', 'Owner', 'Real Estate', '$10k-$50k')",
    )
    ctx = dc.build_database_context("What are Hughes financial disclosures?")
    assert "[VEC Financial Disclosures — Jennifer Hughes]" in ctx
    assert "Hughes LLC" in ctx


def test_disclosures_top_filers_when_no_name_in_query(db):
    db.polls(
        _DISC_DDL,
        "INSERT INTO legislator_financial_disclosures VALUES "
        "('Jennifer Hughes', 2024, 'A', 'Acme Corp', 'Partner', 'Tech', '$50k+')",
        "INSERT INTO legislator_financial_disclosures VALUES "
        "('Robert Mills', 2024, 'B', 'Mills Farm', 'Owner', 'Agriculture', '$5k-$10k')",
    )
    ctx = dc.build_database_context("show me all financial disclosures filed this year")
    assert "[VEC Financial Disclosures — Top Filers]" in ctx


def test_disclosures_no_trigger_no_block(db):
    db.polls(
        _DISC_DDL,
        "INSERT INTO legislator_financial_disclosures VALUES "
        "('Jennifer Hughes', 2024, 'A', 'Acme Corp', 'Partner', 'Tech', '$50k+')",
    )
    ctx = dc.build_database_context("How did Hughes vote on HB200?")
    assert "[VEC Financial Disclosures" not in ctx


def test_disclosures_soei_trigger(db):
    db.polls(
        _DISC_DDL,
        "INSERT INTO legislator_financial_disclosures VALUES "
        "('Jennifer Hughes', 2024, 'A', 'Acme Corp', 'Partner', 'Tech', '$50k+')",
    )
    ctx = dc.build_database_context("Does Hughes have an SOEI filing?")
    # "SOEI" triggers the block; no last-name match long enough → top filers
    assert "[VEC Financial Disclosures" in ctx


def test_disclosures_missing_table_silent(db):
    ctx = dc.build_database_context("What are Hughes financial disclosures?")
    assert "[VEC Financial Disclosures" not in ctx


def test_disclosures_last_name_too_short_no_name_match(db):
    db.polls(
        _DISC_DDL,
        "INSERT INTO legislator_financial_disclosures VALUES "
        "('Bob Lee', 2024, 'A', 'Lee Corp', 'Owner', 'Finance', '$5k+')",
    )
    # last name "lee" is only 3 chars → len(last) < 4 → no name match → top filers
    ctx = dc.build_database_context("What are Lee financial disclosures?")
    # still fires because trigger matches, but goes to top-filers branch
    assert "[VEC Financial Disclosures — Top Filers]" in ctx


# ── Lobbyist ──────────────────────────────────────────────────────────────────

def test_lobbyist_top_principals(db):
    db.polls(
        _LOB_DDL,
        "INSERT INTO lobbyist_registrations VALUES "
        "('Dominion Energy', 'Alice Doe', 'Approved', '2024-2025')",
        "INSERT INTO lobbyist_registrations VALUES "
        "('Dominion Energy', 'Bob Smith', 'Approved', '2024-2025')",
        "INSERT INTO lobbyist_registrations VALUES "
        "('VHHA', 'Carol Brown', 'Approved', '2024-2025')",
    )
    ctx = dc.build_database_context("who are the top lobbying firms in Virginia?")
    assert "[Virginia Lobbyist Registrations — Top Principals]" in ctx
    assert "Dominion Energy" in ctx


def test_lobbyist_filters_unapproved_rows(db):
    db.polls(
        _LOB_DDL,
        "INSERT INTO lobbyist_registrations VALUES "
        "('SecretCorp', 'Unknown', 'Pending', '2024-2025')",
    )
    ctx = dc.build_database_context("who are the most lobbyists in Virginia?")
    assert "[Virginia Lobbyist Registrations" not in ctx


def test_lobbyist_matching_label_when_org_named(db):
    db.polls(
        _LOB_DDL,
        "INSERT INTO lobbyist_registrations VALUES "
        "('Dominion Energy', 'Alice Doe', 'Approved', '2024-2025')",
        "INSERT INTO lobbyist_registrations VALUES "
        "('VHHA', 'Carol Brown', 'Approved', '2024-2025')",
    )
    # "dominion" (6 chars > 4) overlaps with "Dominion Energy" principal name
    ctx = dc.build_database_context("who lobbies for Dominion Energy?")
    assert "[Virginia Lobbyist Registrations — Matching Principals]" in ctx
    assert "Dominion Energy" in ctx


def test_lobbyist_no_trigger_no_block(db):
    db.polls(
        _LOB_DDL,
        "INSERT INTO lobbyist_registrations VALUES "
        "('Dominion Energy', 'Alice Doe', 'Approved', '2024-2025')",
    )
    ctx = dc.build_database_context("What did Dominion Energy donate to delegates?")
    assert "[Virginia Lobbyist Registrations" not in ctx


def test_lobbyist_missing_table_silent(db):
    ctx = dc.build_database_context("who are the most lobbyists in Virginia?")
    assert "[Virginia Lobbyist Registrations" not in ctx
