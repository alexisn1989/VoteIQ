"""
Scrape the upcoming Suffolk Planning Commission agenda and store it in
suffolk_pc_upcoming_agenda for chat context injection.

Rezoning/CUP cases are decided in substance at Planning Commission -- City
Council later acts on the Commission's recommendation. Suffolk's
AgendaCenter (see scrape_suffolk_council.py's docstring for the
UpdateCategoryList enumeration mechanism) runs Planning Commission as a
separate category from City Council: confirmed live (2026-07-19) via the
category checkbox list on the AgendaCenter page itself ("Planning
Commission Meeting Agendas" -> catID=12, vs Council's catID=11), and
verified the same UpdateCategoryList AJAX call works unchanged with
catID=12 substituted in.

This is self-contained (does not import from scrape_suffolk_council.py)
since that module hardcodes CATEGORY_ID_AGENDAS=11 -- duplicating the
~15-line enumeration function with catID=12 is simpler than threading a
parameter through a module built around a single fixed category.

Confirmed against a real live document (doc 3783, Jul 21 2026 -- posted
and genuinely upcoming as of this writing): lettered items (A, B, C...)
under "4. Public Hearings:", each a case number (e.g. "CUP2026-00009",
"CEX2026-00002", "OTA2026-006"), applicant, property address, zoning
map/parcel, voting borough, and Comprehensive Plan land-use designation.
No "how to speak" boilerplate found in the document -- how_to_participate
is always stored NULL here, same as the Council agenda scraper.

Usage:
    python scrape_suffolk_pc_agenda.py
    python scrape_suffolk_pc_agenda.py --doc-id 3783 --meeting-date 2026-07-21 --dry-run
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
from datetime import date
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
SITE_BASE = "https://www.suffolkva.us"
CATEGORY_ID_PLANNING = 12  # "Planning Commission Meeting Agendas" -- confirmed via the category checkbox list
GEMINI_MODEL = "gemini-2.5-flash"
_USER_AGENT = "VoteIQ/1.0 (Virginia civic data; alexisnieuwenhuys89@gmail.com)"


def enumerate_meetings(year: int) -> list[dict]:
    """Return [{doc_id, meeting_date, url_date}] for Planning Commission
    Meeting Agenda documents in the given year."""
    body = (
        f"year={year}&catID={CATEGORY_ID_PLANNING}"
        f"&startDate=&endDate=&term=&prevVersionScreen=false"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{SITE_BASE}/AgendaCenter/UpdateCategoryList",
        data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"  {year}: request failed ({exc})")
        return []

    meetings, seen = [], set()
    for m in re.finditer(r"ViewFile/Agenda/_(\d{2})(\d{2})(\d{4})-(\d+)", html):
        mm, dd, yyyy, doc_id = m.groups()
        if doc_id in seen:
            continue
        seen.add(doc_id)
        meetings.append({
            "doc_id": doc_id,
            "meeting_date": f"{yyyy}-{mm}-{dd}",
            "url_date": f"{mm}{dd}{yyyy}",
        })
    return meetings


def find_upcoming_meeting() -> dict | None:
    today_str = date.today().isoformat()
    meetings = enumerate_meetings(date.today().year)
    future = [m for m in meetings if m["meeting_date"] >= today_str]
    if not future:
        return None
    future.sort(key=lambda m: m["meeting_date"])
    return future[0]


def download_agenda_pdf(doc_id: str, url_date: str) -> bytes | None:
    url = f"{SITE_BASE}/AgendaCenter/ViewFile/Agenda/_{url_date}-{doc_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if data and data[:4] == b"%PDF":
            return data
    except Exception as exc:
        print(f"    download failed: {exc}")
    return None


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 20) -> str:
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed -- run: pip install pypdf")
    reader = _pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = [pg.extract_text() or "" for pg in reader.pages[:max_pages]]
    return "\n".join(pages)


EXTRACT_PROMPT = """You are parsing an upcoming Suffolk, Virginia Planning
Commission meeting AGENDA (not yet held). Extracted text may have odd
spacing from the PDF's font kerning (e.g. "Ju ly 21 , 202 6") -- read
past that, it doesn't change the meaning.

The document has numbered top-level items (1. Call to Order, 2. Election
of Officers, 3. Approval of Minutes, 4. Public Hearings:) with lettered
sub-items (A, B, C...) under Public Hearings, each a case with a type
(CONDITIONAL USE PERMIT REQUEST, EXCEPTION REQUEST, ORDINANCE TEXT
AMENDMENT, REZONING REQUEST, etc.), a case number (e.g. "CUP2026-00009",
"CEX2026-00002", "OTA2026-006"), an applicant, a property address if
site-specific, and zoning/voting-borough details.

Return a JSON array. Each object:
{
  "item_ref": "string -- the case number if present (e.g. 'CUP2026-00009'),
              otherwise the item letter (e.g. '4.F')",
  "section": "string -- the enclosing numbered section, e.g.
              'Public Hearings'",
  "title": "string -- short description (max 150 chars) -- include the
            case type and property address/location if present",
  "category": "one of: conditional-use-permit, rezoning, exception,
               ordinance-text-amendment, comprehensive-plan, procedural, other"
}

Rules:
- Skip purely procedural entries with no substantive content (Call to
  Order, Invocation, Pledge of Allegiance, Roll Call, Election of
  Officers, Approval of Minutes) unless they are the only content.
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
        CREATE TABLE IF NOT EXISTS suffolk_pc_upcoming_agenda (
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
    parser = argparse.ArgumentParser(description="Scrape the upcoming Suffolk Planning Commission agenda")
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
        conn.execute("DELETE FROM suffolk_pc_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    today_str = date.today().isoformat()
    n_removed = conn.execute(
        "DELETE FROM suffolk_pc_upcoming_agenda WHERE meeting_date < ?", (today_str,)
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.doc_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --doc-id")
        yyyy, mm, dd = args.meeting_date.split("-")
        mtg = {"doc_id": args.doc_id, "meeting_date": args.meeting_date, "url_date": f"{mm}{dd}{yyyy}"}
    else:
        print("Looking for the next Suffolk Planning Commission meeting document...")
        mtg = find_upcoming_meeting()
        if not mtg:
            print("No upcoming meeting document posted yet -- nothing to do.")
            conn.close()
            return
        print(f"Found: {mtg['meeting_date']} (doc_id={mtg['doc_id']})")

    agenda_url = f"{SITE_BASE}/AgendaCenter/ViewFile/Agenda/_{mtg['url_date']}-{mtg['doc_id']}"
    pdf_bytes = download_agenda_pdf(mtg["doc_id"], mtg["url_date"])
    if not pdf_bytes:
        print("  agenda PDF download failed")
        conn.close()
        return

    text = extract_pdf_text(pdf_bytes)
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
            INSERT INTO suffolk_pc_upcoming_agenda
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
