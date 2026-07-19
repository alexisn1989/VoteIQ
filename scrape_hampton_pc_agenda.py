"""
Scrape the upcoming Hampton Planning Commission agenda and store it in
hampton_pc_upcoming_agenda for chat context injection.

Rezoning/use-permit cases are decided in substance at Planning Commission
-- City Council later acts on the Commission's recommendation. Hampton
runs Legistar, same as scrape_hampton_agenda.py's City Council scraper --
confirmed live (2026-07-19) that "Planning Commission" is its own distinct
meeting type on the same Calendar.aspx, with the identical Agenda-link
(View.ashx?M=A) mechanism. Simpler than Council's Regular Meeting/Work
Session pairing: Planning Commission's own pre-meeting work session is a
section *inside* the same agenda document, not a separate calendar entry,
so there's no title-variant exclusion needed here.

Confirmed against a real live document (meeting_id 1404715, Jul 16 2026):
Roman-numeral sections (I. Call to Order ... V. Public Hearing Items ...
VIII. Adjournment) with real file numbers (e.g. "26-0222", "26-0218") and
full property addresses under Public Hearing Items. Also confirmed a
real, stable "Protocol for Public Input at Planning Commission Meetings"
boilerplate section (3-minute speaking limit, sign-up sheet process) --
extracted via a cheap one-time regex, same approach as
scrape_hampton_agenda.py's Council participation-info extraction (with
a different anchor phrase -- this is worded differently from Council's).

Usage:
    python scrape_hampton_pc_agenda.py
    python scrape_hampton_pc_agenda.py --meeting-id 1404715 --guid AE59CD00-800C-45CC-A859-E0C265BFCCE6 --meeting-date 2026-07-16 --dry-run
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
LEGISTAR_BASE = "https://hampton.legistar.com"
GEMINI_MODEL = "gemini-2.5-flash"


def find_upcoming_meeting() -> dict | None:
    """Return the soonest 'Planning Commission' meeting with meeting_date
    >= today AND an Agenda (M=A) link already posted, or None if Hampton
    hasn't posted one yet."""
    from playwright.sync_api import sync_playwright

    today_str = date.today().isoformat()
    candidates: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{LEGISTAR_BASE}/Calendar.aspx", timeout=60000, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=40000)
        page.click("#ctl00_ContentPlaceHolder1_lstYears_Arrow")
        page.wait_for_timeout(500)
        page.click(f'#ctl00_ContentPlaceHolder1_lstYears_DropDown >> text="{date.today().year}"')
        page.wait_for_load_state("networkidle", timeout=40000)
        page.wait_for_timeout(1000)

        for tr in page.query_selector_all("tr"):
            txt = tr.inner_text()
            if "Planning Commission" not in txt.split("\t")[0]:
                continue
            date_m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", txt)
            if not date_m:
                continue
            try:
                mdate = datetime.strptime(date_m.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            if mdate < today_str:
                continue
            agenda_link = tr.query_selector('a[href*="M=A"]')
            if not agenda_link:
                continue  # not posted yet
            href = agenda_link.get_attribute("href") or ""
            m = re.search(r"ID=(\d+)&GUID=([\w-]+)", href)
            if not m:
                continue
            candidates.append({
                "meeting_id": m.group(1), "guid": m.group(2), "meeting_date": mdate,
            })
        browser.close()

    if not candidates:
        return None
    candidates.sort(key=lambda m: m["meeting_date"])
    return candidates[0]


def download_agenda_pdf(meeting_id: str, guid: str) -> bytes | None:
    url = f"{LEGISTAR_BASE}/View.ashx?M=A&ID={meeting_id}&GUID={guid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
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


# ── Standing "how to speak" instructions -- fixed template, cheap regex ──

def extract_participation_info(text: str) -> str | None:
    m = re.search(
        r"(Protocol for Public Input at Planning Commission Meetings:.{0,600}?"
        r"made by previous\s*\n?speakers\.)",
        text, re.S,
    )
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()


EXTRACT_PROMPT = """You are parsing an upcoming Hampton, Virginia Planning
Commission meeting AGENDA (not yet held).

The document has Roman-numeral sections (I. Call to Order, II. Roll
Call, III. Approval of Minutes, IV. Community Development Director's
Report, V. Public Hearing Items, VI. Items by the Public, VII. Matters
by the Commission, VIII. Adjournment). Substantive items are under
Public Hearing Items, each with a file number (e.g. "26-0222",
"26-0218") and a title that usually names a property address, applicant,
and the type of request (rezoning, use permit, subdivision, etc.). A
"WORK SESSION" block near the top just previews the same items -- don't
double-count it, only extract from the main "MEETING AGENDA" section.

Return a JSON array. Each object:
{
  "item_ref": "string -- the file number, e.g. '26-0222'",
  "section": "string -- the enclosing section, e.g. 'Public Hearing
              Items', 'Community Development Director's Report'",
  "title": "string -- short description (max 150 chars) -- include the
            property address/location and request type if present",
  "category": "one of: rezoning, use-permit, subdivision,
               comprehensive-plan, procedural, other"
}

Rules:
- Skip purely procedural entries with no substantive content (Call to
  Order, Roll Call, Approval of Minutes if just a minutes-approval
  reference, Items by the Public, Matters by the Commission, Adjournment)
  unless they are the only content.
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
        CREATE TABLE IF NOT EXISTS hampton_pc_upcoming_agenda (
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
    parser = argparse.ArgumentParser(description="Scrape the upcoming Hampton Planning Commission agenda")
    parser.add_argument("--meeting-id", default=None, help="Process a single meeting (for testing)")
    parser.add_argument("--guid", default=None)
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
        conn.execute("DELETE FROM hampton_pc_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    today_str = date.today().isoformat()
    n_removed = conn.execute(
        "DELETE FROM hampton_pc_upcoming_agenda WHERE meeting_date < ?", (today_str,)
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.meeting_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --meeting-id")
        mtg = {"meeting_id": args.meeting_id, "guid": args.guid, "meeting_date": args.meeting_date}
    else:
        print("Looking for the next Hampton Planning Commission meeting with a posted agenda...")
        mtg = find_upcoming_meeting()
        if not mtg:
            print("No upcoming meeting with a posted agenda yet -- nothing to do.")
            conn.close()
            return
        print(f"Found: {mtg['meeting_date']} (meeting_id={mtg['meeting_id']})")

    agenda_url = f"{LEGISTAR_BASE}/View.ashx?M=A&ID={mtg['meeting_id']}&GUID={mtg['guid']}"
    pdf_bytes = download_agenda_pdf(mtg["meeting_id"], mtg["guid"])
    if not pdf_bytes:
        print("  agenda PDF download failed")
        conn.close()
        return

    text = extract_pdf_text(pdf_bytes)
    if len(text) < 200:
        print("  agenda text too short -- skipping")
        conn.close()
        return

    participate = extract_participation_info(text)
    if participate:
        print(f"  participation info: {participate[:100]}...")
    else:
        print("  no participation instructions found in this agenda")

    if args.dry_run:
        print(f"  [dry-run] {len(text)} chars extracted, would send to Gemini")
        conn.close()
        return

    items = extract_items_gemini(text, api_key)
    print(f"  extracted {len(items)} agenda items")

    for it in items:
        conn.execute("""
            INSERT INTO hampton_pc_upcoming_agenda
                (event_id, meeting_date, item_ref, section, title, category,
                 agenda_url, how_to_participate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, item_ref) DO UPDATE SET
                section = excluded.section, title = excluded.title,
                category = excluded.category, how_to_participate = excluded.how_to_participate
        """, (
            int(mtg["meeting_id"]), mtg["meeting_date"], it.get("item_ref", ""),
            it.get("section", ""), it.get("title", ""), it.get("category", "other"),
            agenda_url, participate,
        ))
    conn.commit()
    time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
