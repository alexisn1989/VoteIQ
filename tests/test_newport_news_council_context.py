"""Tests for the Newport News wiring of _add_scraped_council_context.

The shared builder engine is pinned in detail by
tests/test_chesapeake_council_context.py — these cover only what is
Newport-News-specific: two-word trigger, full-name member map (unlike
Chesapeake/Portsmouth's bare surnames), table prefix with underscore, ward-
suffixed roster exclusion ("Newport News Council 1/2/3"), and source URL.

Newport News's data comes from scrape_newport_news_council.py, which drives
CivicWeb's "Attendance & Voting Records" search (nngov.civicweb.net) — a
structured, purpose-built per-member vote lookup, unlike Chesapeake/
Portsmouth's PDF-minutes-plus-Gemini approach.
"""
from __future__ import annotations

from voteiq.services.context.builders import local

_MEMBERS_DDL = """
    CREATE TABLE newport_news_council_members (
        id INTEGER PRIMARY KEY, name TEXT, district TEXT, district_num INTEGER,
        email TEXT, jurisdiction TEXT, scraped_date TEXT
    )
"""
_VOTES_DDL = """
    CREATE TABLE newport_news_council_votes (
        id INTEGER PRIMARY KEY, meeting_date TEXT, title TEXT,
        agenda_item TEXT, motion TEXT, moved_by TEXT, seconded_by TEXT,
        result TEXT, vote_count TEXT, votes_json TEXT, category TEXT, scraped_at TEXT
    )
"""
_MEMBER_VOTES_DDL = """
    CREATE TABLE newport_news_council_member_votes (
        id INTEGER PRIMARY KEY, vote_id INTEGER, meeting_date TEXT,
        member_name TEXT, vote TEXT, agenda_item TEXT, title TEXT, result TEXT, category TEXT
    )
"""


def _seed_full(db) -> None:
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO newport_news_council_members VALUES"
        " (1, 'Phillip Jones', 'Council Member', 0, '', 'Newport News', '2026-07-12')",
        _VOTES_DDL,
        "INSERT INTO newport_news_council_votes VALUES"
        " (1, 'Apr 28 2026', 'Halfway house CUP expansion', 'Item 2', 'Approve', 'Jones', 'Long',"
        "  'passed', '4-3', '{}', 'zoning', '2026-01-01')",
        _MEMBER_VOTES_DDL,
        "INSERT INTO newport_news_council_member_votes VALUES"
        " (1, 1, 'Apr 28 2026', 'Phillip Jones', 'no', 'Item 2', 'Halfway house CUP expansion', 'passed', 'zoning'),"
        " (2, 1, 'Apr 28 2026', 'Cleon M. Long, P.E.', 'no', 'Item 2', 'Halfway house CUP expansion', 'passed', 'zoning'),"
        " (3, 1, 'Apr 28 2026', 'Tina L. Vick', 'yes', 'Item 2', 'Halfway house CUP expansion', 'passed', 'zoning')",
    )


def _blocks(query: str, terms: list[str]) -> list[str]:
    blocks: list[str] = []
    local._add_newport_news_council_context(blocks, query, terms)
    return blocks


def test_trigger_requires_two_word_city_name(db):
    _seed_full(db)
    assert _blocks("Who is on the city council?", ["city", "council"]) == []
    assert _blocks("newport bills", ["newport", "bills"]) == []  # "newport" alone, no "news"


def test_common_surname_alone_does_not_trigger(db):
    """'Jones'/'Long'/'Scott'/'Price' are common surnames — must require
    'Newport News' + an office keyword, not fire on the surname alone."""
    _seed_full(db)
    assert _blocks("How did Jones vote?", ["jones", "vote"]) == []


def test_roster_and_contested_default(db):
    _seed_full(db)
    blocks = _blocks("Who is on the Newport News City Council?", ["newport", "news", "city", "council"])
    assert len(blocks) == 1
    assert "Newport News City Council — Member Roster" in blocks[0]
    assert "Phillip Jones" in blocks[0]
    assert "Most Contested Votes" in blocks[0]
    assert "Halfway house" in blocks[0]  # 2 no votes, meets HAVING >= 2


def test_named_member_uses_full_name_map(db):
    """Unlike Chesapeake/Portsmouth (bare surnames), member_name here is the
    full display name — the config's member_map must map surname keys to
    full-name values matching what the scraper actually stored."""
    _seed_full(db)
    blocks = _blocks("How did Long vote on the Newport News council?", ["long", "newport", "news", "council"])
    assert "Cleon M. Long, P.E. Vote Summary" in blocks[0]
    assert "1 recorded votes" in blocks[0]
    assert "No: 1" in blocks[0]


def test_topic_search_excludes_both_city_name_words(db):
    """Regression guard: excluding only the underscored prefix
    ('newport_news') wouldn't match the tokenized words 'newport'/'news' that
    _keywords() actually produces — both must be excluded individually."""
    _seed_full(db)
    blocks = _blocks("Who is on the Newport News City Council?", ["newport", "news", "city", "council"])
    assert "Vote Search" not in blocks[0]


def test_topic_search_finds_matching_votes(db):
    _seed_full(db)
    blocks = _blocks(
        "How did Newport News council vote on the halfway house?",
        ["newport", "news", "council", "halfway", "house"],
    )
    assert "Vote Search" in blocks[0]
    assert "Halfway house" in blocks[0]
    assert "Dissent:" in blocks[0]


def test_source_is_civicweb_voting_records(db):
    _seed_full(db)
    blocks = _blocks("Newport News council votes", ["newport", "news", "council"])
    assert "nngov.civicweb.net/Portal/VotingRecords.aspx" in blocks[0]


def test_officials_roster_excludes_ward_suffixed_council_rows(monkeypatch, db):
    """Newport News uses ward-suffixed labels ('Newport News Council 1/2/3'),
    not a flat 'Newport News Council' — the exclusion must be a prefix match,
    not exact equality, or these rows would never be excluded."""
    _seed_full(db)
    monkeypatch.setattr(local, "_officials_cache", [
        {"name": "Someone Councilmember", "district": "Newport News Council 1", "email": "",
         "jurisdiction": "Newport News", "party": "", "url": ""},
        {"name": "The Mayor", "district": "Newport News Mayor", "email": "",
         "jurisdiction": "Newport News", "party": "", "url": ""},
    ])
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Newport News City Council?")
    assert "Someone Councilmember" not in blocks[0]  # ward-suffixed Council row — excluded
    assert "The Mayor" in blocks[0]                    # Mayor row — still served here


def test_officials_roster_keeps_council_when_votes_absent(monkeypatch, db):
    monkeypatch.setattr(local, "_officials_cache", [
        {"name": "Someone Councilmember", "district": "Newport News Council 1", "email": "",
         "jurisdiction": "Newport News", "party": "", "url": ""},
    ])
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Newport News City Council?")
    assert "Someone Councilmember" in blocks[0]


def test_orchestrator_routes_newport_news_query(db):
    from voteiq.services import database_context as dc
    _seed_full(db)
    ctx = dc.build_database_context("Who is on the Newport News City Council?")
    assert "Newport News City Council" in ctx
    assert "Member Roster" in ctx
