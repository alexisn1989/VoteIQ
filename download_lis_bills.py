#!/usr/bin/env python3
"""
Download 2026 Virginia General Assembly bills from the LIS API and ingest into ChromaDB.

Usage:
    python download_lis_bills.py              # download + ingest HB and SB bills
    python download_lis_bills.py --types HB   # only House bills
    python download_lis_bills.py --save       # save raw JSON to bills_2026.json before ingesting
    python download_lis_bills.py --dry-run    # download and print counts, no embedding
"""
import os
import re
import json
import time
import argparse
from html.parser import HTMLParser
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

LIS_API_KEY    = os.getenv("LIS_API_KEY")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
CHROMA_TENANT  = os.getenv("CHROMA_TENANT")
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE")
CHROMA_API_KEY  = os.getenv("CHROMA_API_KEY")

SESSION_CODE    = "20261"   # overridden by --session arg at runtime
SESSION_LABEL   = "2026"
LIS_BASE        = "https://lis.virginia.gov"
COLLECTION_NAME = "voteiq_bills"
EMBED_MODEL     = "voyage-law-2"
VOYAGE_BATCH    = 64    # standard batch size
CHROMA_BATCH    = 100   # upsert batch size for ChromaDB


# ── HTML stripper ─────────────────────────────────────────────────────────────

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return " ".join(self._parts).strip()


