"""
Scrape upcoming Chesapeake Planning Commission agendas and store them in
chesapeake_pc_upcoming_agenda for chat context injection.

Rezoning/CUP cases are decided in substance at Planning Commission -- City
Council later votes on the Commission's recommendation. Confirmed live
(2026-07-19) that Chesapeake's Granicus instance runs Planning Commission
on entirely separate view_ids from City Council's view_id=29 (found by
sweeping view_id 1-44): view_id=35 is a dedicated "Upcoming Events" view
(nothing but upcoming/live meetings -- unlike Council's view_id=29, which
mixes past and future in one table and needs a MinutesViewer.php-absence
check to tell them apart, view_id=35 contains ONLY upcoming events, so
every row here is fair game with no filtering needed) and view_id=36 is
the matching historical archive (not used by this script).

Confirmed real live data: two meetings currently posted -- a "Planning
Commission Work Session" (Jul 22, oddly dated 2025 in the PDF itself, a
recurring orientation/training session with no real case content -- the
extraction prompt's "skip pure procedural" rule naturally produces an
empty item list for this one) and a real "Planning Commission Meeting"
(Aug 12, 2026, event_id 4338) with 5 real public-hearing cases: rezonings
and conditional-use-permits, each with a PLN case number, applicant,
address, and tax map parcel. AgendaViewer.php returns a raw PDF stream
directly (same as Council's, unlike Portsmouth's HTML-wrapper
indirection).

No stable regex anchor exists for a "how to speak" section here (unlike
Council's Speaker Card boilerplate) -- the closest is a generic "all
interested parties are invited to attend" notice with an accommodation-
request contact, worded differently each time. Rather than force a
fragile regex, this bundles how_to_participate into the same Gemini call
as the item extraction (same approach as scrape_portsmouth_agenda.py's
scanned-PDF handling), asking Gemini to report it only if genuinely
present.

Usage:
    python scrape_chesapeake_pc_agenda.py
    python scrape_chesapeake_pc_agenda.py --event-id 4338 --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

try:
    import pypdf as _pypdf
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
GRANICUS_BASE = "https://chesapeake.granicus.com"
VIEW_ID = 35  # "Current Upcoming View - Planning" -- confirmed live, separate from Council's view_id=29
GEMINI_MODEL = "gemini-2.5-flash"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

_ROW_RE = re.compile(r'<tr class="listingRow">(.*?)</tr>', re.S)
_NAME_RE = re.compile(r'headers="Name"[^>]*>\s*(.+?)\s*</td>', re.S)
_DATE_RE = re.compile(r'([A-Za-z]+&nbsp;\s*\d{1,2},&nbsp;\d{4})')
_AGENDA_RE = re.compile(r'AgendaViewer\.php\?view_id=(\d+)&(?:amp;)?event_id=(\d+)')


def _get(url: str, retries: int = 3) -> bytes | None:
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    return r.read()
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
            else:
                print(f"  WARN: {url}: {exc}")
    return None


def _parse_granicus_date(raw: str) -> str | None:
    raw = raw.replace("&nbsp;", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def fetch_upcoming_meetings() -> list[dict]:
    """view_id=35 is a dedicated upcoming-only view -- every row is fair
    game, no MinutesViewer.php filtering needed (unlike Council)."""
    url = f"{GRANICUS_BASE}/ViewPublisher.php?view_id={VIEW_ID}"
    html_bytes = _get(url)
    if not html_bytes:
        return []
    html = html_bytes.decode("utf-8", errors="replace")

    out: list[dict] = []
    for row_m in _ROW_RE.finditer(html):
        row = row_m.group(1)
        agenda_m = _AGENDA_RE.search(row)
        if not agenda_m:
            continue  # no agenda posted yet for this meeting
        date_m = _DATE_RE.search(row)
        mdate = _parse_granicus_date(date_m.group(1)) if date_m else None
        if not mdate:
            continue
        name_m = _NAME_RE.search(row)
        title = re.sub(r"<[^>]+>", "", name_m.group(1)).strip() if name_m else "Planning Commission Meeting"
        out.append({
            "event_id": int(agenda_m.group(2)),
            "meeting_date": mdate,
            "title": title,
            "agenda_url": f"{GRANICUS_BASE}/AgendaViewer.php?view_id={agenda_m.group(1)}&event_id={agenda_m.group(2)}",
        })
    return out


def download_agenda_pdf(agenda_url: str) -> bytes | None:
    data = _get(agenda_url)
    if data and data[:4] == b"%PDF":
        return data
    return None


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 25) -> str:
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed -- run: pip install pypdf")
    reader = _pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = [pg.extract_text() or "" for pg in reader.pages[:max_pages]]
    return "\n".join(pages)


EXTRACT_PROMPT = """You are parsing an upcoming Chesapeake, Virginia
Planning Commission meeting AGENDA (not yet held).

