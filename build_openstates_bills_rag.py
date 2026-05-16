#!/usr/bin/env python3
"""
Build per-bill RAG chunks from openstates_va.db for ingest into ChromaDB.

Each chunk is a narrative bill summary including:
  - Bill title, session, sponsors, result
  - Concise YES/NO vote breakdown (up to 40 named voters each)
  - Committee votes separately from floor votes

Output: va_openstates_bills.jsonl  (ingest with: python ingest.py --file va_openstates_bills.jsonl)

Usage:
    python build_openstates_bills_rag.py
    python build_openstates_bills_rag.py --session 2026
    python build_openstates_bills_rag.py --bill SB583
    python build_openstates_bills_rag.py --out custom.jsonl
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "openstates_va.db"


def is_committee_motion(motion: str) -> bool:
    m = (motion or "").lower()
    return m.startswith("reported from") or m.startswith("subcommittee")


def build_bill_chunks(conn: sqlite3.Connection, sessions: list[str], bill_filter: str | None) -> list[dict]:
    chunks = []

    where_session = f"AND b.session IN ({','.join('?'*len(sessions))})" if sessions else ""
    where_bill    = "AND b.bill_id = ?" if bill_filter else ""
    params        = list(sessions) + ([bill_filter] if bill_filter else [])

    bill_rows = conn.execute(
        f"SELECT bill_id, session, title, subjects, sponsors, result, openstates_url "
        f"FROM bills b WHERE 1=1 {where_session} {where_bill} "
        f"ORDER BY session, bill_id",
        params
    ).fetchall()

    if not bill_rows:
        return chunks

    for bill_id, session, title, subjects, sponsors, result, url in bill_rows:
        vote_rows = conn.execute(
            "SELECT voter_name, option, motion, chamber, party "
            "FROM votes "
            "WHERE bill_id=? AND session=? AND voter_name != '' "
            "ORDER BY motion",
            (bill_id, session)
        ).fetchall()

        # Separate floor vs committee votes
        floor_yes, floor_no = [], []
        comm_yes,  comm_no  = [], []
        committees_seen = set()

        for voter_name, option, motion, chamber, party in vote_rows:
            if is_committee_motion(motion):
                if option == "yes":
                    comm_yes.append(voter_name)
                elif option == "no":
                    comm_no.append(voter_name)
                # extract committee name
                cname = re.sub(r"\s+with\s+.*$", "", motion or "", flags=re.IGNORECASE)
                cname = re.sub(r"^reported from\s+", "", cname, flags=re.IGNORECASE)
                cname = cname.strip().title()
                if cname and "Subcommittee" not in cname:
                    committees_seen.add(cname)
            else:
                if option == "yes":
                    floor_yes.append(voter_name)
                elif option == "no":
                    floor_no.append(voter_name)

        result_label = {"pass": "PASSED", "fail": "FAILED"}.get(result or "", result or "pending").upper()

        lines = [
            f"Bill {bill_id} ({session} Virginia Session) — {result_label}",
            f"Title: {(title or '').strip()}",
        ]
        if subjects:
            lines.append(f"Subjects: {subjects}")
        if sponsors:
            lines.append(f"Sponsors: {sponsors}")
        if url:
            lines.append(f"OpenStates: {url}")

        # Floor votes
        if floor_yes or floor_no:
            total = len(floor_yes) + len(floor_no)
            lines.append(
                f"\nFloor vote: {len(floor_yes)}-Y  {len(floor_no)}-N  (total {total})"
            )
            if floor_yes:
                lines.append(f"Floor YES ({len(floor_yes)}): {', '.join(floor_yes[:40])}")
            if floor_no:
                lines.append(f"Floor NO  ({len(floor_no)}): {', '.join(floor_no[:40])}")

        # Committee votes
        if comm_yes or comm_no:
            comm_total = len(comm_yes) + len(comm_no)
            committee_str = f" [{', '.join(sorted(committees_seen))}]" if committees_seen else ""
            lines.append(
                f"\nCommittee vote{committee_str}: {len(comm_yes)}-Y  {len(comm_no)}-N  (total {comm_total})"
            )
            if comm_yes:
                lines.append(f"Committee YES ({len(comm_yes)}): {', '.join(comm_yes[:40])}")
            if comm_no:
                lines.append(f"Committee NO  ({len(comm_no)}): {', '.join(comm_no[:40])}")

        text = "\n".join(lines)

        slug = re.sub(r"[^a-z0-9]+", "_", bill_id.lower()).strip("_")
        chunk_id = f"openstates_bill_{slug}_{session}"

        chunks.append({
            "chunk_id":    chunk_id,
            "text":        text,
            "bill_id":     bill_id,
            "session_id":  session,
            "state":       "VA",
            "year":        session,
            "chunk_index": 0,
            "chunk_type":  "openstates_bill",
            "metadata": {
                "source":    "openstates",
                "bill_id":   bill_id,
                "session":   session,
                "result":    result or "",
                "sponsors":  (sponsors or "")[:200],
                "subjects":  (subjects or "")[:200],
                "floor_yes_count": str(len(floor_yes)),
                "floor_no_count":  str(len(floor_no)),
            },
        })

    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", help="Single session e.g. 2026 (default: all in DB)")
    parser.add_argument("--bill",    help="Single bill e.g. SB583")
    parser.add_argument("--out",     default="va_openstates_bills.jsonl")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found. Run build_openstates_db.py first.")
        return

    conn = sqlite3.connect(DB_PATH)

    if args.session:
        sessions = [args.session]
    else:
        rows = conn.execute("SELECT DISTINCT session FROM bills ORDER BY session").fetchall()
        sessions = [r[0] for r in rows]

    bill_filter = args.bill.upper() if args.bill else None
    label = f"bill {bill_filter}" if bill_filter else f"sessions: {sessions}"
    print(f"Building bill chunks for {label}")

    chunks = build_bill_chunks(conn, sessions, bill_filter)
    conn.close()

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"Wrote {len(chunks)} chunks to {out}")
    if chunks:
        print("\nSample:")
        print(chunks[0]["text"][:500])


if __name__ == "__main__":
    main()
