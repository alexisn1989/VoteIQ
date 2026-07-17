"""
Scrape Chesapeake City Council meeting vote records.

Chesapeake does not run IQM2 (unlike Norfolk and Virginia Beach). Regular City
Council meetings live on the legacy Granicus "ViewPublisher" archive at
chesapeake.granicus.com (view_id=29). CivicPlus AgendaCenter on
cityofchesapeake.net only hosts sub-committees (e.g. the Audit Committee),
not the full Council.

Flow:
  1. Fetch the ViewPublisher archive page to enumerate meetings (name, date,
     clip_id, minutes doc_id)
  2. Download the Minutes PDF via MinutesViewer.php
  3. Extract minutes-only pages with pypdf (skipping ordinance exhibits)
  4. Send to Gemini 2.5 Flash for structured vote extraction
  5. Store in chesapeake_council_votes / chesapeake_council_member_votes

Note: chesapeake.granicus.com blocks requests with a bare "Mozilla/5.0"
User-Agent (403). A realistic full browser UA string is required.

Usage:
    python scrape_chesapeake_council.py --limit 20
    python scrape_chesapeake_council.py --clip-id 9611
    python scrape_chesapeake_council.py --dry-run --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.request
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
CACHE_DIR  = BASE_DIR / "chesapeake_council_cache"
GRANICUS_BASE = "https://chesapeake.granicus.com"
VIEW_ID    = 29   # Regular City Council Meeting archive
GEMINI_MODEL = "gemini-2.5-flash"

# chesapeake.granicus.com 403s on a bare "Mozilla/5.0" UA — needs a real browser UA
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# Current roster (chesapeake_council_members) — last names, for the Gemini prompt
COUNCIL_LAST_NAMES = [
    "West", "Ritter", "Bunn", "Jefferies", "King", "Newins", "Smith", "Ward", "Whitaker",
]

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


# ── ViewPublisher archive: enumerate meetings ────────────────────────────────

_ROW_RE = re.compile(
    r'<tr class="listingRow">(.*?)</tr>', re.S
)
_NAME_RE = re.compile(r'class="listItem"[^>]*scope="row">\s*([^<]+?)\s*</td>', re.S)
_DATE_RE = re.compile(r'>\s*([A-Za-z]+&nbsp;\d{1,2},&nbsp;\d{4})\s*<')
_MINUTES_RE = re.compile(
    r'MinutesViewer\.php\?view_id=(\d+)&clip_id=(\d+)&doc_id=([\w-]+)'
)


def fetch_meetings(view_id: int = VIEW_ID) -> list[dict]:
    """Return list of {clip_id, doc_id, meeting_date, title} for meetings with minutes."""
    url = f"{GRANICUS_BASE}/ViewPublisher.php?view_id={view_id}"
    html = _get(url)
    if not html:
        return []

    text = html.decode("utf-8", errors="replace")

    meetings = []
    for row_m in _ROW_RE.finditer(text):
        row = row_m.group(1)

        name_m = _NAME_RE.search(row)
        title = name_m.group(1).strip() if name_m else "City Council Meeting"

        date_m = _DATE_RE.search(row)
        date_str = date_m.group(1).replace("&nbsp;", " ") if date_m else "unknown"

        min_m = _MINUTES_RE.search(row)
        if not min_m:
            continue  # no minutes posted yet for this meeting

        meetings.append({
            "clip_id": int(min_m.group(2)),
            "doc_id": min_m.group(3),
            "meeting_date": date_str,
            "title": title,
        })

    # Deduplicate by clip_id
    seen = set()
    result = []
    for m in meetings:
        if m["clip_id"] not in seen:
            seen.add(m["clip_id"])
            result.append(m)
    return result


# ── PDF download + text extraction ───────────────────────────────────────────

def download_minutes_pdf(clip_id: int, doc_id: str) -> Path | None:
    cache_path = CACHE_DIR / f"meeting_{clip_id}_minutes.pdf"
    if cache_path.exists():
        return cache_path

    url = f"{GRANICUS_BASE}/MinutesViewer.php?view_id={VIEW_ID}&clip_id={clip_id}&doc_id={doc_id}"
    data = _get(url)
    if data and data[:4] == b"%PDF":
        cache_path.write_bytes(data)
        return cache_path
    return None


def extract_minutes_text(pdf_path: Path, max_pages: int = 20) -> str:
    """
    Extract text from the minutes-only pages of the packet, stopping once
    ordinance/exhibit attachment pages start (same heuristic as Norfolk).
    """
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed — run: pip install pypdf")

    reader = _pypdf.PdfReader(str(pdf_path))
    pages_text: list[str] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        t = page.extract_text() or ""
        pages_text.append(t)
        if "EXHIBITS:" in t or (i > 3 and t.strip().startswith(("1\n", "2\n", "3\n"))):
            break

    return "\n".join(pages_text)


# ── Gemini vote extraction ────────────────────────────────────────────────────

EXTRACT_PROMPT = f"""You are parsing Chesapeake, Virginia City Council meeting minutes.

The current council members' last names are: {", ".join(COUNCIL_LAST_NAMES)}
(West is Mayor, Ritter is Vice Mayor — all seats are elected At-Large, no districts).

Extract EVERY roll-call vote from this document and return a JSON array.

Each vote object must have:
{{
  "agenda_item": "string — item number or label (e.g. 'PH-1', 'CA-2', 'R-5')",
  "title": "string — short description of what was voted on (max 120 chars)",
  "motion": "string — motion type (Adopt, Approve, Deny, Table, Amend, etc.)",
  "moved_by": "string — council member last name who made the motion",
  "seconded_by": "string — council member last name who seconded",
  "votes": {{
    "MEMBER_LAST_NAME": "yes|no|abstain|absent"
  }},
  "result": "passed|failed|tabled|withdrawn",
  "vote_count": "e.g. '9-0' or '5-3'"
}}

