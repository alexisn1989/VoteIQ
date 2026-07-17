"""
fetch_bill_texts_only.py
Fetch bill text for all bills in congress_bills via govinfo.gov XML.
Skips Congress.gov API (govinfo is faster and covers all introduced bills).
Processes newest congress first (119th → 118th → 117th).
"""
import sqlite3
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from ingest_congress import (
    _fetch_govinfo_text, upsert_bill_text,
    setup_db, _is_real_bill_text,
)

DB_PATH = Path(__file__).parent / "polls.db"


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    setup_db(conn)

    rows = conn.execute(
        """SELECT DISTINCT congress, bill_type, bill_number
           FROM congress_bills
           WHERE bill_type != '' AND bill_number != ''
           ORDER BY congress DESC, bill_number ASC"""
    ).fetchall()

    existing = {
        (r[0], r[1], r[2])
        for r in conn.execute(
            "SELECT congress, bill_type, bill_number FROM congress_bill_texts"
        ).fetchall()
    }

    todo = [r for r in rows if (r[0], r[1], r[2]) not in existing]

    print(f"Bills in DB:       {len(rows)}", flush=True)
    print(f"Already have text: {len(existing)}", flush=True)
    print(f"To fetch:          {len(todo)}", flush=True)
    print(flush=True)

    if not todo:
        print("Nothing to do.")
        conn.close()
        return 0

    fetched = skipped = 0
    start = time.time()

    for i, (congress, bill_type, bill_number) in enumerate(todo):
        label = f"{bill_type.upper()}{bill_number} ({congress})"

        selected = _fetch_govinfo_text(congress, bill_type, bill_number)

        if not selected or not _is_real_bill_text(selected.get("text")):
            skipped += 1
            if i % 100 == 0:
                elapsed = int(time.time() - start)
                pct = i / len(todo) * 100
                print(
                    f"  [{i+1}/{len(todo)} {pct:.0f}%] "
                    f"no text so far: {fetched} found, {skipped} skipped "
                    f"({elapsed}s)",
                    flush=True,
                )
            time.sleep(0.1)
            continue

        written = upsert_bill_text(
            conn, congress, bill_type, bill_number, selected, dry_run=False
        )
        if written:
            conn.commit()
            fetched += 1
            elapsed = int(time.time() - start)
            print(
                f"  [{i+1}/{len(todo)}] {label} OK "
                f"({len(selected.get('text',''))} chars) [{elapsed}s]",
                flush=True,
            )
        else:
            skipped += 1

        time.sleep(0.1)

    elapsed = int(time.time() - start)
    final = conn.execute("SELECT COUNT(*) FROM congress_bill_texts").fetchone()[0]
    print(f"\nDone in {elapsed}s.")
    print(f"Fetched: {fetched} | Skipped/no text: {skipped}")
    print(f"Total bill text records: {final}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
