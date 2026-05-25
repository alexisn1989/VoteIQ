#!/usr/bin/env python3
"""
ingest_signed_bills_from_lis.py

Reads signed bills from virginia_legislature.db (LIS database)
and writes to polls.db governor_actions table.

Identifies signed bills by presence of Chapter_id field.
"""

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else BASE_DIR
POLLS_DB = os.environ.get("POLLS_DB", str(DATA_DIR / "polls.db"))
LIS_DB = os.environ.get("LIS_DB", str(DATA_DIR / "virginia_legislature.db"))

ACTION_LABELS = {
    "signed": "Signed into law",
    "vetoed": "Vetoed",
}


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Ensure governor_actions table exists with proper schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS governor_actions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number    TEXT,
            session        TEXT,
            title          TEXT,
            raw_status     TEXT,
            action         TEXT,
            action_key     TEXT,
            action_label   TEXT,
            action_date    TEXT,
            chapter_number TEXT,
            effective_date TEXT,
            governor       TEXT,
            sponsor_name   TEXT,
            sponsor_party  TEXT,
            source_url     TEXT,
            fetched_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bill_number, session)
        );
        CREATE INDEX IF NOT EXISTS idx_gov_action_session
            ON governor_actions(session, action);
        CREATE INDEX IF NOT EXISTS idx_gov_action_date
            ON governor_actions(action_date);
    """)
    conn.commit()


def extract_session_from_date(date_str: str) -> str:
    """Extract session year from date string (e.g., '4/8/2026' -> '2026')."""
    if not date_str:
        return "2026"
    parts = date_str.split('/')
    if len(parts) >= 3:
        return parts[2]
    return "2026"


def extract_chapter_number(chapter_id: str) -> str:
    """Extract chapter number from Chapter_id field (e.g., 'CHAP0350' -> '350')."""
    if not chapter_id or not chapter_id.startswith('CHAP'):
        return ""
    # Remove 'CHAP' prefix and leading zeros
    return chapter_id.replace('CHAP', '').lstrip('0') or chapter_id


def run() -> int:
    """Main entry point."""
    if not os.path.exists(POLLS_DB):
        print(f"[error] polls.db not found at {POLLS_DB}")
        return 0

    if not os.path.exists(LIS_DB):
        print(f"[error] virginia_legislature.db not found at {LIS_DB}")
        return 0

    # Connect to both databases
    lis_conn = sqlite3.connect(LIS_DB)
    lis_conn.row_factory = sqlite3.Row
    lis_cursor = lis_conn.cursor()

    polls_conn = sqlite3.connect(POLLS_DB)
    _ensure_table(polls_conn)

    # Query signed bills from LIS database
    # Bills are signed if they have a Chapter_id
    lis_cursor.execute("""
        SELECT
            Bill_id,
            Bill_description as title,
            Chapter_id,
            Last_governor_action_date,
            Patron_name,
            Introduction_date
        FROM bills
        WHERE Chapter_id IS NOT NULL AND Chapter_id != ''
        ORDER BY Last_governor_action_date DESC
    """)

    signed_bills = lis_cursor.fetchall()
    print(f"[ingest] Found {len(signed_bills)} signed bills in LIS database")

    count = 0
    for bill in signed_bills:
        try:
            bill_number = bill['Bill_id']
            title = bill['title'] or ""
            chapter_id = bill['Chapter_id']
            action_date = bill['Last_governor_action_date'] or ""
            patron_name = bill['Patron_name'] or ""

            # Extract session from date
            session = extract_session_from_date(action_date)

            # Extract clean chapter number
            chapter_num = extract_chapter_number(chapter_id)

            # Insert into polls.db
            polls_conn.execute(
                """
                INSERT INTO governor_actions (
                    bill_number, session, title, raw_status,
                    action, action_key, action_label, action_date,
                    chapter_number, effective_date, governor,
                    sponsor_name, sponsor_party, source_url, fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(bill_number, session) DO UPDATE SET
                    action         = excluded.action,
                    action_key     = excluded.action_key,
                    action_label   = excluded.action_label,
                    action_date    = excluded.action_date,
                    chapter_number = excluded.chapter_number,
                    raw_status     = excluded.raw_status,
                    fetched_at     = datetime('now')
                WHERE governor_actions.action != 'signed'
                """,
                (
                    bill_number,
                    session,
                    title,
                    f"Signed into law (Chapter {chapter_num})",
                    "signed",
                    "signed",
                    ACTION_LABELS["signed"],
                    action_date,
                    chapter_num,
                    "",
                    "Spanberger",
                    patron_name,
                    "",
                    "https://virginiageneralassembly.gov/",
                ),
            )
            count += 1

            # Show progress for first 10
            if count <= 10:
                print(f"  {bill_number:8} Ch.{chapter_num:4} | {title[:50]:50}")

        except Exception as e:
            print(f"[warn] Error processing {bill.get('Bill_id')}: {e}")
            continue

    polls_conn.commit()
    polls_conn.close()
    lis_conn.close()

    print(f"[done] Ingested {count} signed bills from LIS database")
    return count


if __name__ == "__main__":
    run()
