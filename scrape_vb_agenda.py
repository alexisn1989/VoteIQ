#!/usr/bin/env python3
"""
scrape_vb_agenda.py

Scrapes upcoming Virginia Beach City Council (Formal Session) agendas
from the IQM2 citizen portal and stores agenda items in vb_upcoming_agenda.

Usage:
  python scrape_vb_agenda.py [--db PATH] [--days N] [--reset]
"""

import argparse
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import date, datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")

_IQM2_BASE  = "https://virginiabeachva.iqm2.com/Citizens"
_CAL_URL    = f"{_IQM2_BASE}/Calendar.aspx?View=List"
_HEADERS    = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Agenda sections we want to capture (skip procedural items)
_CAPTURE_SECTIONS = {
    "ORDINANCES/RESOLUTION", "ORDINANCES/RESOLUTIONS",
    "ORDINANCE", "RESOLUTION", "RESOLUTIONS",
    "PUBLIC HEARING", "APPOINTMENTS", "PLANNING ITEMS",
    "CONSENT AGENDA",
}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _unescape(s: str) -> str:
    s = s.replace("&#09;", "\t").replace("&#13;", "\r").replace("&#10;", "\n")
    s = s.replace("&amp;", "&").replace("&nbsp;", " ").replace("&laquo;", "«")
    s = s.replace("&#8217;", "'").replace("&#8220;", '"').replace("&#8221;", '"')
    s = re.sub(r"&#\d+;", "", s)
    return s


def _parse_date(raw: str) -> str | None:
    """Convert 'TUESDAY, JANUARY 6, 2026  6:00 PM' → '2026-01-06'."""
    raw = _unescape(raw).strip()
    m = re.search(r"(\w+),\s+(\w+)\s+(\d+),\s+(\d{4})", raw)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(2)} {m.group(3)} {m.group(4)}", "%B %d %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _get_upcoming_council_meetings(days: int) -> list[tuple[str, str]]:
    """Return list of (meeting_id, meeting_date) for upcoming City Council sessions."""
    html = _fetch(_CAL_URL)
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    today_str = date.today().isoformat()

    # title attr contains date + board + type info
    entries = re.findall(
        r'Detail_Meeting\.aspx\?ID=(\d+)[^>]*title="([^"]+)"',
        html,
    )

    results = []
    for mid, title in entries:
        title = _unescape(title)
        if "City Council" not in title:
            continue
        if "Formal Session" not in title:
            continue
        mdate = _parse_date(title)
        if not mdate:
            continue
        if mdate < today_str or mdate > cutoff:
            continue
        results.append((mid, mdate))

    results.sort(key=lambda x: x[1])
    return results


