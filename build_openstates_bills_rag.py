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
from collections import OrderedDict
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
            "SELECT voter_name, option, motion, chamber, vote_date "
            "FROM votes "
            "WHERE bill_id=? AND session=? AND voter_name != '' "
            "ORDER BY vote_date, chamber, motion",
            (bill_id, session)
        ).fetchall()

        # Group votes by (chamber, motion) preserving order; deduplicate per voter
        motion_votes: dict[tuple, dict] = OrderedDict()
        seen_voter_per_motion: dict[tuple, set] = {}
        for voter_name, option, motion, chamber, vote_date in vote_rows:
            key = (chamber or "Unknown", (motion or "").strip())
            if key not in motion_votes:
                motion_votes[key] = {"yes": 0, "no": 0, "not voting": 0, "abstain": 0,
                                     "yes_names": [], "no_names": [],
                                     "is_committee": is_committee_motion(motion),
                                     "vote_date": vote_date or ""}
                seen_voter_per_motion[key] = set()
            if voter_name in seen_voter_per_motion[key]:
                continue
            seen_voter_per_motion[key].add(voter_name)
            d = motion_votes[key]
            opt = (option or "").lower()
            if opt in d:
                d[opt] += 1
            if opt == "yes" and len(d["yes_names"]) < 40:
                d["yes_names"].append(voter_name)
            if opt == "no" and len(d["no_names"]) < 40:
                d["no_names"].append(voter_name)

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

        # Emit each round of voting with chamber label
        senate_rounds = [(k, v) for k, v in motion_votes.items() if k[0] == "Senate"]
        house_rounds  = [(k, v) for k, v in motion_votes.items() if k[0] == "House"]

        def _emit_rounds(rounds: list, chamber_label: str):
            if not rounds:
                return
            lines.append(f"\n── {chamber_label} ──")
            for (chamber, motion), d in rounds:
                vote_type = "Committee" if d["is_committee"] else "Floor"
                motion_clean = re.sub(r"\s+with\s+.*$", "", motion, flags=re.IGNORECASE).strip()
                motion_clean = re.sub(r"^(Reported from|Subcommittee recommends reporting)\s*", "", motion_clean, flags=re.IGNORECASE).strip()
                date_str = f" ({d['vote_date']})" if d["vote_date"] else ""
                total = d["yes"] + d["no"]
                lines.append(
                    f"  [{vote_type}] {motion_clean}{date_str}: "
                    f"{d['yes']}-Y  {d['no']}-N"
                    + (f"  {d['not voting']}-NV" if d["not voting"] else "")
                )
                if d["no_names"]:
                    lines.append(f"    NO votes: {', '.join(d['no_names'])}")

        _emit_rounds(senate_rounds, "State Senate")
        _emit_rounds(house_rounds,  "House of Delegates")

        lines.append(
            "\nNote: Vote totals reflect all recorded votes across both chambers "
            "and all vote types (passage, amendments, conference reports). "
            "Individual chamber breakdowns available on request."
        )

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
