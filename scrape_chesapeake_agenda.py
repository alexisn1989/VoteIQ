"""
Scrape upcoming Chesapeake City Council agendas and store them in
chesapeake_upcoming_agenda for chat context injection.

Chesapeake's Granicus ViewPublisher archive (chesapeake.granicus.com,
view_id=29 -- same source scrape_chesapeake_council.py uses for historical
minutes) lists both past AND upcoming meetings in one table. A row is
upcoming when it has no MinutesViewer.php link yet -- its agenda is served
directly as a PDF via AgendaViewer.php?view_id=29&event_id=N, not as clean
HTML rows like Norfolk/VB's IQM2 calendar.

Unlike scrape_norfolk_agenda.py / scrape_vb_agenda.py (regex over clean
HTML, no Gemini), this uses Gemini for extraction -- same call as
scrape_chesapeake_council.py, and for the same reason: real Chesapeake
agenda PDFs nest lettered proffer sub-items (a., b., c.) *inside* a
lettered top-level agenda item (A., B.), which a naive regex over
PDF-extracted text can't reliably tell apart.

Also extracts the standing "how to speak at this meeting" instructions
from the agenda's own header (Speaker Card registration, City Clerk phone
number) -- the actual civic-action information, not just the agenda list.

Usage:
    python scrape_chesapeake_agenda.py
    python scrape_chesapeake_agenda.py --event-id 4009 --dry-run
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
GRANICUS_BASE = "https://chesapeake.granicus.com"
VIEW_ID = 29  # Regular City Council Meeting archive -- same as scrape_chesapeake_council.py
GEMINI_MODEL = "gemini-2.5-flash"

# chesapeake.granicus.com 403s on a bare "Mozilla/5.0" UA -- needs a real browser UA
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

_ROW_RE = re.compile(r'<tr class="listingRow">(.*?)</tr>', re.S)
_NAME_RE = re.compile(r'headers="Name"[^>]*>\s*(.+?)\s*</td>', re.S)
_DATE_RE = re.compile(r'([A-Za-z]+&nbsp;\s*\d{1,2},&nbsp;\d{4})')
_AGENDA_RE = re.compile(r'AgendaViewer\.php\?view_id=(\d+)&(?:amp;)?event_id=(\d+)')


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
                print(f"  WARN: {url}: {exc}")
    return None


def _parse_granicus_date(raw: str) -> str | None:
    """'Jul&nbsp;21,&nbsp;2026' -> '2026-07-21'."""
    raw = raw.replace("&nbsp;", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def fetch_upcoming_meetings() -> list[dict]:
    """Return [{event_id, meeting_date, title}] for rows with no minutes
    link yet and a meeting_date today or in the future."""
    url = f"{GRANICUS_BASE}/ViewPublisher.php?view_id={VIEW_ID}"
    html = _get(url)
    if not html:
        return []
    text = html.decode("utf-8", errors="replace")
    today_str = date.today().isoformat()

    out: list[dict] = []
    for row_m in _ROW_RE.finditer(text):
        row = row_m.group(1)
        if "MinutesViewer.php" in row:
            continue  # already happened

        agenda_m = _AGENDA_RE.search(row)
        if not agenda_m:
            continue  # no agenda posted yet either -- nothing to show

        date_m = _DATE_RE.search(row)
        if not date_m:
            continue
        mdate = _parse_granicus_date(date_m.group(1))
        if not mdate or mdate < today_str:
            continue

        name_m = _NAME_RE.search(row)
        title = re.sub(r"<[^>]+>", "", name_m.group(1)).strip() if name_m else "City Council Meeting"
        if "council" not in title.lower():
            continue  # skip Work Sessions / committee meetings, keep full Council meetings

        out.append({
            "event_id": int(agenda_m.group(2)),
            "meeting_date": mdate,
            "title": title,
            "agenda_url": f"{GRANICUS_BASE}/AgendaViewer.php?view_id={VIEW_ID}&event_id={agenda_m.group(2)}",
        })

    seen: set[int] = set()
    deduped = []
    for m in out:
        if m["event_id"] not in seen:
            seen.add(m["event_id"])
            deduped.append(m)
    return deduped


def download_agenda_pdf(agenda_url: str) -> bytes | None:
    data = _get(agenda_url)
    if data and data[:4] == b"%PDF":
        return data
    return None


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 25) -> str:
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed -- run: pip install pypdf")
    reader = _pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = [pg.extract_text() or "" for pg in reader.pages[:max_pages]]
    return "\n".join(pages)


# ── Standing "how to speak" instructions -- fixed template, cheap regex,
# no need to burn a Gemini call on boilerplate that repeats every meeting ──

def extract_participation_info(text: str) -> str | None:
    m = re.search(
        r"(Speaker Cards? will be accepted.{0,400}?\(8:00\s*a\.?m\.?\s*-\s*5:00\s*p\.?m\.?\))",
        text, re.S,
    )
    if not m:
        return None
    info = re.sub(r"\s+", " ", m.group(1)).strip()
    return info


EXTRACT_PROMPT = """You are parsing an upcoming Chesapeake, Virginia City
Council meeting AGENDA (not yet held -- nothing has been voted on).

