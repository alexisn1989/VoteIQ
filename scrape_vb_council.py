#!/usr/bin/env python3
"""
scrape_vb_council.py

Scrapes Virginia Beach City Council member roster from clerk.virginiabeach.gov
and writes to vb_council_members table in polls.db.

Usage:
  python scrape_vb_council.py [--db PATH]
"""

import argparse
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import date

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")
_VB_CLERK_URL = "https://clerk.virginiabeach.gov/city-council/city-council-members"


def _fetch(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _scrape_members() -> list[dict]:
    html = _fetch(_VB_CLERK_URL)

    if not _HAS_BS4:
        print("WARNING: beautifulsoup4 not installed — install it for reliable parsing")
        return []

    soup = BeautifulSoup(html, "html.parser")
    members = []
    for card in soup.find_all("div", class_="shadow-news-card-simple"):
        name_tag = card.find("p", class_="faux-h5")
        name = name_tag.get_text(strip=True) if name_tag else "Unknown"

        dist_tag = card.find("p", class_="font-semibold")
        district = dist_tag.get_text(strip=True) if dist_tag else "Unknown"

        email_tag = card.find("a", href=lambda h: h and h.startswith("mailto:"))
        email = email_tag["href"].replace("mailto:", "").strip() if email_tag else None

        dm = re.search(r"District\s+(\d+)", district, re.IGNORECASE)
        district_num = int(dm.group(1)) if dm else 0  # 0 = Mayor (at-large)

        members.append({
            "name": name,
            "district": district,
            "district_num": district_num,
            "email": email,
        })
    return members


def _create_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vb_council_members (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            district      TEXT NOT NULL,
            district_num  INTEGER DEFAULT 0,
            email         TEXT,
            jurisdiction  TEXT DEFAULT 'Virginia Beach',
            scraped_date  TEXT,
            UNIQUE(name)
        )
    """)
    conn.commit()


def _upsert(conn: sqlite3.Connection, members: list[dict]) -> int:
    today = date.today().isoformat()
    for m in members:
        conn.execute("""
            INSERT INTO vb_council_members (name, district, district_num, email, scraped_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                district     = excluded.district,
                district_num = excluded.district_num,
                email        = excluded.email,
                scraped_date = excluded.scraped_date
        """, (m["name"], m["district"], m["district_num"], m["email"], today))
    conn.commit()
    return len(members)


def main() -> int:
    p = argparse.ArgumentParser(description="Scrape VB City Council roster into polls.db")
    p.add_argument("--db", default=_DEFAULT_DB, help="Path to polls.db")
    args = p.parse_args()

    print(f"Fetching VB council roster from {_VB_CLERK_URL}")
    try:
        members = _scrape_members()
    except Exception as exc:
        print(f"ERROR fetching roster: {exc}", file=sys.stderr)
        return 1

    if not members:
        print("ERROR: No members parsed — check page structure or install beautifulsoup4")
        return 1

    print(f"  Found {len(members)} members:")
    for m in members:
        print(f"    {m['name']:<40} {m['district']:<12} {m['email'] or ''}")

    conn = sqlite3.connect(args.db)
    _create_table(conn)
    n = _upsert(conn, members)
    conn.close()
    print(f"Upserted {n} members -> {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