def _parse_meeting_agenda(meeting_id: str, meeting_date: str) -> list[dict]:
    """Parse agenda items from a meeting detail page."""
    url = f"{_IQM2_BASE}/Detail_Meeting.aspx?ID={meeting_id}"
    html = _fetch(url)

    items = []
    current_section = ""
    current_section_letter = ""
    item_seq = 0

    # Find all table rows
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)

    for row in rows:
        # Extract cells
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        if not cells:
            continue

        def cell_text(c):
            t = re.sub(r"<[^>]+>", " ", c)
            t = re.sub(r"\s+", " ", _unescape(t)).strip()
            return t

        texts = [cell_text(c) for c in cells]
        full_text = " ".join(t for t in texts if t).strip()

        if not full_text or len(full_text) < 3:
            continue

        # Section header: has strong + section letter (I, II, J, K …)
        # Pattern: Num cell = "J." or "VII." + Title cell in strong
        num_cell = texts[1] if len(texts) > 1 else ""
        title_cell_raw = cells[3] if len(cells) > 3 else (cells[2] if len(cells) > 2 else "")
        title_cell = cell_text(title_cell_raw) if title_cell_raw else ""

        # Detect section header (bold, no LegiFile link)
        is_section_header = (
            "<strong>" in (cells[1] if len(cells) > 1 else "") and
            "LegiFile" not in row and
            re.match(r"^[A-Z]+\.?\s*$", num_cell.replace(".", "").strip()) if num_cell else False
        )

        # Detect LegiFile agenda item (&ID= not MeetingID=)
        lf_match = re.search(r"Detail_LegiFile\.aspx\?[^\"]*&ID=(\d+)", row)

        if is_section_header and title_cell:
            current_section = title_cell.upper().strip("*• ")
            current_section_letter = num_cell.strip(". ") if num_cell else ""
            item_seq = 0
            continue

        if lf_match:
            lf_id = int(lf_match.group(1))
            # Title is the link text
            link_text_m = re.search(
                r"Detail_LegiFile\.aspx[^>]+>([^<]+)<", row
            )
            item_title = link_text_m.group(1).strip() if link_text_m else title_cell
            item_title = re.sub(r"\s+", " ", _unescape(item_title)).strip()

            if not item_title or len(item_title) < 5:
                continue

            item_seq += 1
            item_ref = f"{current_section_letter}.{item_seq}" if current_section_letter else str(item_seq)

            # Classify category
            cat = "Other"
            tup = item_title.upper()
            if tup.startswith("ORDINANCE"):
                cat = "Ordinance"
            elif tup.startswith("RESOLUTION"):
                cat = "Resolution"
            elif "PUBLIC HEARING" in current_section or "HEARING" in tup:
                cat = "Public Hearing"
            elif "APPOINT" in current_section:
                cat = "Appointment"
            elif "CONSENT" in current_section:
                cat = "Consent"

            items.append({
                "meeting_id":   int(meeting_id),
                "meeting_date": meeting_date,
                "section":      current_section,
                "item_ref":     item_ref,
                "title":        item_title[:500],
                "category":     cat,
                "legifile_id":  lf_id,
                "status":       "scheduled",
            })

    return items


def _create_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vb_upcoming_agenda (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id   INTEGER NOT NULL,
            meeting_date TEXT    NOT NULL,
            section      TEXT,
            item_ref     TEXT,
            title        TEXT,
            category     TEXT,
            legifile_id  INTEGER,
            status       TEXT DEFAULT 'scheduled',
            UNIQUE(meeting_id, legifile_id)
        )
    """)
    conn.commit()


def _upsert_items(conn: sqlite3.Connection, items: list[dict]) -> int:
    count = 0
    for it in items:
        conn.execute("""
            INSERT INTO vb_upcoming_agenda
                (meeting_id, meeting_date, section, item_ref, title, category, legifile_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(meeting_id, legifile_id) DO UPDATE SET
                meeting_date = excluded.meeting_date,
                section      = excluded.section,
                item_ref     = excluded.item_ref,
                title        = excluded.title,
                category     = excluded.category,
                status       = excluded.status
        """, (
            it["meeting_id"], it["meeting_date"], it["section"],
            it["item_ref"], it["title"], it["category"],
            it["legifile_id"], it["status"],
        ))
        count += 1
    conn.commit()
    return count


def main() -> int:
    p = argparse.ArgumentParser(description="Scrape VB City Council agendas into polls.db")
    p.add_argument("--db",    default=_DEFAULT_DB)
    p.add_argument("--days",  type=int, default=90, help="Days ahead to look")
    p.add_argument("--reset", action="store_true",  help="Drop and recreate table")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    if args.reset:
        conn.execute("DROP TABLE IF EXISTS vb_upcoming_agenda")
        conn.commit()
        print("Dropped vb_upcoming_agenda")
    _create_table(conn)

    print(f"Fetching VB City Council meetings for next {args.days} days…")
    try:
        meetings = _get_upcoming_council_meetings(args.days)
    except Exception as exc:
        print(f"ERROR fetching calendar: {exc}", file=sys.stderr)
        conn.close()
        return 1

    print(f"  Found {len(meetings)} upcoming Formal Sessions")

    total = 0
    for mid, mdate in meetings:
        print(f"  Parsing meeting {mid} ({mdate})…", end=" ")
        try:
            items = _parse_meeting_agenda(mid, mdate)
            n = _upsert_items(conn, items)
            total += n
            print(f"{n} items")
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)

    conn.close()
    print(f"Done — {total} agenda items stored in {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
