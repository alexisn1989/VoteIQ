"""
Scrape Hampton City Council meeting vote records.

Hampton runs Legistar (hampton.legistar.com), but — unlike a typical Legistar
tenant — the on-site MeetingDetail.aspx grid leaves its Action/Result columns
empty for every agenda item; no structured vote data is exposed on the
website itself. The real record lives in each meeting's "Notice of Action"
minutes PDF, which (confirmed against a real December 2024 sample) is
genuine extractable text, not a scan, with a clean recurring per-item
pattern:

    1. 24-0446 Resolution to Amend the Fiscal Year 2025 ... Emergency
       Management Performance (EMP) Grant ...
    Item approved.
    Aye: Councilmember Bowman, Councilmember Brown, Vice Mayor Gray, ...
    7 -

A first-pass regex parser mis-segmented items at page breaks and repeated
title prefixes (several consecutive items in the same meeting are all
"Resolution to Amend the Fiscal Year 2025..." for different grants), so —
same as Chesapeake/Portsmouth — this sends the extracted text to Gemini for
structured extraction rather than fighting a fragile regex.

Council membership changes over time (the Dec 2024 minutes show Tuck as
Mayor/Gray as Vice Mayor; the current roster has Gray as Mayor/Brown as Vice
Mayor), so votes are captured by whatever last names actually appear in a
given meeting's own text, not restricted to the current roster.

Flow:
  1. Enumerate "City Council Legislative Session" meetings with minutes
     available, via Legistar's Calendar.aspx year filter (Playwright —
     the filter is a Telerik RadComboBox, not a plain <select>)
  2. Download each minutes PDF (plain HTTP; no auth needed)
  3. Extract text with pypdf
  4. Send text to Gemini 2.5 Flash for structured vote extraction
  5. Store in hampton_council_votes / hampton_council_member_votes

Usage:
    python scrape_hampton_council.py --start-year 2023
    python scrape_hampton_council.py --meeting-id 1160743 --guid 778F6802-D909-415F-B8FB-CA472B8BC68C --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
import os

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
DB_PATH = BASE_DIR / "polls.db"
CACHE_DIR = BASE_DIR / "hampton_council_cache"
LEGISTAR_BASE = "https://hampton.legistar.com"
GEMINI_MODEL = "gemini-2.5-flash"


# ── Meeting enumeration (Playwright) ──────────────────────────────────────────

def enumerate_meetings(start_year: int, end_year: int) -> list[dict]:
    """Return [{meeting_id, guid, meeting_date}] for Legislative Session
    meetings with minutes available, across start_year..end_year inclusive."""
    from playwright.sync_api import sync_playwright

    meetings: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for year in range(start_year, end_year + 1):
            page.goto(f"{LEGISTAR_BASE}/Calendar.aspx", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=20000)
            page.click("#ctl00_ContentPlaceHolder1_lstYears_Arrow")
            page.wait_for_timeout(500)
            page.click(f'#ctl00_ContentPlaceHolder1_lstYears_DropDown >> text="{year}"')
            page.wait_for_load_state("networkidle", timeout=20000)

            for tr in page.query_selector_all("tr"):
                txt = tr.inner_text()
                if "City Council Legislative Session" not in txt:
                    continue
                if "Minutes" not in txt or "Not available" in txt.split("Minutes")[0][-40:]:
                    pass  # cheap heuristic below is more reliable
                link = tr.query_selector('a[href*="M=M"]')
                detail = tr.query_selector('a[href*="MeetingDetail"]')
                if not link:
                    continue  # no minutes link -> not yet posted
                href = link.get_attribute("href") or ""
                m = re.search(r"ID=(\d+)&GUID=([\w-]+)", href)
                if not m:
                    continue
                date_m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", txt)
                meetings.append({
                    "meeting_id": m.group(1),
                    "guid": m.group(2),
                    "meeting_date": date_m.group(1) if date_m else "unknown",
                })
            print(f"  {year}: {len(meetings)} total meetings found so far")
        browser.close()
    # de-dupe by meeting_id
    seen: set[str] = set()
    out = []
    for mt in meetings:
        if mt["meeting_id"] not in seen:
            seen.add(mt["meeting_id"])
            out.append(mt)
    return out


def download_minutes_pdf(meeting_id: str, guid: str) -> Path | None:
    cache_path = CACHE_DIR / f"meeting_{meeting_id}_minutes.pdf"
    if cache_path.exists():
        return cache_path
    url = f"{LEGISTAR_BASE}/View.ashx?M=M&ID={meeting_id}&GUID={guid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if not data or data[:4] != b"%PDF":
            return None
        CACHE_DIR.mkdir(exist_ok=True)
        cache_path.write_bytes(data)
        return cache_path
    except Exception as exc:
        print(f"    download failed: {exc}")
        return None


def extract_pdf_text(pdf_path: Path, max_pages: int = 20) -> str:
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed — run: pip install pypdf")
    reader = _pypdf.PdfReader(str(pdf_path))
    pages = [pg.extract_text() or "" for pg in reader.pages[:max_pages]]
    return "\n".join(pages)


# ── Gemini vote extraction ────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are parsing Hampton, Virginia City Council "Notice of
Action" meeting minutes. Council membership changes over time — use whatever
last names actually appear in THIS document's roll-call lists (do not assume
a fixed roster).

Extract EVERY roll-call vote from this document and return a JSON array.

Each vote object must have:
{
  "agenda_item": "string — the file number, e.g. '24-0446'",
  "title": "string — short description of what was voted on (max 120 chars)",
  "motion": "string — motion type (Approve, Adopt, Deny, Table, Amend, etc.)",
  "moved_by": "string — council member last name who made the motion, or empty string if not stated",
  "seconded_by": "string — council member last name who seconded, or empty string if not stated",
  "votes": {
    "MEMBER_LAST_NAME": "yes|no|abstain|absent"
  },
  "result": "passed|failed|tabled|withdrawn",
  "vote_count": "e.g. '7-0' or '5-2'"
}

Rules:
- Include ALL recorded votes: consent agenda items, public hearings, regular
  agenda items, approval of minutes, adjournment if a vote is recorded
- Use "Aye"/"For" -> yes, "Nay"/"Against" -> no, "Abstain" -> abstain,
  not listed / "Absent" -> absent
- Use council member LAST NAMES ONLY as keys in the votes object (e.g. "Gray"
  not "Vice Mayor Gray", "Bowman" not "Councilmember Bowman")
- Return ONLY a valid JSON array — no markdown fences, no explanation
- If no votes found, return []
"""


