#!/usr/bin/env python3
"""
Pull education bills + named votes from openstates_va.db
and upsert them into the cloud ChromaDB collection (voteiq_bills).

Usage:
    python ingest_education_bills.py
    python ingest_education_bills.py --session 2025
"""
import argparse
import json
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import chromadb
import voyageai
from chromadb import Documents, EmbeddingFunction, Embeddings
from dotenv import load_dotenv

load_dotenv()

DB_PATH         = Path(__file__).parent / "openstates_va.db"
COLLECTION_NAME = "voteiq_bills"
EMBED_MODEL     = "voyage-law-2"
VOYAGE_MAX_TOKENS = 75_000

vo = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))


def _token_estimate(text: str) -> int:
    return max(1, int(len(text) * 0.45))


class VoyageEmbeddingFunction(EmbeddingFunction):
    def name(self) -> str:
        return "voyageai"

    def __call__(self, input: Documents) -> Embeddings:
        docs = list(input)
        batches, current, cur_tokens = [], [], 0
        for doc in docs:
            tok = _token_estimate(doc)
            if cur_tokens + tok > VOYAGE_MAX_TOKENS and current:
                batches.append(current)
                current, cur_tokens = [], 0
            current.append(doc)
            cur_tokens += tok
        if current:
            batches.append(current)

        all_embeddings = []
        for batch in batches:
            result = vo.embed(batch, model=EMBED_MODEL, input_type="document")
            all_embeddings.extend(result.embeddings)
            if len(batches) > 1:
                time.sleep(1)
        return all_embeddings


def get_collection():
    client = chromadb.HttpClient(
        ssl=True,
        host="api.trychroma.com",
        tenant=os.getenv("CHROMA_TENANT"),
        database=os.getenv("CHROMA_DATABASE"),
        headers={"x-chroma-token": os.getenv("CHROMA_API_KEY")},
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=VoyageEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )


def build_chunks(session: str) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)

    # Pull education bills
    bill_rows = conn.execute("""
        SELECT bill_id, title, subjects, sponsors, result, openstates_url
        FROM bills
        WHERE session = ?
          AND (subjects LIKE '%education%'
               OR subjects LIKE '%school%'
               OR title    LIKE '%school%'
               OR title    LIKE '%education%'
               OR title    LIKE '%teacher%'
               OR title    LIKE '%student%'
               OR title    LIKE '%university%'
               OR title    LIKE '%college%')
        ORDER BY bill_id
    """, (session,)).fetchall()

    if not bill_rows:
        print(f"No education bills found for session {session}")
        conn.close()
        return []

    print(f"Found {len(bill_rows)} education bills in {session}")

    # Pull all votes for those bills in one query
    bill_ids = [r[0] for r in bill_rows]
    placeholders = ",".join("?" * len(bill_ids))
    vote_rows = conn.execute(f"""
        SELECT bill_id, voter_name, option, motion, party
        FROM votes
        WHERE session = ?
          AND bill_id IN ({placeholders})
          AND voter_name != ''
        ORDER BY bill_id, voter_name
    """, [session] + bill_ids).fetchall()
    conn.close()

    # Group votes by bill
    votes_by_bill: dict[str, dict] = defaultdict(lambda: {"yes": [], "no": []})
    for bill_id, voter_name, option, motion, party in vote_rows:
        motion_lower = (motion or "").lower()
        is_committee = motion_lower.startswith("reported from") or motion_lower.startswith("subcommittee")
        if is_committee:
            continue  # floor votes only for this view
        if option == "yes":
            votes_by_bill[bill_id]["yes"].append(voter_name)
        elif option == "no":
            votes_by_bill[bill_id]["no"].append(voter_name)

    chunks = []
    for bill_id, title, subjects, sponsors, result, url in bill_rows:
        result_label = {"pass": "PASSED", "fail": "FAILED"}.get(result or "", "PENDING")
        floor_yes = votes_by_bill[bill_id]["yes"]
        floor_no  = votes_by_bill[bill_id]["no"]
        total     = len(floor_yes) + len(floor_no)

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

        if total:
            yes_pct = round(len(floor_yes) / total * 100)
            lines.append(f"\nFloor vote: {len(floor_yes)}-Y  {len(floor_no)}-N  ({yes_pct}% yes)")
            if floor_yes:
                lines.append(f"Voted YES ({len(floor_yes)}): {', '.join(floor_yes[:50])}")
            if floor_no:
                lines.append(f"Voted NO  ({len(floor_no)}): {', '.join(floor_no[:50])}")

        text = "\n".join(lines)
        chunk_id = f"edu_bill_{bill_id.lower()}_{session}"

        chunks.append({
            "id":       chunk_id,
            "text":     text,
            "metadata": {
                "bill_id":    bill_id,
                "session":    session,
                "chunk_type": "education_bill",
                "result":     result or "",
                "subjects":   (subjects or "")[:200],
                "sponsors":   (sponsors or "")[:200],
                "floor_yes":  str(len(floor_yes)),
                "floor_no":   str(len(floor_no)),
            },
        })

    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="2026")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found. Run build_openstates_db.py first.")
        return

    chunks = build_chunks(args.session)
    if not chunks:
        return

    print(f"Built {len(chunks)} chunks — upserting to '{COLLECTION_NAME}'...")
    collection = get_collection()

    batch_size = 200
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        collection.upsert(
            ids=       [c["id"]       for c in batch],
            documents= [c["text"]     for c in batch],
            metadatas= [c["metadata"] for c in batch],
        )
        print(f"  Upserted {min(i + batch_size, len(chunks))}/{len(chunks)}")
        if i + batch_size < len(chunks):
            time.sleep(1)

    print(f"Done. Collection now has {collection.count()} total documents.")


if __name__ == "__main__":
    main()
