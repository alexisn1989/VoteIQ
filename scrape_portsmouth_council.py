"""
Scrape Portsmouth City Council meeting vote records.

Portsmouth does not run IQM2 (unlike Norfolk/VB) and its Granicus instance
(portsmouthva.granicus.com) only publishes pre-meeting Agendas (no recorded
vote outcomes) behind a JS bot-challenge. The real source of roll-call
results is the City Clerk's Laserfiche WebLink archive:

    https://www2.portsmouthva.gov/weblink7CCMinutes/

  Portsmouth-City-Clerk > Minutes > 2000s > Year YYYY > "Minutes MM/DD/YYYY"

Each entry is a scanned document with no text layer, so Gemini reads the
PDF directly (multimodal) rather than extracting text with pypdf.

Getting the actual PDF bytes requires driving the WebLink viewer's "Print
(as PDF)" dialog with a real browser (Playwright) — the export endpoint is
a per-session generated URL, there's no stable direct-download link. Note
also: this host's TLS cert (www2.portsmouthva.gov) is expired, so requests
must ignore HTTPS errors — it's a read-only public records archive, not an
authenticated system.

Flow:
  1. Enumerate "Year YYYY" folders under Minutes/2000s
  2. For each year, enumerate "Minutes MM/DD/YYYY" documents
  3. Drive the WebLink PDF export dialog (Playwright) to download each PDF
  4. Send the PDF directly to Gemini 2.5 Flash for structured vote extraction
  5. Store in portsmouth_council_votes / portsmouth_council_member_votes

Usage:
    python scrape_portsmouth_council.py --years 2023 2024 2025 2026
    python scrape_portsmouth_council.py --doc-id 7441 --meeting-date "05/12/2026"
    python scrape_portsmouth_council.py --dry-run --years 2026
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv
import os

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except ImportError:
    _PLAYWRIGHT_OK = False

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BASE_DIR   = Path(__file__).resolve().parent
DB_PATH    = BASE_DIR / "polls.db"
CACHE_DIR  = BASE_DIR / "portsmouth_council_cache"
WEBLINK_BASE = "https://www2.portsmouthva.gov/weblink7CCMinutes"
MINUTES_2000S_FOL = 1620   # Portsmouth-City-Clerk > Minutes > 2000s
GEMINI_MODEL = "gemini-2.5-flash"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Current roster (portsmouth_council_members) — last names, for the Gemini prompt
COUNCIL_LAST_NAMES = ["Glover", "Moody", "Hugel", "Tillage", "Bryant", "Dodson", "Thomas"]


# ── WebLink enumeration (Playwright) ─────────────────────────────────────────

def _fol_links(page) -> list[dict]:
    return page.eval_on_selector_all(
        "a",
        "els => els.map(e => ({href: e.href, text: e.innerText}))",
    )


def fetch_year_folder_ids(page, years: list[int]) -> dict[int, int]:
    """Return {year: fol_id} for the requested years, from Minutes/2000s (paginated)."""
    result: dict[int, int] = {}
    url = f"{WEBLINK_BASE}/0/fol/{MINUTES_2000S_FOL}/Row1.aspx"
    seen_pages = set()
    while url and url not in seen_pages:
        seen_pages.add(url)
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)
        links = _fol_links(page)
        next_url = None
        for l in links:
            m = re.search(r"Year (\d{4})", l["text"])
            if m and "/fol/" in l["href"]:
                yr = int(m.group(1))
                if yr in years:
                    fol_m = re.search(r"/fol/(\d+)/", l["href"])
                    if fol_m:
                        result[yr] = int(fol_m.group(1))
            if l["text"].strip().lower() == "last" or l["text"].strip() == "2":
                pass  # pagination handled by scanning all rows below
        # find "next page" link distinctly (row offset increases)
        pager = [l["href"] for l in links if re.search(r"/fol/\d+/Row\d+\.aspx", l["href"])
                  and l["href"] != url and "Row1.aspx" not in l["href"]]
        url = pager[0] if pager and len(result) < len(years) else None
    return result


def fetch_meeting_docs(page, fol_id: int) -> list[dict]:
    """Return [{doc_id, label}] for all 'Minutes ...' entries in a year folder (paginated)."""
    docs: list[dict] = []
    url = f"{WEBLINK_BASE}/0/fol/{fol_id}/Row1.aspx"
    seen_pages = set()
    while url and url not in seen_pages:
        seen_pages.add(url)
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)
        links = _fol_links(page)
        for l in links:
            m = re.search(r"/doc/(\d+)/", l["href"])
            if m and "Minutes" in l["text"]:
                docs.append({"doc_id": int(m.group(1)), "label": l["text"].strip()})
        next_links = [l["href"] for l in links
                      if re.search(rf"/fol/{fol_id}/Row\d+\.aspx", l["href"]) and l["href"] != url]
        url = next_links[0] if next_links else None
    # dedupe
    seen_ids = set()
    result = []
    for d in docs:
        if d["doc_id"] not in seen_ids:
            seen_ids.add(d["doc_id"])
            result.append(d)
    return result


def download_minutes_pdf(context, page, doc_id: int) -> Path | None:
    """Drive the WebLink 'Print (as PDF)' dialog to export the full document."""
    cache_path = CACHE_DIR / f"minutes_{doc_id}.pdf"
    if cache_path.exists():
        return cache_path

    try:
        page.goto(f"{WEBLINK_BASE}/0/doc/{doc_id}/Page1.aspx", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        page.click("#PdfDialog_PdfDownloadLink", force=True, timeout=5000)
        page.wait_for_timeout(500)
        with context.expect_page(timeout=10000) as pop_info:
            page.click("#PdfDialog_download", force=True)
        popup = pop_info.value
        with popup.expect_download(timeout=30000) as dl_info:
            popup.wait_for_load_state("networkidle", timeout=30000)
        download = dl_info.value
        download.save_as(str(cache_path))
        popup.close()
        return cache_path
    except Exception as exc:
        print(f"    PDF export failed: {exc}")
        return None


# ── Gemini vote extraction (direct PDF — these are scanned, no text layer) ──

EXTRACT_PROMPT = f"""You are parsing Portsmouth, Virginia City Council meeting minutes (a scanned document).