def _fix_encoding(text: str) -> str:
    """Fix mojibake from UTF-8 bytes interpreted as Latin-1 (common in LIS data)."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def strip_html(html: str) -> str:
    if not html:
        return ""
    import html as _html
    text = _html.unescape(html)
    s = _HTMLStripper()
    s.feed(text)
    text = s.get_text()
    text = re.sub(r"\s+", " ", text).strip()
    return _fix_encoding(text)


# ── LIS API ───────────────────────────────────────────────────────────────────

def fetch_bills(_: str = "") -> list[dict]:
    """Download all bills for session 20261."""
    import urllib.request
    url = f"{LIS_BASE}/AdvancedLegislationSearch/api/GetLegislationListAsync"
    body_bytes = json.dumps({"sessionCode": SESSION_CODE}).encode()
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "WebAPIKey": LIS_API_KEY,
            "User-Agent": "VoteIQ/1.0",
        },
        method="POST",
    )
    print(f"[lis] Fetching all bills for session {SESSION_CODE}...")
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    bills = data.get("Legislations") if isinstance(data, dict) else data
    print(f"[lis] Got {len(bills or [])} bills")
    return bills or []


# ── Build chunks ──────────────────────────────────────────────────────────────

def bill_to_chunk(bill: dict, session: str = SESSION_LABEL) -> dict | None:
    """Convert a LIS bill record into a ChromaDB chunk dict."""
    num = bill.get("LegislationNumber", "")      # "HB1", "SB1", etc.
    if not num:
        return None

    summary_html = bill.get("LegislationSummary", "") or ""
    summary = strip_html(summary_html)
    description = _fix_encoding((bill.get("Description") or "").strip())
    title = _fix_encoding((bill.get("LegislationTitle") or "").strip())
    status = (bill.get("LegislationStatus") or "").strip()

    # Skip bills with no useful content
    if not summary and not description:
        return None

    patrons = bill.get("Patrons") or []
    chief = next(
        (p["MemberDisplayName"] for p in patrons if p.get("Name") == "Chief Patron"),
        None,
    )
    co_patrons = [
        p["MemberDisplayName"] for p in patrons if p.get("Name") != "Chief Patron"
    ]

    lines = [f"Bill {num} ({session} Virginia Regular Session)"]
    if title:
        lines.append(f"Title: {title}")
    if description:
        lines.append(f"Short title: {description}")
    if chief:
        lines.append(f"Primary patron: {chief}")
    if co_patrons:
        lines.append(f"Co-patrons ({len(co_patrons)}): {', '.join(co_patrons)}")
    if status:
        lines.append(f"Status: {status}")
    if summary:
        lines.append(f"Summary: {summary}")

    text = "\n".join(lines)
    chunk_id = f"{num}_{session}_summary_0"

    return {
        "id":       chunk_id,
        "text":     text,
        "metadata": {
            "bill_id":    num,
            "session":    session,
            "chunk_type": "bill_summary",
            "subject":    description,
            "status":     status,
            "approved":   "",
        },
    }


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_batch(texts: list[str], vo) -> list[list[float]]:
    """Embed a list of texts using Voyage AI with retry on rate limit."""
    for attempt in range(6):
        try:
            result = vo.embed(texts, model=EMBED_MODEL, input_type="document")
            return result.embeddings
        except Exception as e:
            msg = str(e).lower()
            if "rate" in msg or "429" in msg or "limit" in msg or "quota" in msg:
                wait = 20 + 20 * attempt
                print(f"[rate limit] attempt {attempt+1}, waiting {wait}s... ({e})")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Embedding failed after 6 retries")


# ── ChromaDB upsert ───────────────────────────────────────────────────────────

def get_collection():
    import chromadb as _chroma
    client = _chroma.HttpClient(
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


def upsert_chunks(collection, chunks: list[dict], embeddings: list[list[float]]):
    for i in range(0, len(chunks), CHROMA_BATCH):
        batch_c = chunks[i : i + CHROMA_BATCH]
        batch_e = embeddings[i : i + CHROMA_BATCH]
        collection.upsert(
            ids=        [c["id"]       for c in batch_c],
            documents=  [c["text"]     for c in batch_c],
            embeddings= batch_e,
            metadatas=  [c["metadata"] for c in batch_c],
        )
        print(f"  upserted {i + len(batch_c)}/{len(chunks)}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="20261", help="LIS session code, e.g. 20261 (2026) or 20251 (2025)")
    parser.add_argument("--types",   default="HB,SB", help="Comma-separated bill types, e.g. HB,SB")
    parser.add_argument("--save",    action="store_true", help="Save raw LIS JSON before ingesting")
    parser.add_argument("--dry-run", action="store_true", help="Download only, no embedding or upsert")
    parser.add_argument("--limit",   type=int, default=0, help="Only ingest first N chunks (0 = all)")
    args = parser.parse_args()

    global SESSION_CODE, SESSION_LABEL
    SESSION_CODE  = args.session
    SESSION_LABEL = args.session[:4]   # "2026", "2025", etc.

    # 1. Download all bills for the session (HB + SB in one call, deduplicated by ID)
    all_raw = fetch_bills("ALL")
    seen = set()
    deduped = []
    for b in all_raw:
        bid = b.get("LegislationID")
        if bid not in seen:
            seen.add(bid)
            deduped.append(b)
    all_raw = deduped
    print(f"[lis] {len(all_raw)} unique bills after dedup")

    if args.save:
        out = Path("bills_2026_raw.json")
        out.write_text(json.dumps(all_raw, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[save] Wrote {len(all_raw)} bills to {out}")

    # 2. Build chunks
    chunks = []
    skipped = 0
    for b in all_raw:
        c = bill_to_chunk(b)
        if c:
            chunks.append(c)
        else:
            skipped += 1

    print(f"[chunk] Built {len(chunks)} chunks  (skipped {skipped} bills with no content)")

    if args.limit:
        chunks = chunks[: args.limit]
        print(f"[limit] Capped to {len(chunks)} chunks")

    if args.dry_run:
        print("[dry-run] Stopping before embed/upsert.")
        print("Sample chunk:")
        if chunks:
            print(chunks[0]["text"][:500])
        return

    # 3. Embed
    import voyageai as _vo
    vo = _vo.Client(api_key=VOYAGE_API_KEY)

    all_embeddings: list[list[float]] = []
    texts = [c["text"] for c in chunks]
    print(f"[embed] Embedding {len(texts)} chunks in batches of {VOYAGE_BATCH}...")
    for i in range(0, len(texts), VOYAGE_BATCH):
        batch = texts[i : i + VOYAGE_BATCH]
        embs = embed_batch(batch, vo)
        all_embeddings.extend(embs)
        print(f"  embedded {i + len(batch)}/{len(texts)}")
        if i + VOYAGE_BATCH < len(texts):
            time.sleep(1)  # brief pause between batches

    # 4. Upsert
    print(f"[upsert] Connecting to ChromaDB...")
    collection = get_collection()
    print(f"[upsert] Upserting {len(chunks)} chunks...")
    upsert_chunks(collection, chunks, all_embeddings)
    print(f"[done] Collection '{COLLECTION_NAME}' now has {collection.count()} total documents.")


if __name__ == "__main__":
    main()
