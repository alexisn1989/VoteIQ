#!/usr/bin/env python3
"""
Ingest Virginia polling data into openstates_va.db.

Sources:
  - FiveThirtyEight CSV data endpoints, filtered to state=Virginia.
  - Optional Ballotpedia race pages, parsed for poll tables when present.
  - RSS feeds/news search feeds, stored as poll-related article mentions.

Examples:
    python ingest_va_polls.py
    python ingest_va_polls.py --source fivethirtyeight
    python ingest_va_polls.py --ballotpedia-url "https://ballotpedia.org/Virginia_gubernatorial_election,_2025"
    python ingest_va_polls.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "openstates_va.db"
USER_AGENT = "VoteIQ/1.0 Virginia polling ingester (contact: local VoteIQ app)"

FIVETHIRTYEIGHT_URLS = {
    # FiveThirtyEight's documented projects.fivethirtyeight.com CSV links now
    # route through ABC News HTML. Datasette mirrors the same CC-BY polling data
    # as plain CSV and is more reliable for automated ingestion.
    "president": "https://fivethirtyeight.datasettes.com/polls/president_polls.csv?_stream=on",
    "president_primary": "https://fivethirtyeight.datasettes.com/polls/president_primary_polls.csv?_stream=on",
    "senate": "https://fivethirtyeight.datasettes.com/polls/senate_polls.csv?_stream=on",
    "house": "https://fivethirtyeight.datasettes.com/polls/house_polls.csv?_stream=on",
    "governor": "https://fivethirtyeight.datasettes.com/polls/governor_polls.csv?_stream=on",
}

DEFAULT_BALLOTPEDIA_URLS = [
    "https://ballotpedia.org/Virginia_gubernatorial_election,_2025",
    "https://ballotpedia.org/United_States_Senate_election_in_Virginia,_2026",
]

DEFAULT_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia election poll OR polling OR survey"),
    "https://www.virginiamercury.com/feed/",
    "https://www.wvtf.org/rss.xml",
    "https://www.whro.org/rss.xml",
]

POLL_TERMS = re.compile(r"\b(poll|polling|survey|favorability|approval|horserace|margin)\b", re.I)
VOTEHUB_POLLS_URL = "https://api.votehub.com/polls"


@dataclass
class PollRow:
    source: str
    source_record_id: str
    race_id: str
    cycle: str
    state: str
    office_type: str
    seat_name: str
    stage: str
    pollster: str
    sponsor: str
    fte_grade: str
    sample_size: str
    population: str
    methodology: str
    start_date: str
    end_date: str
    election_date: str
    created_at: str
    url: str
    notes: str
    internal: int
    partisan: str
    raw_json: str


@dataclass
class PollResult:
    source_record_id: str
    answer: str
    candidate_name: str
    candidate_party: str
    pct: float | None


class BallotpediaBlocked(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_int_bool(value: object) -> int:
    text = clean_text(value).lower()
    return 1 if text in {"1", "true", "yes", "y"} else 0


def parse_pct(value: object) -> float | None:
    text = clean_text(value).replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def stable_id(*parts: object) -> str:
    joined = "|".join(clean_text(p).lower() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def fetch_text(url: str, timeout: int = 45) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_ballotpedia_html(url: str) -> str:
    resp = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; VoteIQ/1.0; +local civic app)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=45,
    )
    resp.raise_for_status()
    text = resp.text or ""
    lower = text.lower()
    if resp.status_code == 202 and not text.strip():
        raise BallotpediaBlocked("Ballotpedia returned HTTP 202 with an empty bot-check response.")
    if "verify that you're not a robot" in lower or "javascript is disabled" in lower:
        raise BallotpediaBlocked("Ballotpedia returned a JavaScript/bot-check page.")
    return text


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS polls (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source           TEXT NOT NULL,
            source_record_id TEXT NOT NULL UNIQUE,
            race_id          TEXT,
            cycle            TEXT,
            state            TEXT,
            office_type      TEXT,
            seat_name        TEXT,
            stage            TEXT,
            pollster         TEXT,
            sponsor          TEXT,
            fte_grade        TEXT,
            sample_size      TEXT,
            population       TEXT,
            methodology      TEXT,
            start_date       TEXT,
            end_date         TEXT,
            election_date    TEXT,
            created_at       TEXT,
            url              TEXT,
            notes            TEXT,
            internal         INTEGER DEFAULT 0,
            partisan         TEXT,
            raw_json         TEXT,
            fetched_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS poll_results (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source_record_id TEXT NOT NULL,
            answer           TEXT,
            candidate_name   TEXT,
            candidate_party  TEXT,
            pct              REAL,
            UNIQUE(source_record_id, answer, candidate_name, candidate_party)
        );

        CREATE TABLE IF NOT EXISTS poll_articles (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source           TEXT NOT NULL,
            article_id       TEXT NOT NULL UNIQUE,
            title            TEXT,
            summary          TEXT,
            published_at     TEXT,
            url              TEXT,
            matched_terms    TEXT,
            raw_json         TEXT,
            fetched_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS poll_ingest_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source           TEXT NOT NULL,
            started_at       TEXT NOT NULL,
            finished_at      TEXT,
            status           TEXT NOT NULL,
            rows_seen        INTEGER DEFAULT 0,
            rows_written     INTEGER DEFAULT 0,
            message          TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_polls_state_cycle ON polls(state, cycle);
        CREATE INDEX IF NOT EXISTS idx_polls_race ON polls(race_id);
        CREATE INDEX IF NOT EXISTS idx_poll_results_record ON poll_results(source_record_id);
        CREATE INDEX IF NOT EXISTS idx_poll_articles_published ON poll_articles(published_at);
        """
    )
    conn.commit()


