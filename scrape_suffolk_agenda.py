"""
Scrape the upcoming Suffolk City Council agenda and store it in
suffolk_upcoming_agenda for chat context injection.

Suffolk's AgendaCenter (see scrape_suffolk_council.py for the
UpdateCategoryList enumeration mechanism) publishes one PDF per meeting
that starts as a pre-meeting agenda and gets updated *in place* after
the meeting with vote outcomes written directly beneath each item. This
script reuses that same enumeration/download/extract-text machinery,
but instead of walking every historical year, it looks only at the
soonest meeting_date that is still >= today and treats that document's
current PDF content as the upcoming agenda.

Confirmed against the real live document (doc 3780, 2026-07-15 -- the
most recent one posted as of this writing): Suffolk's numbering is
flat integers (no lettered sub-items like Chesapeake's), and unlike
Chesapeake's agenda PDFs, there is no "how to speak at this meeting"
boilerplate anywhere in the document -- so how_to_participate is always
stored NULL here rather than fabricated. Also confirmed: several items
are pure section-header placeholders with no real content ("22.
Ordinances -- No City Council action required."); the extraction prompt
is told to skip those.

As of 2026-07-18 no meeting document with meeting_date >= today has
been posted yet (Suffolk's council meets 1st/3rd Wednesdays; the next
expected meeting is 2026-08-05, but AgendaCenter only publishes the PDF
a short time beforehand) -- this is expected, not a bug, and the script
exits cleanly with "no upcoming meeting posted yet" in that case. The
Render cron re-runs this periodically so it picks the document up as
soon as Suffolk posts it.

Usage:
    python scrape_suffolk_agenda.py
    python scrape_suffolk_agenda.py --doc-id 3780 --meeting-date 2026-07-15 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from scrape_suffolk_council import (
    SITE_BASE,
    download_agenda_pdf,
    enumerate_meetings,
    extract_pdf_text,
)

load_dotenv()

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
GEMINI_MODEL = "gemini-2.5-flash"


def find_upcoming_meeting() -> dict | None:
    """Return the soonest meeting with meeting_date >= today, or None if
    Suffolk hasn't posted one yet."""
    today = date.today()
    meetings = enumerate_meetings(today.year, today.year + 1)
    today_str = today.isoformat()
    future = [m for m in meetings if m["meeting_date"] >= today_str]
    if not future:
        return None
    future.sort(key=lambda m: m["meeting_date"])
    return future[0]


EXTRACT_PROMPT = """You are parsing an upcoming Suffolk, Virginia City
Council meeting AGENDA (not yet held). Items are numbered as a flat
list (1., 2., 3. ...), often with a leading category label before a
dash, e.g. "7. Consent Agenda - An ordinance creating a..." or
"18. Public Hearing - An ordinance authorizing...".

Some items may already show a vote outcome line beneath them (e.g.
"Approved 8-0") if this document is being re-checked shortly after the
meeting actually occurred -- IGNORE any such outcome text and extract
only the item's own description.

Return a JSON array. Each object:
{
  "item_ref": "string -- the item number as it appears, e.g. '19'",
  "section": "string -- the leading label before the dash if present,
              e.g. 'Consent Agenda', 'Public Hearing', 'Ordinances',
              'Resolutions', 'New Business', 'Motion', 'Staff Report' --
              empty string if the item has no such label",
  "title": "string -- short description (max 150 chars) -- include the
            case/permit number if present (e.g. 'CUP2026-010')",
  "category": "one of: public-hearing, ordinance, resolution, consent,
               presentation, appointment, procedural, other"
}

Rules:
- Skip pure section-header placeholders with no real content (items
  whose entire text is just a label like "Ordinances" or "Resolutions"
  followed by "No City Council action required" and nothing else).
- Skip purely procedural items with no substantive content (Call to
  Order, Invocation, Adjournment, Announcements, Agenda Speakers,
  Non-Agenda Speakers) unless they are the only content in the document.
- Return ONLY a valid JSON array -- no markdown fences, no explanation.
- If nothing parses, return [].
"""


def extract_items_gemini(agenda_text: str, api_key: str) -> list[dict]:
    if not _GENAI_OK:
        print("    ERROR: google-genai not installed. pip install google-genai")
        return []
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
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"    Gemini error: {exc}")
        return []


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS suffolk_upcoming_agenda (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id           INTEGER NOT NULL,
            meeting_date       TEXT NOT NULL,
            item_ref           TEXT,
            section            TEXT,
            title              TEXT,
            category           TEXT,
            agenda_url         TEXT,
            how_to_participate TEXT,
            scraped_at         TEXT DEFAULT (datetime('now')),
            UNIQUE(event_id, item_ref)
        )
    """)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape the upcoming Suffolk council agenda")
    parser.add_argument("--doc-id", default=None, help="Process a single doc_id (for testing)")
    parser.add_argument("--meeting-date", default=None, help="YYYY-MM-DD, required with --doc-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM suffolk_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    today_str = date.today().isoformat()
    n_removed = conn.execute(
        "DELETE FROM suffolk_upcoming_agenda WHERE meeting_date < ?", (today_str,)
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.doc_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --doc-id")
        yyyy, mm, dd = args.meeting_date.split("-")
        mtg = {
            "doc_id": args.doc_id,
            "meeting_date": args.meeting_date,
            "url_date": f"{mm}{dd}{yyyy}",
        }
    else:
        print("Looking for the next Suffolk City Council meeting document...")
        mtg = find_upcoming_meeting()
        if not mtg:
            print("No upcoming meeting document posted yet -- nothing to do.")
            conn.close()
            return
        print(f"Found: {mtg['meeting_date']} (doc_id={mtg['doc_id']})")

    agenda_url = f"{SITE_BASE}/AgendaCenter/ViewFile/Agenda/_{mtg['url_date']}-{mtg['doc_id']}"
    pdf_path = download_agenda_pdf(mtg["doc_id"], mtg["url_date"])
    if not pdf_path:
        print("  agenda PDF download failed")
        conn.close()
        return

    text = extract_pdf_text(pdf_path)
    if len(text) < 200:
        print("  agenda text too short -- skipping")
        conn.close()
        return

    if args.dry_run:
        print(f"  [dry-run] {len(text)} chars extracted, would send to Gemini")
        conn.close()
        return

    items = extract_items_gemini(text, api_key)
    print(f"  extracted {len(items)} agenda items")

    for it in items:
        conn.execute("""
            INSERT INTO suffolk_upcoming_agenda
                (event_id, meeting_date, item_ref, section, title, category,
                 agenda_url, how_to_participate)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(event_id, item_ref) DO UPDATE SET
                section = excluded.section, title = excluded.title,
                category = excluded.category
        """, (
            int(mtg["doc_id"]), mtg["meeting_date"], it.get("item_ref", ""),
            it.get("section", ""), it.get("title", ""), it.get("category", "other"),
            agenda_url,
        ))
    conn.commit()
    time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
