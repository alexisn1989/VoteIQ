#!/usr/bin/env python3
"""
Build per-bill RAG chunks from Congress.gov bill text stored in polls.db.

Each chunk is built from the latest text version saved in congress_bill_texts.
Output: congress_bills_text_rag.jsonl

Usage:
    python build_congress_bills_rag.py
    python build_congress_bills_rag.py --congress 119
    python build_congress_bills_rag.py --bill HR1
    python build_congress_bills_rag.py --out congress_bills_text_rag.jsonl
"""

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent))) / "polls.db"


def _count_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))


def _split_text(text: str, base_id: str, meta: dict) -> list[dict]:
    lines = [line for line in text.split("\n") if line.strip()]
    chunks = []
    current = []
    cur_tokens = 0
    idx = 0

    for line in lines:
        tokens = _count_tokens(line)
        if current and cur_tokens + tokens > 250:
            chunks.append({
                "chunk_id": f"{base_id}_{idx}",
                "text": "\n".join(current),
                "metadata": {**meta, "chunk_index": idx},
            })
            overlap = []
            overlap_tokens = 0
            for prev_line in reversed(current):
                prev_tokens = _count_tokens(prev_line)
                if overlap_tokens + prev_tokens > 40:
                    break
                overlap.insert(0, prev_line)
                overlap_tokens += prev_tokens
            current = overlap
            cur_tokens = overlap_tokens
            idx += 1
        current.append(line)
        cur_tokens += tokens

    if current:
        chunks.append({
            "chunk_id": f"{base_id}_{idx}",
            "text": "\n".join(current),
            "metadata": {**meta, "chunk_index": idx},
        })
    return chunks


def _normalize_bill_filter(bill: str) -> tuple[str, str] | None:
    cleaned = re.sub(r"\s+", "", str(bill or "")).upper()
    match = re.match(r"^(H\.?R|S\.?)(\d+)$", cleaned)
    if not match:
        return None
    bill_type = match.group(1).replace(".", "").lower()
    bill_number = match.group(2)
    return bill_type, bill_number


def build_chunks(conn: sqlite3.Connection, congress_filter: int | None, bill_filter: str | None) -> list[dict]:
    where_clauses = ["1=1"]
    params = []

    if congress_filter is not None:
        where_clauses.append("cb.congress = ?")
        params.append(congress_filter)
    if bill_filter:
        normalized = _normalize_bill_filter(bill_filter)
        if normalized:
            t, n = normalized
            where_clauses.append("cb.bill_type = ? AND cb.bill_number = ?")
            params.extend([t, n])
        else:
            raise ValueError(f"Invalid bill filter: {bill_filter}")

    rows = conn.execute(
        f"SELECT cb.congress, cb.bill_type, cb.bill_number, cb.title, bt.version_name, bt.version_type, bt.version_date, bt.source_url, bt.text "
        f"FROM congress_bill_texts bt "
        f"JOIN congress_bills cb ON bt.congress = cb.congress AND bt.bill_type = cb.bill_type AND bt.bill_number = cb.bill_number "
        f"WHERE {' AND '.join(where_clauses)} "
        f"ORDER BY cb.congress DESC, cb.bill_type, CAST(cb.bill_number AS INTEGER)"
        , params
    ).fetchall()

    chunks = []
    for congress, bill_type, bill_number, title, version_name, version_type, version_date, source_url, text in rows:
        if not text:
            continue
        bill_id = f"{bill_type.upper()}{bill_number}"
        header = [
            f"[Congress.gov Bill Text — {bill_id} | {congress}th Congress]",
            f"Title: {title or 'Unknown title'}",
            f"Version: {version_name or version_type or 'unknown'}",
            f"Version date: {version_date or 'unknown'}",
            f"Source: {source_url or 'congress.gov'}",
            "",
        ]
        full_text = "\n".join(header) + text
        base_id = f"congress_bill_{congress}_{bill_type}{bill_number}_text"
        meta = {
            "source": "congress.gov",
            "bill_id": bill_id,
            "congress": congress,
            "bill_type": bill_type,
            "bill_number": bill_number,
            "title": (title or "").strip(),
            "version_name": version_name or "",
            "version_type": version_type or "",
            "version_date": version_date or "",
            "source_url": source_url or "",
            "chunk_type": "congress_bill_text",
        }
        for chunk in _split_text(full_text, base_id, meta):
            chunk["bill_id"] = bill_id
            chunk["congress"] = congress
            chunk["chunk_type"] = "congress_bill_text"
            chunks.append(chunk)

    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Congress.gov bill text RAG chunks from polls.db")
    parser.add_argument("--congress", type=int, help="Filter to one Congress, e.g. 119")
    parser.add_argument("--bill", help="Filter to one bill, e.g. HR1 or S2")
    parser.add_argument("--out", default="congress_bills_text_rag.jsonl")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"ERROR: {DB_PATH} not found. Run ingest_congress.py with --fetch-text first.")

    conn = sqlite3.connect(DB_PATH)
    chunks = build_chunks(conn, args.congress, args.bill)
    conn.close()

    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"Wrote {len(chunks)} chunks to {out_path}")
    if chunks:
        print("Sample chunk text:\n")
        print(chunks[0]["text"][:500])

if __name__ == "__main__":
    main()