Rules:
- Include ALL votes including: approval of minutes, consent agenda items, public hearings,
  regular agenda items, adjournment
- For unanimous votes shown as '[Unanimous]': list each member with vote='yes'
- Use council member last names as keys in the votes object
- AYES → yes, NAYS → no, ABSTENTIONS → abstain, ABSENT → absent
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
                http_options=_gtypes.HttpOptions(timeout=60_000),  # 60s — avoid indefinite hangs
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
    """
    Chesapeake labels items as '#24-O-053' (ordinance) / '#24-R-026' (resolution)
    rather than Norfolk's PH-/R-/C- prefixes, so match on the ordinance/resolution
    number embedded in the label instead.
    """
    a = (agenda_item or "").strip().upper()
    if "CONSENT" in a:
        return "consent"
    if re.search(r'#\d+-[OR]-\d+', a):
        return "substantive"
    return "procedural"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chesapeake_council_votes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            clip_id         INTEGER NOT NULL,
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
        CREATE INDEX IF NOT EXISTS idx_ccv_clip     ON chesapeake_council_votes(clip_id);
        CREATE INDEX IF NOT EXISTS idx_ccv_date     ON chesapeake_council_votes(meeting_date);
        CREATE INDEX IF NOT EXISTS idx_ccv_category ON chesapeake_council_votes(category);

        CREATE TABLE IF NOT EXISTS chesapeake_council_member_votes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            vote_id         INTEGER NOT NULL REFERENCES chesapeake_council_votes(id),
            clip_id         INTEGER NOT NULL,
            meeting_date    TEXT,
            member_name     TEXT,
            vote            TEXT,
            agenda_item     TEXT,
            title           TEXT,
            result          TEXT,
            category        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ccmv_member   ON chesapeake_council_member_votes(member_name);
        CREATE INDEX IF NOT EXISTS idx_ccmv_clip     ON chesapeake_council_member_votes(clip_id);
        CREATE INDEX IF NOT EXISTS idx_ccmv_category ON chesapeake_council_member_votes(category);
    """)
    conn.commit()


def already_scraped(conn: sqlite3.Connection, clip_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM chesapeake_council_votes WHERE clip_id=? LIMIT 1",
        (clip_id,)
    ).fetchone()
    return row is not None


def store_votes(conn: sqlite3.Connection, clip_id: int, meeting_date: str,
                votes: list[dict]) -> int:
    count = 0
    for v in votes:
        member_votes = v.get("votes") or {}
        agenda_item = v.get("agenda_item", "")
        cat = _categorize(agenda_item)
        cur = conn.execute(
            """INSERT INTO chesapeake_council_votes
               (clip_id, meeting_date, agenda_item, title, motion,
                moved_by, seconded_by, result, vote_count, votes_json, category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                clip_id, meeting_date, agenda_item,
                v.get("title", ""), v.get("motion", ""),
                v.get("moved_by", ""), v.get("seconded_by", ""),
                v.get("result", ""), v.get("vote_count", ""),
                json.dumps(member_votes), cat,
            )
        )
        vote_id = cur.lastrowid
        for member, vote in member_votes.items():
            conn.execute(
                """INSERT INTO chesapeake_council_member_votes
                   (vote_id, clip_id, meeting_date, member_name, vote,
                    agenda_item, title, result, category)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (vote_id, clip_id, meeting_date, member, vote,
                 agenda_item, v.get("title", ""), v.get("result", ""), cat)
            )
        count += 1
    conn.commit()
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def process_meeting(clip_id: int, doc_id: str, meeting_date: str,
                    conn: sqlite3.Connection, api_key: str, dry_run: bool) -> None:
    print(f"\n  Meeting clip_id={clip_id} ({meeting_date})")

    if not dry_run and already_scraped(conn, clip_id):
        print("    already scraped — skip")
        return

    pdf_path = download_minutes_pdf(clip_id, doc_id)
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

    if meeting_date == "unknown":
        first_line = minutes_text.strip().split("\n")[0].strip()
        date_from_pdf = re.search(
            r'(January|February|March|April|May|June|July|August|September|October|November|December)'
            r'\s+\d{1,2},\s*\d{4}',
            first_line, re.I
        )
        if date_from_pdf:
            meeting_date = date_from_pdf.group(0)

    if dry_run:
        print("    [dry-run] would send to Gemini")
        print("    --- minutes preview (first 300 chars) ---")
        print(minutes_text[:300])
        return

    votes = extract_votes_gemini(minutes_text, api_key)
    print(f"    Gemini extracted {len(votes)} vote items")

    if votes:
        n = store_votes(conn, clip_id, meeting_date, votes)
        print(f"    stored {n} vote records")
    else:
        print("    no votes extracted")

    time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Chesapeake City Council votes")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the N most recent meetings")
    parser.add_argument("--clip-id", type=int, help="Process a single meeting clip_id")
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

    print("Fetching Chesapeake City Council meeting archive...")
    meetings = fetch_meetings()
    print(f"  Found {len(meetings)} meetings with posted minutes")

    if args.clip_id:
        meetings = [m for m in meetings if m["clip_id"] == args.clip_id]
        if not meetings:
            meetings = [{"clip_id": args.clip_id, "doc_id": "", "meeting_date": "unknown", "title": ""}]
    elif args.limit:
        meetings = meetings[:args.limit]

    print(f"\nProcessing {len(meetings)} meetings...")
    for m in meetings:
        process_meeting(m["clip_id"], m["doc_id"], m["meeting_date"], conn, api_key, args.dry_run)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
