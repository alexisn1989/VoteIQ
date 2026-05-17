#!/usr/bin/env python3
"""
Ingest Virginia political news into polls.db using Gemini Flash.

Fetches RSS feeds from Virginia news outlets, sends each article to
Gemini 1.5 Flash for structured extraction, and stores the result as
JSON in the va_news table.

Examples:
    python ingest_va_news.py
    python ingest_va_news.py --limit 20
    python ingest_va_news.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from google import genai as _genai
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
USER_AGENT = "VoteIQ/1.0 Virginia news ingester (voteiq.io)"
GEMINI_MODEL = "gemini-2.5-flash"

NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia politics governor legislature"),
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia Spanberger election 2025 2026"),
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia Senate House delegates budget"),
    "https://www.virginiamercury.com/feed/",
]

VIRGINIA_TERMS = re.compile(
    r"\b(virginia|richmond|norfolk|hampton roads|nova|northern virginia|"
    r"spanberger|youngkin|warner|kaine|virginia beach|fairfax|arlington|"
    r"general assembly|house of delegates|virginia senate)\b",
    re.I,
)

GEMINI_PROMPT = """You are a Virginia politics analyst. Extract structured data from this news article.

Return ONLY valid JSON with these fields (omit any field not clearly present in the article):
{
  "headline": "article headline",
  "published": "YYYY-MM-DD",
  "outlet": "news outlet name",
  "author": "author name or null",
  "summary": "2-3 sentence plain-English summary of what happened",
  "politicians": [
    {"name": "Full Name", "office": "e.g. Governor, State Senator", "party": "D/R/I"}
  ],
  "topics": ["one or more of: redistricting, budget, education, healthcare, crime, environment, economy, elections, legislation, endorsement, polling"],
  "geographic_focus": "statewide / northern VA / Hampton Roads / Richmond / Shenandoah / Southwest VA / other",
  "election_relevance": true or false,
  "election_race": "e.g. Virginia Governor 2025, U.S. Senate Virginia 2026, or null",
  "key_quote": "most notable direct quote from the article, or null",
  "sentiment": "neutral / positive / negative / mixed"
}

Article URL: {url}

