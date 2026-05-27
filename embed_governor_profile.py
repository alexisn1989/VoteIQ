"""
embed_governor_profile.py
Chunk, embed, and upsert Virginia governor-action bill text into the
voteiq_bills ChromaDB collection under chunk_type="governor_action".

Covers all governor actions (signed, vetoed, amended) for Spanberger so
that semantic search can retrieve relevant bills when users ask about
the governor's legislative record.

Uses voyage-law-2 — same model as bill text and EOs, so everything
coexists in the same collection and is returned by existing queries.

Usage:
    python embed_governor_profile.py              # embed all un-embedded actions
    python embed_governor_profile.py --force      # re-embed everything
    python embed_governor_profile.py --limit 50   # embed first 50 only (test)
    python embed_governor_profile.py --action signed   # one action type only
"""
from __future__ import annotations

import argparse
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
VOYAGE_BATCH    = 16   # governor-action docs are short — larger batches fine
CHROMA_BATCH    = 100
DB_PATH         = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"

# Actions to embed (lower-case keys from governor_actions.action column)
EMBED_ACTIONS = {"signed", "vetoed", "amended"}


# ── text utilities ────────────────────────────────────────────────────────────

def build_document(row: sqlite3.Row) -> str:
    """Combine governor-action fields into a rich text document for embedding."""
    parts: list[str] = []

    bill    = row["bill_number"] or ""
    title   = row["title"] or ""
    label   = row["action_label"] or row["action"] or ""
    date    = row["action_date"] or ""
    gov     = row["governor"] or ""
    sponsor = row["sponsor_name"] or ""
    party   = row["sponsor_party"] or ""
    session = row["session"] or ""
    chapter = row["chapter_number"] or ""
    eff     = row["effective_date"] or ""

    parts.append(f"{bill}: {title}")
    parts.append(f"Governor action: {label}")
    if date:
        parts.append(f"Action date: {date}")
    if gov:
        parts.append(f"Governor: {gov}")
    if sponsor:
        sponsor_str = sponsor
        if party:
            sponsor_str += f" ({party})"
        parts.append(f"Sponsor: {sponsor_str}")
    if session:
        parts.append(f"Session: {session}")
    if chapter:
        parts.append(f"Chapter: {chapter}")
    if eff:
        parts.append(f"Effective date: {eff}")

    return "\n".join(parts)


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
    for attempt in range(6):
        try:
            return vo.embed(texts, model=EMBED_MODEL, input_type="document").embeddings
        except Exception as e:
            if any(k in str(e).lower() for k in ("rate", "429", "limit", "quota")):
                wait = 15 + 15 * attempt
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


def already_embedded_ids(collection) -> set[str]:
    """Return set of Chroma doc IDs already stored as governor_action chunks."""
    try:
        result = collection.get(where={"chunk_type": "governor_action"}, include=[])
        return set(result["ids"])
    except Exception:
        return set()


# ── main pipeline ─────────────────────────────────────────────────────────────

def run(
    db_path: str = str(DB_PATH),
    force: bool = False,
    limit: int | None = None,
    action_filter: str | None = None,
) -> None:
    import voyageai

    if not VOYAGE_API_KEY:
        raise SystemExit("VOYAGE_API_KEY not set")
    if not all([CHROMA_TENANT, CHROMA_DATABASE, CHROMA_API_KEY]):
        raise SystemExit("CHROMA_TENANT / CHROMA_DATABASE / CHROMA_API_KEY not set")

    vo = voyageai.Client(api_key=VOYAGE_API_KEY)
    collection = get_collection()

    existing = set() if force else already_embedded_ids(collection)
    print(f"Already embedded: {len(existing)} governor-action chunks", flush=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    actions_to_embed = (
        {action_filter.lower()} if action_filter else EMBED_ACTIONS
    )
    placeholders = ",".join("?" * len(actions_to_embed))
    rows = conn.execute(f"""
        SELECT id, bill_number, session, title, raw_status,
               action, action_key, action_label, action_date,
               chapter_number, effective_date, governor,
               sponsor_name, sponsor_party, source_url
        FROM governor_actions
        WHERE lower(governor) LIKE '%spanberger%'
          AND action IN ({placeholders})
        ORDER BY action_date DESC, bill_number
    """, tuple(actions_to_embed)).fetchall()
    conn.close()

    if limit:
        rows = rows[:limit]

    print(f"Governor-action rows to process: {len(rows)}", flush=True)

    all_chunks: list[dict] = []
    for row in rows:
        doc = build_document(row)
        if not doc.strip():
            continue

        # Stable ID: session + bill_number + action_key
        bill_slug   = re.sub(r"[^a-z0-9]", "-", (row["bill_number"] or str(row["id"])).lower())
        action_slug = re.sub(r"[^a-z0-9]", "-", (row["action_key"] or row["action"] or "act").lower())
        session_slug = re.sub(r"[^a-z0-9]", "-", (row["session"] or "0").lower())
        chunk_id = f"ga-{session_slug}-{bill_slug}-{action_slug}"

        if chunk_id in existing:
            continue

        all_chunks.append({
            "id":   chunk_id,
            "text": doc,
            "meta": {
                "chunk_type":   "governor_action",
                "bill_id":      row["bill_number"] or f"GA-{row['id']}",
                "session":      row["session"] or "2026",
                "title":        row["title"] or "",
                "action":       row["action"] or "",
                "action_label": row["action_label"] or "",
                "action_date":  row["action_date"] or "",
                "governor":     row["governor"] or "",
                "sponsor_name": row["sponsor_name"] or "",
                "sponsor_party":row["sponsor_party"] or "",
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
        time.sleep(0.2)

    print(f"\nDone. {embedded} governor-action chunks embedded into '{COLLECTION_NAME}'.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed Virginia governor-action bill text into ChromaDB."
    )
    parser.add_argument("--db",     default=str(DB_PATH), help="Path to polls.db")
    parser.add_argument("--force",  action="store_true",  help="Re-embed already-stored chunks")
    parser.add_argument("--limit",  type=int, default=None, help="Process first N rows (test)")
    parser.add_argument("--action", default=None,
                        help="Only embed one action type: signed | vetoed | amended")
    args = parser.parse_args()
    run(db_path=args.db, force=args.force, limit=args.limit, action_filter=args.action)


if __name__ == "__main__":
    main()
