"""
Scrape the upcoming Newport News Planning Commission agenda and store it in
newport_news_pc_upcoming_agenda for chat context injection.

Rezoning and conditional-use-permit fights are typically decided in
substance at Planning Commission -- City Council later votes on the
Commission's recommendation, often a full month afterward (confirmed on a
real July 15, 2026 PC agenda: three separate items each note "(To be heard
by City Council on August 11, 2026)"). Surfacing PC agendas gives residents
a month's more notice on rezoning/CUP cases near them than waiting for the
Council agenda alone.

Same CivicWeb platform and same ID-probing mechanism as
scrape_newport_news_agenda.py (see that file's docstring for the full
mechanism writeup) -- confirmed live that Planning Commission meeting pages
follow the identical MeetingInformation.aspx?Org=Cal&Id=N / Agenda-document-
link pattern. The one difference: PC meeting IDs are NOT in as tight a
chronological lockstep as City Council Regular Meeting/Work Session pairs,
since other meeting types (Council, special sessions) get interspersed IDs
between PC meetings (confirmed: PC ids 4257-4264 for Apr-Jul 2026, but id
4283 -- numerically much higher -- belongs to a Mar 18 2026 PC Work Session
created out of sequence). So this uses a wider forward probe window than
the Council scraper.

Confirmed against a real PC agenda (document 285398, Jul 15 2026 meeting,
used only to verify extraction logic): lettered top-level sections (A. Call
to Order ... E. Minutes, F. Public Hearing) with lettered sub-items (a),
(b), (c) under Public Hearing, each carrying a real case number (e.g.
"CU-2026-0005", "ZT-2026-0001"), a property address, and often an explicit
"(To be heard by City Council on <date>)" cross-reference -- captured here
as council_hearing_date since it's genuinely useful ("this gets decided
next month, here's when"). No "how to speak" boilerplate anywhere in the
document (same as the Council agenda), so how_to_participate is NULL.

Usage:
    python scrape_newport_news_pc_agenda.py
    python scrape_newport_news_pc_agenda.py --meeting-id 4264 --meeting-date 2026-07-15 --dry-run
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
_PROBE_RANGE = 40  # wider than the Council scraper -- PC ids interleave with other meeting types


def _parse_date(raw: str) -> str | None:
    raw = re.sub(r"\s+", " ", raw).strip().rstrip(",")
    for fmt in ("%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def target_date_str_in_text(txt: str, iso_date: str) -> bool:
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    # Confirmed live: Newport News formats Council dates WITH a comma
    # ("AUG 11, 2026") but Planning Commission dates WITHOUT one
    # ("AUG 05 2026") -- check both so neither meeting type false-negatives.
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        needle = d.strftime(fmt).upper()
        if needle in txt.upper():
            return True
    return False


def find_upcoming_meeting() -> dict | None:
    """Return {meeting_id, meeting_date} for the next Newport News Planning
    Commission meeting (excluding Work Sessions), or None if not posted yet."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{CIVICWEB_BASE}/Portal/MeetingSchedule.aspx", timeout=45000, wait_until="load")
        page.wait_for_load_state("networkidle", timeout=30000)

        last_id = 0
        for a in page.query_selector_all('a[href*="MeetingInformation"]'):
            t = (a.inner_text() or "").strip()
            if not t.startswith("Newport News Planning Commission") or "Work Session" in t:
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
            r"UPCOMING MEETINGS\s*\n(?:.*\n)*?Newport News Planning Commission - ([A-Za-z]+ \d{1,2},? \d{4})",
            body_text,
        )
        if m:
            target_date = _parse_date(m.group(1))

        if not target_date:
            browser.close()
            return None

        found_id = None
        for candidate in range(last_id + 1, last_id + 1 + _PROBE_RANGE):
            txt = None
            # civicweb.net is occasionally flaky on individual page loads
            # (confirmed live) -- a single timeout on the one candidate that
            # would actually match silently produces a false "not found", so
            # retry once before giving up on this candidate.
            for attempt in range(2):
                try:
                    page.goto(
                        f"{CIVICWEB_BASE}/Portal/MeetingInformation.aspx?Org=Cal&Id={candidate}",
                        timeout=20000, wait_until="load",
                    )
                    page.wait_for_timeout(500)
                    txt = page.inner_text("body")
                    break
                except Exception:
                    continue
            if txt is None:
                continue
            if "NEWPORT NEWS PLANNING COMMISSION" not in txt:
                continue
            if target_date_str_in_text(txt, target_date):
                found_id = candidate
                break
        browser.close()

    if not found_id:
        return None
    return {"meeting_id": found_id, "meeting_date": target_date}


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


