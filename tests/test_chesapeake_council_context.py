"""Tests for _add_chesapeake_council_context — real per-member vote data.

Found fully populated in polls.db (chesapeake_council_members /
_council_votes / _council_member_votes, scraped via
scrape_chesapeake_council.py from Chesapeake's Granicus archive) with zero
code consumers. Chesapeake has no IQM2 portal like Norfolk/VB, so this is a
separate builder rather than a generalization of _add_vb_council_context.
"""
from __future__ import annotations

from voteiq.services.context.builders import local

_MEMBERS_DDL = """
    CREATE TABLE chesapeake_council_members (
        id INTEGER PRIMARY KEY, name TEXT, district TEXT, district_num INTEGER,
        email TEXT, jurisdiction TEXT, scraped_date TEXT
    )
"""
_VOTES_DDL = """
    CREATE TABLE chesapeake_council_votes (
        id INTEGER PRIMARY KEY, clip_id INTEGER, meeting_date TEXT, title TEXT,
        agenda_item TEXT, motion TEXT, moved_by TEXT, seconded_by TEXT,
        result TEXT, vote_count TEXT, votes_json TEXT, category TEXT, scraped_at TEXT
    )
"""
_MEMBER_VOTES_DDL = """
    CREATE TABLE chesapeake_council_member_votes (
        id INTEGER PRIMARY KEY, vote_id INTEGER, clip_id INTEGER, meeting_date TEXT,
        member_name TEXT, vote TEXT, agenda_item TEXT, title TEXT, result TEXT, category TEXT
    )
"""


def _seed_full(db) -> None:
    db.polls(
        _MEMBERS_DDL,
        "INSERT INTO chesapeake_council_members VALUES"
        " (1, 'Dr. Richard W. \"Rick\" West', 'Mayor', 0, 'rwest@cityofchesapeake.net', 'Chesapeake', '2026-06-01'),"
        " (2, 'Daniel W. Whitaker', 'Council Member (At-Large)', 1, 'dwhitaker@cityofchesapeake.net', 'Chesapeake', '2026-06-01')",
        _VOTES_DDL,
        "INSERT INTO chesapeake_council_votes VALUES"
        " (1, 100, 'Jun 11, 2024', 'Rezoning of 93 acres for residential use', 'Item 1', 'Approve', 'West', 'Ward', 'failed', '3-6', '{}', 'zoning', '2026-01-01'),"
        " (2, 'unknown', 'unknown', 'Ordinance increasing salaries of Mayor and City Council', 'Item 2', 'Approve', 'Ward', 'Bunn', 'passed', '5-4', '{}', 'procedural', '2026-01-01')",
        _MEMBER_VOTES_DDL,
        "INSERT INTO chesapeake_council_member_votes VALUES"
        " (1, 1, 100, 'Jun 11, 2024', 'Whitaker', 'no', 'Item 1', 'Rezoning of 93 acres for residential use', 'failed', 'zoning'),"
        " (2, 1, 100, 'Jun 11, 2024', 'West', 'yes', 'Item 1', 'Rezoning of 93 acres for residential use', 'failed', 'zoning'),"
        " (3, 1, 100, 'Jun 11, 2024', 'Ward', 'no', 'Item 1', 'Rezoning of 93 acres for residential use', 'failed', 'zoning'),"
        " (4, 2, 'unknown', 'unknown', 'Whitaker', 'yes', 'Item 2', 'Ordinance increasing salaries of Mayor and City Council', 'passed', 'procedural'),"
        " (5, 2, 'unknown', 'unknown', 'West', 'yes', 'Item 2', 'Ordinance increasing salaries of Mayor and City Council', 'passed', 'procedural')",
    )


def _blocks(query: str, terms: list[str]) -> list[str]:
    blocks: list[str] = []
    local._add_chesapeake_council_context(blocks, query, terms)
    return blocks


# ── Trigger gating ────────────────────────────────────────────────────────────

def test_no_trigger_without_chesapeake(db):
    _seed_full(db)
    assert _blocks("Who is on the city council?", ["city", "council"]) == []


def test_locality_without_office_keyword_no_block(db):
    """'Chesapeake' alone must not fire — avoids injecting council data into
    unrelated Chesapeake questions (mirrors the Bay/roster guard)."""
    _seed_full(db)
    assert _blocks("Tell me about the Chesapeake Bay.", ["chesapeake", "bay"]) == []


def test_common_surname_alone_does_not_trigger(db):
    """'Ward'/'Smith'/'King'/'Bunn' are common words/surnames — must require
    'chesapeake' + an office keyword, not fire on the surname alone."""
    _seed_full(db)
    assert _blocks("How did Ward vote?", ["ward", "vote"]) == []


