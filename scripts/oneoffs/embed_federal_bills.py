"""
embed_federal_bills.py
Chunk, embed, and upsert federal bill text into the voteiq_bills ChromaDB collection.
Uses voyage-law-2 — same model as VA state bills, so they coexist in one collection.
Run after fetch_bill_texts_only.py.
"""
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
VOYAGE_BATCH    = 16
CHROMA_BATCH    = 100
CHUNK_SIZE      = 1000
CHUNK_OVERLAP   = 150
DB_PATH         = Path(__file__).parent / "polls.db"


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
            ids=       [c["id"]       for c in bc],
            documents= [c["text"]     for c in bc],
            embeddings=be,
            metadatas= [c["metadata"] for c in bc],
        )
        print(f"  upserted {i + len(bc)}/{len(chunks)}", flush=True)


def main():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT bt.congress, bt.bill_type, bt.bill_number, bt.text,
               COALESCE(cb.title, '')       AS title,
               COALESCE(cb.policy_area, '') AS policy_area
        FROM congress_bill_texts bt
        LEFT JOIN congress_bills cb
            ON  cb.congress    = bt.congress
            AND cb.bill_type   = bt.bill_type
            AND cb.bill_number = bt.bill_number
        WHERE bt.text IS NOT NULL AND LENGTH(bt.text) > 200
        GROUP BY bt.congress, bt.bill_type, bt.bill_number
        ORDER BY bt.congress DESC
    """).fetchall()
    conn.close()

    print(f"[federal] {len(rows)} bill text records", flush=True)

    all_chunks = []
    for congress, bill_type, bill_number, text, title, policy_area in rows:
        bid     = f"{bill_type.upper()}{bill_number}"
        session = "2025" if int(congress or 119) >= 119 else str(congress)

        for idx, chunk in enumerate(chunk_text(text)):
            doc = (f"{bid}: {title}\n\n{chunk}" if idx == 0 and title else chunk)
            all_chunks.append({
                "id":   f"federal_{bid}_{congress}_chunk{idx}",
                "text": doc,
                "metadata": {
                    "bill_id":     bid,
                    "session":     session,
                    "congress":    str(congress),
                    "chunk_type":  "bill_summary" if idx == 0 else "bill_text",
                    "source":      "federal",
                    "policy_area": policy_area or "",
                },
            })

    print(f"[federal] {len(all_chunks)} chunks from {len(rows)} bills", flush=True)

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


if __name__ == "__main__":
    main()
