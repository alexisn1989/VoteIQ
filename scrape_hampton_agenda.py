"""
Scrape the upcoming Hampton City Council agenda and store it in
hampton_upcoming_agenda for chat context injection.

Hampton runs Legistar (hampton.legistar.com), same as
scrape_hampton_council.py's historical scraper -- but that script only
enumerates meetings that already have a Minutes link (M=M), since it's
extracting recorded votes. This script instead looks at Calendar.aspx
for the soonest "City Council Legislative Session" row with
meeting_date >= today, and downloads its Agenda document (View.ashx?
M=A&ID=...&GUID=...) once Hampton has posted one -- confirmed via a
real live probe (2026-07-18) that the next scheduled meeting (8/12/2026)
has neither an Agenda nor Minutes link yet, so there is currently
nothing to scrape; Hampton evidently posts the agenda closer to the
meeting date, same pattern as Suffolk.

Confirmed against a real Agenda PDF (meeting_id 1374052, 7/8/2026 --
used here only to verify extraction logic, since it's a past meeting):
Hampton's structure is ALL-CAPS section headers (CONSENT AGENDA, PUBLIC
HEARINGS, GENERAL ITEMS, etc., with "Ordinances" as a title-case
sub-header appearing under more than one top-level section) followed by
flat-numbered items in the form "N. FILE-NUMBER Title text" (file
numbers look like "26-0220"). The agenda's own header also carries a
real, detailed "how to participate" policy -- a 3-minute speaking time
limit and a requirement to sign in before the meeting in the City Hall
lobby -- plus a Clerk of Council contact line, both extracted via a
cheap one-time regex rather than Gemini, same reasoning as Chesapeake's
scraper (fixed boilerplate that repeats every meeting).

Usage:
    python scrape_hampton_agenda.py
    python scrape_hampton_agenda.py --meeting-id 1374052 --guid 86C74997-8AA9-4760-A7F2-D4DE0A81583E --meeting-date 2026-07-08 --dry-run
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
    """Return the soonest 'City Council Legislative Session' meeting with
    meeting_date >= today AND an Agenda (M=A) link already posted, or
    None if Hampton hasn't posted one yet."""
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
            if "City Council Legislative Session" not in txt:
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


# ── Standing "how to speak" instructions -- fixed template, cheap regex,
# no need to burn a Gemini call on boilerplate that repeats every meeting ──

def extract_participation_info(text: str) -> str | None:
    m = re.search(
        r"(\(2\)\s*The City Council has adopted.{0,500}?public comment portion of the meeting\.)",
        text, re.S,
    )
    if not m:
        return None
    info = re.sub(r"\s+", " ", m.group(1)).strip()

    contact_m = re.search(r"(Clerk of Council,\s*[\d\-]+,\s*\S+@\S+)", text)
    if contact_m:
        info += " " + re.sub(r"\s+", " ", contact_m.group(1)).strip()
    return info


EXTRACT_PROMPT = """You are parsing an upcoming Hampton, Virginia City
Council meeting AGENDA (not yet held -- nothing has been voted on).

The document has ALL-CAPS top-level section headers (e.g. "CONSENT
AGENDA", "PUBLIC HEARINGS", "GENERAL ITEMS") and some title-case
sub-headers that can repeat under more than one top-level section (e.g.
"Ordinances" appears once under PUBLIC HEARINGS and again under GENERAL
ITEMS -- treat each occurrence as its own section context). Numbered
items look like:

    8. 26-0193 Ordinance to Amend and Re-enact the Zoning Ordinance...

Return a JSON array. Each object:
{
  "item_ref": "string -- the file number, e.g. '26-0193'",
  "section": "string -- the nearest preceding header, e.g. 'Ordinances
              (Public Hearings)' if it's the Ordinances sub-header
              under Public Hearings, or just the top-level header name
              if there's no sub-header",
  "title": "string -- short description (max 150 chars)",
  "category": "one of: public-hearing, ordinance, resolution, consent,
               presentation, appointment, procedural, other"
}

Rules:
- Skip section headers with no numbered items under them.
- Skip purely procedural entries with no file number (Call to Order,
  Invocation, Pledge of Allegiance, Mayor's Comments, Public Comment,
  Reports by City Manager/Council/Staff, Miscellaneous New Business,
  Adjournment) unless they are the only content.
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
        CREATE TABLE IF NOT EXISTS hampton_upcoming_agenda (
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
    parser = argparse.ArgumentParser(description="Scrape the upcoming Hampton council agenda")
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
        conn.execute("DELETE FROM hampton_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    # 45-day retention on past meetings -- builders/local.py links agenda
    # items to recorded outcomes, so keep the rows past meeting day.
    n_removed = conn.execute(
        "DELETE FROM hampton_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.meeting_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --meeting-id")
        mtg = {"meeting_id": args.meeting_id, "guid": args.guid, "meeting_date": args.meeting_date}
    else:
        print("Looking for the next Hampton City Council meeting with a posted agenda...")
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
            INSERT INTO hampton_upcoming_agenda
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
