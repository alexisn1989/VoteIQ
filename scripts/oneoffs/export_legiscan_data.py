#!/usr/bin/env python3
"""Export pulled LegiScan data into app SQL tables and JSONL.

Reads legislative_intelligence.db, writes:
  - polls.db tables: legiscan_va_bills, legiscan_va_bill_sponsors, legiscan_va_votes
  - JSONL file: legiscan_va_2025_2026.jsonl by default
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LEG_DB = BASE_DIR / "legislative_intelligence.db"
POLLS_DB = BASE_DIR / "polls.db"


STATUS_LABELS = {
    "0": "Unknown",
    "1": "Introduced",
    "2": "Engrossed",
    "3": "Enrolled",
    "4": "Passed",
    "5": "Vetoed",
    "6": "Failed",
}


def _row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _status_label(status: str | None) -> str:
    if status is None:
        return ""
    return STATUS_LABELS.get(str(status), str(status))


def _ensure_sql_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS legiscan_va_bills (
            bill_id TEXT PRIMARY KEY,
            session TEXT NOT NULL,
            bill_number TEXT NOT NULL,
            title TEXT,
            short_title TEXT,
            subject TEXT,
            summary TEXT,
            status TEXT,
            status_label TEXT,
            status_date TEXT,
            primary_sponsor TEXT,
            legiscan_id INTEGER,
            source TEXT DEFAULT 'legiscan',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS legiscan_va_bill_sponsors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id TEXT NOT NULL,
            session TEXT NOT NULL,
            legislator_name TEXT,
            legislator_id TEXT,
            sponsor_order INTEGER,
            source TEXT DEFAULT 'legiscan'
        );

        CREATE TABLE IF NOT EXISTS legiscan_va_votes (
            vote_id TEXT PRIMARY KEY,
            bill_id TEXT NOT NULL,
            session TEXT NOT NULL,
            bill_number TEXT,
            vote_date TEXT,
            chamber TEXT,
            description TEXT,
            result TEXT,
            total_yes INTEGER,
            total_no INTEGER,
            total_abstain INTEGER,
            source TEXT DEFAULT 'legiscan'
        );

        CREATE INDEX IF NOT EXISTS idx_legiscan_va_bills_session
            ON legiscan_va_bills(session);
        CREATE INDEX IF NOT EXISTS idx_legiscan_va_bills_number
            ON legiscan_va_bills(bill_number, session);
        CREATE INDEX IF NOT EXISTS idx_legiscan_va_sponsors_bill
            ON legiscan_va_bill_sponsors(bill_id);
        CREATE INDEX IF NOT EXISTS idx_legiscan_va_votes_bill
            ON legiscan_va_votes(bill_id);
        CREATE INDEX IF NOT EXISTS idx_legiscan_va_votes_session
            ON legiscan_va_votes(session);
        """
    )


