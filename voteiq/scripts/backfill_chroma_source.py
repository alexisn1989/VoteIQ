"""
voteiq/scripts/backfill_chroma_source.py

Backfill source="virginia" onto existing Chroma Cloud docs
that were upserted without a source field.

Does NOT re-embed — metadata update only.
Safe to re-run; already-tagged docs are skipped.
"""
import os

from dotenv import load_dotenv
load_dotenv()

import chromadb

COLLECTION_NAME = "voteiq_bills"
BATCH_SIZE      = 250  # Chroma Cloud hard cap on limit= is 300


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
        metadata={"hnsw:space": "cosine"},
    )


def backfill_source_metadata(source_value: str = "virginia"):
    """
    Set source=source_value on every doc missing a source field.
    Fetches all IDs first, then paginates by slicing the ID list.
    """
    collection = get_collection()
    total = collection.count()
    print(f"Collection docs: {total:,}")

    # Paginate ID fetch — cloud caps a single get() at ~300
    all_ids: list[str] = []
    fetch_offset = 0
    while True:
        page = collection.get(limit=BATCH_SIZE, offset=fetch_offset, include=[])
        chunk = page["ids"]
        if not chunk:
            break
        all_ids.extend(chunk)
        fetch_offset += len(chunk)
        if len(chunk) < BATCH_SIZE:
            break
    print(f"IDs fetched: {len(all_ids):,}")

    updated = 0
    skipped = 0

    for i in range(0, len(all_ids), BATCH_SIZE):
        batch_ids = all_ids[i : i + BATCH_SIZE]

        batch = collection.get(
            ids=batch_ids,
            include=["metadatas"],
        )

        update_ids      = []
        update_metadatas = []

        for doc_id, metadata in zip(batch["ids"], batch["metadatas"]):
            metadata = metadata or {}
            if metadata.get("source"):
                skipped += 1
                continue
            metadata["source"] = source_value
            update_ids.append(doc_id)
            update_metadatas.append(metadata)

        if update_ids:
            collection.update(
                ids=update_ids,
                metadatas=update_metadatas,
            )
            updated += len(update_ids)

        print(f"  [{i + len(batch_ids):>6}/{len(all_ids)}] updated={updated:,} skipped={skipped:,}")

    print("=" * 40)
    print(f"Backfill complete")
    print(f"Updated : {updated:,}")
    print(f"Skipped : {skipped:,}")
    print(f"Total   : {total:,}")


if __name__ == "__main__":
    backfill_source_metadata("virginia")
