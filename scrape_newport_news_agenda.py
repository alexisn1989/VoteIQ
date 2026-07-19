"""
Scrape the upcoming Newport News City Council agenda and store it in
newport_news_upcoming_agenda for chat context injection.

Newport News runs CivicWeb (iCompass/Diligent), same platform as
scrape_newport_news_council.py's historical vote scraper -- but that
one uses the site's separate VotingRecords.aspx tool, which only covers
already-recorded votes. This script instead works off individual
MeetingInformation.aspx?Org=Cal&Id=N pages.

Confirmed via live reconnaissance (2026-07-18/19) that CivicWeb's own
"UPCOMING MEETINGS" sidebar widget (present on every meeting detail
page) is plain text with NO links or IDs -- e.g. it names "City Council
Regular Meeting - Aug 11, 2026" but gives no way to reach that meeting's
page directly. However, meeting IDs for City Council Regular
Meeting/Work Session pairs increment in lockstep with chronological
order (confirmed: Jun 09 2026 = ids 4229/4230, Jun 23 = 4231/4232,
Jul 14 = 4233/4234) and the *next* pair's IDs already resolve to real
pages before their agenda is posted (confirmed: id 4236 -> "CITY
COUNCIL REGULAR MEETING", Aug 11 2026, with no Agenda document link
yet). So this reads the target date off the UPCOMING MEETINGS widget
(the site's own authoritative text, not a guessed formula), then probes
a small forward range of IDs past the last known linked meeting until
one's own heading matches both "CITY COUNCIL REGULAR MEETING" and that
date.

As of today, Newport News hasn't posted the Aug 11 2026 agenda yet
(same "posted a short time before the meeting" pattern confirmed for
Suffolk and Hampton) -- this script handles that as a clean no-op, same
as those two.

Confirmed against a real past Agenda PDF (document 285422, Jul 14
2026 meeting, used only to verify extraction logic): the structure is
lettered top-level sections (A. Call to Order, B. Invocation, ... E.
Public Hearings, F. Consent Agenda, G. Other City Council Actions, ...)
with numbered items carrying an "Agenda Item #26-087" reference and a
page-range footer to ignore. No "how to speak" boilerplate appears
anywhere in the document (unlike Chesapeake/Hampton), so
how_to_participate is always stored NULL here, same as Suffolk.

Usage:
    python scrape_newport_news_agenda.py
    python scrape_newport_news_agenda.py --meeting-id 4234 --meeting-date 2026-07-14 --dry-run
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
CIVICWEB_BASE = "https://nngov.civicweb.net"
GEMINI_MODEL = "gemini-2.5-flash"
_PROBE_RANGE = 16  # small forward window past the last known meeting id


def _parse_date(raw: str) -> str | None:
    raw = re.sub(r"\s+", " ", raw).strip().rstrip(",")
    for fmt in ("%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def find_upcoming_meeting() -> dict | None:
    """Return {meeting_id, meeting_date} for the next City Council Regular
    Meeting, or None if Newport News hasn't posted its agenda yet."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{CIVICWEB_BASE}/Portal/MeetingSchedule.aspx", timeout=45000, wait_until="load")
        page.wait_for_load_state("networkidle", timeout=30000)

        last_id = 0
        for a in page.query_selector_all('a[href*="MeetingInformation"]'):
            t = (a.inner_text() or "").strip()
            if not t.startswith("City Council Regular Meeting"):
                continue
            href = a.get_attribute("href") or ""
            m = re.search(r"[Ii]d=(\d+)", href)
            if m:
                last_id = max(last_id, int(m.group(1)))
        if not last_id:
            browser.close()
            return None

        body_text = page.inner_text("body")
        target_date = None
        m = re.search(
            r"UPCOMING MEETINGS\s*\n(?:.*\n)*?City Council Regular Meeting - ([A-Za-z]+ \d{1,2},? \d{4})",
            body_text,
        )
        if m:
            target_date = _parse_date(m.group(1))

        if not target_date:
            browser.close()
            return None

        found_id = None
        for candidate in range(last_id + 1, last_id + 1 + _PROBE_RANGE):
            try:
                page.goto(
                    f"{CIVICWEB_BASE}/Portal/MeetingInformation.aspx?Org=Cal&Id={candidate}",
                    timeout=20000, wait_until="load",
                )
                page.wait_for_timeout(500)
                txt = page.inner_text("body")
            except Exception:
                continue
            if "CITY COUNCIL REGULAR MEETING" not in txt:
                continue
            if target_date_str_in_text(txt, target_date):
                found_id = candidate
                break
        browser.close()

    if not found_id:
        return None
    return {"meeting_id": found_id, "meeting_date": target_date}


