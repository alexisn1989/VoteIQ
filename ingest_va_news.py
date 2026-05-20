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
import io
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
import pdfplumber
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False
    _gtypes = None

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
USER_AGENT = "VoteIQ/1.0 Virginia news ingester (voteiq.io)"
GEMINI_MODEL = "gemini-2.5-flash"
PDF_CACHE_DIR = BASE_DIR / ".pdf_cache"
MAX_PDF_BYTES = 6_000_000
MAX_PDF_TEXT_CHARS = 5000

try:
    sys.path.insert(0, str(BASE_DIR))
    from ingest_va_polls import _lookup_va_politician as _db_lookup
except Exception:
    _db_lookup = lambda name: {}  # noqa: E731  — fallback if polls module unavailable

NEWS_FEEDS = [
    # Statewide / political (best quality, full article text)
    "https://www.virginiamercury.com/feed/",
    "https://cardinalnews.org/feed/",         # Southwest VA — excellent journalism
    "https://bluevirginia.us/feed",            # VA political blog, very active
    "https://www.vpm.org/news.rss",            # Virginia Public Media

    # Richmond area
    "https://www.wric.com/feed/",

    # Hampton Roads
    "https://www.wavy.com/feed/",
    "https://www.13newsnow.com/feeds/syndication/rss/news/",

    # Northern Virginia
    "https://www.loudountimes.com/search/?f=rss",
    "https://www.arlnow.com/feed/",           # Arlington local politics

    # Fredericksburg / Central VA
    "https://www.fredericksburg.com/search/?f=rss&t=article&c=news&l=50&s=start_time&sd=desc",

    # Google News — paywalled outlets covered via headlines
    "https://news.google.com/rss/search?q=" + quote_plus("Richmond Times-Dispatch Virginia politics legislature"),
    "https://news.google.com/rss/search?q=" + quote_plus("Roanoke Times Virginia politics Southwest"),
    "https://news.google.com/rss/search?q=" + quote_plus("Virginian-Pilot Hampton Roads Virginia politics"),
    "https://news.google.com/rss/search?q=" + quote_plus("Daily Progress Charlottesville Virginia politics"),

    # Google News — topic searches
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia politics governor legislature"),
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia Spanberger election 2025 2026"),
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia Senate House delegates budget"),
    "https://news.google.com/rss/search?q=" + quote_plus("Virginia General Assembly 2026"),
]