def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO poll_ingest_runs(source, started_at, status) VALUES (?, ?, ?)",
        (source, now_iso(), "running"),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    rows_seen: int,
    rows_written: int,
    message: str = "",
) -> None:
    conn.execute(
        """
        UPDATE poll_ingest_runs
        SET finished_at=?, status=?, rows_seen=?, rows_written=?, message=?
        WHERE id=?
        """,
        (now_iso(), status, rows_seen, rows_written, message[:1000], run_id),
    )
    conn.commit()


def upsert_poll(conn: sqlite3.Connection, poll: PollRow, results: list[PollResult]) -> int:
    conn.execute(
        """
        INSERT INTO polls (
            source, source_record_id, race_id, cycle, state, office_type, seat_name, stage,
            pollster, sponsor, fte_grade, sample_size, population, methodology,
            start_date, end_date, election_date, created_at, url, notes, internal,
            partisan, raw_json, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_record_id) DO UPDATE SET
            source=excluded.source,
            race_id=excluded.race_id,
            cycle=excluded.cycle,
            state=excluded.state,
            office_type=excluded.office_type,
            seat_name=excluded.seat_name,
            stage=excluded.stage,
            pollster=excluded.pollster,
            sponsor=excluded.sponsor,
            fte_grade=excluded.fte_grade,
            sample_size=excluded.sample_size,
            population=excluded.population,
            methodology=excluded.methodology,
            start_date=excluded.start_date,
            end_date=excluded.end_date,
            election_date=excluded.election_date,
            created_at=excluded.created_at,
            url=excluded.url,
            notes=excluded.notes,
            internal=excluded.internal,
            partisan=excluded.partisan,
            raw_json=excluded.raw_json,
            fetched_at=excluded.fetched_at
        """,
        (
            poll.source,
            poll.source_record_id,
            poll.race_id,
            poll.cycle,
            poll.state,
            poll.office_type,
            poll.seat_name,
            poll.stage,
            poll.pollster,
            poll.sponsor,
            poll.fte_grade,
            poll.sample_size,
            poll.population,
            poll.methodology,
            poll.start_date,
            poll.end_date,
            poll.election_date,
            poll.created_at,
            poll.url,
            poll.notes,
            poll.internal,
            poll.partisan,
            poll.raw_json,
            now_iso(),
        ),
    )
    for result in results:
        conn.execute(
            """
            INSERT INTO poll_results(source_record_id, answer, candidate_name, candidate_party, pct)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_record_id, answer, candidate_name, candidate_party)
            DO UPDATE SET pct=excluded.pct
            """,
            (
                result.source_record_id,
                result.answer,
                result.candidate_name,
                result.candidate_party,
                result.pct,
            ),
        )
    return 1


