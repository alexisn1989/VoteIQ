"""Fixture-backed regression tests for _add_pac_vote_correlation_context.

This is the WHRO demo-critical asset (see CLAUDE.md CORE DEMO PROTECTION ZONE).
The existing tests in test_pac_vote_causation.py run against the real local
polls.db and skip when it's absent — which means CI (no polls.db) exercised
none of them. These tests seed their own fixture tables so they can never
skip: any regression in the correlation block's gates, member resolution,
PAC aggregation, vote fallback, or the INFERENCE RULE text fails CI.

Tests only CALL the protected function — they must never require changes to it.
"""
from __future__ import annotations

from voteiq.services import database_context as dc

_MEMBERS_DDL = """
    CREATE TABLE congress_members (
        bioguide_id TEXT, name TEXT, party TEXT, chamber TEXT,
        state TEXT, district TEXT
    )
"""

_IE_DDL = """
    CREATE TABLE fec_independent_expenditures (
        bioguide_id TEXT, committee_name TEXT, support_oppose TEXT,
        expenditure_amount REAL, cycle INTEGER
    )
"""

_IDEOLOGY_DDL = """
    CREATE TABLE pac_ideology (
        id INTEGER PRIMARY KEY, committee_name TEXT, short_name TEXT,
        ideology TEXT, alignment TEXT
    )
"""

_VOTES_DDL = """
    CREATE TABLE congress_votes (
        bioguide_id TEXT, vote_date TEXT, bill TEXT, question TEXT,
        member_vote TEXT, result TEXT, congress INTEGER
    )
"""


def _seed_full(db) -> None:
    """One member, PAC spending for and against, and a vote record."""
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES"
        " ('K000399', 'Kiggans, Jennifer', 'R', 'House', 'VA', '2')",
        _IE_DDL,
        "INSERT INTO fec_independent_expenditures VALUES"
        " ('K000399', 'DEFEND AMERICAN FREEDOM PAC', 'S', 150000.0, 2024),"
        " ('K000399', 'DEFEND AMERICAN FREEDOM PAC', 'S', 50000.0, 2024),"
        " ('K000399', 'PROGRESS NOW COMMITTEE', 'O', 75000.0, 2024)",
        _IDEOLOGY_DDL,
        "INSERT INTO pac_ideology VALUES"
        " (1, 'DEFEND AMERICAN FREEDOM PAC', 'DAF', 'conservative', 'Republican-aligned')",
        _VOTES_DDL,
        "INSERT INTO congress_votes VALUES"
        " ('K000399', '2025-06-14', 'HR 2670', 'On Passage: National Defense Authorization Act',"
        "  'Yea', 'Passed', 119),"
        " ('K000399', '2025-05-02', 'HR 1234', 'On Passage: Veterans Health Care Expansion',"
        "  'Yea', 'Passed', 119)",
    )


def _blocks(query: str, terms: list[str]) -> list[str]:
    blocks: list[str] = []
    dc._add_pac_vote_correlation_context(blocks, query, terms)
    return blocks


# ── Gates ─────────────────────────────────────────────────────────────────────

def test_no_pac_signal_no_block(db):
    _seed_full(db)
    assert _blocks("how did kiggans vote on defense", ["kiggans", "vote", "defense"]) == []


def test_no_vote_signal_no_block(db):
    _seed_full(db)
    assert _blocks("which pacs fund kiggans", ["pacs", "fund", "kiggans"]) == []


def test_unknown_member_no_block(db):
    _seed_full(db)
    assert _blocks(
        "which pacs fund zzzunknown and how did they vote",
        ["pacs", "fund", "zzzunknown", "vote"],
    ) == []


# ── Full correlation block ────────────────────────────────────────────────────

def test_block_contains_pac_spending_and_votes(db):
    _seed_full(db)
    blocks = _blocks(
        "which pacs fund kiggans and how did she vote",
        ["pacs", "fund", "kiggans", "vote"],
    )
    assert len(blocks) == 1, "expected exactly one correlation block"
    block = blocks[0]
    assert "[Database Context - PAC vs. Vote Correlation]" in block
    assert "Kiggans, Jennifer" in block
    # PAC aggregation: two S txns summed, direction labels, ideology join
    assert "FOR Kiggans: DEFEND AMERICAN FREEDOM PAC" in block
    assert "$200,000" in block
    assert "AGAINST Kiggans: PROGRESS NOW COMMITTEE" in block
    assert "conservative" in block
    # Vote record via the recent-votes fallback path
    assert "HR 2670" in block
    assert "Yea" in block


def test_block_contains_inference_rule(db):
    """The no-causation directive is the load-bearing safety line of the demo."""
    _seed_full(db)
    block = _blocks(
        "which pacs fund kiggans and how did she vote",
        ["pacs", "fund", "kiggans", "vote"],
    )[0]
    assert "INFERENCE RULE" in block
    assert "Do NOT assert" in block
    assert "caused" in block  # the rule enumerates banned causal verbs


def test_block_never_uses_causal_connectors_itself(db):
    """The block presents parallel records; it must not editorialize."""
    _seed_full(db)
    block = _blocks(
        "which pacs fund kiggans and how did she vote",
        ["pacs", "fund", "kiggans", "vote"],
    )[0]
    flagged = dc._check_banned_causation_phrases(
        block.split("INFERENCE RULE")[0]  # rule text itself names the banned verbs
    )
    assert flagged is None, f"correlation block contains causal language: {flagged}"


def test_member_without_pac_rows_reports_no_spending(db):
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO congress_members VALUES"
        " ('W000825', 'Wexton, Jennifer', 'D', 'House', 'VA', '10')",
        _IE_DDL,
        _IDEOLOGY_DDL,
        _VOTES_DDL,
    )
    blocks = _blocks(
        "which pacs fund wexton and how did she vote",
        ["pacs", "fund", "wexton", "vote"],
    )
    assert len(blocks) == 1
    assert "No PAC spending records found" in blocks[0]


# ── End-to-end through the orchestrator ───────────────────────────────────────

def test_orchestrator_surfaces_correlation_block(db):
    """build_database_context must route a PAC+vote query into the correlation
    block — the whole demo flow, fixture DB to context string."""
    _seed_full(db)
    context = dc.build_database_context("Which PACs fund Kiggans and how did she vote?")
    assert "[Database Context - PAC vs. Vote Correlation]" in context
    assert "DEFEND AMERICAN FREEDOM PAC" in context
    assert "INFERENCE RULE" in context
