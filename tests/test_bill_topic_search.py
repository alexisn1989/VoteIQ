"""Regression tests for topical bill retrieval and name-match pollution.

Production calibration (2026-07-01) found "What energy bills passed in the
Virginia General Assembly in 2026?" abstaining despite energy bills in the DB.
Two causes, both fixed and pinned here:

1. AND-semantics keyword search required generic words ("passed", "virginia",
   "general") to appear in bill text, filtering out every real energy bill —
   and "energy" never literally matched "Electric utilities" / "solar" titles.
   Fix: _bill_topic_terms strips generic words and expands policy topics into
   OR'd synonyms.
2. The legislator-narrative matcher used substring matching, so the name token
   "bill" matched "energy bills", injecting Bill DeSteph's campaign-finance
   profile into topical queries. Fix: whole-word matching + ambiguous-token
   guard + formal->diminutive name map.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_VA_BILLS_DDL = """
    CREATE TABLE va_bills (
        bill_number TEXT, session TEXT, title TEXT, status TEXT, description TEXT
    )
"""

_NARRATIVES_DDL = """
    CREATE TABLE legislator_narratives (
        legislator_id TEXT, name TEXT, narrative TEXT, narrative_short TEXT
    )
"""


def test_topic_alias_expansion_finds_bills_without_literal_term(db):
    """'energy' must retrieve electric/solar bills whose text never says 'energy'."""
    db.polls(
        _VA_BILLS_DDL,
        "INSERT INTO va_bills VALUES"
        " ('SB249', '2026', 'Electric utilities; integrated resource plans.', 'Passed', 'Utility planning requirements.'),"
        " ('HB590', '2026', 'Smart Solar Permitting Platform.', 'Passed', 'Streamlines local solar permitting.'),"
        " ('HB999', '2026', 'Dog licensing; fees.', 'Passed', 'Adjusts dog license fees.')",
    )
    ctx = dc.build_database_context("What energy bills passed in the Virginia General Assembly in 2026?")
    assert "SB249" in ctx
    assert "HB590" in ctx
    assert "HB999" not in ctx


def test_generic_words_do_not_over_constrain(db):
    """'passed'/'virginia'/'general' must not be required to appear in bill text."""
    db.polls(
        _VA_BILLS_DDL,
        "INSERT INTO va_bills VALUES"
        " ('HB101', '2026', 'Firearm storage requirements.', 'Passed', 'Safe storage of firearms.')",
    )
    ctx = dc.build_database_context("Which gun bills passed the Virginia General Assembly in 2026?")
    assert "HB101" in ctx


def test_bill_topic_terms_falls_back_when_all_generic():
    terms = ["bills", "passed", "virginia"]
    out, any_semantics = dc._bill_topic_terms(terms)
    assert out == terms and any_semantics is False


def test_common_noun_bill_does_not_match_legislator_named_bill(db):
    db.polls(
        _NARRATIVES_DDL,
        "INSERT INTO legislator_narratives VALUES"
        " ('vl1', 'Bill DeSteph', 'Bill DeSteph raised $9M across cycles.', 'Short.')",
    )
    ctx = dc.build_database_context("What energy bills passed in the Virginia General Assembly in 2026?")
    assert "DeSteph" not in ctx
    ctx2 = dc.build_database_context("How does a bill become law in Virginia?")
    assert "DeSteph" not in ctx2


def test_real_name_query_still_matches_ambiguous_first_name(db):
    db.polls(
        _NARRATIVES_DDL,
        "INSERT INTO legislator_narratives VALUES"
        " ('vl1', 'Bill DeSteph', 'Bill DeSteph is a Republican state senator.', 'Short.')",
    )
    ctx = dc.build_database_context("What is Bill DeSteph known for?")
    assert "Bill DeSteph is a Republican state senator" in ctx