The current council members' last names are: {", ".join(COUNCIL_LAST_NAMES)}
(Glover is Mayor, Moody is Vice Mayor — all seats are elected At-Large, no wards).
Older minutes in this archive may reference different, former council members —
transcribe whatever names actually appear in the Ayes/Nays lists, even if not in this list.

Extract EVERY roll-call vote from this document and return a JSON array.

Each vote object must have:
{{
  "agenda_item": "string — item number or label if shown (e.g. '26-121a', ordinance/resolution number)",
  "title": "string — short description of what was voted on (max 120 chars)",
  "motion": "string — motion type (Adopt, Approve, Deny, Table, Amend, Defer, etc.)",
  "moved_by": "string — council member last name who made the motion, if stated",
  "seconded_by": "string — council member last name who seconded, if stated",
  "votes": {{
    "MEMBER_LAST_NAME": "yes|no|abstain|absent"
  }},
  "result": "passed|failed|tabled|withdrawn|deferred",
  "vote_count": "e.g. '7-0' or '5-2'"
}}

Rules:
- Include ALL roll-call votes including: certification of closed meeting, approval of minutes,
  consent agenda items, public hearings, ordinances, resolutions, adjournment
- Ayes → yes, Nays → no, Abstentions → abstain, Absent → absent
- Use council member last names as keys in the votes object
- Return ONLY a valid JSON array — no markdown fences, no explanation
- If no roll-call votes found, return []
"""


def extract_votes_gemini(pdf_bytes: bytes, api_key: str) -> list[dict]:
    if not _GENAI_OK:
        print("    ERROR: google-genai not installed. pip install google-genai")
        return []

    client = _genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                EXTRACT_PROMPT,
                _gtypes.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            ],
            config=_gtypes.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                http_options=_gtypes.HttpOptions(timeout=90_000),
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


# ── SQLite schema ─────────────────────────────────────────────────────────────

def _categorize(agenda_item: str, title: str) -> str:
    """
    Portsmouth labels items with ordinance/resolution numbers (e.g. '26-121a')
    or spells out ORDINANCE/RESOLUTION in the title — match on either.
    """
    a = (agenda_item or "").strip().upper()
    t = (title or "").strip().upper()
    if "CONSENT" in a or "CONSENT" in t:
        return "consent"
    if re.search(r'\d{2,4}-\d+', a) or "ORDINANCE" in t or "RESOLUTION" in t:
        return "substantive"
    return "procedural"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portsmouth_council_votes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id          INTEGER NOT NULL,
            meeting_date    TEXT,
            title           TEXT,
            agenda_item     TEXT,
            motion          TEXT,
            moved_by        TEXT,
            seconded_by     TEXT,
            result          TEXT,
            vote_count      TEXT,
            votes_json      TEXT,
            category        TEXT,
            scraped_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_pcv_doc      ON portsmouth_council_votes(doc_id);
        CREATE INDEX IF NOT EXISTS idx_pcv_date     ON portsmouth_council_votes(meeting_date);
        CREATE INDEX IF NOT EXISTS idx_pcv_category ON portsmouth_council_votes(category);

        CREATE TABLE IF NOT EXISTS portsmouth_council_member_votes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            vote_id         INTEGER NOT NULL REFERENCES portsmouth_council_votes(id),
            doc_id          INTEGER NOT NULL,
            meeting_date    TEXT,
            member_name     TEXT,
            vote            TEXT,
            agenda_item     TEXT,
            title           TEXT,
            result          TEXT,
            category        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pcmv_member   ON portsmouth_council_member_votes(member_name);
        CREATE INDEX IF NOT EXISTS idx_pcmv_doc      ON portsmouth_council_member_votes(doc_id);
        CREATE INDEX IF NOT EXISTS idx_pcmv_category ON portsmouth_council_member_votes(category);
    """)
    conn.commit()