def ingest_fivethirtyeight(conn: sqlite3.Connection, dry_run: bool = False) -> tuple[int, int]:
    rows_seen = rows_written = 0
    for office_hint, url in FIVETHIRTYEIGHT_URLS.items():
        print(f"Fetching FiveThirtyEight {office_hint}: {url}")
        try:
            text = fetch_text(url)
        except requests.RequestException as exc:
            print(f"  skipped {office_hint}: {exc}")
            continue
        reader = csv.DictReader(io.StringIO(text))
        grouped: dict[str, tuple[PollRow, list[PollResult]]] = {}
        for row in reader:
            if clean_text(row.get("state")).lower() != "virginia":
                continue
            rows_seen += 1
            poll_id = clean_text(row.get("poll_id"))
            question_id = clean_text(row.get("question_id"))
            source_record_id = f"538:{office_hint}:{poll_id}:{question_id}"
            poll = PollRow(
                source="FiveThirtyEight",
                source_record_id=source_record_id,
                race_id=clean_text(row.get("race_id")),
                cycle=clean_text(row.get("cycle")),
                state=clean_text(row.get("state")),
                office_type=clean_text(row.get("office_type") or office_hint),
                seat_name=clean_text(row.get("seat_name")),
                stage=clean_text(row.get("stage")),
                pollster=clean_text(row.get("pollster") or row.get("display_name")),
                sponsor=clean_text(row.get("sponsors")),
                fte_grade=clean_text(row.get("fte_grade")),
                sample_size=clean_text(row.get("sample_size")),
                population=clean_text(row.get("population") or row.get("population_full")),
                methodology=clean_text(row.get("methodology")),
                start_date=clean_text(row.get("start_date")),
                end_date=clean_text(row.get("end_date")),
                election_date=clean_text(row.get("election_date")),
                created_at=clean_text(row.get("created_at")),
                url=clean_text(row.get("url")),
                notes=clean_text(row.get("notes")),
                internal=parse_int_bool(row.get("internal")),
                partisan=clean_text(row.get("partisan")),
                raw_json=json.dumps(row, ensure_ascii=False, sort_keys=True),
            )
            result = PollResult(
                source_record_id=source_record_id,
                answer=clean_text(row.get("answer")),
                candidate_name=clean_text(row.get("candidate_name")),
                candidate_party=clean_text(row.get("candidate_party")),
                pct=parse_pct(row.get("pct")),
            )
            grouped.setdefault(source_record_id, (poll, []))[1].append(result)
        if not dry_run:
            for poll, results in grouped.values():
                rows_written += upsert_poll(conn, poll, results)
            conn.commit()
        else:
            rows_written += len(grouped)
        print(f"  Virginia rows: {rows_seen}; polls in this pass: {len(grouped)}")
    return rows_seen, rows_written


def _votehub_is_virginia_poll(row: dict) -> bool:
    text = " ".join(
        clean_text(row.get(key))
        for key in ("subject", "seat_name", "url", "poll_type")
    ).lower()
    if "virginia" in text:
        return True
    candidate_text = " ".join(
        clean_text(answer.get("choice"))
        for answer in row.get("answers", [])
        if isinstance(answer, dict)
    ).lower()
    va_candidate_terms = (
        "spanberger", "earle-sears", "winsome sears", "jay jones",
        "jason miyares", "ghazala hashmi", "john reid",
    )
    return any(term in candidate_text for term in va_candidate_terms)


