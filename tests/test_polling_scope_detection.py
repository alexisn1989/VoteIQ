"""Regression tests for the polling scope-limit detector and reply builder.

Production calibration (2026-07-02) found "What do current polls show about
Virginia voters' views on the state budget?" wrongly claiming VoteIQ has zero
polling data, even though a full polling subsystem already exists
(_external_polling_reply querying real polls/poll_results/poll_articles
tables). Three compounding bugs, all fixed here:

1. _is_public_opinion_polling_scope_limit_query required a second confirming
   word (survey/approval/favorability/a named pollster/etc), which missed
   common phrasings like "what do current polls show" — so the query never
   reached the polling subsystem at all and fell through to the general LLM
   path with zero polling awareness.
2. _external_polling_reply's poll_articles query grabbed the 3 most recent
   articles UNCONDITIONALLY, with no relevance check — so an off-target
   topical question ("state budget") returned unrelated news (a photo-ID law,
   a gas plant) dressed up as "related poll-news records."
3. _polling_name_terms' generic-word stoplist was missing common words
   ("virginians", "voters", "show", "current", etc.), so a plain office-level
   ask like "polling on the governor race" over-constrained the SQL WHERE
   clause with a term that could never match, hiding real polling data that
   existed in the DB.

These tests exercise the actual functions against a small in-memory fixture
DB — no network calls, no dependency on the real polls.db content.
"""
from __future__ import annotations

import sqlite3

import pytest

import voteiq.api.routes.chat as chat_module
from voteiq.api.routes.chat import (
    _external_polling_reply,
    _is_public_opinion_polling_scope_limit_query,
    _polling_name_terms,
    _scope_limit_polling_reply,
)

_POLLS_DDL = """
    CREATE TABLE polls (
        source_record_id TEXT PRIMARY KEY, source TEXT, cycle INTEGER,
        state TEXT, office_type TEXT, seat_name TEXT, pollster TEXT,
        sponsor TEXT, sample_size INTEGER, population TEXT, methodology TEXT,
        start_date TEXT, end_date TEXT, created_at TEXT, url TEXT,
        notes TEXT, fetched_at TEXT
    )
"""
_RESULTS_DDL = """
    CREATE TABLE poll_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_record_id TEXT,
        answer TEXT, candidate_name TEXT, candidate_party TEXT, pct REAL
    )
"""
_ARTICLES_DDL = """
    CREATE TABLE poll_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, article_id TEXT,
        title TEXT, summary TEXT, published_at TEXT, url TEXT,
        matched_terms TEXT, fetched_at TEXT
    )
"""


@pytest.fixture()
def polling_db(tmp_path, monkeypatch):
    path = tmp_path / "polls.db"
    conn = sqlite3.connect(str(path))
    conn.execute(_POLLS_DDL)
    conn.execute(_RESULTS_DDL)
    conn.execute(_ARTICLES_DDL)
    conn.execute(
        "INSERT INTO polls VALUES ('p1','InsiderAdvantage',2025,'virginia','Governor',"
        "'','InsiderAdvantage','',800,'lv','','2025-11-01','2025-11-03','2025-11-03',"
        "'https://example.com/poll','','2025-11-03')"
    )
    conn.execute(
        "INSERT INTO poll_results (source_record_id, answer, candidate_name, candidate_party, pct) "
        "VALUES ('p1','Spanberger','Abigail Spanberger','D',50.0)"
    )
    conn.execute(
        "INSERT INTO poll_articles (source, title, summary, published_at, url, matched_terms) "
        "VALUES ('Google News RSS', 'New West Virginia law requiring photo IDs at polling places', "
        "'', '2026-05-12', 'https://example.com/photo-id', 'polling')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(chat_module, "_POLLS_DB", str(path))
    return path


# ── Bug 1: trigger detection ───────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "What do current polls show about Virginia voters' views on the state budget?",
    "How are Virginians polling on the governor race?",
    "What do recent polls say about Spanberger?",
    "What are the latest polls in Virginia?",
])
def test_natural_polling_phrasings_are_detected(query):
    assert _is_public_opinion_polling_scope_limit_query(query) is True


@pytest.mark.parametrize("query", [
    "Where is my polling place?",
    "What is the history of the poll tax in Virginia?",
    "How many poll workers does Fairfax County need?",
])
def test_non_opinion_polling_senses_are_excluded(query):
    assert _is_public_opinion_polling_scope_limit_query(query) is False


# ── Bug 2: irrelevant articles attached to off-target questions ────────────

def test_off_target_topic_does_not_attach_unrelated_articles(polling_db):
    reply = _scope_limit_polling_reply(
        "What do current polls show about Virginia voters' views on the state budget?"
    )
    assert reply is not None
    assert "photo ID" not in reply
    assert "doesn't have polling data matching that question" in reply


def test_generic_query_still_gets_recent_articles(polling_db):
    reply = _scope_limit_polling_reply("What are the latest polls in Virginia?")
    assert reply is not None
    assert "photo ID" in reply or "Spanberger" in reply


# ── Bug 3: demonym/generic words over-constraining office-level queries ────

def test_virginians_does_not_over_constrain_name_terms():
    assert "virginians" not in _polling_name_terms("How are Virginians polling on the governor race?")


def test_governor_race_query_finds_real_polling_data(polling_db):
    reply = _scope_limit_polling_reply("How are Virginians polling on the governor race?")
    assert reply is not None
    assert "Spanberger" in reply
    assert "doesn't have polling data" not in reply


def test_specific_candidate_name_still_filters_results(polling_db):
    reply = _external_polling_reply("What do polls say about Youngkin?")
    # No Youngkin data seeded — must not fall back to showing Spanberger's numbers.
    assert reply is None or "Spanberger" not in reply
