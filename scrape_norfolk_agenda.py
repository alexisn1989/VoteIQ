"""
scrape_norfolk_agenda.py
Fetch upcoming Norfolk City Council Formal Session agendas from IQM2
and store them in norfolk_upcoming_agenda for context injection.

Designed to run as a Render cron job (lightweight — no Gemini, no PDFs).

Usage:
    python scrape_norfolk_agenda.py            # next 90 days
    python scrape_norfolk_agenda.py --days 30
    python scrape_norfolk_agenda.py --reset
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

DB_PATH   = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"
IQM2_BASE = "https://norfolkcityva.iqm2.com/Citizens"
HEADERS   = {"User-Agent": "Mozilla/5.0 (VoteIQ/1.0)"}


# ── HTTP helper ───────────────────────────────────────────────────────────────

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


# ── DB setup ──────────────────────────────────────────────────────────────────

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_upcoming_agenda (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id   INTEGER NOT NULL,
            meeting_date TEXT NOT NULL,
            item_ref     TEXT,
            title        TEXT,
            status       TEXT DEFAULT 'scheduled',
            agenda_url   TEXT,
            scraped_at   TEXT DEFAULT (datetime('now')),
            UNIQUE(meeting_id, item_ref)
        )
    """)
    conn.commit()


# ── IQM2: upcoming Formal Session meeting IDs ─────────────────────────────────

def fetch_upcoming_meetings(days: int = 90) -> list[dict]:
    today = date.today()
    end   = today + timedelta(days=days)
    url   = (
        f"{IQM2_BASE}/calendar.aspx"
        f"?From={today.strftime('%m/%d/%Y')}"
        f"&To={end.strftime('%m/%d/%Y')}"
    )
    html = _get(url)
    if not html:
        return []

    text = html.decode("utf-8", errors="replace")
    meetings: list[dict] = []
    seen: set[int] = set()

    for m in re.finditer(r'Detail_Meeting\.aspx\?ID=(\d+)', text):
        mid = int(m.group(1))
        if mid in seen:
            continue
        ctx_start = max(0, m.start() - 400)
        ctx = text[ctx_start: m.end() + 200]
        if "Formal Session" not in ctx:
            continue
        dm = re.search(r'(\w+ \d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})', ctx)
        date_str = dm.group(1).strip() if dm else "unknown"
        seen.add(mid)
        meetings.append({"meeting_id": mid, "date_str": date_str})

    return meetings


# ── IQM2: agenda items from meeting detail page ───────────────────────────────

