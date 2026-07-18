"""
Scrape Suffolk City Council meeting vote records.

Suffolk runs a CivicEngage/Granicus "Agenda Center"
(suffolkva.us/AgendaCenter) rather than Legistar. The site's own
year-selector triggers a plain AJAX call with no auth/session required:

    POST /AgendaCenter/UpdateCategoryList
    body: year=<YYYY>&catID=11&startDate=&endDate=&term=&prevVersionScreen=false

(catID 11 = "City Council Meeting Agendas" -- confirmed via the year-link
hrefs on the page itself, e.g. `changeYear(2025, 11, 'a1')`.) This returns
an HTML fragment containing every meeting's document link for that year,
e.g. `/AgendaCenter/ViewFile/Agenda/_11202024-3415`, going back to 2004.
Reproduced with a bare `curl`/`requests` call, no cookies needed -- this
was the actual enumeration blocker from the prior research session,
solved by executing the page's own `changeYear()` JS to find what
network request it fires.

The documents themselves start as pre-meeting agendas and get updated
in place after each meeting with the outcome directly under each item:

    Approved 8-0
    Approved 7-1 (Council Member Bennett voted in opposition.)

Confirmed (real Nov 2024 sample): no attendance/roll-call list appears
anywhere in the document, so a unanimous "N-0" tally has no per-member
breakdown available in the source at all -- only named dissenters on
split votes do. Rather than fabricate full-roster yes-votes we can't
verify, this only records the named dissenter(s); unanimous votes are
stored at the aggregate (vote_count/result) level with an empty votes
dict. This is a real, honest difference from Hampton/Chesapeake/
Portsmouth's richer per-item roll calls -- not a bug, a source
limitation -- but dissent patterns (who breaks ranks, on what) are
exactly the signal this app cares about most.

Same as Chesapeake/Portsmouth/Hampton, this sends extracted text to
Gemini for structured extraction rather than a fragile regex, since
item segmentation (numbered items, multi-line titles, page breaks) is
the same finicky problem those scrapers already solved that way.

Council membership changes over time, so votes are captured by whatever
last names actually appear in a given meeting's own text, not restricted
to the current roster.

Flow:
  1. Enumerate City Council Meeting Agenda documents via
     UpdateCategoryList (plain HTTP, no browser needed)
  2. Download each agenda PDF (plain HTTP; no auth needed)
  3. Extract text with pypdf
  4. Send text to Gemini 2.5 Flash for structured vote extraction
  5. Store in suffolk_council_votes / suffolk_council_member_votes

Usage:
    python scrape_suffolk_council.py --start-year 2020
    python scrape_suffolk_council.py --doc-id 3415 --meeting-date 2024-11-20 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.request
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
DB_PATH = BASE_DIR / "polls.db"
CACHE_DIR = BASE_DIR / "suffolk_council_cache"
SITE_BASE = "https://www.suffolkva.us"
CATEGORY_ID_AGENDAS = 11  # "City Council Meeting Agendas" -- confirmed via changeYear() hrefs
GEMINI_MODEL = "gemini-2.5-flash"
_USER_AGENT = "VoteIQ/1.0 (Virginia civic data; alexisnieuwenhuys89@gmail.com)"


# ── Meeting enumeration (plain HTTP -- no browser needed) ────────────────────

def enumerate_meetings(start_year: int, end_year: int) -> list[dict]:
    """Return [{doc_id, meeting_date, url_date}] for City Council Meeting
    Agenda documents across start_year..end_year inclusive."""
    meetings: list[dict] = []
    for year in range(start_year, end_year + 1):
        body = (
            f"year={year}&catID={CATEGORY_ID_AGENDAS}"
            f"&startDate=&endDate=&term=&prevVersionScreen=false"
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{SITE_BASE}/AgendaCenter/UpdateCategoryList",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            print(f"  {year}: request failed ({exc}) -- skipping")
            continue

        year_count = 0
        for m in re.finditer(r"ViewFile/Agenda/_(\d{2})(\d{2})(\d{4})-(\d+)", html):
            mm, dd, yyyy, doc_id = m.groups()
            meetings.append({
                "doc_id": doc_id,
                "meeting_date": f"{yyyy}-{mm}-{dd}",
                "url_date": f"{mm}{dd}{yyyy}",
            })
            year_count += 1
        print(f"  {year}: {year_count} documents found")
        time.sleep(0.5)  # light pacing

    # de-dupe by doc_id (a document can theoretically appear in more than
    # one year's fragment near year boundaries)
    seen: set[str] = set()
    out = []
    for mt in meetings:
        if mt["doc_id"] not in seen:
            seen.add(mt["doc_id"])
            out.append(mt)
    return out


def download_agenda_pdf(doc_id: str, url_date: str) -> Path | None:
    cache_path = CACHE_DIR / f"doc_{doc_id}.pdf"
    if cache_path.exists():
        return cache_path
    url = f"{SITE_BASE}/AgendaCenter/ViewFile/Agenda/_{url_date}-{doc_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
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


def extract_pdf_text(pdf_path: Path, max_pages: int = 30) -> str:
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed -- run: pip install pypdf")
    reader = _pypdf.PdfReader(str(pdf_path))
    pages = [pg.extract_text() or "" for pg in reader.pages[:max_pages]]
    return "\n".join(pages)


# ── Gemini vote extraction ────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are parsing a Suffolk, Virginia City Council agenda
document. These documents start as pre-meeting agendas and get updated
after the meeting with the outcome written directly beneath each numbered
item, in forms like:

    Approved 8-0
    Approved 7-1 (Council Member Bennett voted in opposition.)
    Denied 6-2 (Council Members Smith and Jones voted in opposition.)

Exact wording varies -- Approved/Denied/Adopted/Tabled/Failed as the verb,
and dissent may be phrased "voted in opposition", "voted no", "opposed",
etc. Council membership changes over time -- use whatever last names
actually appear in THIS document, do not assume a fixed roster.

IMPORTANT -- this document does NOT include an attendance/roll-call list,
so for a UNANIMOUS outcome (e.g. "8-0") you cannot know who the individual
members were. Do NOT invent or guess member names for unanimous votes --
leave "votes" as an empty object {} in that case. Only populate "votes"
with members who are EXPLICITLY named in the text (i.e. named dissenters
on a split vote, or any other individually-named vote/abstention/absence
if the text states one).

Extract EVERY numbered agenda item that has a recorded outcome (skip items
that say "No City Council action required" or have no outcome yet -- this
may be a pre-meeting agenda with no results at all, in which case return
[]) and return a JSON array.

Each vote object must have:
{
  "agenda_item": "string -- the item number as it appears (e.g. '23')",
  "title": "string -- short description of what was voted on (max 120 chars)",
  "motion": "string -- Approve, Deny, Adopt, Table, Amend, etc.",
  "votes": {
    "MEMBER_LAST_NAME": "no|yes|abstain|absent"
  },
  "result": "passed|failed|tabled",
  "vote_count": "e.g. '8-0' or '7-1' -- copy exactly as printed"
}

Rules:
- Use council member LAST NAMES ONLY as keys (e.g. "Bennett" not "Council
  Member Bennett")
- votes MUST be {} for any outcome where no individual member is named in
  the text -- never fabricate a full roster
- Return ONLY a valid JSON array -- no markdown fences, no explanation
- If no votes found, return []
"""


