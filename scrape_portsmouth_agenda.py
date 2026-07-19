"""
Scrape the upcoming Portsmouth City Council agenda and store it in
portsmouth_upcoming_agenda for chat context injection.

Portsmouth's Granicus instance (portsmouthva.granicus.com, view_id=2 --
same source scrape_portsmouth_council.py's docstring flags as
agenda-only/no-recorded-votes) publishes a dedicated "Upcoming Events"
table on ViewPublisher.php, separate from the historical "Available
Archives" table -- confirmed live (2026-07-18/19): it currently shows
"Currently there are no Upcoming Events" (Portsmouth hasn't posted the
next meeting yet, same pattern already confirmed for Suffolk, Hampton,
and Newport News).

Despite scrape_portsmouth_council.py's docstring describing a "JS
bot-challenge" blocking Granicus, that challenge was NOT encountered
here with a real browser User-Agent on a plain HTTP GET (confirmed via
both AgendaViewer.php and DocumentViewer.php) -- no Playwright needed
at all, unlike the WebLink-based historical minutes scraper. The
AgendaViewer.php page itself is an HTML wrapper (not a raw PDF stream
like Chesapeake's) that embeds the real PDF via a Google Docs Viewer
iframe URL; the actual filename (portsmouthva_<hash>.pdf) is extracted
from that embedded URL with a regex, then fetched directly from
DocumentViewer.php?file=<name>&view=1, which DOES return raw PDF bytes.

Confirmed against a real past agenda (clip_id 286, Jul 14 2026, used
only to verify extraction logic): pypdf extracts almost no text (3
characters across 4 pages) -- this is a SCANNED document with no text
layer, same as Portsmouth's historical minutes. So, like
scrape_portsmouth_council.py, this sends the PDF bytes directly to
Gemini multimodal rather than extracting text first. The single Gemini
call also asks for any "how to participate" boilerplate visible in the
scan, since it can't be cheaply regex-extracted from an image the way
Chesapeake's and Hampton's text-layer PDFs allow.

Usage:
    python scrape_portsmouth_agenda.py
    python scrape_portsmouth_agenda.py --clip-id 286 --meeting-date 2026-07-14 --dry-run
"""
from __future__ import annotations

import argparse
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

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
GRANICUS_BASE = "https://portsmouthva.granicus.com"
VIEW_ID = 2
GEMINI_MODEL = "gemini-2.5-flash"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA}