def fetch_agenda_items(meeting_id: int) -> list[dict]:
    """
    Parse the IQM2 detail page for agenda items.
    Returns list of {item_ref, title, agenda_url}.
    Empty list if agenda not yet posted.
    """
    url  = f"{IQM2_BASE}/Detail_Meeting.aspx?ID={meeting_id}"
    html = _get(url)
    if not html:
        return []

    text = html.decode("utf-8", errors="replace")

    # If agenda not yet available
    if "not available at this time" in text.lower():
        return []

    # Check for agenda PDF (Type=1)
    agenda_url = None
    m = re.search(r'FileOpen\.aspx\?Type=1&(?:amp;)?ID=(\d+)', text)
    if m:
        agenda_url = f"{IQM2_BASE}/FileOpen.aspx?Type=1&ID={m.group(1)}&Inline=True"

    # Parse legislative file rows — each item has a ref (e.g. "R-1", "PH-2", "C-3")
    # IQM2 table rows look like: <td>R-1</td><td>Title of item</td>
    items: list[dict] = []

    # Pattern 1: agenda item rows with item number + title
    for row_m in re.finditer(
        r'<tr[^>]*>.*?</tr>', text, re.DOTALL | re.IGNORECASE
    ):
        row = row_m.group(0)
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        if len(cells) < 2:
            continue
        ref_raw  = re.sub(r'<[^>]+>', '', cells[0]).strip()
        title_raw = re.sub(r'<[^>]+>', '', cells[1]).strip()
        title_raw = unescape(title_raw).strip()
        # Item refs look like R-1, PH-2, C-3, O-4 etc.
        if re.match(r'^[A-Z]{1,3}-\d+$', ref_raw) and title_raw:
            items.append({
                "item_ref":   ref_raw,
                "title":      title_raw[:300],
                "agenda_url": agenda_url,
            })

    # Pattern 2: if no table rows found, look for list items
    if not items:
        for li_m in re.finditer(r'<li[^>]*>(.*?)</li>', text, re.DOTALL | re.IGNORECASE):
            raw = re.sub(r'<[^>]+>', '', li_m.group(1)).strip()
            raw = unescape(raw)
            if len(raw) > 10:
                items.append({
                    "item_ref":   None,
                    "title":      raw[:300],
                    "agenda_url": agenda_url,
                })

    # If nothing parsed but agenda PDF exists, record a placeholder
    if not items and agenda_url:
        items.append({
            "item_ref":   "AGENDA",
            "title":      "Agenda posted (PDF only — items not yet parsed)",
            "agenda_url": agenda_url,
        })

    return items


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",  type=int, default=90)
    parser.add_argument("--db",    default=str(DB_PATH))
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM norfolk_upcoming_agenda")
        conn.commit()
        print("Table cleared.")

    # Remove meetings that are now in the past
    today_str = date.today().strftime("%B %d, %Y").lstrip("0").upper()
    conn.execute("""
        DELETE FROM norfolk_upcoming_agenda
        WHERE status != 'minutes_available'
          AND date(
              substr(meeting_date, -4) || '-' ||
              CASE substr(meeting_date, 1, 3)
                WHEN 'JAN' THEN '01' WHEN 'FEB' THEN '02' WHEN 'MAR' THEN '03'
                WHEN 'APR' THEN '04' WHEN 'MAY' THEN '05' WHEN 'JUN' THEN '06'
                WHEN 'JUL' THEN '07' WHEN 'AUG' THEN '08' WHEN 'SEP' THEN '09'
                WHEN 'OCT' THEN '10' WHEN 'NOV' THEN '11' WHEN 'DEC' THEN '12'
                ELSE '01' END || '-' ||
              printf('%02d', CAST(trim(substr(meeting_date, 5,
                  instr(substr(meeting_date, 5), ',') - 1)) AS INTEGER))
          ) < date('now', '-1 day')
    """)
    conn.commit()

    meetings = fetch_upcoming_meetings(args.days)
    print(f"Found {len(meetings)} upcoming Formal Sessions")

    now = datetime.now(timezone.utc).isoformat()
    total_items = 0

    for mtg in meetings:
        mid  = mtg["meeting_id"]
        dstr = mtg["date_str"].upper()
        items = fetch_agenda_items(mid)

        if not items:
            # Record meeting date with no items yet
            conn.execute("""
                INSERT OR IGNORE INTO norfolk_upcoming_agenda
                    (meeting_id, meeting_date, item_ref, title, status, scraped_at)
                VALUES (?, ?, NULL, 'Agenda not yet posted', 'pending', ?)
            """, (mid, dstr, now))
            print(f"  {dstr} (ID={mid}): agenda not yet posted")
        else:
            for item in items:
                conn.execute("""
                    INSERT OR REPLACE INTO norfolk_upcoming_agenda
                        (meeting_id, meeting_date, item_ref, title,
                         status, agenda_url, scraped_at)
                    VALUES (?, ?, ?, ?, 'agenda_posted', ?, ?)
                """, (mid, dstr, item["item_ref"], item["title"],
                      item["agenda_url"], now))
            total_items += len(items)
            print(f"  {dstr} (ID={mid}): {len(items)} agenda items")

    conn.commit()
    conn.close()
    print(f"\nDone. {total_items} agenda items stored.")


if __name__ == "__main__":
    main()