def ingest_votehub(conn: sqlite3.Connection, dry_run: bool = False) -> tuple[int, int]:
    print(f"Fetching VoteHub polls: {VOTEHUB_POLLS_URL}")
    data = requests.get(
        VOTEHUB_POLLS_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    data.raise_for_status()
    rows = data.json()
    if not isinstance(rows, list):
        raise RuntimeError("VoteHub polls API returned a non-list JSON response")

    rows_seen = rows_written = 0
    for row in rows:
        if not isinstance(row, dict) or not _votehub_is_virginia_poll(row):
            continue
        rows_seen += 1
        poll_type = clean_text(row.get("poll_type"))
        subject = clean_text(row.get("subject"))
        source_record_id = "votehub:" + clean_text(row.get("id") or stable_id(row))
        poll = PollRow(
            source="VoteHub",
            source_record_id=source_record_id,
            race_id=stable_id("votehub", subject, poll_type),
            cycle=re.search(r"\b(20\d{2})\b", subject).group(1) if re.search(r"\b(20\d{2})\b", subject) else "",
            state="Virginia",
            office_type=poll_type.replace("-", " ").title(),
            seat_name=clean_text(row.get("seat_name")),
            stage="general",
            pollster=clean_text(row.get("pollster")),
            sponsor=", ".join(clean_text(s) for s in row.get("sponsors", []) if clean_text(s)),
            fte_grade="",
            sample_size=clean_text(row.get("sample_size")),
            population=clean_text(row.get("population")),
            methodology="",
            start_date=clean_text(row.get("start_date")),
            end_date=clean_text(row.get("end_date")),
            election_date="",
            created_at=clean_text(row.get("created_at")),
            url=clean_text(row.get("url")),
            notes=f"VoteHub subject: {subject}" if subject else "",
            internal=parse_int_bool(row.get("internal")),
            partisan=clean_text(row.get("partisan")),
            raw_json=json.dumps(row, ensure_ascii=False, sort_keys=True),
        )
        results = []
        for answer in row.get("answers", []):
            if not isinstance(answer, dict):
                continue
            choice = clean_text(answer.get("choice"))
            if not choice:
                continue
            results.append(
                PollResult(
                    source_record_id=source_record_id,
                    answer=choice,
                    candidate_name=choice,
                    candidate_party="",
                    pct=parse_pct(answer.get("pct")),
                )
            )
        if not dry_run:
            rows_written += upsert_poll(conn, poll, results)
        else:
            rows_written += 1
    if not dry_run:
        conn.commit()
    print(f"  Virginia VoteHub polls: {rows_seen}")
    return rows_seen, rows_written


def table_headers(table) -> list[str]:
    first = table.find("tr")
    if not first:
        return []
    cells = first.find_all(["th", "td"])
    return [clean_text(c.get_text(" ")) for c in cells]


def parse_ballotpedia_table(source_url: str, table) -> Iterable[tuple[PollRow, list[PollResult]]]:
    headers = table_headers(table)
    lowered = [h.lower() for h in headers]
    if not headers or not any("poll" in h for h in lowered) or not any("date" in h for h in lowered):
        return
    candidate_cols = [
        i for i, h in enumerate(headers)
        if h and not re.search(r"poll|date|sample|margin|source|result|lead", h, re.I)
    ]
    if not candidate_cols:
        return
    for tr in table.find_all("tr")[1:]:
        cells = [clean_text(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        values = dict(zip(headers, cells))
        pollster = next((v for k, v in values.items() if re.search("poll", k, re.I)), "")
        date_text = next((v for k, v in values.items() if re.search("date", k, re.I)), "")
        sample = next((v for k, v in values.items() if re.search("sample", k, re.I)), "")
        if not pollster and not date_text:
            continue
        source_record_id = "ballotpedia:" + stable_id(source_url, pollster, date_text, sample, cells)
        poll = PollRow(
            source="Ballotpedia",
            source_record_id=source_record_id,
            race_id=stable_id(source_url),
            cycle="",
            state="Virginia",
            office_type="",
            seat_name="",
            stage="",
            pollster=pollster,
            sponsor="",
            fte_grade="",
            sample_size=sample,
            population="",
            methodology="",
            start_date=date_text,
            end_date=date_text,
            election_date="",
            created_at="",
            url=source_url,
            notes="Parsed from Ballotpedia poll table; inspect source URL for table context.",
            internal=0,
            partisan="",
            raw_json=json.dumps(values, ensure_ascii=False, sort_keys=True),
        )
        results: list[PollResult] = []
        for i in candidate_cols:
            if i >= len(cells):
                continue
            pct = parse_pct(cells[i])
            if pct is None:
                continue
            results.append(
                PollResult(
                    source_record_id=source_record_id,
                    answer=headers[i],
                    candidate_name=headers[i],
                    candidate_party="",
                    pct=pct,
                )
            )
        if results:
            yield poll, results


def ingest_ballotpedia(
    conn: sqlite3.Connection,
    urls: list[str],
    files: list[str] | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    rows_seen = rows_written = 0
    for url in urls:
        print(f"Fetching Ballotpedia: {url}")
        try:
            soup = BeautifulSoup(fetch_ballotpedia_html(url), "html.parser")
        except BallotpediaBlocked as exc:
            print(f"  blocked by Ballotpedia protection: {exc}")
            print("  tip: save the page HTML in a browser and rerun with --ballotpedia-file path\\to\\page.html")
            continue
        except requests.RequestException as exc:
            print(f"  skipped Ballotpedia page: {exc}")
            continue
        parsed = []
        for table in soup.find_all("table"):
            parsed.extend(list(parse_ballotpedia_table(url, table)))
        rows_seen += len(parsed)
        if not dry_run:
            for poll, results in parsed:
                rows_written += upsert_poll(conn, poll, results)
            conn.commit()
        else:
            rows_written += len(parsed)
        print(f"  parsed poll tables/rows: {len(parsed)}")
    for file_path in files or []:
        path = Path(file_path)
        print(f"Parsing Ballotpedia HTML file: {path}")
        try:
            html_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            html_text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"  skipped local Ballotpedia file: {exc}")
            continue
        source_url = f"file:{path.name}"
        soup = BeautifulSoup(html_text, "html.parser")
        parsed = []
        for table in soup.find_all("table"):
            parsed.extend(list(parse_ballotpedia_table(source_url, table)))
        rows_seen += len(parsed)
        if not dry_run:
            for poll, results in parsed:
                rows_written += upsert_poll(conn, poll, results)
            conn.commit()
        else:
            rows_written += len(parsed)
        print(f"  parsed poll tables/rows: {len(parsed)}")
    return rows_seen, rows_written


def rss_items(feed_text: str) -> Iterable[dict[str, str]]:
    root = ET.fromstring(feed_text)
    channel_items = root.findall(".//item")
    if channel_items:
        for item in channel_items:
            yield {
                "title": clean_text(item.findtext("title")),
                "summary": clean_text(item.findtext("description")),
                "published_at": parse_feed_date(item.findtext("pubDate")),
                "url": clean_text(item.findtext("link")),
            }
        return
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        link = entry.find("atom:link", ns)
        yield {
            "title": clean_text(entry.findtext("atom:title", default="", namespaces=ns)),
            "summary": clean_text(entry.findtext("atom:summary", default="", namespaces=ns)),
            "published_at": parse_feed_date(entry.findtext("atom:updated", default="", namespaces=ns)),
            "url": clean_text(link.get("href") if link is not None else ""),
        }


def parse_feed_date(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).isoformat()
    except (TypeError, ValueError):
        return text


def feed_source_name(feed_url: str) -> str:
    if "news.google.com" in feed_url:
        return "Google News RSS"
    match = re.search(r"https?://(?:www\.)?([^/]+)", feed_url)
    return match.group(1) if match else feed_url


def ingest_news_feeds(
    conn: sqlite3.Connection,
    feeds: list[str],
    dry_run: bool = False,
) -> tuple[int, int]:
    rows_seen = rows_written = 0
    for feed_url in feeds:
        source = feed_source_name(feed_url)
        print(f"Fetching news feed: {source}")
        try:
            text = fetch_text(feed_url)
        except requests.RequestException as exc:
            print(f"  skipped feed: {exc}")
            continue
        try:
            items = list(rss_items(text))
        except ET.ParseError as exc:
            print(f"  skipped feed parse error: {exc}")
            continue
        for item in items:
            combined = html.unescape(f"{item.get('title', '')} {item.get('summary', '')}")
            if "virginia" not in combined.lower() or not POLL_TERMS.search(combined):
                continue
            rows_seen += 1
            article_id = "news:" + stable_id(source, item.get("url"), item.get("title"))
            terms = sorted(set(m.group(0).lower() for m in POLL_TERMS.finditer(combined)))
            if dry_run:
                rows_written += 1
                continue
            conn.execute(
                """
                INSERT INTO poll_articles (
                    source, article_id, title, summary, published_at, url,
                    matched_terms, raw_json, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    title=excluded.title,
                    summary=excluded.summary,
                    published_at=excluded.published_at,
                    url=excluded.url,
                    matched_terms=excluded.matched_terms,
                    raw_json=excluded.raw_json,
                    fetched_at=excluded.fetched_at
                """,
                (
                    source,
                    article_id,
                    item.get("title", ""),
                    item.get("summary", ""),
                    item.get("published_at", ""),
                    item.get("url", ""),
                    ", ".join(terms),
                    json.dumps(item, ensure_ascii=False, sort_keys=True),
                    now_iso(),
                ),
            )
            rows_written += 1
        if not dry_run:
            conn.commit()
        print(f"  matching articles so far: {rows_seen}")
    return rows_seen, rows_written


def run_source(conn: sqlite3.Connection, source: str, func, *args, dry_run: bool = False):
    run_id = start_run(conn, source)
    try:
        seen, written = func(conn, *args, dry_run=dry_run)
    except Exception as exc:
        finish_run(conn, run_id, "error", 0, 0, str(exc))
        raise
    finish_run(conn, run_id, "dry_run" if dry_run else "ok", seen, written)
    return seen, written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest Virginia polling data into SQLite.")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite DB path")
    parser.add_argument(
        "--source",
        action="append",
        choices=["fivethirtyeight", "votehub", "ballotpedia", "news"],
        help="Source to run. Repeatable. Default: all.",
    )
    parser.add_argument("--ballotpedia-url", action="append", default=[], help="Extra Ballotpedia race page URL")
    parser.add_argument("--ballotpedia-file", action="append", default=[], help="Saved Ballotpedia HTML file to parse")
    parser.add_argument("--news-feed", action="append", default=[], help="Extra RSS/Atom feed URL")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without writing poll rows")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    conn = sqlite3.connect(db_path)
    setup_db(conn)
    sources = args.source or ["fivethirtyeight", "votehub", "ballotpedia", "news"]

    totals: dict[str, tuple[int, int]] = {}
    try:
        if "fivethirtyeight" in sources:
            totals["fivethirtyeight"] = run_source(conn, "fivethirtyeight", ingest_fivethirtyeight, dry_run=args.dry_run)
        if "votehub" in sources:
            totals["votehub"] = run_source(conn, "votehub", ingest_votehub, dry_run=args.dry_run)
        if "ballotpedia" in sources:
            urls = DEFAULT_BALLOTPEDIA_URLS + args.ballotpedia_url
            totals["ballotpedia"] = run_source(
                conn,
                "ballotpedia",
                ingest_ballotpedia,
                urls,
                args.ballotpedia_file,
                dry_run=args.dry_run,
            )
        if "news" in sources:
            feeds = DEFAULT_NEWS_FEEDS + args.news_feed
            totals["news"] = run_source(conn, "news", ingest_news_feeds, feeds, dry_run=args.dry_run)
    finally:
        conn.close()

    print("\nPoll ingest complete")
    for source, (seen, written) in totals.items():
        verb = "would write" if args.dry_run else "wrote"
        print(f"  {source}: saw {seen}, {verb} {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