VIRGINIA_TERMS = re.compile(
    r"\b(virginia|richmond|norfolk|hampton roads|nova|northern virginia|"
    r"spanberger|youngkin|warner|kaine|virginia beach|fairfax|arlington|"
    r"chesapeake|portsmouth|suffolk|newport news|hampton|williamsburg|"
    r"roanoke|charlottesville|lynchburg|fredericksburg|loudoun|prince william|"
    r"harrisonburg|staunton|waynesboro|blacksburg|radford|danville|"
    r"general assembly|house of delegates|virginia senate|va\. senate|va\. house|"
    r"times-dispatch|virginian-pilot|roanoke times|daily progress)\b",
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


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract structured text from PDF using pdfplumber (tables first, then prose)."""
    parts = []
    try:
        html = fetch_text(url, timeout=timeout)
        soup = BeautifulSoup(html, "html.parser")
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:8]:
                # Try table extraction first — preserves rows/columns
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            cleaned = [str(cell or "").strip() for cell in row]
                            if any(cleaned):
                                parts.append(" | ".join(cleaned))
                else:
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(text.strip())
    except Exception as exc:
        return f"[pdfplumber error: {exc}]"

    full = "\n\n".join(parts)
    return full[:MAX_PDF_TEXT_CHARS] if full else ""


def fetch_article_text(url: str, timeout: int = 15) -> tuple[str, bytes | None]:
    """Fetch a full article page or PDF and return (text, pdf_bytes)."""
    try:
        # Handle PDF caching
        is_pdf = url.lower().split("?", 1)[0].endswith(".pdf")
        if is_pdf:
            PDF_CACHE_DIR.mkdir(exist_ok=True)
            cache_key = hashlib.md5(url.encode()).hexdigest()
            cache_path = PDF_CACHE_DIR / cache_key
            if cache_path.exists():
                print(f"  [cache] {url}")
                pdf_bytes = cache_path.read_bytes()
                return extract_pdf_text(pdf_bytes), pdf_bytes

        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "").lower()
        if "pdf" in content_type or is_pdf:
            pdf_bytes = resp.content
            if len(pdf_bytes) > MAX_PDF_BYTES:
                return "[PDF too large to process]", None
            if is_pdf:
                cache_key = hashlib.md5(url.encode()).hexdigest()
                (PDF_CACHE_DIR / cache_key).write_bytes(pdf_bytes)
            return extract_pdf_text(pdf_bytes), pdf_bytes

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
            tag.decompose()
        body = soup.find("article") or soup.find(id=re.compile(r"content|article|body", re.I))
        raw = (body or soup).get_text(" ", strip=True)
        return re.sub(r"\s{2,}", " ", raw)[:5000], None
    except Exception:
        return "", None


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


def gemini_extract(url: str, article_text: str, api_key: str, pdf_bytes: bytes | None = None) -> dict | None:
    """Call Gemini Flash to extract structured political news data."""
    if not _GENAI_AVAILABLE or not api_key:
        return None
    if not article_text.strip() and not pdf_bytes:
        return None
    try:
        client = _genai.Client(api_key=api_key)
        prompt = GEMINI_PROMPT.replace("{url}", url).replace("{text}", article_text[:4500])
        
        contents = [prompt]
        if pdf_bytes:
            contents.append(_gtypes.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))

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
            article_text, pdf_bytes = fetch_article_text(url) if url else ("", None)
            if not article_text.strip() and not pdf_bytes:
                article_text = combined  # fall back to RSS summary

            gemini_data = gemini_extract(url, article_text, api_key, pdf_bytes=pdf_bytes)
            if gemini_data:
                pdf_info = f" | PDF attached ({len(pdf_bytes)} bytes)" if pdf_bytes else ""
                status = "ok" if "_error" not in gemini_data else f"error: {gemini_data['_error']}"
                print(f"    gemini: {status}{pdf_info} | topics={gemini_data.get('topics')} | politicians={[p.get('name') for p in gemini_data.get('politicians', [])]}")
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


def _name_in_text(name: str, text: str) -> bool:
    """Check whether a politician name (or unambiguous last name) appears in article text."""
    tl = text.lower()
    nl = name.lower().strip()
    if nl in tl:
        return True
    parts = nl.split()
    # Only use last-name-alone for distinctive surnames (>= 6 chars avoids "Jones", "Reid", "Lee")
    last = parts[-1] if parts else ""
    if len(last) >= 6 and last in tl:
        return True
    return False


def monitor_accuracy(
    conn: sqlite3.Connection,
    sample_size: int = 20,
    verbose: bool = False,
) -> dict:
    """
    Spot-check Gemini extractions against their source articles and report
    three tiers of concern:

      text_absent      — politician name not found in article text at all
                         (context confusion or hallucination)
      db_unknown       — name IS in the text but not in our VA politician DB
                         (real person we don't know about yet — expand the DB)
      likely_hallucinated — not in text AND not in DB
                         (strongest hallucination signal)
    """
    rows = conn.execute(
        """
        SELECT article_id, gemini_json, url, title
        FROM va_news
        WHERE gemini_json IS NOT NULL
        ORDER BY fetched_at DESC
        LIMIT ?
        """,
        (sample_size,),
    ).fetchall()

    total_politicians = 0
    text_absent = 0
    db_unknown = 0
    likely_hallucinated = 0
    fetch_failures = 0
    issues: list[dict] = []

    for article_id, gjson, url, title in rows:
        try:
            extracted = json.loads(gjson or "{}")
        except json.JSONDecodeError:
            continue

        politicians = extracted.get("politicians") or []
        if not politicians:
            continue

        article_text = fetch_article_text(url)
        article_text, _ = fetch_article_text(url)
        if not article_text:
            fetch_failures += 1
            if verbose:
                print(f"  [fetch fail] {url[:80]}")
            continue

        for pol in politicians:
            if not isinstance(pol, dict):
                continue
            name = (pol.get("name") or "").strip()
            if not name:
                continue
            total_politicians += 1

            in_text = _name_in_text(name, article_text)
            in_db   = _db_lookup(name) is not None

            if not in_text:
                text_absent += 1
                if not in_db:
                    likely_hallucinated += 1
                    issues.append({
                        "type": "likely_hallucination",
                        "name": name,
                        "url":  url,
                        "title": title,
                    })
                    if verbose:
                        print(f"  LIKELY HALLUCINATION: '{name}' — not in text or DB")
                        print(f"    {url[:80]}")
                else:
                    issues.append({
                        "type": "text_absent",
                        "name": name,
                        "url":  url,
                        "title": title,
                    })
                    if verbose:
                        print(f"  TEXT ABSENT: '{name}' in DB but missing from article text")
            elif not in_db:
                db_unknown += 1
                issues.append({
                    "type": "db_unknown",
                    "name": name,
                    "url":  url,
                    "title": title,
                })
                if verbose:
                    print(f"  DB GAP: '{name}' found in text but not in VA politician DB — "
                          f"consider adding to lookup")

        time.sleep(0.3)

    articles_checked = len(rows) - fetch_failures
    accuracy = (
        round((total_politicians - likely_hallucinated) / total_politicians, 3)
        if total_politicians else 1.0
    )

    report = {
        "articles_sampled":    len(rows),
        "articles_fetched":    articles_checked,
        "fetch_failures":      fetch_failures,
        "total_politicians":   total_politicians,
        "text_absent":         text_absent,
        "db_unknown":          db_unknown,
        "likely_hallucinated": likely_hallucinated,
        "accuracy_rate":       accuracy,
        "issues":              issues,
    }

    print(
        f"\n[monitor] {articles_checked}/{len(rows)} articles fetched | "
        f"{total_politicians} politicians | "
        f"{likely_hallucinated} likely hallucinated | "
        f"accuracy {accuracy:.1%}"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest Virginia political news via Gemini.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--limit", type=int, default=50, help="Max articles to process per run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--monitor", action="store_true",
                        help="Spot-check recent extractions for hallucinations and exit")
    parser.add_argument("--monitor-sample", type=int, default=20,
                        help="Number of recent articles to check with --monitor (default: 20)")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    setup_db(conn)

    if args.monitor:
        report = monitor_accuracy(conn, sample_size=args.monitor_sample, verbose=True)
        conn.close()
        if report["likely_hallucinated"]:
            print(f"\n{report['likely_hallucinated']} likely hallucination(s) found — "
                  f"review issues above and expand va_members_2026.json or the hardcoded list "
                  f"in ingest_va_polls.py if the names are real.")
        return 0

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set in environment.", file=sys.stderr)
        conn.close()
        return 1
    if not _GENAI_AVAILABLE:
        print("ERROR: google-genai not installed. Run: pip install google-genai", file=sys.stderr)
        conn.close()
        return 1

    seen, written = ingest(conn, NEWS_FEEDS, api_key, limit=args.limit, dry_run=args.dry_run)
    conn.close()

    verb = "would write" if args.dry_run else "wrote"
    print(f"\nDone. Saw {seen} Virginia articles, {verb} {written}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