def extract_votes_gemini(agenda_text: str, api_key: str) -> list[dict]:
    if not _GENAI_OK:
        print("    ERROR: google-genai not installed. pip install google-genai")
        return []
    client = _genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[EXTRACT_PROMPT, "\n\n---AGENDA DOCUMENT---\n\n" + agenda_text],
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
        CREATE TABLE IF NOT EXISTS suffolk_council_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER, meeting_date TEXT, title TEXT,
            agenda_item TEXT, motion TEXT,
            result TEXT, vote_count TEXT, votes_json TEXT, category TEXT,
            scraped_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS suffolk_council_member_votes (
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
    a = (agenda_item or "").strip()
    # Suffolk's agenda item numbers are plain integers (e.g. "23"), unlike
    # Hampton's file-number format -- everything with a recorded vote here
    # is treated as substantive; procedural-only items (no vote) never
    # reach this function since extraction skips "No action required" rows.
    return "substantive" if a else "procedural"


def store_meeting_votes(doc_id: str, meeting_date: str, votes: list[dict]) -> int:
    conn = sqlite3.connect(DB_PATH)
    stored = 0
    for v in votes:
        agenda_item = v.get("agenda_item", "")
        cur = conn.execute(
            "SELECT id FROM suffolk_council_votes WHERE meeting_id=? AND agenda_item=?",
            (doc_id, agenda_item),
        ).fetchone()
        if cur:
            vote_id = cur[0]
        else:
            conn.execute(
                "INSERT INTO suffolk_council_votes "
                "(meeting_id, meeting_date, title, agenda_item, motion, "
                " result, vote_count, votes_json, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (doc_id, meeting_date, v.get("title", ""), agenda_item,
                 v.get("motion", ""), v.get("result", ""), v.get("vote_count", ""),
                 json.dumps(v.get("votes", {})), _categorize(agenda_item)),
            )
            vote_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for member, choice in (v.get("votes") or {}).items():
            exists = conn.execute(
                "SELECT 1 FROM suffolk_council_member_votes WHERE vote_id=? AND member_name=?",
                (vote_id, member),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO suffolk_council_member_votes "
                    "(vote_id, meeting_id, meeting_date, member_name, vote, "
                    " agenda_item, title, result, category) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (vote_id, doc_id, meeting_date, member, choice,
                     agenda_item, v.get("title", ""), v.get("result", ""),
                     _categorize(agenda_item)),
                )
                stored += 1
    conn.commit()
    conn.close()
    return stored


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape Suffolk council votes")
    ap.add_argument("--start-year", type=int, default=2020)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--doc-id", default=None, help="Single document (for testing)")
    ap.add_argument("--meeting-date", default=None, help="YYYY-MM-DD, required with --doc-id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set")
        return

    init_db()

    if args.doc_id:
        if not args.meeting_date:
            print("ERROR: --meeting-date YYYY-MM-DD required with --doc-id")
            return
        yyyy, mm, dd = args.meeting_date.split("-")
        meetings = [{
            "doc_id": args.doc_id,
            "meeting_date": args.meeting_date,
            "url_date": f"{mm}{dd}{yyyy}",
        }]
    else:
        print(f"Enumerating City Council Meeting Agendas {args.start_year}-{args.end_year}...")
        meetings = enumerate_meetings(args.start_year, args.end_year)
        print(f"Found {len(meetings)} documents")

    total_stored = 0
    for i, mt in enumerate(meetings, 1):
        print(f"  [{i}/{len(meetings)}] doc {mt['doc_id']} ({mt['meeting_date']})")
        pdf_path = download_agenda_pdf(mt["doc_id"], mt["url_date"])
        if not pdf_path:
            print("    no PDF -- skipping")
            continue
        text = extract_pdf_text(pdf_path)
        if len(text) < 200:
            print("    agenda text too short -- skipping")
            continue
        votes = extract_votes_gemini(text, api_key)
        print(f"    Gemini extracted {len(votes)} vote items")
        if args.dry_run:
            for v in votes[:3]:
                print("   ", v)
            continue
        stored = store_meeting_votes(mt["doc_id"], mt["meeting_date"], votes)
        total_stored += stored
        time.sleep(1)  # light pacing between Gemini calls

    if not args.dry_run:
        conn = sqlite3.connect(DB_PATH)
        vcount = conn.execute("SELECT COUNT(*) FROM suffolk_council_votes").fetchone()[0]
        mvcount = conn.execute("SELECT COUNT(*) FROM suffolk_council_member_votes").fetchone()[0]
        conn.close()
        print(f"\nDone -- {total_stored} new member-vote rows this run")
        print(f"Totals: {vcount} distinct votes, {mvcount} member-vote records")


if __name__ == "__main__":
    main()
