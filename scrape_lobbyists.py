#!/usr/bin/env python3
"""
Scrape Virginia Ethics Council lobbyist registrations from ethicssearch.dls.virginia.gov.

The CAPTCHA is client-side only — just grab a session cookie from the root page
and POST directly to Search.aspx.  Iterates A-Z on principal name to cover all
registrations for each session year.

Output: lobbyist_registrations table in polls.db
  columns: id, year_range, lobbyist_name, principal_name, authorizing_officer,
           form_type, submitted_date, status, filing_id, scraped_at

Usage:
    python scrape_lobbyists.py                   # 2024-2025, 2025-2026, 2026-2027
    python scrape_lobbyists.py --years 2025-2026
    python scrape_lobbyists.py --reset           # drop table and re-scrape
"""
import argparse
import re
import sqlite3
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings()

DB_PATH = Path(__file__).parent / "polls.db"
BASE    = "https://ethicssearch.dls.virginia.gov"
SLEEP   = 1.0   # seconds between requests


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _session() -> requests.Session:
    """Return a requests Session with a valid ASP.NET_SessionId cookie."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    })
    s.get(f"{BASE}/", verify=False, timeout=20)       # sets ASP.NET_SessionId
    return s


def _viewstate(session: requests.Session) -> dict:
    """GET Search.aspx and extract ASP.NET hidden fields."""
    r = session.get(f"{BASE}/Search.aspx", verify=False, timeout=20)
    vs  = re.search(r'name="__VIEWSTATE"[^>]+value="([^"]+)"', r.text)
    vsg = re.search(r'name="__VIEWSTATEGENERATOR"[^>]+value="([^"]+)"', r.text)
    return {
        "__VIEWSTATE":          vs.group(1)  if vs  else "",
        "__VIEWSTATEGENERATOR": vsg.group(1) if vsg else "",
    }


def _search(session: requests.Session, vs_fields: dict,
            year_range: str, prefix: str) -> str:
    """POST a lobbyist search and return response HTML."""
    data = {
        **vs_fields,
        "__EVENTTARGET":     "",
        "__EVENTARGUMENT":   "",
        "__SCROLLPOSITIONX": "0",
        "__SCROLLPOSITIONY": "0",
        "hdnSelectedTab":    "0",
        "lstYears":          year_range,
        "txtLobbyistName":   "",
        "txtPrincipalName":  prefix,
        "txtPrincipalOfficerName": "",
        "cmdSearch":         "Search",
    }
    r = session.post(
        f"{BASE}/Search.aspx",
        data=data,
        verify=False,
        timeout=60,
        headers={"Referer": f"{BASE}/Search.aspx"},
    )
    r.raise_for_status()
    return r.text


# ── HTML parser ──────────────────────────────────────────────────────────────

def _parse_rows(html: str) -> list[dict]:
    """Extract rows from tblFormsLobby."""
    tbl = re.search(
        r'<table[^>]+id="tblFormsLobby"[^>]*>(.*?)</table>',
        html, re.DOTALL
    )
    if not tbl:
        return []

    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl.group(1), re.DOTALL):
        # skip header row (contains <th>)
        if "<th" in tr.lower():
            continue
        cells = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
        if len(cells) < 6:
            continue

        def _t(cell):
            return re.sub(r'<[^>]+>', '', cell).strip()

        # extract filing_id from hidden input
        fid_m = re.search(r'value="(\d+)"', cells[0])
        filing_id = int(fid_m.group(1)) if fid_m else None

        # form_type link text
        ft_m = re.search(r'>([^<]+)</a>', cells[4])
        form_type = ft_m.group(1).strip() if ft_m else _t(cells[4])

        rows.append({
            "lobbyist_name":      _t(cells[1]),
            "principal_name":     _t(cells[2]),
            "authorizing_officer": _t(cells[3]),
            "form_type":          form_type,
            "submitted_date":     _t(cells[5]),
            "status":             _t(cells[6]) if len(cells) > 6 else "",
            "filing_id":          filing_id,
        })
    return rows


# ── Database ─────────────────────────────────────────────────────────────────

def _init_db(conn: sqlite3.Connection, reset: bool) -> None:
    if reset:
        conn.execute("DROP TABLE IF EXISTS lobbyist_registrations")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lobbyist_registrations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            year_range          TEXT    NOT NULL,
            lobbyist_name       TEXT,
            principal_name      TEXT,
            authorizing_officer TEXT,
            form_type           TEXT,
            submitted_date      TEXT,
            status              TEXT,
            filing_id           INTEGER UNIQUE,
            scraped_at          TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lobby_principal "
        "ON lobbyist_registrations(principal_name)"
    )
    conn.commit()


def _upsert(conn: sqlite3.Connection, rows: list[dict], year_range: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for r in rows:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO lobbyist_registrations
                  (year_range, lobbyist_name, principal_name, authorizing_officer,
                   form_type, submitted_date, status, filing_id, scraped_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                year_range,
                r["lobbyist_name"],
                r["principal_name"],
                r["authorizing_officer"],
                r["form_type"],
                r["submitted_date"],
                r["status"],
                r["filing_id"],
                now,
            ))
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except sqlite3.Error as e:
            print(f"  [db-warn] {e} — row={r}")
    conn.commit()
    return inserted


# ── Main scrape ──────────────────────────────────────────────────────────────

PREFIXES = list(string.ascii_uppercase) + list(string.digits)


def scrape_year(year_range: str, conn: sqlite3.Connection) -> int:
    """Scrape all lobbyists for one session year range. Returns total inserted."""
    print(f"\n{'='*60}")
    print(f"Scraping {year_range}")
    print(f"{'='*60}")

    sess = _session()
    total = 0

    for prefix in PREFIXES:
        vs = _viewstate(sess)
        try:
            html = _search(sess, vs, year_range, prefix)
        except requests.RequestException as exc:
            print(f"  [warn] {prefix}: network error {exc} — skipping")
            time.sleep(5)
            continue

        rows = _parse_rows(html)
        inserted = _upsert(conn, rows, year_range)
        total += inserted
        print(f"  [{prefix}] {len(rows)} results | {inserted} new | {total} total", flush=True)

        # Refresh session every 10 letters to avoid expiry
        if PREFIXES.index(prefix) % 10 == 9:
            sess = _session()

        time.sleep(SLEEP)

    print(f"\n  [done] {year_range}: {total} new rows inserted")
    return total


def main():
    parser = argparse.ArgumentParser(description="Scrape VA Ethics Council lobbyist data")
    parser.add_argument("--years", default="all",
                        help="Year range like '2025-2026', or 'all' for 2024-2027")
    parser.add_argument("--reset", action="store_true",
                        help="Drop and recreate lobbyist_registrations table")
    args = parser.parse_args()

    if args.years == "all":
        year_ranges = ["2026-2027", "2025-2026", "2024-2025"]
    else:
        year_ranges = [args.years]

    conn = sqlite3.connect(str(DB_PATH))
    _init_db(conn, reset=args.reset)

    grand_total = 0
    for yr in year_ranges:
        grand_total += scrape_year(yr, conn)

    conn.close()
    print(f"\nTotal rows inserted across all years: {grand_total}")
    print(f"Database: {DB_PATH}")


if __name__ == "__main__":
    main()
