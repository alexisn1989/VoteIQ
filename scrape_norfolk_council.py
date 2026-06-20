"""
Scrape Norfolk City Council meeting vote records.

Flow:
  1. Fetch IQM2 calendar to enumerate Formal Session meeting IDs
  2. For each meeting, fetch detail page to find the minutes PDF (Type 12)
  3. Download the PDF
  4. Send to Gemini 2.5 Flash with inline bytes for structured vote extraction
  5. Store in norfolk_council_votes / norfolk_council_members tables

Usage:
    python scrape_norfolk_council.py --years 2024 2025 2026
    python scrape_norfolk_council.py --start 2025-01-01 --end 2025-12-31
    python scrape_norfolk_council.py --meeting-id 3713   # single meeting
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.request
from datetime import date, timedelta
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
    import pypdf as _pypdf
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

load_dotenv()

BASE_DIR   = Path(__file__).resolve().parent
DB_PATH    = BASE_DIR / "polls.db"
CACHE_DIR  = BASE_DIR / "norfolk_council_cache"
IQM2_BASE  = "https://norfolkcityva.iqm2.com/Citizens"
GEMINI_MODEL = "gemini-2.5-flash"

HEADERS = {"User-Agent": "Mozilla/5.0 (VoteIQ/1.0)"}

# ── HTTP helpers ──────────────────────────────────────────────────────────────

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
                print(f"    WARN: failed to fetch {url}: {exc}")
    return None


# ── IQM2 calendar: get meeting IDs ───────────────────────────────────────────

def fetch_meeting_ids(start: date, end: date) -> list[dict]:
    """Return list of {meeting_id, date_str, session_type} for City Council Formal Sessions."""
    url = (
        f"{IQM2_BASE}/calendar.aspx"
        f"?From={start.strftime('%m/%d/%Y')}"
        f"&To={end.strftime('%m/%d/%Y')}"
    )
    html = _get(url)
    if not html:
        return []

    text = html.decode("utf-8", errors="replace")

    # Find all Detail_Meeting links + their surrounding text
    meetings = []
    # Pattern: <a href="Detail_Meeting.aspx?ID=3713">...</a> with date context
    for m in re.finditer(r'Detail_Meeting\.aspx\?ID=(\d+)', text):
        meeting_id = int(m.group(1))
        # Grab surrounding context (300 chars) to find date + session type
        start_pos = max(0, m.start() - 300)
        ctx = text[start_pos: m.end() + 200]

        # Look for Formal Session in context
        if "Formal Session" not in ctx:
            continue

        # Extract date from context  e.g. "January 14, 2025" or "1/14/2025"
        date_match = re.search(
            r'(\w+ \d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})', ctx
        )
        date_str = date_match.group(1).strip() if date_match else "unknown"

        meetings.append({
            "meeting_id": meeting_id,
            "date_str": date_str,
            "session_type": "Formal Session",
        })

    # Deduplicate by meeting_id
    seen = set()
    result = []
    for m in meetings:
        if m["meeting_id"] not in seen:
            seen.add(m["meeting_id"])
            result.append(m)
    return result


# ── IQM2 detail page: find minutes document ID ───────────────────────────────

def fetch_minutes_doc_id(meeting_id: int) -> int | None:
    """Fetch the meeting detail page and return the Type-12 (minutes) document ID."""
    url = f"{IQM2_BASE}/Detail_Meeting.aspx?ID={meeting_id}"
    html = _get(url)
    if not html:
        return None

    text = html.decode("utf-8", errors="replace")

    # HTML encodes & as &amp; — match both forms
    for pattern in (
        r'FileOpen\.aspx\?Type=12&(?:amp;)?ID=(\d+)',
        r'FileOpen\.aspx\?Type=15&(?:amp;)?ID=(\d+)',
    ):
        m = re.search(pattern, text)
        if m:
            return int(m.group(1))

    return None


# ── PDF download + text extraction ───────────────────────────────────────────

def download_minutes_pdf(meeting_id: int, doc_id: int) -> Path | None:
    """Download the IQM2 meeting packet PDF and return the local cache path."""
    cache_path = CACHE_DIR / f"meeting_{meeting_id}_minutes.pdf"
    if cache_path.exists():
        return cache_path

    url = f"{IQM2_BASE}/FileOpen.aspx?Type=12&ID={doc_id}&Inline=True"
    data = _get(url)
    if data and data[:4] == b"%PDF":
        cache_path.write_bytes(data)
        return cache_path
    return None


def extract_minutes_text(pdf_path: Path, max_pages: int = 20) -> str:
    """
    Extract text from the minutes-only pages of the IQM2 packet.

    The IQM2 packet bundles the meeting minutes (pages 1-~8) followed by
    hundreds of pages of ordinances and exhibit attachments. We stop as soon
    as we see the first 'EXHIBITS:' section heading so Gemini only sees the
    relevant vote pages.
    """
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed — run: pip install pypdf")

    reader = _pypdf.PdfReader(str(pdf_path))
    pages_text: list[str] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        t = page.extract_text() or ""
        pages_text.append(t)
        # Stop once we hit ordinance/exhibit body pages
        if "EXHIBITS:" in t or (i > 3 and t.strip().startswith(("1\n", "2\n", "3\n"))):
            break

    return "\n".join(pages_text)


# ── Gemini vote extraction ────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are parsing Norfolk, Virginia City Council meeting minutes.

Extract EVERY roll-call vote from this document and return a JSON array.

Each vote object must have:
{
  "agenda_item": "string — item number or label (e.g. 'PH-1', 'CA-2', 'R-5')",
  "title": "string — short description of what was voted on (max 120 chars)",
  "motion": "string — motion type (Adopt, Approve, Deny, Table, Amend, etc.)",
  "moved_by": "string — council member last name who made the motion",
  "seconded_by": "string — council member last name who seconded",
  "votes": {
    "MEMBER_LAST_NAME": "yes|no|abstain|absent"
  },
  "result": "passed|failed|tabled|withdrawn",
  "vote_count": "e.g. '8-0' or '5-3'"
}

Rules:
- Include ALL votes including: approval of minutes, consent agenda items, public hearings,
  regular agenda items, adjournment
- For unanimous votes shown as '[Unanimous]': list each AYES member with vote='yes'
- Use council member last names as keys in the votes object
- AYES → yes, NAYS → no, ABSTENTIONS → abstain, ABSENT → absent
- Return ONLY a valid JSON array — no markdown fences, no explanation
- If no votes found, return []
"""


