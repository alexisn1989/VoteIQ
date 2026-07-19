"""
Scrape the upcoming Portsmouth Planning Commission agenda and store it in
portsmouth_pc_upcoming_agenda for chat context injection.

Rezoning/use-permit cases are decided in substance at Planning Commission --
City Council later acts on the Commission's recommendation (confirmed on a
real Jul 7, 2026 agenda: "ITEMS REVIEWED TODAY WILL BE PRESENTED TO CITY
COUNCIL FOR ACTION AT THEIR August 11, 2026, PUBLIC HEARING", captured here
as council_hearing_date, same idea as Newport News PC's cross-reference).

Portsmouth's Planning Commission does NOT live on Granicus (confirmed live,
2026-07-19: sweeping view_id 1-29 on portsmouthva.granicus.com only found
"City Council LiveStream" channels, nothing Planning-specific) or on the
WebLink archive scrape_portsmouth_council.py uses (that repository's root
folder is Portsmouth-City-Clerk only -- no Planning sibling). It lives on
the city's own CivicPlus website as a plain, manually-curated link list at
/planning-commission-agendas-minutes, with files hosted at
content.civicplus.com/api/assets/va-portsmouth/<uuid> -- no queryable
enumeration API, just a flat reverse-chronological page of links. Filenames
are inconsistent across months ("PCAgenda07072026" some months, "November
18, 2025 Planning Commission Agenda (PDF)" others), so this uses a flexible
regex to pull a date out of either style, and explicitly excludes "Packet",
"with Actions", "Minutes", and ".docx" links to keep just the plain agenda
PDF. There's no way to tell "upcoming" from "past" on this page except by
comparing the parsed date to today -- confirmed as of this writing the most
recent posted is Jul 7, 2026 (already past; Portsmouth PC meets 1st Tuesday
of the month, so Aug 4 is next but not posted yet), same "posted shortly
before the meeting" pattern as the other cities.

The department's own page (not the PDF) states a real public-comment
process (pre-register by email or phone before the meeting) -- captured
here as a fixed constant rather than a fragile per-PDF regex, since it's a
standing page-level fact confirmed live, not something that varies by
meeting document.

Usage:
    python scrape_portsmouth_pc_agenda.py
    python scrape_portsmouth_pc_agenda.py --url "https://content.civicplus.com/api/assets/va-portsmouth/292e921b-68ef-4aca-b4d4-f059d5e5dd75" --meeting-date 2026-07-07 --dry-run
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
LISTING_URL = "https://www.portsmouthva.gov/planning-commission-agendas-minutes"
GEMINI_MODEL = "gemini-2.5-flash"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Confirmed live (2026-07-19) on the Planning Commission department page --
# a standing process, not per-meeting, so kept as a constant rather than
# re-extracted from each PDF.
HOW_TO_PARTICIPATE = (
    "Members of the public may comment on specific agenda items by emailing "
    "planningcommission@portsmouthva.gov prior to the hearing date. To speak "
    "during the meeting, pre-register by emailing the Planning Commission or "
    "calling 757-393-8836 prior to the meeting."
)

_EXCLUDE_RE = re.compile(r"packet|actions|minutes|\.docx", re.IGNORECASE)
_DATE_STYLE1_RE = re.compile(r"PCAgenda(\d{2})(\d{2})(\d{4})", re.IGNORECASE)
_DATE_STYLE2_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s+Planning Commission Agenda", re.IGNORECASE
)


def _parse_agenda_link(text: str) -> str | None:
    """Return an ISO date if this link's text looks like a plain Agenda
    (not Packet/Actions/Minutes/docx), else None."""
    if _EXCLUDE_RE.search(text):
        return None
    m = _DATE_STYLE1_RE.search(text)
    if m:
        mm, dd, yyyy = m.groups()
        try:
            return f"{yyyy}-{mm}-{dd}"
        except ValueError:
            return None
    m = _DATE_STYLE2_RE.search(text)
    if m:
        month_name, dd, yyyy = m.groups()
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(f"{month_name} {dd} {yyyy}", fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def find_upcoming_meeting() -> dict | None:
    """Return {agenda_url, meeting_date} for the most recently posted plain
    Agenda link IF its date is >= today, else None (not posted yet)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(LISTING_URL, timeout=45000, wait_until="networkidle")
        page.wait_for_timeout(1500)

        candidates: list[dict] = []
        for a in page.query_selector_all("a"):
            t = (a.inner_text() or "").strip()
            href = a.get_attribute("href") or ""
            if "content.civicplus.com" not in href:
                continue
            mdate = _parse_agenda_link(t)
            if mdate:
                candidates.append({"agenda_url": href, "meeting_date": mdate})
        browser.close()

    if not candidates:
        return None
    candidates.sort(key=lambda m: m["meeting_date"], reverse=True)
    latest = candidates[0]
    if latest["meeting_date"] < date.today().isoformat():
        return None
    return latest


