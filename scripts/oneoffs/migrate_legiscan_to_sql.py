#!/usr/bin/env python3
"""
Migrate LegiScan data into SQLite for VoteIQ.

Creates va_bills and va_votes tables, populates from LegiScan dataset.
"""
import os
import re
import json
import zipfile
import base64
import argparse
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path("legislative_intelligence.db")
LEGISCAN_API_KEY = os.getenv("LEGISCAN_API_KEY")

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS va_bills (
    bill_id TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    bill_number TEXT NOT NULL,
    title TEXT,
    short_title TEXT,
    subject TEXT,
    summary TEXT,
    status TEXT,
    status_date TEXT,
    primary_sponsor TEXT,
    legiscan_id INTEGER,
    legiscan_session_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bill_number, session)
);

CREATE TABLE IF NOT EXISTS legiscan_sessions (
    session_id INTEGER PRIMARY KEY,
    session_name TEXT,
    session_title TEXT,
    session_label TEXT,
    year_start INTEGER,
    special INTEGER DEFAULT 0,
    dataset_hash TEXT,
    last_pulled_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS va_bill_sponsors (
    id INTEGER PRIMARY KEY,
    bill_id TEXT NOT NULL,
    legislator_name TEXT,
    legislator_id TEXT,
    sponsor_order INTEGER,
    FOREIGN KEY(bill_id) REFERENCES va_bills(bill_id)
);

CREATE TABLE IF NOT EXISTS va_votes (
    vote_id TEXT PRIMARY KEY,
    bill_id TEXT NOT NULL,
    session TEXT NOT NULL,
    vote_date TEXT,
    chamber TEXT,
    description TEXT,
    result TEXT,
    total_yes INTEGER,
    total_no INTEGER,
    total_abstain INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(bill_id) REFERENCES va_bills(bill_id)
);

CREATE TABLE IF NOT EXISTS va_vote_records (
    id INTEGER PRIMARY KEY,
    vote_id TEXT NOT NULL,
    legislator_id TEXT,
    legislator_name TEXT,
    vote TEXT,
    FOREIGN KEY(vote_id) REFERENCES va_votes(vote_id)
);
"""


def init_db():
    """Create schema if not exists."""
    conn = sqlite3.connect(DB_PATH)
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)

    # Create indexes
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_va_bills_session ON va_bills(session)",
        "CREATE INDEX IF NOT EXISTS idx_va_bills_sponsor ON va_bills(primary_sponsor)",
        "CREATE INDEX IF NOT EXISTS idx_va_votes_bill ON va_votes(bill_id)",
        "CREATE INDEX IF NOT EXISTS idx_va_votes_chamber ON va_votes(chamber)",
        "CREATE INDEX IF NOT EXISTS idx_va_vote_records_legislator ON va_vote_records(legislator_id)",
        "CREATE INDEX IF NOT EXISTS idx_va_bills_legiscan_session ON va_bills(legiscan_session_id)",
    ]
    for idx in indexes:
        conn.execute(idx)

    # Migrate existing DBs that predate legiscan_session_id column
    try:
        conn.execute("ALTER TABLE va_bills ADD COLUMN legiscan_session_id INTEGER")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    print(f"[OK] Database initialized: {DB_PATH}")


def legiscan(op: str, **params) -> dict:
    """Call LegiScan API."""
    import urllib.request
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://api.legiscan.com/?key={LEGISCAN_API_KEY}&op={op}&{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "VoteIQ/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    if data.get("status") == "ERROR":
        raise RuntimeError(f"LegiScan error: {data}")
    return data


def session_label_from_meta(ds: dict) -> str:
    """Derive a short session label: '2026' for regular, '2026S1' for 1st special, etc."""
    year = ds.get("year_start") or ds.get("year_end") or ""
    if ds.get("special"):
        tag = ds.get("session_tag", "")  # e.g. "1st Special Session"
        m = re.search(r"(\d+)", tag)
        num = m.group(1) if m else "1"
        return f"{year}S{num}"
    return str(year)


def get_active_datasets() -> list[dict]:
    """Return all VA sessions that are not yet sine_die and not marked prior."""
    data = legiscan("getDatasetList", state="VA", year="")
    items = data.get("datasetlist", [])
    return [d for d in items if not d.get("prior") and not d.get("sine_die")]


def get_stored_hash(conn: sqlite3.Connection, session_id: int) -> str | None:
    row = conn.execute(
        "SELECT dataset_hash FROM legiscan_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row[0] if row else None


def upsert_session(conn: sqlite3.Connection, ds: dict, label: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO legiscan_sessions
        (session_id, session_name, session_title, session_label, year_start, special, dataset_hash, last_pulled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            ds["session_id"],
            ds.get("session_name"),
            ds.get("session_title"),
            label,
            ds.get("year_start"),
            int(bool(ds.get("special"))),
            ds.get("dataset_hash"),
        ),
    )


def download_and_populate(session_id: int, access_key: str, session_label: str):
    """Download LegiScan dataset and populate database."""
    print(f"\nDownloading {session_label} dataset...")
    data = legiscan("getDataset", id=session_id, access_key=access_key)
    zip_b64 = data.get("dataset", {}).get("zip", "")
    if not zip_b64:
        raise RuntimeError("No zip in dataset response")

    zip_bytes = base64.b64decode(zip_b64)
    conn = sqlite3.connect(DB_PATH)

    with zipfile.ZipFile(__import__("io").BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith(".json") or "/bill/" not in name:
                continue

            raw = json.loads(zf.read(name))
            bill = raw.get("bill", {})
            bill_number = bill.get("bill_number", "")
            if not bill_number:
                continue

            # Insert bill
            bill_id = f"{bill_number}_{session_label}"
            primary_sponsor = bill.get("primary_sponsor", {})
            primary_name = primary_sponsor.get("name", "") if isinstance(primary_sponsor, dict) else primary_sponsor

            # subjects is an array of {subject_id, subject_name}; flatten to comma string
            subjects = bill.get("subjects") or []
            subject_str = ", ".join(
                s["subject_name"] for s in subjects if isinstance(s, dict) and s.get("subject_name")
            )

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO va_bills
                    (bill_id, session, bill_number, title, short_title, subject, summary,
                     status, status_date, primary_sponsor, legiscan_id, legiscan_session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bill_id, session_label, bill_number,
                    bill.get("title", ""),
                    bill.get("short_title", ""),
                    subject_str or bill.get("subject", ""),
                    bill.get("summary", ""),
                    bill.get("status", ""),
                    bill.get("status_date", ""),
                    primary_name,
                    bill.get("bill_id", None),
                    session_id,
                ))
            except sqlite3.IntegrityError:
                pass

            # Insert sponsors
            sponsors = bill.get("sponsors", [])
            for order, s in enumerate(sponsors, 1):
                name = s.get("name", "") if isinstance(s, dict) else s
                if name:
                    conn.execute("""
                        INSERT INTO va_bill_sponsors (bill_id, legislator_name, sponsor_order)
                        VALUES (?, ?, ?)
                    """, (bill_id, name, order))

            # Insert votes
            votes = bill.get("votes", [])
            for idx, vote in enumerate(votes, 1):
                vote_id = f"{bill_number}_{session_label}_vote_{idx}"
                chamber = "House" if vote.get("chamber", "") in ("H", "House") else "Senate"
                yeas = vote.get("yea", 0)
                nays = vote.get("nay", 0)
                result = "PASSED" if yeas > nays else "FAILED"

                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO va_votes
                        (vote_id, bill_id, session, vote_date, chamber, description, result, total_yes, total_no)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        vote_id, bill_id, session_label,
                        vote.get("date", ""),
                        chamber,
                        vote.get("desc", ""),
                        result,
                        yeas,
                        nays
                    ))
                except sqlite3.IntegrityError:
                    pass

                # Insert individual votes
                for member_vote in vote.get("votes", []):
                    vote_text = member_vote.get("vote_text", "").upper()
                    if vote_text in ("YEA", "YES"):
                        vote_val = "YES"
                    elif vote_text in ("NAY", "NO"):
                        vote_val = "NO"
                    else:
                        vote_val = "ABSTAIN"

                    conn.execute("""
                        INSERT INTO va_vote_records (vote_id, legislator_name, vote)
                        VALUES (?, ?, ?)
                    """, (vote_id, member_vote.get("name", ""), vote_val))

    conn.commit()
    conn.close()
    print(f"✓ Populated {session_label}")


def main():
    parser = argparse.ArgumentParser(description="Migrate LegiScan to SQLite")
    parser.add_argument(
        "--year",
        default=None,
        help="Force-pull a specific year label (e.g. 2026 or 2026S1). Default: pull all active sessions.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-pull even if dataset_hash is unchanged.",
    )
    args = parser.parse_args()

    if not LEGISCAN_API_KEY:
        print("ERROR: LEGISCAN_API_KEY not set")
        return

    init_db()
    conn = sqlite3.connect(DB_PATH)

    active = get_active_datasets()
    if not active:
        print("No active VA sessions returned by LegiScan.")
        conn.close()
        return

    # Filter to a specific year label if --year was passed
    if args.year:
        active = [d for d in active if session_label_from_meta(d) == args.year]
        if not active:
            print(f"No active session found matching label '{args.year}'.")
            conn.close()
            return

    pulled = 0
    for ds in active:
        label = session_label_from_meta(ds)
        sid = ds["session_id"]
        new_hash = ds.get("dataset_hash")
        stored_hash = get_stored_hash(conn, sid)

        if not args.force and stored_hash and stored_hash == new_hash:
            print(f"  {label} ({ds.get('session_name')}): unchanged (hash match), skipping")
            continue

        try:
            download_and_populate(sid, ds["access_key"], label)
            upsert_session(conn, ds, label)
            conn.commit()
            pulled += 1
            print(f"  ✓ {label} updated")
        except Exception as e:
            print(f"  ✗ {label}: {e}")

    conn.close()

    print(f"\n✓ Done — {pulled}/{len(active)} session(s) pulled")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    bills = c.execute("SELECT COUNT(*) FROM va_bills").fetchone()[0]
    votes = c.execute("SELECT COUNT(*) FROM va_votes").fetchone()[0]
    vote_records = c.execute("SELECT COUNT(*) FROM va_vote_records").fetchone()[0]
    conn.close()
    print(f"  Bills: {bills} | Votes: {votes} | Vote records: {vote_records}")


if __name__ == "__main__":
    main()