def extract_votes_gemini(minutes_text: str, meeting_date: str, api_key: str) -> list[dict]:
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

def _categorize(agenda_item: str) -> str:
    """Classify an agenda item into substantive / consent / procedural."""
    a = (agenda_item or "").strip().upper()
    if re.match(r'^(PH|R)-?\d', a):
        return "substantive"
    if re.match(r'^C-?\d', a):
        return "consent"
    return "procedural"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS norfolk_council_votes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id      INTEGER NOT NULL,
            meeting_date    TEXT,
            agenda_item     TEXT,
            title           TEXT,
            motion          TEXT,
            moved_by        TEXT,
            seconded_by     TEXT,
            result          TEXT,
            vote_count      TEXT,
            votes_json      TEXT,
            category        TEXT,       -- substantive / consent / procedural
            scraped_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ncv_meeting  ON norfolk_council_votes(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_ncv_date     ON norfolk_council_votes(meeting_date);
        CREATE INDEX IF NOT EXISTS idx_ncv_category ON norfolk_council_votes(category);

        CREATE TABLE IF NOT EXISTS norfolk_council_member_votes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            vote_id         INTEGER NOT NULL REFERENCES norfolk_council_votes(id),
            meeting_id      INTEGER NOT NULL,
            meeting_date    TEXT,
            member_name     TEXT,
            vote            TEXT,       -- yes / no / abstain / absent
            agenda_item     TEXT,
            title           TEXT,
            result          TEXT,
            category        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ncmv_member   ON norfolk_council_member_votes(member_name);
        CREATE INDEX IF NOT EXISTS idx_ncmv_meeting  ON norfolk_council_member_votes(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_ncmv_category ON norfolk_council_member_votes(category);
    """)
    # Add category column to existing tables if missing (migration safety)
    for tbl in ("norfolk_council_votes", "norfolk_council_member_votes"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        if "category" not in cols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN category TEXT")
    conn.commit()


def already_scraped(conn: sqlite3.Connection, meeting_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM norfolk_council_votes WHERE meeting_id=? LIMIT 1",
        (meeting_id,)
    ).fetchone()
    return row is not None


def store_votes(conn: sqlite3.Connection, meeting_id: int, meeting_date: str,
                votes: list[dict]) -> int:
    count = 0
    for v in votes:
        member_votes = v.get("votes") or {}
        agenda_item = v.get("agenda_item", "")
        cat = _categorize(agenda_item)
        cur = conn.execute(
            """INSERT INTO norfolk_council_votes
               (meeting_id, meeting_date, agenda_item, title, motion,
                moved_by, seconded_by, result, vote_count, votes_json, category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meeting_id, meeting_date, agenda_item,
                v.get("title", ""), v.get("motion", ""),
                v.get("moved_by", ""), v.get("seconded_by", ""),
                v.get("result", ""), v.get("vote_count", ""),
                json.dumps(member_votes), cat,
            )
        )
        vote_id = cur.lastrowid
        for member, vote in member_votes.items():
            conn.execute(
                """INSERT INTO norfolk_council_member_votes
                   (vote_id, meeting_id, meeting_date, member_name, vote,
                    agenda_item, title, result, category)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (vote_id, meeting_id, meeting_date, member, vote,
                 agenda_item, v.get("title", ""), v.get("result", ""), cat)
            )
        count += 1
    conn.commit()
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def process_meeting(meeting_id: int, date_str: str, conn: sqlite3.Connection,
                    api_key: str, dry_run: bool) -> None:
    print(f"\n  Meeting {meeting_id} ({date_str})")

    if not dry_run and already_scraped(conn, meeting_id):
        print("    already scraped — skip")
        return

    doc_id = fetch_minutes_doc_id(meeting_id)
    if not doc_id:
        print("    no minutes document found — skip")
        return
    print(f"    doc_id={doc_id}")

    pdf_path = download_minutes_pdf(meeting_id, doc_id)
    if not pdf_path:
        print("    PDF download failed — skip")
        return
    print(f"    PDF {pdf_path.stat().st_size:,} bytes")

    try:
        minutes_text = extract_minutes_text(pdf_path)
    except Exception as exc:
        print(f"    text extraction failed: {exc} — skip")
        return
    print(f"    extracted {len(minutes_text):,} chars of minutes text")

    # Extract authoritative date from the first line of the minutes
    # (e.g. "March 25, 2025") — more reliable than the HTML calendar parse
    first_line = minutes_text.strip().split("\n")[0].strip()
    date_from_pdf = re.search(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+\d{1,2},\s*\d{4}',
        first_line, re.I
    )
    if date_from_pdf:
        date_str = date_from_pdf.group(0).upper()

    if dry_run:
        print(f"    date: {date_str}")
        print("    [dry-run] would send to Gemini")
        print("    --- minutes preview (first 300 chars) ---")
        print(minutes_text[:300])
        return

    votes = extract_votes_gemini(minutes_text, date_str, api_key)
    print(f"    Gemini extracted {len(votes)} vote items")

    if votes:
        n = store_votes(conn, meeting_id, date_str, votes)
        print(f"    stored {n} vote records")
    else:
        print("    no votes extracted")

    time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Norfolk City Council votes")
    parser.add_argument("--years", nargs="+", type=int, default=[2024, 2025, 2026])
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   type=str, help="End date YYYY-MM-DD")
    parser.add_argument("--meeting-id", type=int, help="Process a single meeting ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Download only, no Gemini calls or DB writes")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    CACHE_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_tables(conn)

    if args.meeting_id:
        meetings = [{"meeting_id": args.meeting_id, "date_str": "unknown"}]
    elif args.start and args.end:
        s = date.fromisoformat(args.start)
        e = date.fromisoformat(args.end)
        print(f"Fetching meeting list {s} → {e}...")
        meetings = fetch_meeting_ids(s, e)
    else:
        meetings = []
        for year in args.years:
            s = date(year, 1, 1)
            e = date(year, 12, 31)
            print(f"Fetching {year} meeting list...")
            batch = fetch_meeting_ids(s, e)
            print(f"  Found {len(batch)} Formal Sessions")
            meetings.extend(batch)

    print(f"\nProcessing {len(meetings)} meetings...")
    for m in meetings:
        process_meeting(m["meeting_id"], m["date_str"], conn, api_key, args.dry_run)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
