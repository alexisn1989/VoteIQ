"""
embed_norfolk_votes.py
Embed Norfolk City Council vote records into the voteiq_bills ChromaDB collection.
Uses voyage-law-2 — same model as VA/federal bills, coexists in one collection.
Run after scrape_norfolk_council.py and enrich_norfolk_votes.py.

Usage:
    python embed_norfolk_votes.py
    python embed_norfolk_votes.py --dry-run   # count chunks, no embed
    python embed_norfolk_votes.py --reset     # delete existing Norfolk docs first
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VOYAGE_API_KEY  = os.getenv("VOYAGE_API_KEY")
CHROMA_TENANT   = os.getenv("CHROMA_TENANT")
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE")
CHROMA_API_KEY  = os.getenv("CHROMA_API_KEY")
COLLECTION_NAME = "voteiq_bills"
EMBED_MODEL     = "voyage-law-2"
VOYAGE_BATCH    = 16
CHROMA_BATCH    = 100
DB_PATH         = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"


def get_collection():
    import chromadb
    client = chromadb.HttpClient(
        ssl=True,
        host="api.trychroma.com",
        tenant=CHROMA_TENANT,
        database=CHROMA_DATABASE,
        headers={"x-chroma-token": CHROMA_API_KEY},
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def embed_batch(texts: list[str], vo) -> list[list[float]]:
    for attempt in range(6):
        try:
            return vo.embed(texts, model=EMBED_MODEL, input_type="document").embeddings
        except Exception as e:
            if any(k in str(e).lower() for k in ("rate", "429", "limit", "quota")):
                wait = 20 + 20 * attempt
                print(f"  [rate limit] waiting {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Embedding failed after 6 retries")


def upsert_chunks(collection, chunks: list[dict], embeddings: list[list[float]]) -> None:
    for i in range(0, len(chunks), CHROMA_BATCH):
        bc = chunks[i: i + CHROMA_BATCH]
        be = embeddings[i: i + CHROMA_BATCH]
        collection.upsert(
            ids=       [c["id"]       for c in bc],
            documents= [c["text"]     for c in bc],
            embeddings=be,
            metadatas= [c["metadata"] for c in bc],
        )
        print(f"  upserted {i + len(bc)}/{len(chunks)}", flush=True)


def build_chunks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT
            v.id, v.meeting_date, v.agenda_item, v.title,
            v.result, v.vote_count, v.category,
            e.plain_english, e.topic, e.state_tags, e.federal_tags
        FROM norfolk_council_votes v
        LEFT JOIN norfolk_vote_enrichment e ON v.title = e.title
        WHERE v.category IN ('substantive', 'consent')
          AND v.title != ''
        ORDER BY v.meeting_date, v.agenda_item
    """).fetchall()

    chunks = []
    for (vote_id, meeting_date, agenda_item, title,
         result, vote_count, category,
         plain_english, topic, state_tags_json, federal_tags_json) in rows:

        plain = plain_english or title or ""
        topic_str = topic or "other"
        # meeting_date is stored as "APRIL 23, 2024" — extract 4-digit year
        _yr = re.search(r'\b(20\d{2})\b', meeting_date or "")
        year = _yr.group(1) if _yr else "unknown"

        state_tags = []
        federal_tags = []
        try:
            state_tags = json.loads(state_tags_json) if state_tags_json else []
        except Exception:
            pass
        try:
            federal_tags = json.loads(federal_tags_json) if federal_tags_json else []
        except Exception:
            pass

        lines = [
            f"Norfolk City Council — {meeting_date}",
            f"{agenda_item} ({category}): {plain}",
            f"Topic: {topic_str} | Result: {result} ({vote_count})",
        ]
        if state_tags:
            lines.append(f"Virginia legislative topics: {', '.join(state_tags)}")
        if federal_tags:
            lines.append(f"Federal programs: {', '.join(federal_tags)}")

        text = "\n".join(lines)

        chunks.append({
            "id": f"norfolk_vote_{vote_id}",
            "text": text,
            "metadata": {
                "source":       "norfolk_council_vote",
                "chunk_type":   "norfolk_council_vote",
                "jurisdiction": "local",
                "locality":     "norfolk",
                "bill_id":      f"NCC-{agenda_item} {year}",
                "session":      year,
                "year":         year,
                "meeting_date": meeting_date or "",
                "agenda_item":  agenda_item or "",
                "category":     category or "",
                "result":       result or "",
                "topic":        topic_str,
                "vote_count":   vote_count or "",
            },
        })

    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed Norfolk council votes into voteiq_bills ChromaDB")
    parser.add_argument("--db",      default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true", help="Count chunks without embedding")
    parser.add_argument("--reset",   action="store_true", help="Delete existing Norfolk docs first")
    args = parser.parse_args()

    if not VOYAGE_API_KEY and not args.dry_run:
        print("ERROR: VOYAGE_API_KEY not set in .env", file=sys.stderr)
        return 1
    if not all([CHROMA_TENANT, CHROMA_DATABASE, CHROMA_API_KEY]) and not args.dry_run:
        print("ERROR: CHROMA_TENANT / CHROMA_DATABASE / CHROMA_API_KEY not set", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    chunks = build_chunks(conn)
    conn.close()

    print(f"[norfolk] {len(chunks)} vote chunks to embed", flush=True)

    if args.dry_run:
        print("[dry-run] skipping embed/upsert")
        for c in chunks[:3]:
            print(f"\n--- {c['id']} ---\n{c['text']}")
        return 0

    import voyageai
    vo = voyageai.Client(api_key=VOYAGE_API_KEY)

    collection = get_collection()

    if args.reset:
        existing = collection.get(where={"source": "norfolk_council_vote"}, include=[])
        ids_to_delete = existing.get("ids", [])
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
            print(f"[reset] deleted {len(ids_to_delete)} existing Norfolk docs", flush=True)

    texts = [c["text"] for c in chunks]
    all_embeddings: list[list[float]] = []
    print(f"[embed] {len(texts)} chunks in batches of {VOYAGE_BATCH}...", flush=True)
    for i in range(0, len(texts), VOYAGE_BATCH):
        batch = texts[i: i + VOYAGE_BATCH]
        all_embeddings.extend(embed_batch(batch, vo))
        print(f"  embedded {i + len(batch)}/{len(texts)}", flush=True)
        if i + VOYAGE_BATCH < len(texts):
            time.sleep(1)

    print("[upsert] Connecting to ChromaDB...", flush=True)
    upsert_chunks(collection, chunks, all_embeddings)
    print(f"[done] '{COLLECTION_NAME}' now has {collection.count()} total documents.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