def extract_votes_gemini(minutes_text: str, api_key: str) -> list[dict]:
    if not _GENAI_OK:
        print("    ERROR: google-genai not installed. pip install google-genai")
        return []
    client = _genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[EXTRACT_PROMPT, "\n\n---MEETING MINUTES---\n\n" + minutes_text],
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


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hampton_council_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER, meeting_date TEXT, title TEXT,
            agenda_item TEXT, motion TEXT, moved_by TEXT, seconded_by TEXT,
            result TEXT, vote_count TEXT, votes_json TEXT, category TEXT,
            scraped_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS hampton_council_member_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vote_id INTEGER, meeting_id INTEGER, meeting_date TEXT,
            member_name TEXT, vote TEXT, agenda_item TEXT, title TEXT,
            result TEXT, category TEXT
        );
    """)
    conn.commit()
    conn.close()
    print(f"[OK] Database initialized: {DB_PATH}")


def _categorize(agenda_item: str) -> str:
    a = (agenda_item or "").strip().upper()
    if re.match(r"^\d{2}-\d{4}$", a):
        return "substantive"
    return "procedural"


def store_meeting_votes(meeting_id: str, meeting_date: str, votes: list[dict]) -> int:
    conn = sqlite3.connect(DB_PATH)
    stored = 0
    for v in votes:
        agenda_item = v.get("agenda_item", "")
        cur = conn.execute(
            "SELECT id FROM hampton_council_votes WHERE meeting_id=? AND agenda_item=?",
            (meeting_id, agenda_item),
        ).fetchone()
        if cur:
            vote_id = cur[0]
        else:
            conn.execute(
                "INSERT INTO hampton_council_votes "
                "(meeting_id, meeting_date, title, agenda_item, motion, moved_by, "
                " seconded_by, result, vote_count, votes_json, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (meeting_id, meeting_date, v.get("title", ""), agenda_item,
                 v.get("motion", ""), v.get("moved_by", ""), v.get("seconded_by", ""),
                 v.get("result", ""), v.get("vote_count", ""),
                 json.dumps(v.get("votes", {})), _categorize(agenda_item)),
            )
            vote_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for member, choice in (v.get("votes") or {}).items():
            exists = conn.execute(
                "SELECT 1 FROM hampton_council_member_votes WHERE vote_id=? AND member_name=?",
                (vote_id, member),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO hampton_council_member_votes "
                    "(vote_id, meeting_id, meeting_date, member_name, vote, "
                    " agenda_item, title, result, category) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (vote_id, meeting_id, meeting_date, member, choice,
                     agenda_item, v.get("title", ""), v.get("result", ""),
                     _categorize(agenda_item)),
                )
                stored += 1
    conn.commit()
    conn.close()
    return stored


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape Hampton council votes")
    ap.add_argument("--start-year", type=int, default=2023)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--meeting-id", default=None, help="Single meeting (for testing)")
    ap.add_argument("--guid", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set")
        return

    init_db()

    if args.meeting_id:
        meetings = [{"meeting_id": args.meeting_id, "guid": args.guid, "meeting_date": "test"}]
    else:
        print(f"Enumerating Legislative Session meetings {args.start_year}-{args.end_year}...")
        meetings = enumerate_meetings(args.start_year, args.end_year)
        print(f"Found {len(meetings)} meetings with minutes available")

    total_stored = 0
    for i, mt in enumerate(meetings, 1):
        print(f"  [{i}/{len(meetings)}] meeting {mt['meeting_id']} ({mt['meeting_date']})")
        pdf_path = download_minutes_pdf(mt["meeting_id"], mt["guid"])
        if not pdf_path:
            print("    no PDF — skipping")
            continue
        text = extract_pdf_text(pdf_path)
        if len(text) < 200:
            print("    minutes text too short — skipping")
            continue
        votes = extract_votes_gemini(text, api_key)
        print(f"    Gemini extracted {len(votes)} vote items")
        if args.dry_run:
            for v in votes[:3]:
                print("   ", v)
            continue
        stored = store_meeting_votes(mt["meeting_id"], mt["meeting_date"], votes)
        total_stored += stored
        time.sleep(1)  # light pacing between Gemini calls

    if not args.dry_run:
        conn = sqlite3.connect(DB_PATH)
        vcount = conn.execute("SELECT COUNT(*) FROM hampton_council_votes").fetchone()[0]
        mvcount = conn.execute("SELECT COUNT(*) FROM hampton_council_member_votes").fetchone()[0]
        conn.close()
        print(f"\nDone — {total_stored} new member-vote rows this run")
        print(f"Totals: {vcount} distinct votes, {mvcount} member-vote records")


if __name__ == "__main__":
    main()
