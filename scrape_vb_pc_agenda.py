"""
Scrape the upcoming Virginia Beach Planning Commission agenda and store it
in vb_pc_upcoming_agenda for chat context injection.

Rezoning/CUP cases are decided in substance at Planning Commission -- City
Council later acts on the Commission's recommendation. VB runs the same
IQM2 platform as scrape_vb_agenda.py's City Council scraper, but Planning
Commission's Detail_Meeting.aspx "Web Outline" is NOT populated for PC
meetings -- confirmed live (2026-07-19) that even genuinely-past PC
meetings (e.g. meeting_id 1701, Jul 8 2026, already held) return "The
meeting is not available at this time" with no outline table and no
FileOpen link at all. That's a real, structural gap for THAT specific
document type, not a fetch/rendering issue (confirmed both via plain HTTP
and a real browser, and confirmed City Council's own Detail_Meeting pages
DO work fine via the identical code path).

The actual agenda PDFs live one level up, on the Planning Commission's own
IQM2 "Board" page (discovered by following the site's own "Boards+" nav
link, at /Citizens/Board/1009-Planning-Commission) as a "Past Meetings"
archive with direct FileOpen.aspx?Type=14&ID=N links (Type=14, not
Council's Type=1) -- but that archive lags by roughly 2 meetings behind
the live calendar (confirmed: as of writing, agendas exist there for
May 13/Apr 8/Mar 11 but NOT for the two most recent past meetings, Jun 10
and Jul 8). So this checks the specific upcoming meeting's own
Detail_Meeting.aspx page directly for a FileOpen link (Type=14 or Type=1)
rather than relying on the board archive page, which can't reliably
distinguish "not yet posted" from "posted but not yet archived here".

Confirmed against a real PDF (FileOpen Type=14, ID=1488, Apr 8 2026
meeting -- used only to verify extraction logic): the agenda is much
thinner than every other city scraped this session -- no case number, no
stated proposal type (rezoning vs CUP vs variance), just a bare list of
applicant name + property address + City Council district + staff planner
under section headers (II. Withdrawals & Deferrals, III. Consent Agenda,
IV. Planning Commission Items). The extraction prompt is told not to
invent a proposal type it can't see. The Detail_Meeting.aspx HTML itself
(not the PDF) carries a real, genuine "sign up to speak" deadline via a JS
modal (`onclick="showSpeakerSignup(id)"`) -- extracted via regex as
how_to_participate, pointing residents to the meeting page itself since
the signup is JS-driven, not a plain URL.

Usage:
    python scrape_vb_pc_agenda.py
    python scrape_vb_pc_agenda.py --meeting-id 1698 --meeting-date 2026-04-08 --dry-run
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
from datetime import date, datetime, timedelta
from html import unescape
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
IQM2_BASE = "https://virginiabeachva.iqm2.com/Citizens"
GEMINI_MODEL = "gemini-2.5-flash"
HEADERS = {"User-Agent": "Mozilla/5.0 (VoteIQ/1.0)"}


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


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def find_upcoming_meeting(days: int = 90) -> dict | None:
    """Return {meeting_id, meeting_date} for the soonest Planning
    Commission Formal Hearing, or None if not found in the window."""
    today = date.today()
    end = today + timedelta(days=days)
    url = f"{IQM2_BASE}/calendar.aspx?From={today.strftime('%m/%d/%Y')}&To={end.strftime('%m/%d/%Y')}"
    html_bytes = _get(url)
    if not html_bytes:
        return None
    text = html_bytes.decode("utf-8", errors="replace")
    today_str = today.isoformat()

    candidates: list[dict] = []
    for m in re.finditer(r'Detail_Meeting\.aspx\?ID=(\d+)[^>]*title="([^"]+)"', text):
        mid, title = m.groups()
        title = unescape(title)
        if "Planning Commission" not in title or "Formal Hearing" not in title:
            continue
        dm = re.search(r'(\w+ \d{1,2},\s*\d{4})', title)
        if not dm:
            continue
        mdate = _parse_date(dm.group(1))
        if not mdate or mdate < today_str:
            continue
        candidates.append({"meeting_id": int(mid), "meeting_date": mdate})

    if not candidates:
        return None
    candidates.sort(key=lambda m: m["meeting_date"])
    return candidates[0]


def fetch_agenda_pdf_url_and_participation(meeting_id: int) -> tuple[str | None, str | None]:
    """Return (agenda_pdf_url, how_to_participate) for the given meeting."""
    url = f"{IQM2_BASE}/Detail_Meeting.aspx?ID={meeting_id}"
    html_bytes = _get(url)
    if not html_bytes:
        return None, None
    text = html_bytes.decode("utf-8", errors="replace")

    pdf_url = None
    m = re.search(r'FileOpen\.aspx\?Type=(?:14|1)&(?:amp;)?ID=(\d+)', text)
    if m:
        type_m = re.search(r'FileOpen\.aspx\?Type=(\d+)', m.group(0))
        pdf_url = f"{IQM2_BASE}/FileOpen.aspx?Type={type_m.group(1)}&ID={m.group(1)}"

    participate = None
    sm = re.search(r'to sign up to speak at this meeting\.', text)
    if sm:
        dm = re.search(r'Click here before\s*<strong>([^<]+)</strong>\s*to sign up to speak', text)
        if dm:
            deadline = unescape(dm.group(1)).strip()
            participate = (
                f"Sign up to speak by {deadline} at "
                f"{IQM2_BASE}/Detail_Meeting.aspx?ID={meeting_id} "
                "(click the podium icon)."
            )

    return pdf_url, participate


def download_pdf(url: str) -> bytes | None:
    data = _get(url)
    if data and data[:4] == b"%PDF":
        return data
    return None


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 25) -> str:
    if not _PYPDF_OK:
        raise RuntimeError("pypdf not installed -- run: pip install pypdf")
    reader = _pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = [pg.extract_text() or "" for pg in reader.pages[:max_pages]]
    return "\n".join(pages)


EXTRACT_PROMPT = """You are parsing an upcoming Virginia Beach, Virginia
Planning Commission meeting agenda (not yet held). This is a bare notice
document -- much thinner than a typical agenda. It has section headers
(e.g. "WITHDRAWALS & DEFERRALS", "CONSENT AGENDA", "PLANNING COMMISSION
ITEMS") followed by a flat list of entries, each just an applicant name,
then a property address, City Council district number, and a staff
planner's name. There is NO case number and NO stated proposal type
(rezoning vs conditional use permit vs variance) anywhere in this
document -- do not invent either of those; only report what's actually
printed.

Return a JSON array. Each object:
{
  "item_ref": "string -- the section name abbreviated + a running number
              within that section, e.g. 'PCI.3' for the 3rd item under
              Planning Commission Items (construct this yourself, there
              is no printed reference)",
  "section": "string -- the section header exactly as printed, e.g.
              'Consent Agenda', 'Planning Commission Items'",
  "title": "string -- the applicant name and property address, e.g.
              'Lighthouse Development, LLC -- 125, 129, 133, & 137 Ash
              Avenue (Council District 4)' -- max 150 chars",
  "category": "always 'other' -- the document does not state a proposal
               type, so do not guess one"
}

Rules:
- Skip section headers with no entries under them.
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
        CREATE TABLE IF NOT EXISTS vb_pc_upcoming_agenda (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id           INTEGER NOT NULL,
            meeting_date       TEXT NOT NULL,
            item_ref           TEXT,
            section            TEXT,
            title              TEXT,
            category           TEXT,
            agenda_url         TEXT,
            how_to_participate TEXT,
            lat                REAL,
            lng                REAL,
            geocoded_address   TEXT,
            scraped_at         TEXT DEFAULT (datetime('now')),
            UNIQUE(event_id, item_ref)
        )
    """)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape the upcoming Virginia Beach Planning Commission agenda")
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
        conn.execute("DELETE FROM vb_pc_upcoming_agenda")
        conn.commit()
        print("Cleared existing upcoming-agenda rows.")

    n_removed = conn.execute(
        "DELETE FROM vb_pc_upcoming_agenda WHERE meeting_date < date('now','-45 days')"
    ).rowcount
    conn.commit()
    if n_removed:
        print(f"Removed {n_removed} rows for meetings now in the past.")

    if args.meeting_id:
        if not args.meeting_date:
            raise SystemExit("--meeting-date YYYY-MM-DD required with --meeting-id")
        mtg = {"meeting_id": args.meeting_id, "meeting_date": args.meeting_date}
    else:
        print("Looking for the next Virginia Beach Planning Commission Formal Hearing...")
        mtg = find_upcoming_meeting()
        if not mtg:
            print("No upcoming Planning Commission Formal Hearing found in the next 90 days.")
            conn.close()
            return
        print(f"Found: {mtg['meeting_date']} (meeting_id={mtg['meeting_id']})")

    pdf_url, participate = fetch_agenda_pdf_url_and_participation(mtg["meeting_id"])
    if not pdf_url:
        print("  no agenda posted yet -- nothing to do.")
        conn.close()
        return
    if participate:
        print(f"  participation info: {participate[:100]}...")
    else:
        print("  no participation instructions found on this meeting page")

    pdf_bytes = download_pdf(pdf_url)
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

    meeting_url = f"{IQM2_BASE}/Detail_Meeting.aspx?ID={mtg['meeting_id']}"
    for it in items:
        conn.execute("""
            INSERT INTO vb_pc_upcoming_agenda
                (event_id, meeting_date, item_ref, section, title, category,
                 agenda_url, how_to_participate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, item_ref) DO UPDATE SET
                section = excluded.section, title = excluded.title,
                category = excluded.category, how_to_participate = excluded.how_to_participate
        """, (
            mtg["meeting_id"], mtg["meeting_date"], it.get("item_ref", ""),
            it.get("section", ""), it.get("title", ""), it.get("category", "other"),
            meeting_url, participate,
        ))
    conn.commit()
    time.sleep(1)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
