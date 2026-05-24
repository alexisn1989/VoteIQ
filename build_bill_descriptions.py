#!/usr/bin/env python3
"""
Generate cached bill descriptions for every bill in openstates_va.db.

This is the one-time upfront pass that makes future bill searches local:
  - reads bill/vote metadata from SQLite
  - writes one plain-English description per bill
  - builds an FTS index for instant keyword search

Usage:
    python build_bill_descriptions.py
    python build_bill_descriptions.py --session 2026
    python build_bill_descriptions.py --bill SB583
"""

import argparse
import os
import re
import sqlite3
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DB_PATH = DATA_DIR / "openstates_va.db"
GENERATOR_VERSION = "deterministic-openstates-v1"


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _status(result: str, latest_action: str) -> str:
    action = (latest_action or "").lower()
    if "approved by governor" in action or "chapter" in action:
        return "approved by the Governor"
    if "veto" in action:
        return "vetoed"
    if result == "pass":
        return "passed at least one recorded vote"
    if result == "fail":
        return "failed in at least one recorded vote"
    return "pending or no final vote result recorded"


def _vote_summary(conn: sqlite3.Connection, bill_id: str, session: str) -> tuple[str, str]:
    rows = conn.execute(
        """
        SELECT chamber, motion, result, vote_date, option, COUNT(*)
        FROM votes
        WHERE bill_id=? AND session=? AND option IN ('yes', 'no')
        GROUP BY chamber, motion, result, vote_date, option
        ORDER BY vote_date, chamber, motion
        """,
        (bill_id, session),
    ).fetchall()
    if not rows:
        return "", ""

    rounds: OrderedDict[tuple, Counter] = OrderedDict()
    for chamber, motion, result, vote_date, option, count in rows:
        key = (chamber or "Unknown", _clean(motion), result or "", vote_date or "")
        rounds.setdefault(key, Counter())[option] += count

    floor_rounds = []
    committee_rounds = []
    for (chamber, motion, result, vote_date), counts in rounds.items():
        motion_l = motion.lower()
        is_committee = motion_l.startswith("reported from") or motion_l.startswith("subcommittee")
        label = "committee" if is_committee else "floor"
        date_note = f" on {vote_date}" if vote_date else ""
        result_note = f", result {result}" if result else ""
        text = f"{chamber} {label} vote{date_note}: {counts['yes']}-Y, {counts['no']}-N{result_note}"
        (committee_rounds if is_committee else floor_rounds).append(text)

    short_parts = []
    if floor_rounds:
        short_parts.append(floor_rounds[-1])
    if committee_rounds:
        short_parts.append(committee_rounds[-1])
    all_parts = floor_rounds[-4:] + committee_rounds[-3:]
    return "; ".join(short_parts), "; ".join(all_parts)


def setup_cache(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bill_descriptions (
            bill_id           TEXT NOT NULL,
            session           TEXT NOT NULL,
            title             TEXT,
            description       TEXT NOT NULL,
            search_text       TEXT NOT NULL,
            source_url        TEXT,
            generated_at      TEXT NOT NULL,
            generator_version TEXT NOT NULL,
            PRIMARY KEY (bill_id, session)
        );

        CREATE INDEX IF NOT EXISTS idx_bill_descriptions_session
            ON bill_descriptions(session);
        CREATE INDEX IF NOT EXISTS idx_bill_descriptions_bill
            ON bill_descriptions(bill_id);
        """
    )
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS bill_descriptions_fts
            USING fts5(bill_id, session, title, description, search_text);
            """
        )
    except sqlite3.OperationalError as exc:
        print(f"[warn] FTS5 unavailable, LIKE search fallback will be used: {exc}")
    conn.commit()


def description_for(conn: sqlite3.Connection, row: tuple) -> dict:
    bill_id, session, title, classification, subjects, sponsors, latest_action, latest_date, result, url = row
    title = _clean(title)
    subjects = _clean(subjects)
    sponsors = _clean(sponsors)
    latest_action = _clean(latest_action)
    status = _status(result or "", latest_action)
    vote_short, vote_detail = _vote_summary(conn, bill_id, session)

    lines = [f"{bill_id} ({session}) - {title}"]
    if subjects:
        lines.append(f"Subject area: {subjects}.")
    if sponsors:
        lines.append(f"Sponsor(s): {sponsors}.")
    lines.append(f"Status: {status}.")
    if latest_action:
        date_note = f" on {latest_date}" if latest_date else ""
        lines.append(f"Latest action{date_note}: {latest_action}.")
    if vote_short:
        lines.append(f"Key vote snapshot: {vote_short}.")
    if url:
        lines.append(f"Source: {url}")

    description = "\n".join(lines)
    search_text = " ".join(
        part for part in (
            bill_id, session, title, classification, subjects, sponsors,
            latest_action, result or "", vote_detail, url or "",
        )
        if part
    )
    return {
        "bill_id": bill_id,
        "session": session,
        "title": title,
        "description": description,
        "search_text": search_text,
        "source_url": url or "",
    }


def rebuild_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DELETE FROM bill_descriptions_fts")
        conn.execute(
            """
            INSERT INTO bill_descriptions_fts (bill_id, session, title, description, search_text)
            SELECT bill_id, session, title, description, search_text
            FROM bill_descriptions
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def build_cache(session: str | None = None, bill: str | None = None) -> int:
    if not DB_PATH.exists():
        raise SystemExit(f"ERROR: {DB_PATH} not found. Run build_openstates_db.py first.")

    conn = sqlite3.connect(DB_PATH)
    setup_cache(conn)

    where = []
    params = []
    if session:
        where.append("session=?")
        params.append(session)
    if bill:
        where.append("bill_id=?")
        params.append(re.sub(r"\s+", "", bill.upper()))
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    rows = conn.execute(
        f"""
        SELECT bill_id, session, title, classification, subjects, sponsors,
               latest_action, latest_date, result, openstates_url
        FROM bills
        {where_sql}
        ORDER BY session, bill_id
        """,
        params,
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        item = description_for(conn, row)
        conn.execute(
            """
            INSERT OR REPLACE INTO bill_descriptions
            (bill_id, session, title, description, search_text, source_url,
             generated_at, generator_version)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                item["bill_id"], item["session"], item["title"], item["description"],
                item["search_text"], item["source_url"], now, GENERATOR_VERSION,
            ),
        )
    conn.commit()
    rebuild_fts(conn)
    total = conn.execute("SELECT COUNT(*) FROM bill_descriptions").fetchone()[0]
    conn.close()

    print(f"[bill-descriptions] generated/updated {len(rows)} descriptions")
    print(f"[bill-descriptions] cache now has {total} descriptions")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build cached VoteIQ bill descriptions")
    parser.add_argument("--session", help="Limit to one session, e.g. 2026")
    parser.add_argument("--bill", help="Limit to one bill, e.g. SB583")
    args = parser.parse_args()
    build_cache(session=args.session, bill=args.bill)


if __name__ == "__main__":
    main()