def download_pdf(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers=HEADERS)
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


EXTRACT_PROMPT = """You are parsing an upcoming Portsmouth, Virginia
Planning Commission meeting AGENDA (not yet held).

The document has a CALL TO ORDER / ROLL CALL header, then sections like
"DEFERRED ITEMS" and "PUBLIC HEARING ITEMS" containing numbered cases,
each with a case number (e.g. "UP-26-03"), a project/subdivision name in
parentheses, an applicant, a property address, zoning district, and tax
map parcel. Items often end with a note like "ITEMS REVIEWED TODAY WILL
BE PRESENTED TO CITY COUNCIL FOR ACTION AT THEIR <date>, PUBLIC HEARING"
-- capture that date if present.

Return a JSON array. Each object:
{
  "item_ref": "string -- the case number (e.g. 'UP-26-03')",
  "section": "string -- 'Deferred Items' or 'Public Hearing Items'",
  "title": "string -- short description (max 150 chars) -- include the
            project name, request type, and property address",
  "category": "one of: use-permit, rezoning, subdivision,
               comprehensive-plan, procedural, other",
  "council_hearing_date": "string YYYY-MM-DD if the document states a
              City Council hearing date for this item, otherwise null --
              do not guess this, only use it if genuinely stated"
}

Rules:
- Skip purely procedural entries with no substantive content (Call to
  Order, Roll Call, Transcript of previous hearing, Announcement of
  future meetings) unless they are the only content.
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
        CREATE TABLE IF NOT EXISTS portsmouth_pc_upcoming_agenda (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id            INTEGER NOT NULL,
            meeting_date        TEXT NOT NULL,
            item_ref            TEXT,
            section             TEXT,
            title               TEXT,
            category            TEXT,
            agenda_url          TEXT,
            how_to_participate  TEXT,
            council_hearing_date TEXT,
            lat                 REAL,
            lng                 REAL,
            geocoded_address    TEXT,
            scraped_at          TEXT DEFAULT (datetime('now')),
            UNIQUE(event_id, item_ref)
        )
    """)
    conn.commit()


def _event_id_from_date(meeting_date: str) -> int:
    """No numeric event id exists on this platform -- derive a stable one
    from the ISO date (YYYYMMDD) so UNIQUE(event_id, item_ref) still works."""
    return int(meeting_date.replace("-", ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape the upcoming Portsmouth Planning Commission agenda")
    parser.add_argument("--url", default=None, help="Process a single agenda URL (for testing)")
    parser.add_argument("--meeting-date", default=None, help="YYYY-MM-DD, required with --url")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM portsmouth_pc_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    n_removed = conn.execute(
        "DELETE FROM portsmouth_pc_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.url:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --url")
        mtg = {"agenda_url": args.url, "meeting_date": args.meeting_date}
    else:
        print("Looking for the next Portsmouth Planning Commission agenda...")
        mtg = find_upcoming_meeting()
        if not mtg:
            print("No upcoming agenda posted yet -- nothing to do.")
            conn.close()
            return
        print(f"Found: {mtg['meeting_date']}")

    pdf_bytes = download_pdf(mtg["agenda_url"])
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

    event_id = _event_id_from_date(mtg["meeting_date"])
    for it in items:
        conn.execute("""
            INSERT INTO portsmouth_pc_upcoming_agenda
                (event_id, meeting_date, item_ref, section, title, category,
                 agenda_url, how_to_participate, council_hearing_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, item_ref) DO UPDATE SET
                section = excluded.section, title = excluded.title,
                category = excluded.category,
                council_hearing_date = excluded.council_hearing_date
        """, (
            event_id, mtg["meeting_date"], it.get("item_ref", ""),
            it.get("section", ""), it.get("title", ""), it.get("category", "other"),
            mtg["agenda_url"], HOW_TO_PARTICIPATE, it.get("council_hearing_date"),
        ))
    conn.commit()
    time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
