"""
embed_floor_statements.py
Chunk, embed, and upsert floor statement text into the voteiq_bills ChromaDB collection.
Uses voyage-law-2 — same model as VA/federal bills, so they coexist in one collection.
Run after ingest_floor_statements.py (or ingest_crec.py --fetch-text).
"""
import argparse
import hashlib
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
CHUNK_SIZE      = 1000
CHUNK_OVERLAP   = 150
DB_PATH         = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            for sep in (". ", ".\n", "\n", " "):
                pos = text.rfind(sep, start + CHUNK_SIZE // 2, end)
                if pos != -1:
                    end = pos + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return [c for c in chunks if c]


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


def upsert_chunks(collection, chunks: list[dict], embeddings: list[list[float]]):
    for i in range(0, len(chunks), CHROMA_BATCH):
        bc = chunks[i : i + CHROMA_BATCH]
        be = embeddings[i : i + CHROMA_BATCH]
        collection.upsert(
            ids=        [c["id"]       for c in bc],
            documents=  [c["text"]     for c in bc],
            embeddings= be,
            metadatas=  [c["metadata"] for c in bc],
        )
        print(f"  upserted {i + len(bc)}/{len(chunks)}", flush=True)


def make_chunk_id(granule_id: str, bioguide_id: str, title: str, stmt_date: str, idx: int) -> str:
    if granule_id:
        return f"floor_{granule_id}_chunk{idx}"
    digest = hashlib.sha1(f"{bioguide_id}|{stmt_date}|{title}".encode()).hexdigest()[:12]
    return f"floor_{bioguide_id}_{digest}_chunk{idx}"


def normalize_chamber(chamber: str | None) -> str:
    chamber = (chamber or "").strip()
    low = chamber.lower()
    if "house" in low:
        return "House"
    if "senate" in low:
        return "Senate"
    return chamber


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Embed floor statements into voteiq_bills ChromaDB collection."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--bioguide", help="Embed a single member only")
    parser.add_argument(
        "--start", default="2023-01-01",
        help="Only embed statements on/after this date (default: 2023-01-01 = 118th Congress)"
    )
    parser.add_argument(
        "--min-text", type=int, default=500,
        help="Skip statements shorter than N chars (default: 500 — drops admin stubs)"
    )
    parser.add_argument(
        "--max-text", type=int, default=8000,
        help="Truncate statement text to N chars before chunking (default: 8000)"
    )
    parser.add_argument(
        "--max-chunks", type=int, default=8000,
        help="Maximum floor-statement chunks to embed/upsert (default: 8000)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Count chunks without embedding")
    args = parser.parse_args(argv)

    if not VOYAGE_API_KEY:
        print("ERROR: VOYAGE_API_KEY not set in .env", file=sys.stderr)
        return 1
    if not all([CHROMA_TENANT, CHROMA_DATABASE, CHROMA_API_KEY]) and not args.dry_run:
        print("ERROR: CHROMA_TENANT / CHROMA_DATABASE / CHROMA_API_KEY not set", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    query = """
        SELECT bioguide_id, member_name, chamber, statement_date,
               title, text, source_url, granule_id
        FROM congress_floor_statements
        WHERE text IS NOT NULL AND LENGTH(text) >= ?
          AND statement_date >= ?
    """
    params: list = [args.min_text, args.start]
    if args.bioguide:
        query += " AND bioguide_id = ?"
        params.append(args.bioguide)
    query += " ORDER BY statement_date DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    print(f"[floor] {len(rows)} statements with text (start={args.start})", flush=True)

    all_chunks: list[dict] = []
    for bioguide_id, member_name, chamber, stmt_date, title, text, source_url, granule_id in rows:
        header = f"{member_name} — {title} ({stmt_date})\n\n" if title else f"{member_name} ({stmt_date})\n\n"
        text_to_embed = text[:args.max_text]
        for idx, chunk in enumerate(chunk_text(text_to_embed)):
            if args.max_chunks and len(all_chunks) >= args.max_chunks:
                break
            doc = (header + chunk) if idx == 0 else chunk
            all_chunks.append({
                "id": make_chunk_id(granule_id or "", bioguide_id, title or "", stmt_date or "", idx),
                "text": doc,
                "metadata": {
                    "source":       "floor_statement",
                    "chunk_type":   "floor_statement",
                    "jurisdiction": "federal",
                    "record_source": "GovInfo / Congressional Record",
                    "bioguide_id":  bioguide_id,
                    "member_name":  member_name or "",
                    "congress":     119,
                    "chamber":      normalize_chamber(chamber),
                    "statement_date": stmt_date or "",
                    "title":        (title or "")[:200],
                    "source_url":   source_url or "",
                    "speaker_verified": True,
                    "session":      (stmt_date or "")[:4],
                    "year":         (stmt_date or "")[:4],
                },
            })
        if args.max_chunks and len(all_chunks) >= args.max_chunks:
            break

    print(f"[floor] {len(all_chunks)} chunks from {len(rows)} statements", flush=True)

    if args.dry_run:
        print("[dry-run] skipping embed/upsert")
        return 0

    import voyageai
    vo = voyageai.Client(api_key=VOYAGE_API_KEY)

    texts = [c["text"] for c in all_chunks]
    all_embeddings: list[list[float]] = []
    print(f"[embed] {len(texts)} chunks in batches of {VOYAGE_BATCH}...", flush=True)
    for i in range(0, len(texts), VOYAGE_BATCH):
        batch = texts[i : i + VOYAGE_BATCH]
        all_embeddings.extend(embed_batch(batch, vo))
        print(f"  embedded {i + len(batch)}/{len(texts)}", flush=True)
        if i + VOYAGE_BATCH < len(texts):
            time.sleep(2)

    print("[upsert] Connecting to ChromaDB...", flush=True)
    collection = get_collection()
    print(f"[upsert] Upserting {len(all_chunks)} chunks...", flush=True)
    upsert_chunks(collection, all_chunks, all_embeddings)
    print(f"[done] '{COLLECTION_NAME}' now has {collection.count()} total documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