The document has lettered top-level sections (A. Call to Order, B.
Pledge of Allegiance, C. Invocation, D. Roll Call, E. Approval of
Minutes, F. Public Hearing Regular Items, G. Business Items, H.
Adjournment). Substantive cases are numbered (1., 2., 3. ...) under
Public Hearing Regular Items, each a multi-line block with a PLN case
number (e.g. "PLN-REZ-2025-010", "PLN-USE-2024-001"), PROJECT name,
APPLICANTS/OWNERS, PROPOSAL description, LOCATION (a street address),
and TAX MAP PARCELS. Some meetings are pure training/orientation
sessions (e.g. a "Work Session" agenda about planning fundamentals)
with no real case content -- if so, return an empty items array.

Return a single JSON object:
{
  "items": [
    {
      "item_ref": "string -- the PLN case number (e.g. 'PLN-REZ-2025-010'),
                  or the section letter + number if no case number",
      "section": "string -- the enclosing section, e.g. 'Public Hearing
                  Regular Items', 'Business Items'",
      "title": "string -- short description (max 150 chars) -- include
                the project name and the proposal type (rezoning/CUP/
                special exception) and property address/location",
      "category": "one of: rezoning, conditional-use-permit,
                   special-exception, comprehensive-plan, procedural, other"
    }
  ],
  "how_to_participate": "string or null -- any 'how to speak at this
                         meeting' / public comment / accommodation-
                         request instructions visible in the document.
                         null if no such text appears -- do not invent
                         this."
}

Rules:
- Skip purely procedural entries with no substantive content (Call to
  Order, Pledge of Allegiance, Invocation, Roll Call, Approval of
  Minutes, Adjournment) unless they are the only content.
- Return ONLY the JSON object -- no markdown fences, no explanation.
- If nothing parses, return {"items": [], "how_to_participate": null}.
"""


def extract_agenda_gemini(agenda_text: str, api_key: str) -> dict:
    if not _GENAI_OK:
        print("    ERROR: google-genai not installed. pip install google-genai")
        return {"items": [], "how_to_participate": None}
    client = _genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[EXTRACT_PROMPT, "\n\n---AGENDA---\n\n" + agenda_text],
            config=_gtypes.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                http_options=_gtypes.HttpOptions(timeout=60_000),
            ),
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        if isinstance(data, dict):
            return {"items": data.get("items") or [], "how_to_participate": data.get("how_to_participate")}
        return {"items": [], "how_to_participate": None}
    except Exception as exc:
        print(f"    Gemini error: {exc}")
        return {"items": [], "how_to_participate": None}


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chesapeake_pc_upcoming_agenda (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id           INTEGER NOT NULL,
            meeting_date       TEXT NOT NULL,
            item_ref           TEXT,
            section            TEXT,
            title              TEXT,
            category           TEXT,
            agenda_url         TEXT,
            how_to_participate TEXT,
            lat                REAL,
            lng                REAL,
            geocoded_address   TEXT,
            scraped_at         TEXT DEFAULT (datetime('now')),
            UNIQUE(event_id, item_ref)
        )
    """)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape upcoming Chesapeake Planning Commission agendas")
    parser.add_argument("--event-id", type=int, help="Process a single event_id (for testing)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM chesapeake_pc_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    n_removed = conn.execute(
        "DELETE FROM chesapeake_pc_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.event_id:
        meetings = [{
            "event_id": args.event_id, "meeting_date": "unknown",
            "title": "Planning Commission Meeting",
            "agenda_url": f"{GRANICUS_BASE}/AgendaViewer.php?view_id={VIEW_ID}&event_id={args.event_id}",
        }]
    else:
        print("Fetching Chesapeake Planning Commission upcoming meetings...")
        meetings = fetch_upcoming_meetings()
        print(f"Found {len(meetings)} upcoming meeting(s) with a posted agenda")

    for mtg in meetings:
        print(f"\n  {mtg['meeting_date']} (event_id={mtg['event_id']}): {mtg['title']}")
        pdf_bytes = download_agenda_pdf(mtg["agenda_url"])
        if not pdf_bytes:
            print("    agenda PDF download failed -- skipping")
            continue

        text = extract_pdf_text(pdf_bytes)
        if len(text) < 200:
            print("    agenda text too short -- skipping")
            continue

        if args.dry_run:
            print(f"    [dry-run] {len(text)} chars extracted, would send to Gemini")
            continue

        result = extract_agenda_gemini(text, api_key)
        items = result["items"]
        participate = result["how_to_participate"]
        print(f"    extracted {len(items)} agenda items")
        if participate:
            print(f"    participation info: {participate[:100]}...")

        for it in items:
            conn.execute("""
                INSERT INTO chesapeake_pc_upcoming_agenda
                    (event_id, meeting_date, item_ref, section, title, category,
                     agenda_url, how_to_participate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, item_ref) DO UPDATE SET
                    section = excluded.section, title = excluded.title,
                    category = excluded.category, how_to_participate = excluded.how_to_participate
            """, (
                mtg["event_id"], mtg["meeting_date"], it.get("item_ref", ""),
                it.get("section", ""), it.get("title", ""), it.get("category", "other"),
                mtg["agenda_url"], participate,
            ))
        conn.commit()
        time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
