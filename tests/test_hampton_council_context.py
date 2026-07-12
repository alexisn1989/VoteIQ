"""Tests for the Hampton wiring of _add_scraped_council_context.

The shared builder engine is pinned in detail by
tests/test_chesapeake_council_context.py — these cover only what is
Hampton-specific: trigger, surname member map (matching Chesapeake/
Portsmouth's pattern, unlike Newport News's full names), and source note.

Hampton's data comes from scrape_hampton_council.py: Legistar's on-site
Action/Result columns are empty for every item (unlike a typical Legistar
tenant), so this downloads each meeting's "Notice of Action" minutes PDF
(confirmed real extractable text, not scanned) and sends it to Gemini for
structured vote extraction — a first-pass regex parser mis-segmented items
at page breaks and repeated title prefixes.
"""
from __future__ import annotations

from voteiq.services.context.builders import local

_MEMBERS_DDL = """
    CREATE TABLE hampton_council_members (
        id INTEGER PRIMARY KEY, name TEXT, district TEXT, district_num INTEGER,
        email TEXT, jurisdiction TEXT, scraped_date TEXT
    )
"""
_VOTES_DDL = """
    CREATE TABLE hampton_council_votes (
        id INTEGER PRIMARY KEY, meeting_id INTEGER, meeting_date TEXT, title TEXT,
        agenda_item TEXT, motion TEXT, moved_by TEXT, seconded_by TEXT,
        result TEXT, vote_count TEXT, votes_json TEXT, category TEXT, scraped_at TEXT
    )
"""
_MEMBER_VOTES_DDL = """
    CREATE TABLE hampton_council_member_votes (
        id INTEGER PRIMARY KEY, vote_id INTEGER, meeting_id INTEGER, meeting_date TEXT,
        member_name TEXT, vote TEXT, agenda_item TEXT, title TEXT, result TEXT, category TEXT
    )
"""


def _seed_full(db) -> None:
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO hampton_council_members VALUES"
        " (1, 'Jimmy Gray', 'Mayor', 0, '', 'Hampton', '2026-07-12')",
        _VOTES_DDL,
        "INSERT INTO hampton_council_votes VALUES"
        " (1, 1160743, '12/11/2024', 'Short-term rental use permit at 1536 Morgan Dr', '24-0468',"
        "  'Approve', 'Brown', 'Mugler', 'passed', '5-2', '{}', 'substantive', '2026-01-01')",
        _MEMBER_VOTES_DDL,
        "INSERT INTO hampton_council_member_votes VALUES"
        " (1, 1, 1160743, '12/11/2024', 'Bowman', 'no', '24-0468', 'Short-term rental use permit at 1536 Morgan Dr', 'passed', 'substantive'),"
        " (2, 1, 1160743, '12/11/2024', 'Harper', 'no', '24-0468', 'Short-term rental use permit at 1536 Morgan Dr', 'passed', 'substantive'),"
        " (3, 1, 1160743, '12/11/2024', 'Gray', 'yes', '24-0468', 'Short-term rental use permit at 1536 Morgan Dr', 'passed', 'substantive')",
    )


def _blocks(query: str, terms: list[str]) -> list[str]:
    blocks: list[str] = []
    local._add_hampton_council_context(blocks, query, terms)
    return blocks


def test_trigger_requires_hampton(db):
    _seed_full(db)
    assert _blocks("Who is on the city council?", ["city", "council"]) == []


def test_common_surname_alone_does_not_trigger(db):
    """'Gray'/'Brown'/'Harper' are common surnames — must require 'hampton' +
    an office keyword, not fire on the surname alone."""
    _seed_full(db)
    assert _blocks("How did Gray vote?", ["gray", "vote"]) == []


def test_roster_and_contested_default(db):
    _seed_full(db)
    blocks = _blocks("Who is on the Hampton City Council?", ["hampton", "city", "council"])
    assert len(blocks) == 1
    assert "Hampton City Council — Member Roster" in blocks[0]
    assert "Jimmy Gray" in blocks[0]
    assert "Most Contested Votes" in blocks[0]
    assert "Short-term rental" in blocks[0]  # 2 no votes, meets HAVING >= 2


def test_named_member_uses_surname_map(db):
    """Matches Chesapeake/Portsmouth's bare-surname pattern (Gemini was
    prompted to key votes by last name only), unlike Newport News's full
    names."""
    _seed_full(db)
    blocks = _blocks("How did Bowman vote on the Hampton council?", ["bowman", "hampton", "council"])
    assert "Bowman Vote Summary" in blocks[0]
    assert "1 recorded votes" in blocks[0]
    assert "No: 1" in blocks[0]


def test_historical_member_name_also_matches(db):
    """Council membership changed between the scraped 2023-2024 data and
    today (Tuck/Hobbs are no longer on the roster) — the member map must
    still resolve historical names for old vote lookups."""
    db.polls(
        _MEMBERS_DDL,
        _VOTES_DDL,
        "INSERT INTO hampton_council_votes VALUES"
        " (2, 1160700, '3/1/2023', 'Some 2023 item', '23-0100', 'Approve', '', '',"
        "  'passed', '7-0', '{}', 'substantive', '2026-01-01')",
        _MEMBER_VOTES_DDL,
        "INSERT INTO hampton_council_member_votes VALUES"
        " (10, 2, 1160700, '3/1/2023', 'Tuck', 'yes', '23-0100', 'Some 2023 item', 'passed', 'substantive')",
    )
    blocks = _blocks("How did Tuck vote on the Hampton council?", ["tuck", "hampton", "council"])
    assert "Tuck Vote Summary" in blocks[0]


def test_source_is_legistar_calendar(db):
    _seed_full(db)
    blocks = _blocks("Hampton council votes", ["hampton", "council"])
    assert "hampton.legistar.com/Calendar.aspx" in blocks[0]


def test_officials_roster_excludes_council_when_votes_present(monkeypatch, db):
    _seed_full(db)
    monkeypatch.setattr(local, "_officials_cache", [
        {"name": "Randy Bowman", "district": "Hampton Council", "email": "",
         "jurisdiction": "Hampton", "party": "", "url": ""},
        {"name": "Jimmy Gray", "district": "Hampton Mayor", "email": "",
         "jurisdiction": "Hampton", "party": "", "url": ""},
    ])
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Hampton City Council?")
    assert "Randy Bowman" not in blocks[0]  # Council row — excluded, vote builder covers it
    assert "Jimmy Gray" in blocks[0]         # Mayor row — still served here


def test_officials_roster_keeps_council_when_votes_absent(monkeypatch, db):
    monkeypatch.setattr(local, "_officials_cache", [
        {"name": "Randy Bowman", "district": "Hampton Council", "email": "",
         "jurisdiction": "Hampton", "party": "", "url": ""},
    ])
    blocks: list[str] = []
    local._add_local_officials_context(blocks, "Who is on the Hampton City Council?")
    assert "Randy Bowman" in blocks[0]


def test_orchestrator_routes_hampton_query(db):
    from voteiq.services import database_context as dc
    _seed_full(db)
    ctx = dc.build_database_context("Who is on the Hampton City Council?")
    assert "Hampton City Council" in ctx
    assert "Member Roster" in ctx
