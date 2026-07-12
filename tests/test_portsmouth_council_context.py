"""Tests for the Portsmouth wiring of _add_scraped_council_context.

The shared builder engine is pinned in detail by
tests/test_chesapeake_council_context.py — these cover only what is
Portsmouth-specific: trigger, member map, table prefix, source URL, and the
roster-fallback exclusion. Portsmouth's data comes from
scrape_portsmouth_council.py (City Clerk Laserfiche archive — Portsmouth has
no IQM2 and its Granicus instance publishes agendas only, no vote outcomes).
"""
from __future__ import annotations

from voteiq.services.context.builders import local

_MEMBERS_DDL = """
    CREATE TABLE portsmouth_council_members (
        id INTEGER PRIMARY KEY, name TEXT, district TEXT, district_num INTEGER,
        email TEXT, jurisdiction TEXT, scraped_date TEXT
    )
"""
_VOTES_DDL = """
    CREATE TABLE portsmouth_council_votes (
        id INTEGER PRIMARY KEY, doc_id TEXT, meeting_date TEXT, title TEXT,
        agenda_item TEXT, motion TEXT, moved_by TEXT, seconded_by TEXT,
        result TEXT, vote_count TEXT, votes_json TEXT, category TEXT, scraped_at TEXT
    )
"""
_MEMBER_VOTES_DDL = """
    CREATE TABLE portsmouth_council_member_votes (
        id INTEGER PRIMARY KEY, vote_id INTEGER, doc_id TEXT, meeting_date TEXT,
        member_name TEXT, vote TEXT, agenda_item TEXT, title TEXT, result TEXT, category TEXT
    )
"""


def _seed_full(db) -> None:
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO portsmouth_council_members VALUES"
        " (1, 'Shannon E. Glover', 'Mayor', 0, '', 'Portsmouth', '2026-06-01')",
        _VOTES_DDL,
        "INSERT INTO portsmouth_council_votes VALUES"
        " (1, 'doc1', '09/19/2023', 'Casino development agreement amendment', 'Item 1',"
        "  'Approve', 'Glover', 'Moody', 'passed', '5-2', '{}', 'development', '2026-01-01')",
        _MEMBER_VOTES_DDL,
        "INSERT INTO portsmouth_council_member_votes VALUES"
        " (1, 1, 'doc1', '09/19/2023', 'Glover', 'no', 'Item 1', 'Casino development agreement amendment', 'passed', 'development'),"
        " (2, 1, 'doc1', '09/19/2023', 'Moody', 'no', 'Item 1', 'Casino development agreement amendment', 'passed', 'development'),"
        " (3, 1, 'doc1', '09/19/2023', 'Thomas', 'yes', 'Item 1', 'Casino development agreement amendment', 'passed', 'development')",
    )


def _blocks(query: str, terms: list[str]) -> list[str]:
    blocks: list[str] = []
    local._add_portsmouth_council_context(blocks, query, terms)
    return blocks


def test_trigger_requires_portsmouth(db):
    _seed_full(db)
    assert _blocks("Who is on the city council?", ["city", "council"]) == []
    # Common-first-name surname alone must not fire either
    assert _blocks("How did Thomas vote?", ["thomas", "vote"]) == []


def test_roster_and_contested_default(db):
    _seed_full(db)
    blocks = _blocks("Who is on the Portsmouth City Council?", ["portsmouth", "city", "council"])
    assert len(blocks) == 1
    assert "Portsmouth City Council — Member Roster" in blocks[0]
    assert "Shannon E. Glover" in blocks[0]
    assert "Most Contested Votes" in blocks[0]
    assert "Casino development" in blocks[0]  # 2 no votes, meets HAVING >= 2


def test_named_member_summary_uses_portsmouth_map(db):
    _seed_full(db)
    blocks = _blocks("How did Glover vote on the Portsmouth council?", ["glover", "portsmouth", "council"])
    assert "Glover Vote Summary" in blocks[0]
    assert "1 recorded votes" in blocks[0]
    assert "Casino development" in blocks[0]  # his lone no vote


def test_source_is_laserfiche_archive(db):
    _seed_full(db)
    blocks = _blocks("Portsmouth council votes", ["portsmouth", "council"])
    assert "www2.portsmouthva.gov/weblink7CCMinutes" in blocks[0]
    assert "granicus" not in blocks[0].lower()  # that's Chesapeake's source, not Portsmouth's


def test_officials_roster_excludes_council_when_votes_present(monkeypatch, db):
    _seed_full(db)
    monkeypatch.setattr(local, "_officials_cache", [
        {"name": "Mark A. Hugel", "district": "Portsmouth Council", "email": "",
         "jurisdiction": "Portsmouth", "party": "", "url": ""},
        {"name": "Shannon E. Glover", "district": "Portsmouth Mayor", "email": "",
         "jurisdiction": "Portsmouth", "party": "", "url": ""},
    ])
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Portsmouth City Council?")
    assert "Mark A. Hugel" not in blocks[0]   # Council row — excluded, vote builder covers it
    assert "Shannon E. Glover" in blocks[0]    # Mayor row — still served here


def test_officials_roster_keeps_council_when_votes_absent(monkeypatch, db):
    """Same deployment-state guard as Chesapeake: production seed may predate
    the Portsmouth scrape — council members must not vanish from the roster."""
    monkeypatch.setattr(local, "_officials_cache", [
        {"name": "Mark A. Hugel", "district": "Portsmouth Council", "email": "",
         "jurisdiction": "Portsmouth", "party": "", "url": ""},
    ])
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Portsmouth City Council?")
    assert "Mark A. Hugel" in blocks[0]


def test_orchestrator_routes_portsmouth_query(db):
    from voteiq.services import database_context as dc
    _seed_full(db)
    ctx = dc.build_database_context("Who is on the Portsmouth City Council?")
    assert "Portsmouth City Council" in ctx
    assert "Member Roster" in ctx