EXTRACT_PROMPT = """You are parsing an upcoming Newport News, Virginia
Planning Commission meeting AGENDA (not yet held).

The document has lettered top-level sections (A. Call to Order, B.
Planning Commission Creed and Approval of the Agenda, C. Invocation,
D. Pledge of Allegiance, E. Minutes, F. Public Hearing, G. Executive
Secretary Report, H. Committee Reports, I. Unfinished Business, J. New
Business, K. Adjourn) with lettered sub-items (a), (b), (c) under some
of them, most substantively under Public Hearing. Each Public Hearing
item usually names a case type (CONDITIONAL USE PERMIT, REZONING,
ZONING TEXT AMENDMENT, COMPREHENSIVE PLAN AMENDMENT, etc.), a case
number (e.g. "CU-2026-0005", "ZT-2026-0001"), an applicant, a property
address if it's a site-specific case, and often ends with
"(To be heard by City Council on <date>)".

Return a JSON array. Each object:
{
  "item_ref": "string -- the case number if present (e.g. 'CU-2026-0005'),
              otherwise the section letter + sub-item (e.g. 'F.c')",
  "section": "string -- the enclosing lettered section's name, e.g.
              'Public Hearing', 'Minutes', 'New Business'",
  "title": "string -- short description (max 150 chars) -- include the
            case type and property address/location if present (e.g.
            'CUP for Recovery Home at 7 Darlene Lane')",
  "category": "one of: public-hearing, rezoning, conditional-use-permit,
               comprehensive-plan, procedural, other",
  "applicant_name": "string -- the applicant/requester named right after
              the case number (e.g. 'PC VIRGINIA HOMES LLC and OXFORD
              HOUSE' from 'CU-2026-0005, PC VIRGINIA HOMES LLC and OXFORD
              HOUSE  Request a conditional use permit...'), exactly as
              written, or empty string if not stated",
  "council_hearing_date": "string YYYY-MM-DD if the item states a
              'to be heard by City Council on <date>' cross-reference,
              otherwise null -- do not guess this, only use it if the
              document actually states it"
}

Rules:
- Skip purely procedural entries with no substantive content (Call to
  Order, Creed and Approval of the Agenda, Invocation, Pledge of
  Allegiance, Executive Secretary Report if empty, Adjourn) unless they
  are the only content.
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
        CREATE TABLE IF NOT EXISTS newport_news_pc_upcoming_agenda (
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
    # Additive migration -- this table already shipped without this column.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(newport_news_pc_upcoming_agenda)")}
    if "applicant_name" not in existing_cols:
        conn.execute("ALTER TABLE newport_news_pc_upcoming_agenda ADD COLUMN applicant_name TEXT")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape the upcoming Newport News Planning Commission agenda")
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
        conn.execute("DELETE FROM newport_news_pc_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    # 45-day retention on past meetings -- same reasoning as the Council
    # scraper: builders/local.py can link agenda items to their eventual
    # City Council hearing outcome.
    n_removed = conn.execute(
        "DELETE FROM newport_news_pc_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.meeting_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --meeting-id")
        mtg = {"meeting_id": args.meeting_id, "meeting_date": args.meeting_date}
    else:
        print("Looking for the next Newport News Planning Commission meeting...")
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
            INSERT INTO newport_news_pc_upcoming_agenda
                (event_id, meeting_date, item_ref, section, title, category,
                 agenda_url, how_to_participate, council_hearing_date, applicant_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(event_id, item_ref) DO UPDATE SET
                section = excluded.section, title = excluded.title,
                category = excluded.category,
                council_hearing_date = excluded.council_hearing_date,
                applicant_name = excluded.applicant_name
        """, (
            mtg["meeting_id"], mtg["meeting_date"], it.get("item_ref", ""),
            it.get("section", ""), it.get("title", ""), it.get("category", "other"),
            meeting_url, it.get("council_hearing_date"), it.get("applicant_name", ""),
        ))
    conn.commit()
    time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