# ── Roster ────────────────────────────────────────────────────────────────────

def test_roster_query_returns_members(db):
    _seed_full(db)
    blocks = _blocks("Who is on the Chesapeake City Council?", ["chesapeake", "city", "council"])
    assert len(blocks) == 1
    assert "Member Roster" in blocks[0]
    assert "Dr. Richard W." in blocks[0]
    assert "Whitaker" in blocks[0]


# ── Named-member vote summary ─────────────────────────────────────────────────

def test_named_member_vote_summary(db):
    _seed_full(db)
    blocks = _blocks("How did Whitaker vote on the Chesapeake council?", ["whitaker", "chesapeake", "council"])
    assert len(blocks) == 1
    assert "Whitaker Vote Summary" in blocks[0]
    assert "2 recorded votes" in blocks[0]
    assert "Yes: 1" in blocks[0] and "No: 1" in blocks[0]


def test_named_member_no_votes_listed_without_false_recency_claim(db):
    """meeting_date is free text with 'unknown' values mixed in — the label
    must not claim these are chronologically 'recent'."""
    _seed_full(db)
    blocks = _blocks("How did Whitaker vote on the Chesapeake council?", ["whitaker", "chesapeake", "council"])
    assert "Recent No votes" not in blocks[0]
    assert "No votes on record" in blocks[0]
    assert "Rezoning of 93 acres" in blocks[0]


def test_no_period_date_range_shown(db):
    """Regression guard: an earlier draft showed MIN/MAX(meeting_date) as a
    'Period: X to Y' range, which is meaningless — meeting_date includes the
    literal string 'unknown', which sorts after real month names."""
    _seed_full(db)
    blocks = _blocks("How did Whitaker vote on the Chesapeake council?", ["whitaker", "chesapeake", "council"])
    assert "Period:" not in blocks[0]


# ── Topic search ──────────────────────────────────────────────────────────────

def test_topic_search_finds_matching_votes_and_dissent(db):
    _seed_full(db)
    blocks = _blocks("How did Chesapeake council vote on rezoning?", ["chesapeake", "council", "rezoning"])
    assert len(blocks) == 1
    assert "Vote Search: 'rezoning'" in blocks[0]
    assert "Rezoning of 93 acres" in blocks[0]
    assert "Dissent:" in blocks[0]


def test_generic_words_do_not_trigger_spurious_topic_search(db):
    """Regression guard: 'city' survived the old exclusion list and produced
    a bogus 'Vote Search: city' block instead of the roster/contested-votes
    default — 'city', 'who', 'mayor', etc. must not count as search terms."""
    _seed_full(db)
    blocks = _blocks("Who is on the Chesapeake City Council?", ["chesapeake", "city", "council"])
    assert "Vote Search" not in blocks[0]


# ── Default: most contested votes ─────────────────────────────────────────────

def test_default_shows_most_contested_when_no_member_or_topic(db):
    _seed_full(db)
    blocks = _blocks("Chesapeake council votes", ["chesapeake", "council"])
    assert len(blocks) == 1
    assert "Most Contested Votes" in blocks[0]
    assert "Rezoning of 93 acres" in blocks[0]  # 2 no votes, meets HAVING >= 2
    assert "salaries of Mayor" not in blocks[0]  # 0 no votes, filtered out


# ── Source attribution ─────────────────────────────────────────────────────────

def test_source_link_is_real_confirmed_url(db):
    """The Granicus ViewPublisher URL must be the one confirmed present in
    scrape_chesapeake_council.py, not a guessed per-item deep link (Chesapeake
    lacks the doc_id needed to reconstruct a MinutesViewer.php per-item URL)."""
    _seed_full(db)
    blocks = _blocks("Who is on the Chesapeake City Council?", ["chesapeake", "city", "council"])
    assert "chesapeake.granicus.com/ViewPublisher.php?view_id=29" in blocks[0]


def test_no_data_no_block(db):
    db.polls(_MEMBERS_DDL, _VOTES_DDL, _MEMBER_VOTES_DDL)
    assert _blocks("Who is on the Chesapeake City Council?", ["chesapeake", "city", "council"]) == []


# ── End-to-end through the orchestrator ───────────────────────────────────────

def test_orchestrator_routes_chesapeake_query_to_new_builder(db):
    from voteiq.services import database_context as dc
    _seed_full(db)
    ctx = dc.build_database_context("Who is on the Chesapeake City Council?")
    assert "Chesapeake City Council" in ctx
    assert "Member Roster" in ctx
