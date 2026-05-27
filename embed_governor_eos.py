"""
embed_governor_eos.py
Chunk, embed, and upsert Virginia governor executive-order text into the
voteiq_bills ChromaDB collection under chunk_type="executive_order".

Uses voyage-law-2 — same model as bill text, so EOs coexist in the same
collection and are returned by existing semantic-search queries.

Usage:
    python embed_governor_eos.py              # embed all un-embedded EOs
    python embed_governor_eos.py --force      # re-embed everything
    python embed_governor_eos.py --limit 5    # embed first 5 only (test)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
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
VOYAGE_BATCH    = 8    # EOs are long — smaller batches to stay under token limit
CHROMA_BATCH    = 50
CHUNK_SIZE      = 1200
CHUNK_OVERLAP   = 150
DB_PATH         = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"


# ── text utilities ────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def chunk_text(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks: list[str] = []
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


def build_document(row: sqlite3.Row) -> str:
    """Combine all useful fields into a single rich text document for embedding."""
    parts: list[str] = []

    order_num = row["order_number"] or ""
    title     = row["title"] or ""
    governor  = row["governor"] or ""
    signed    = row["signed_date"] or ""

    parts.append(f"Executive Order {order_num}: {title}")
    if governor:
        parts.append(f"Governor: {governor}")
    if signed:
        parts.append(f"Signed: {signed}")

    try:
        topics = json.loads(row["policy_topics"] or "[]")
        if topics:
            parts.append("Policy topics: " + ", ".join(topics))
    except Exception:
        pass

    try:
        agencies = json.loads(row["agencies"] or "[]")
        if agencies:
            parts.append("Agencies: " + ", ".join(agencies))
    except Exception:
        pass

    if row["summary"]:
        parts.append("Summary: " + row["summary"])

    if row["full_text"]:
        parts.append("Full text:\n" + row["full_text"])

    return "\n\n".join(parts)


# ── ChromaDB / Voyage helpers ─────────────────────────────────────────────────

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
    import voyageai
    for attempt in range(6):
        try:
            return vo.embed(texts, model=EMBED_MODEL, input_type="document").embeddings
        except Exception as e:
            if any(k in str(e).lower() for k in ("rate", "429", "limit", "quota")):
                wait = 20 + 20 * attempt
                print(f"  [rate limit] waiting {wait}s …", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Embedding failed after 6 retries")


def upsert_chunks(collection, chunks: list[dict], embeddings: list[list[float]]) -> None:
    for i in range(0, len(chunks), CHROMA_BATCH):
        batch_c = chunks[i : i + CHROMA_BATCH]
        batch_e = embeddings[i : i + CHROMA_BATCH]
        collection.upsert(
            ids        = [c["id"] for c in batch_c],
            documents  = [c["text"] for c in batch_c],
            metadatas  = [c["meta"] for c in batch_c],
            embeddings = batch_e,
        )


# ── already-embedded check ────────────────────────────────────────────────────

def already_embedded_ids(collection) -> set[str]:
    """Return the set of Chroma doc IDs that are already in the collection."""
    try:
        result = collection.get(where={"chunk_type": "executive_order"}, include=[])
        return set(result["ids"])
    except Exception:
        return set()


# ── main pipeline ─────────────────────────────────────────────────────────────

def run(db_path: str = str(DB_PATH), force: bool = False, limit: int | None = None) -> None:
    import voyageai

    if not VOYAGE_API_KEY:
        raise SystemExit("VOYAGE_API_KEY not set")
    if not all([CHROMA_TENANT, CHROMA_DATABASE, CHROMA_API_KEY]):
        raise SystemExit("CHROMA_TENANT / CHROMA_DATABASE / CHROMA_API_KEY not set")

    vo = voyageai.Client(api_key=VOYAGE_API_KEY)
    collection = get_collection()

    existing = set() if force else already_embedded_ids(collection)
    print(f"Already embedded: {len(existing)} chunks", flush=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, order_number, title, signed_date, governor, jurisdiction,
               summary, policy_topics, agencies, actions_required,
               effective_date, full_text, source_url
        FROM governor_executive_orders
        ORDER BY signed_date DESC
    """).fetchall()
    conn.close()

    if limit:
        rows = rows[:limit]

    print(f"EOs in DB: {len(rows)}", flush=True)

    all_chunks: list[dict] = []
    for row in rows:
        doc = build_document(row)
        text_chunks = chunk_text(doc)
        order_slug = re.sub(r"[^a-z0-9]", "-", (row["order_number"] or str(row["id"])).lower())
        for idx, chunk in enumerate(text_chunks):
            chunk_id = f"eo-{order_slug}-{idx}"
            if chunk_id in existing:
                continue
            all_chunks.append({
                "id":   chunk_id,
                "text": chunk,
                "meta": {
                    "chunk_type":   "executive_order",
                    "bill_id":      row["order_number"] or f"EO-{row['id']}",
                    "session":      (row["signed_date"] or "")[:4] or "2026",
                    "title":        row["title"] or "",
                    "governor":     row["governor"] or "",
                    "signed_date":  row["signed_date"] or "",
                    "source_url":   row["source_url"] or "",
                    "source":       "virginia",
                },
            })

    if not all_chunks:
        print("Nothing new to embed.", flush=True)
        return

    print(f"Chunks to embed: {len(all_chunks)}", flush=True)

    embedded = 0
    for i in range(0, len(all_chunks), VOYAGE_BATCH):
        batch = all_chunks[i : i + VOYAGE_BATCH]
        texts = [c["text"] for c in batch]
        print(f"  embedding {i+1}–{i+len(batch)} / {len(all_chunks)} …", flush=True)
        vecs = embed_batch(texts, vo)
        upsert_chunks(collection, batch, vecs)
        embedded += len(batch)
        time.sleep(0.3)

    print(f"\nDone. {embedded} chunks embedded into '{COLLECTION_NAME}'.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed Virginia EO text into ChromaDB.")
    parser.add_argument("--db",    default=str(DB_PATH), help="Path to polls.db")
    parser.add_argument("--force", action="store_true",  help="Re-embed already-stored chunks")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N EOs (test)")
    args = parser.parse_args()
    run(db_path=args.db, force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
