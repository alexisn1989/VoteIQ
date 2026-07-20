"""
Scrape the upcoming Norfolk Planning Commission Public Hearing agenda and
store it in norfolk_pc_upcoming_agenda for chat context injection.

Rezoning/CUP cases are decided in substance at Planning Commission -- City
Council later acts on the Commission's recommendation. Norfolk runs the
same IQM2 platform as scrape_norfolk_agenda.py's City Council scraper, but
that one only looks for "Formal Session" meetings via lightweight regex
(no Gemini) since Council's outline uses a stable "R-1"/"PH-2" item-ref
scheme. Planning Commission has no such scheme -- items are addresses or
applicant names followed by a free-text description with no case number
at all -- so this uses Gemini extraction instead, same approach as the 5
scraped cities' PC scrapers.

Confirmed live (2026-07-19): Norfolk's calendar lists Planning Commission
twice on the same day -- a "Regular Meeting" (administrative business) and
a "Public Hearing" (the actual rezoning/CUP cases) -- this scraper only
targets "Public Hearing". Confirmed against a real live document (meeting_id
4662, Jul 23 2026 Public Hearing): the outline has roman-numeral sections
(I. Call to Order, II. Architectural Review Board, III. Consent Agenda, IV.
Regular Agenda) with real addresses and applicant names (e.g. "526
Frederick Street - Fire Station No. 8 - Construction of addition", "W.
Ocean View Partners LLC, for a Conditional Use Permit to allow..."). Note
Architectural Review Board items appear on the same joint agenda -- kept
and tagged with their real section name rather than dropped, since they
genuinely are on this posted agenda. No "how to speak" sign-up info found
on this page (unlike Virginia Beach's IQM2 instance, which has a JS
speaker-signup modal) -- how_to_participate is always NULL here.

Usage:
    python scrape_norfolk_pc_agenda.py
    python scrape_norfolk_pc_agenda.py --meeting-id 4662 --meeting-date 2026-07-23 --dry-run
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
from datetime import date, datetime, timedelta
from html import unescape
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
IQM2_BASE = "https://norfolkcityva.iqm2.com/Citizens"
GEMINI_MODEL = "gemini-2.5-flash"
HEADERS = {"User-Agent": "Mozilla/5.0 (VoteIQ/1.0)"}


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


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def find_upcoming_meeting(days: int = 90) -> dict | None:
    """Return {meeting_id, meeting_date} for the soonest Planning
    Commission Public Hearing, or None if not found in the window."""
    today = date.today()
    end = today + timedelta(days=days)
    url = f"{IQM2_BASE}/calendar.aspx?From={today.strftime('%m/%d/%Y')}&To={end.strftime('%m/%d/%Y')}"
    html_bytes = _get(url)
    if not html_bytes:
        return None
    text = html_bytes.decode("utf-8", errors="replace")

    today_str = today.isoformat()
    candidates: list[dict] = []
    seen: set[int] = set()
    for m in re.finditer(r'Detail_Meeting\.aspx\?ID=(\d+)', text):
        mid = int(m.group(1))
        if mid in seen:
            continue
        ctx = text[max(0, m.start() - 500): m.end() + 300]
        if "Planning Commission" not in ctx or "Public Hearing" not in ctx:
            continue
        dm = re.search(r'(\w+ \d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})', ctx)
        if not dm:
            continue
        mdate = _parse_date(dm.group(1))
        if not mdate or mdate < today_str:
            continue
        seen.add(mid)
        candidates.append({"meeting_id": mid, "meeting_date": mdate})

    if not candidates:
        return None
    candidates.sort(key=lambda m: m["meeting_date"])
    return candidates[0]


def fetch_agenda_pdf_url(meeting_id: int) -> str | None:
    url = f"{IQM2_BASE}/Detail_Meeting.aspx?ID={meeting_id}"
    html_bytes = _get(url)
    if not html_bytes:
        return None
    text = html_bytes.decode("utf-8", errors="replace")
    if "not available at this time" in text.lower():
        return None
    m = re.search(r'FileOpen\.aspx\?Type=1&(?:amp;)?ID=(\d+)', text)
    if not m:
        return None
    return f"{IQM2_BASE}/FileOpen.aspx?Type=1&ID={m.group(1)}&Inline=True"


def download_pdf(url: str) -> bytes | None:
    data = _get(url)
    if data and data[:4] == b"%PDF":
        return data
    return None


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 25) -> str:
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed -- run: pip install pypdf")
    reader = _pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = [pg.extract_text() or "" for pg in reader.pages[:max_pages]]
    return "\n".join(pages)


EXTRACT_PROMPT = """You are parsing an upcoming Norfolk, Virginia Planning
Commission PUBLIC HEARING meeting agenda (not yet held).

The document has roman-numeral top-level sections (I. Call to Order, II.
Architectural Review Board, III. Consent Agenda, IV. Regular Agenda,
etc.) with numbered items under each. Items are usually either a street
address followed by a dash and a short description, or an applicant
name followed by "for a Conditional Use Permit / Rezoning / Zoning
Ordinance Text Amendment to ..." with a property address. There is no
case number scheme -- do not invent one.

Return a JSON array. Each object:
{
  "item_ref": "string -- the section numeral + item number as printed,
              e.g. 'IV.2' (construct this from the section and the
              item's position, since there's no case number)",
  "section": "string -- the enclosing section name, e.g. 'Consent
              Agenda', 'Regular Agenda', 'Architectural Review Board'",
  "title": "string -- short description (max 150 chars) -- include the
            applicant/project and the property address",
  "category": "one of: rezoning, conditional-use-permit,
               zoning-text-amendment, architectural-review,
               comprehensive-plan, procedural, other"
}

Rules:
- Skip purely procedural entries with no substantive content (Call to
  Order, Roll Call, Approval of Minutes) unless they are the only
  content.
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
        CREATE TABLE IF NOT EXISTS norfolk_pc_upcoming_agenda (
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
    parser = argparse.ArgumentParser(description="Scrape the upcoming Norfolk Planning Commission agenda")
    parser.add_argument("--meeting-id", type=int, default=None, help="Process a single meeting (for testing)")
    parser.add_argument("--meeting-date", default=None, help="YYYY-MM-DD, required with --meeting-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM norfolk_pc_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    n_removed = conn.execute(
        "DELETE FROM norfolk_pc_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.meeting_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --meeting-id")
        mtg = {"meeting_id": args.meeting_id, "meeting_date": args.meeting_date}
    else:
        print("Looking for the next Norfolk Planning Commission Public Hearing...")
        mtg = find_upcoming_meeting()
        if not mtg:
            print("No upcoming Planning Commission Public Hearing found in the next 90 days.")
            conn.close()
            return
        print(f"Found: {mtg['meeting_date']} (meeting_id={mtg['meeting_id']})")

    pdf_url = fetch_agenda_pdf_url(mtg["meeting_id"])
    if not pdf_url:
        print("  no agenda posted yet -- nothing to do.")
        conn.close()
        return

    pdf_bytes = download_pdf(pdf_url)
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
            INSERT INTO norfolk_pc_upcoming_agenda
                (event_id, meeting_date, item_ref, section, title, category,
                 agenda_url, how_to_participate)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(event_id, item_ref) DO UPDATE SET
                section = excluded.section, title = excluded.title,
                category = excluded.category
        """, (
            mtg["meeting_id"], mtg["meeting_date"], it.get("item_ref", ""),
            it.get("section", ""), it.get("title", ""), it.get("category", "other"),
            f"{IQM2_BASE}/Detail_Meeting.aspx?ID={mtg['meeting_id']}",
        ))
    conn.commit()
    time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