_DATE_RE = re.compile(r'([A-Za-z]+&nbsp;\d{1,2},&nbsp;\d{4})')
_AGENDA_RE = re.compile(r'AgendaViewer\.php\?view_id=(\d+)&(?:amp;)?clip_id=(\d+)')
_PDF_FILE_RE = re.compile(r'(portsmouthva_[a-f0-9]+\.pdf)')


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
    raw = raw.replace("&nbsp;", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def fetch_upcoming_meetings() -> list[dict]:
    """Return [{clip_id, meeting_date, title, agenda_url}] from the
    dedicated 'Upcoming Events' table -- empty if none posted yet."""
    url = f"{GRANICUS_BASE}/ViewPublisher.php?view_id={VIEW_ID}"
    html_bytes = _get(url)
    if not html_bytes:
        return []
    html = html_bytes.decode("utf-8", errors="replace")

    section_m = re.search(r"Upcoming Events</h2>\s*<table.*?</table>", html, re.S)
    if not section_m:
        return []
    section = section_m.group(0)
    if "no Upcoming Events" in section:
        return []

    out: list[dict] = []
    for row_m in re.finditer(r"<tr>(.*?)</tr>", section, re.S):
        row = row_m.group(1)
        agenda_m = _AGENDA_RE.search(row)
        if not agenda_m:
            continue
        date_m = _DATE_RE.search(row)
        mdate = _parse_granicus_date(date_m.group(1)) if date_m else None
        if not mdate:
            continue
        name_m = re.search(r'id="([^"]+)"', row)
        title = name_m.group(1).replace("-", " ") if name_m else "City Council Meeting"
        out.append({
            "clip_id": int(agenda_m.group(2)),
            "meeting_date": mdate,
            "title": title,
            "agenda_url": f"{GRANICUS_BASE}/AgendaViewer.php?view_id={agenda_m.group(1)}&clip_id={agenda_m.group(2)}",
        })
    return out


def resolve_pdf_url(agenda_url: str) -> str | None:
    """AgendaViewer.php is an HTML wrapper embedding the real PDF via a
    Google Docs Viewer iframe -- pull the filename out and hit
    DocumentViewer.php directly, which returns raw PDF bytes."""
    html_bytes = _get(agenda_url)
    if not html_bytes:
        return None
    html = html_bytes.decode("utf-8", errors="replace")
    m = _PDF_FILE_RE.search(html)
    if not m:
        return None
    return f"{GRANICUS_BASE}/DocumentViewer.php?file={m.group(1)}&view=1"


def download_pdf(pdf_url: str) -> bytes | None:
    data = _get(pdf_url)
    if data and data[:4] == b"%PDF":
        return data
    return None


# ── Gemini extraction (scanned doc -- send PDF bytes directly, no text layer) ──

EXTRACT_PROMPT = """You are parsing an upcoming Portsmouth, Virginia City
Council meeting AGENDA (not yet held -- nothing has been voted on). This
is a scanned document image, not machine-readable text.

Return a single JSON object:
{
  "items": [
    {
      "item_ref": "string -- the item number/label as it appears (e.g.
                  '7', '7.a', an ordinance/resolution number if shown)",
      "section": "string -- the section heading it falls under, e.g.
                  'Consent Agenda', 'Public Hearings', 'Ordinances',
                  'Resolutions', 'Presentations'",
      "title": "string -- short description (max 150 chars)",
      "category": "one of: public-hearing, ordinance, resolution,
                   consent, presentation, appointment, procedural, other"
    }
  ],
  "how_to_participate": "string or null -- any 'how to speak at this
                         meeting' / public comment sign-up instructions
                         visible in the document (e.g. speaker card
                         registration, sign-in requirements, a City
                         Clerk phone number). null if no such text
                         appears anywhere in the document -- do not
                         invent this."
}

Rules:
- Skip purely procedural items with no substantive content (Call to
  Order, Invocation, Pledge of Allegiance, Roll Call, Adjournment)
  unless they are the only content.
- Return ONLY the JSON object -- no markdown fences, no explanation.
- If nothing parses, return {"items": [], "how_to_participate": null}.
"""


def extract_agenda_gemini(pdf_bytes: bytes, api_key: str) -> dict:
    if not _GENAI_OK:
        print("    ERROR: google-genai not installed. pip install google-genai")
        return {"items": [], "how_to_participate": None}
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
        if isinstance(data, dict):
            return {"items": data.get("items") or [], "how_to_participate": data.get("how_to_participate")}
        return {"items": [], "how_to_participate": None}
    except Exception as exc:
        print(f"    Gemini error: {exc}")
        return {"items": [], "how_to_participate": None}


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portsmouth_upcoming_agenda (
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
    parser = argparse.ArgumentParser(description="Scrape upcoming Portsmouth council agendas")
    parser.add_argument("--clip-id", type=int, help="Process a single clip_id (for testing)")
    parser.add_argument("--meeting-date", default=None, help="YYYY-MM-DD, required with --clip-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM portsmouth_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    # 45-day retention on past meetings -- builders/local.py links agenda
    # items to recorded outcomes, so keep the rows past meeting day.
    n_removed = conn.execute(
        "DELETE FROM portsmouth_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.clip_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --clip-id")
        meetings = [{
            "clip_id": args.clip_id, "meeting_date": args.meeting_date,
            "title": "City Council Meeting",
            "agenda_url": f"{GRANICUS_BASE}/AgendaViewer.php?view_id={VIEW_ID}&clip_id={args.clip_id}",
        }]
    else:
        print("Fetching Portsmouth City Council upcoming meetings...")
        meetings = fetch_upcoming_meetings()
        print(f"Found {len(meetings)} upcoming meeting(s) with a posted agenda")

    for mtg in meetings:
        print(f"\n  {mtg['meeting_date']} (clip_id={mtg['clip_id']}): {mtg['title']}")
        pdf_url = resolve_pdf_url(mtg["agenda_url"])
        if not pdf_url:
            print("    could not resolve underlying PDF -- skipping")
            continue
        pdf_bytes = download_pdf(pdf_url)
        if not pdf_bytes:
            print("    agenda PDF download failed -- skipping")
            continue
        print(f"    PDF {len(pdf_bytes):,} bytes")

        if args.dry_run:
            print("    [dry-run] would send to Gemini")
            continue

        result = extract_agenda_gemini(pdf_bytes, api_key)
        items = result["items"]
        participate = result["how_to_participate"]
        print(f"    extracted {len(items)} agenda items")
        if participate:
            print(f"    participation info: {participate[:100]}...")

        for it in items:
            conn.execute("""
                INSERT INTO portsmouth_upcoming_agenda
                    (event_id, meeting_date, item_ref, section, title, category,
                     agenda_url, how_to_participate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, item_ref) DO UPDATE SET
                    section = excluded.section, title = excluded.title,
                    category = excluded.category, how_to_participate = excluded.how_to_participate
            """, (
                mtg["clip_id"], mtg["meeting_date"], it.get("item_ref", ""),
                it.get("section", ""), it.get("title", ""), it.get("category", "other"),
                mtg["agenda_url"], participate,
            ))
        conn.commit()
        time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