Extract each TOP-LEVEL numbered section (1., 2., 3. ...) and, where a
section has lettered items (A., B., C.), each lettered item within it.
IMPORTANT: some lettered items (like rezoning proffers) contain their own
NESTED lower-case lettered sub-points (a., b., c.) or numbered conditions
(1., 2., 3.) as supporting detail -- do NOT extract those nested points as
separate items, they belong inside the parent lettered item's own
description.

Return a JSON array. Each object:
{
  "item_ref": "string -- e.g. '7.A' for section 7 item A, or just '5' for
               a section with no lettered items",
  "section": "string -- the section name, e.g. 'PUBLIC HEARING'",
  "title": "string -- short description (max 150 chars) -- for rezoning/
            permit items, include the case number if present (e.g.
            'PLN-REZ-2025-019 Charlton Woods')",
  "category": "one of: public-hearing, ordinance, resolution, consent,
               presentation, appointment, procedural, other"
}

Rules:
- Skip purely procedural sections with no substantive content (Invocation,
  Pledge of Allegiance, Roll Call) unless they're the only content.
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
        CREATE TABLE IF NOT EXISTS chesapeake_upcoming_agenda (
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
    parser = argparse.ArgumentParser(description="Scrape upcoming Chesapeake council agendas")
    parser.add_argument("--event-id", type=int, help="Process a single event_id (for testing)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM chesapeake_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    # Keep past meetings for 45 days so builders/local.py can link agenda
    # items to their recorded outcomes ("what happened with that rezoning?")
    # before they age out -- deleting at meeting_date lost the link exactly
    # when it became useful.
    n_removed = conn.execute(
        "DELETE FROM chesapeake_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.event_id:
        meetings = [{
            "event_id": args.event_id, "meeting_date": "unknown",
            "title": "City Council Meeting",
            "agenda_url": f"{GRANICUS_BASE}/AgendaViewer.php?view_id={VIEW_ID}&event_id={args.event_id}",
        }]
    else:
        print("Fetching Chesapeake City Council upcoming meetings...")
        meetings = fetch_upcoming_meetings()
        print(f"Found {len(meetings)} upcoming meeting(s) with a posted agenda")

    for mtg in meetings:
        print(f"\n  {mtg['meeting_date']} (event_id={mtg['event_id']}): {mtg['title']}")
        pdf_bytes = download_agenda_pdf(mtg["agenda_url"])
        if not pdf_bytes:
            print("    agenda PDF download failed -- skipping")
            continue

        text = extract_pdf_text(pdf_bytes)
        if len(text) < 200:
            print("    agenda text too short -- skipping")
            continue

        participate = extract_participation_info(text)
        if participate:
            print(f"    participation info: {participate[:100]}...")
        else:
            print("    no participation instructions found in this agenda")

        if args.dry_run:
            print("    [dry-run] would send to Gemini")
            continue

        items = extract_items_gemini(text, api_key)
        print(f"    extracted {len(items)} agenda items")

        for it in items:
            conn.execute("""
                INSERT INTO chesapeake_upcoming_agenda
                    (event_id, meeting_date, item_ref, section, title, category,
                     agenda_url, how_to_participate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, item_ref) DO UPDATE SET
                    section = excluded.section, title = excluded.title,
                    category = excluded.category, how_to_participate = excluded.how_to_participate
            """, (
                mtg["event_id"], mtg["meeting_date"], it.get("item_ref", ""),
                it.get("section", ""), it.get("title", ""), it.get("category", "other"),
                mtg["agenda_url"], participate,
            ))
        conn.commit()
        time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
