"""Tests for _add_local_officials_context — roster-only elected officials for
Chesapeake, Hampton, Portsmouth, Suffolk, and Newport News.

voteiq_officials.json (133 rows, git-tracked) had zero code consumers before
this — a fully orphaned dataset despite substantial historical curation.
Norfolk and Virginia Beach are deliberately excluded from this builder since
they already have deeper dedicated vote-data builders
(_add_norfolk_council_context / _add_vb_council_context).
"""
from __future__ import annotations

import pytest

from voteiq.services.context.builders import local


@pytest.fixture(autouse=True)
def _isolated_officials_cache(monkeypatch):
    """Every test gets a clean, explicit officials list — never the real
    voteiq_officials.json file — so these tests are stable regardless of
    real-world roster churn."""
    monkeypatch.setattr(local, "_officials_cache", None)
    yield
    monkeypatch.setattr(local, "_officials_cache", None)


_FIXTURE_OFFICIALS = [
    {"name": "Jane Doe", "district": "Chesapeake Mayor", "email": "",
     "jurisdiction": "Chesapeake", "party": "Democrat", "url": "https://example.com/doe"},
    {"name": "John Smith", "district": "Chesapeake Council", "email": "",
     "jurisdiction": "Chesapeake", "party": "", "url": "https://example.com/smith"},
    {"name": "Pat Lee", "district": "Suffolk School Board", "email": "",
     "jurisdiction": "Suffolk", "party": "", "url": ""},
]


def test_chesapeake_council_query_returns_roster(monkeypatch):
    monkeypatch.setattr(local, "_officials_cache", _FIXTURE_OFFICIALS)
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Chesapeake City Council?")
    assert len(blocks) == 1
    assert "Chesapeake Elected Officials" in blocks[0]
    assert "Jane Doe" in blocks[0]
    assert "John Smith" in blocks[0]
    assert "Pat Lee" not in blocks[0]  # different locality


def test_no_trigger_words_no_block(monkeypatch):
    monkeypatch.setattr(local, "_officials_cache", _FIXTURE_OFFICIALS)
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "What's the weather in Chesapeake?")
    assert blocks == []


def test_locality_word_without_office_keyword_no_block(monkeypatch):
    """'Chesapeake' alone (no council/mayor/school board/etc.) must not fire —
    avoids injecting a roster into unrelated Chesapeake questions."""
    monkeypatch.setattr(local, "_officials_cache", _FIXTURE_OFFICIALS)
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Tell me about Chesapeake Bay.")
    assert blocks == []


def test_unmatched_locality_no_block(monkeypatch):
    monkeypatch.setattr(local, "_officials_cache", _FIXTURE_OFFICIALS)
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Richmond City Council?")
    assert blocks == []


def test_school_board_query_matches(monkeypatch):
    monkeypatch.setattr(local, "_officials_cache", _FIXTURE_OFFICIALS)
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Suffolk School Board?")
    assert len(blocks) == 1
    assert "Pat Lee" in blocks[0]
    assert "Jane Doe" not in blocks[0]


def test_norfolk_and_virginia_beach_excluded(monkeypatch):
    """These localities have dedicated deeper builders — this roster-only
    builder must never fire for them, even with real matching data present."""
    fixture = _FIXTURE_OFFICIALS + [
        {"name": "Norfolk Person", "district": "Norfolk Council", "email": "",
         "jurisdiction": "Norfolk", "party": "", "url": ""},
    ]
    monkeypatch.setattr(local, "_officials_cache", fixture)
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Norfolk City Council?")
    assert blocks == []


def test_source_url_included_when_present(monkeypatch):
    monkeypatch.setattr(local, "_officials_cache", _FIXTURE_OFFICIALS)
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Chesapeake council members")
    assert "https://example.com/doe" in blocks[0]


def test_empty_officials_list_no_block(monkeypatch):
    monkeypatch.setattr(local, "_officials_cache", [])
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Hampton City Council?")
    assert blocks == []


# ── End-to-end through the orchestrator (real voteiq_officials.json) ──────────

def test_orchestrator_surfaces_real_chesapeake_roster(db):
    """Uses the REAL committed voteiq_officials.json via build_database_context
    — confirms actual wiring, not just the unit in isolation. Asserts only on
    the Mayor's office title (stable structural fact), not a specific name,
    so this doesn't break on the next real-world roster update."""
    from voteiq.services import database_context as dc
    ctx = dc.build_database_context("Who is on the Chesapeake City Council?")
    assert "Chesapeake Elected Officials" in ctx
    assert "Chesapeake Mayor" in ctx