Article text:
{text}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(*parts: object) -> str:
    joined = "|".join(str(p or "").lower().strip() for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:24]


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS va_news (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id      TEXT NOT NULL UNIQUE,
            source          TEXT,
            url             TEXT,
            title           TEXT,
            published_at    TEXT,
            gemini_json     TEXT,
            fetched_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_va_news_published ON va_news(published_at);
        CREATE INDEX IF NOT EXISTS idx_va_news_source ON va_news(source);
    """)
    conn.commit()


def fetch_text(url: str, timeout: int = 20) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def fetch_article_text(url: str, timeout: int = 15) -> str:
    """Fetch a full article page and return clean readable text (up to 5000 chars)."""
    try:
        html = fetch_text(url, timeout=timeout)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
            tag.decompose()
        body = soup.find("article") or soup.find(id=re.compile(r"content|article|body", re.I))
        raw = (body or soup).get_text(" ", strip=True)
        return re.sub(r"\s{2,}", " ", raw)[:5000]
    except Exception:
        return ""


def rss_items(feed_text: str) -> list[dict]:
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError:
        return []
    items = []
    channel_items = root.findall(".//item")
    if channel_items:
        for item in channel_items:
            items.append({
                "title": (item.findtext("title") or "").strip(),
                "summary": (item.findtext("description") or "").strip(),
                "published_at": item.findtext("pubDate") or "",
                "url": (item.findtext("link") or "").strip(),
            })
    else:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            link = entry.find("atom:link", ns)
            items.append({
                "title": (entry.findtext("atom:title", default="", namespaces=ns) or "").strip(),
                "summary": (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip(),
                "published_at": entry.findtext("atom:updated", default="", namespaces=ns) or "",
                "url": (link.get("href") if link is not None else ""),
            })
    return items


def parse_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).isoformat()
    except Exception:
        return text


def source_name(feed_url: str) -> str:
    if "news.google.com" in feed_url:
        return "Google News"
    m = re.search(r"https?://(?:www\.)?([^/]+)", feed_url)
    return m.group(1) if m else feed_url


def _parse_gemini_json(raw: str) -> dict:
    """Robustly extract JSON from a Gemini response that may have surrounding text."""
    raw = raw.strip()
    # Strip markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.M)
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Find the first { ... } block
    m = re.search(r"\{[\s\S]+\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON found in: {raw[:200]}")


def gemini_extract(url: str, article_text: str, api_key: str) -> dict | None:
    """Call Gemini Flash to extract structured political news data."""
    if not _GENAI_AVAILABLE or not api_key or not article_text.strip():
        return None
    try:
        client = _genai.Client(api_key=api_key)
        prompt = GEMINI_PROMPT.replace("{url}", url).replace("{text}", article_text[:4500])
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return _parse_gemini_json(response.text)
    except Exception as exc:
        return {"_error": str(exc)}


def ingest(
    conn: sqlite3.Connection,
    feeds: list[str],
    api_key: str,
    limit: int = 50,
    dry_run: bool = False,
) -> tuple[int, int]:
    seen = written = 0
    for feed_url in feeds:
        src = source_name(feed_url)
        print(f"Fetching feed: {src}")
        try:
            feed_text = fetch_text(feed_url)
        except Exception as exc:
            print(f"  skipped: {exc}")
            continue

        items = rss_items(feed_text)
        print(f"  {len(items)} items in feed")

        for item in items:
            if written >= limit:
                break
            combined = f"{item['title']} {item['summary']}"
            if not VIRGINIA_TERMS.search(combined):
                continue
            seen += 1
            url = item.get("url", "")
            article_id = "news:" + stable_id(src, url, item.get("title"))

            # Skip if already in DB
            if not dry_run:
                exists = conn.execute(
                    "SELECT 1 FROM va_news WHERE article_id = ?", (article_id,)
                ).fetchone()
                if exists:
                    continue

            print(f"  [{seen}] {item['title'][:70]}")

            # Fetch full article and send to Gemini
            article_text = fetch_article_text(url) if url else ""
            if not article_text:
                article_text = combined  # fall back to RSS summary

            gemini_data = gemini_extract(url, article_text, api_key)
            if gemini_data:
                status = "ok" if "_error" not in gemini_data else f"error: {gemini_data['_error']}"
                print(f"    gemini: {status} | topics={gemini_data.get('topics')} | politicians={[p.get('name') for p in gemini_data.get('politicians', [])]}")
            else:
                gemini_data = {}

            if dry_run:
                written += 1
                time.sleep(1)
                continue

            published = parse_date(item.get("published_at", ""))
            conn.execute(
                """
                INSERT INTO va_news (article_id, source, url, title, published_at, gemini_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    gemini_json=excluded.gemini_json,
                    fetched_at=excluded.fetched_at
                """,
                (
                    article_id,
                    src,
                    url,
                    item.get("title", ""),
                    published,
                    json.dumps(gemini_data, ensure_ascii=False) if gemini_data else None,
                    now_iso(),
                ),
            )
            conn.commit()
            written += 1
            time.sleep(1)  # rate limit: 1 req/s for Gemini free tier

    return seen, written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest Virginia political news via Gemini.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--limit", type=int, default=50, help="Max articles to process per run")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set in environment.", file=sys.stderr)
        return 1
    if not _GENAI_AVAILABLE:
        print("ERROR: google-generativeai not installed. Run: pip install google-generativeai", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    setup_db(conn)

    seen, written = ingest(conn, NEWS_FEEDS, api_key, limit=args.limit, dry_run=args.dry_run)
    conn.close()

    verb = "would write" if args.dry_run else "wrote"
    print(f"\nDone. Saw {seen} Virginia articles, {verb} {written}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