def target_date_str_in_text(txt: str, iso_date: str) -> bool:
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        needle = d.strftime(fmt).upper()
        if needle in txt.upper():
            return True
    return False


def find_agenda_document(meeting_id: int) -> tuple[str, str] | None:
    """Return (document_url, meeting_page_url) for the plain Agenda PDF
    (not the bulkier Agenda Packet), or None if not posted yet."""
    from playwright.sync_api import sync_playwright

    meeting_url = f"{CIVICWEB_BASE}/Portal/MeetingInformation.aspx?Org=Cal&Id={meeting_id}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(meeting_url, timeout=30000, wait_until="load")
        page.wait_for_timeout(1000)
        doc_url = None
        for a in page.query_selector_all("a"):
            t = (a.inner_text() or "").strip().lower()
            href = a.get_attribute("href") or ""
            if t == "agenda" and "/document/" in href:
                doc_url = href if href.startswith("http") else f"{CIVICWEB_BASE}{href}"
                break
        browser.close()
    if not doc_url:
        return None
    return doc_url, meeting_url


def download_pdf(url: str) -> bytes | None:
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


EXTRACT_PROMPT = """You are parsing an upcoming Newport News, Virginia City
Council meeting AGENDA (not yet held).

The document has lettered top-level sections (A. Call to Order, B.
Invocation, C. Pledge of Allegiance, D. Presentations, E. Public
Hearings, F. Consent Agenda, G. Other City Council Actions, H. Citizen
Comments, I. Old Business/New Business, J. Adjourn, etc.) with numbered
items under some of them. Each substantive item usually carries a
reference line like "Agenda Item #26-087 - Pdf" and a page-range
footer like "4 - 15" -- ignore the page range, it is not content.

Return a JSON array. Each object:
{
  "item_ref": "string -- the agenda item number, e.g. '26-087', or the
              section letter + number (e.g. 'D.1') if there's no
              Agenda Item # reference",
  "section": "string -- the enclosing lettered section's name, e.g.
              'Public Hearings', 'Consent Agenda', 'Other City Council
              Actions'",
  "title": "string -- short description (max 150 chars)",
  "category": "one of: public-hearing, ordinance, resolution, consent,
               presentation, appointment, procedural, other"
}

Rules:
- Skip purely procedural entries with no substantive content (Call to
  Order, Invocation, Pledge of Allegiance, Citizen Comments, Old
  Business/New Business/Councilmember Comments roll call, Adjourn)
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
        CREATE TABLE IF NOT EXISTS newport_news_upcoming_agenda (
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
    parser = argparse.ArgumentParser(description="Scrape the upcoming Newport News council agenda")
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
        conn.execute("DELETE FROM newport_news_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    today_str = date.today().isoformat()
    n_removed = conn.execute(
        "DELETE FROM newport_news_upcoming_agenda WHERE meeting_date < ?", (today_str,)
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.meeting_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --meeting-id")
        mtg = {"meeting_id": args.meeting_id, "meeting_date": args.meeting_date}
    else:
        print("Looking for the next Newport News City Council meeting...")
        mtg = find_upcoming_meeting()
        if not mtg:
            print("No upcoming meeting found (or not yet resolvable) -- nothing to do.")
            conn.close()
            return
        print(f"Found: {mtg['meeting_date']} (meeting_id={mtg['meeting_id']})")

    doc_info = find_agenda_document(mtg["meeting_id"])
    if not doc_info:
        print("  no Agenda document posted yet -- nothing to do.")
        conn.close()
        return
    doc_url, meeting_url = doc_info

    pdf_bytes = download_pdf(doc_url)
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
            INSERT INTO newport_news_upcoming_agenda
                (event_id, meeting_date, item_ref, section, title, category,
                 agenda_url, how_to_participate)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(event_id, item_ref) DO UPDATE SET
                section = excluded.section, title = excluded.title,
                category = excluded.category
        """, (
            mtg["meeting_id"], mtg["meeting_date"], it.get("item_ref", ""),
            it.get("section", ""), it.get("title", ""), it.get("category", "other"),
            meeting_url,
        ))
    conn.commit()
    time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