def already_scraped(conn: sqlite3.Connection, doc_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM portsmouth_council_votes WHERE doc_id=? LIMIT 1", (doc_id,)
    ).fetchone()
    return row is not None


def store_votes(conn: sqlite3.Connection, doc_id: int, meeting_date: str,
                votes: list[dict]) -> int:
    count = 0
    for v in votes:
        member_votes = v.get("votes") or {}
        agenda_item = v.get("agenda_item", "")
        title = v.get("title", "")
        cat = _categorize(agenda_item, title)
        cur = conn.execute(
            """INSERT INTO portsmouth_council_votes
               (doc_id, meeting_date, agenda_item, title, motion,
                moved_by, seconded_by, result, vote_count, votes_json, category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc_id, meeting_date, agenda_item, title, v.get("motion", ""),
                v.get("moved_by", ""), v.get("seconded_by", ""),
                v.get("result", ""), v.get("vote_count", ""),
                json.dumps(member_votes), cat,
            )
        )
        vote_id = cur.lastrowid
        for member, vote in member_votes.items():
            conn.execute(
                """INSERT INTO portsmouth_council_member_votes
                   (vote_id, doc_id, meeting_date, member_name, vote,
                    agenda_item, title, result, category)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (vote_id, doc_id, meeting_date, member, vote,
                 agenda_item, title, v.get("result", ""), cat)
            )
        count += 1
    conn.commit()
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def _meeting_date_from_label(label: str) -> str:
    m = re.search(r'(\d{1,2}/\d{1,2}(?:-\d+)?/\d{4})', label)
    return m.group(1) if m else "unknown"


def process_meeting(doc_id: int, meeting_date: str, context, page,
                    conn: sqlite3.Connection, api_key: str, dry_run: bool) -> None:
    print(f"\n  Doc {doc_id} ({meeting_date})")

    if not dry_run and already_scraped(conn, doc_id):
        print("    already scraped — skip")
        return

    pdf_path = download_minutes_pdf(context, page, doc_id)
    if not pdf_path:
        print("    PDF export failed — skip")
        return
    print(f"    PDF {pdf_path.stat().st_size:,} bytes")

    if dry_run:
        print("    [dry-run] would send to Gemini")
        return

    pdf_bytes = pdf_path.read_bytes()
    votes = extract_votes_gemini(pdf_bytes, api_key)
    print(f"    Gemini extracted {len(votes)} vote items")

    if votes:
        n = store_votes(conn, doc_id, meeting_date, votes)
        print(f"    stored {n} vote records")
    else:
        print("    no votes extracted")

    time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Portsmouth City Council votes")
    parser.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025, 2026])
    parser.add_argument("--doc-id", type=int, help="Process a single meeting doc_id")
    parser.add_argument("--meeting-date", type=str, default="unknown",
                        help="Meeting date label to store when using --doc-id")
    parser.add_argument("--dry-run", action="store_true",
                        help="Download only, no Gemini calls or DB writes")
    args = parser.parse_args()

    if not _PLAYWRIGHT_OK:
        raise SystemExit("playwright not installed — run: pip install playwright && playwright install chromium")

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    CACHE_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_tables(conn)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=UA, ignore_https_errors=True, accept_downloads=True)
        page = context.new_page()

        if args.doc_id:
            meetings = [{"doc_id": args.doc_id, "label": args.meeting_date}]
        else:
            print(f"Enumerating year folders for {args.years}...")
            year_fols = fetch_year_folder_ids(page, args.years)
            print(f"  Found folders: {year_fols}")

            meetings = []
            for year in args.years:
                fol_id = year_fols.get(year)
                if not fol_id:
                    print(f"  WARN: no folder found for {year}")
                    continue
                docs = fetch_meeting_docs(page, fol_id)
                print(f"  {year}: {len(docs)} meeting documents")
                meetings.extend(docs)

        print(f"\nProcessing {len(meetings)} meetings...")
        for m in meetings:
            meeting_date = m.get("meeting_date") or _meeting_date_from_label(m.get("label", ""))
            process_meeting(m["doc_id"], meeting_date, context, page, conn, api_key, args.dry_run)

        browser.close()

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