def copy_to_polls_db(years: list[str]) -> dict[str, int]:
    src = sqlite3.connect(LEG_DB)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(POLLS_DB)
    dst.row_factory = sqlite3.Row
    _ensure_sql_tables(dst)

    placeholders = ",".join("?" for _ in years)
    dst.execute(
        f"DELETE FROM legiscan_va_votes WHERE session IN ({placeholders})",
        years,
    )
    dst.execute(
        f"DELETE FROM legiscan_va_bill_sponsors WHERE session IN ({placeholders})",
        years,
    )
    dst.execute(
        f"DELETE FROM legiscan_va_bills WHERE session IN ({placeholders})",
        years,
    )

    bills = src.execute(
        f"""
        SELECT *
        FROM va_bills
        WHERE session IN ({placeholders})
        ORDER BY session DESC, bill_number
        """,
        years,
    ).fetchall()
    for row in bills:
        dst.execute(
            """
            INSERT OR REPLACE INTO legiscan_va_bills
            (bill_id, session, bill_number, title, short_title, subject, summary,
             status, status_label, status_date, primary_sponsor, legiscan_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["bill_id"],
                row["session"],
                row["bill_number"],
                row["title"],
                row["short_title"],
                row["subject"],
                row["summary"],
                row["status"],
                _status_label(row["status"]),
                row["status_date"],
                row["primary_sponsor"],
                row["legiscan_id"],
            ),
        )

    sponsors = src.execute(
        f"""
        SELECT
            s.bill_id,
            b.session,
            s.legislator_name,
            s.legislator_id,
            s.sponsor_order
        FROM va_bill_sponsors s
        JOIN va_bills b ON b.bill_id = s.bill_id
        WHERE b.session IN ({placeholders})
        GROUP BY s.bill_id, b.session, s.legislator_name, s.legislator_id, s.sponsor_order
        ORDER BY b.session DESC, s.bill_id, s.sponsor_order
        """,
        years,
    ).fetchall()
    for row in sponsors:
        dst.execute(
            """
            INSERT INTO legiscan_va_bill_sponsors
            (bill_id, session, legislator_name, legislator_id, sponsor_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                row["bill_id"],
                row["session"],
                row["legislator_name"],
                row["legislator_id"],
                row["sponsor_order"],
            ),
        )

    votes = src.execute(
        f"""
        SELECT v.*, b.bill_number
        FROM va_votes v
        JOIN va_bills b ON b.bill_id = v.bill_id
        WHERE v.session IN ({placeholders})
        ORDER BY v.session DESC, v.bill_id, v.vote_date
        """,
        years,
    ).fetchall()
    for row in votes:
        dst.execute(
            """
            INSERT OR REPLACE INTO legiscan_va_votes
            (vote_id, bill_id, session, bill_number, vote_date, chamber, description,
             result, total_yes, total_no, total_abstain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["vote_id"],
                row["bill_id"],
                row["session"],
                row["bill_number"],
                row["vote_date"],
                row["chamber"],
                row["description"],
                row["result"],
                row["total_yes"],
                row["total_no"],
                row["total_abstain"],
            ),
        )

    dst.commit()
    counts = {
        "legiscan_va_bills": len(bills),
        "legiscan_va_bill_sponsors": len(sponsors),
        "legiscan_va_votes": len(votes),
    }
    src.close()
    dst.close()
    return counts


def export_jsonl(years: list[str], output_path: Path) -> int:
    conn = sqlite3.connect(LEG_DB)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in years)
    bills = conn.execute(
        f"""
        SELECT *
        FROM va_bills
        WHERE session IN ({placeholders})
        ORDER BY session DESC, bill_number
        """,
        years,
    ).fetchall()

    count = 0
    with output_path.open("w", encoding="utf-8", newline="\n") as fh:
        for bill in bills:
            sponsors = [
                row["legislator_name"]
                for row in conn.execute(
                    """
                    SELECT legislator_name
                    FROM va_bill_sponsors
                    WHERE bill_id = ?
                    ORDER BY sponsor_order
                    """,
                    (bill["bill_id"],),
                ).fetchall()
                if row["legislator_name"]
            ]
            votes = [
                _row_dict(row)
                for row in conn.execute(
                    """
                    SELECT vote_date, chamber, description, result,
                           total_yes, total_no, total_abstain
                    FROM va_votes
                    WHERE bill_id = ?
                    ORDER BY vote_date
                    """,
                    (bill["bill_id"],),
                ).fetchall()
            ]
            vote_summary = "; ".join(
                f"{v['vote_date'] or 'date unknown'} {v['chamber'] or 'chamber unknown'} "
                f"{v['result'] or 'result unknown'} ({v['total_yes'] or 0}-Y, {v['total_no'] or 0}-N)"
                for v in votes[:5]
            )
            status_label = _status_label(bill["status"])
            text_parts = [
                f"LegiScan bill {bill['bill_number']} ({bill['session']} Virginia Session)",
                f"Title: {bill['title'] or ''}",
                f"Status: {status_label}",
            ]
            if bill["status_date"]:
                text_parts.append(f"Status date: {bill['status_date']}")
            if sponsors:
                text_parts.append(f"Sponsors: {', '.join(sponsors[:12])}")
            if bill["summary"]:
                text_parts.append(f"Summary: {bill['summary']}")
            if vote_summary:
                text_parts.append(f"Vote summary: {vote_summary}")
            text_parts.append(f"LegiScan ID: {bill['legiscan_id'] or ''}")

            record = {
                "chunk_id": f"legiscan_va_{bill['bill_number'].lower()}_{bill['session']}",
                "text": "\n".join(text_parts),
                "bill_id": bill["bill_number"],
                "legiscan_bill_id": bill["legiscan_id"],
                "session_id": bill["session"],
                "state": "VA",
                "year": bill["session"],
                "chunk_index": 0,
                "chunk_type": "legiscan_bill",
                "metadata": {
                    "source": "legiscan",
                    "bill_id": bill["bill_number"],
                    "bill_key": bill["bill_id"],
                    "session": bill["session"],
                    "title": bill["title"],
                    "status": bill["status"],
                    "status_label": status_label,
                    "status_date": bill["status_date"],
                    "primary_sponsor": bill["primary_sponsor"],
                    "sponsors": sponsors,
                    "vote_count": len(votes),
                },
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    conn.close()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Export LegiScan data to polls.db and JSONL")
    parser.add_argument("--years", nargs="+", default=["2025", "2026"])
    parser.add_argument("--jsonl", default="legiscan_va_2025_2026.jsonl")
    args = parser.parse_args()

    years = [str(year) for year in args.years]
    sql_counts = copy_to_polls_db(years)
    jsonl_count = export_jsonl(years, BASE_DIR / args.jsonl)

    print("SQL export complete:")
    for table, count in sql_counts.items():
        print(f"  {table}: {count}")
    print(f"JSONL export complete: {args.jsonl}: {jsonl_count}")


if __name__ == "__main__":
    main()
