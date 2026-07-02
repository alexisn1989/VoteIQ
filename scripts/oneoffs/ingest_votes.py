#!/usr/bin/env python3
"""
Ingest Virginia GA bills and vote records into ChromaDB using LegiScan.

Extracts: bill metadata (title, sponsors, subject, summary), action history, and vote records.
LegiScan's getDataset downloads a complete session ZIP (bills + votes) in one call.
Free API key: https://legiscan.com/legiscan

Usage:
    python ingest_votes.py --session 2026    # ingest 2026 bills & votes
    python ingest_votes.py --session 2025
    python ingest_votes.py --all             # 2022-2026
    python ingest_votes.py --dry-run --session 2026
"""
import os, re, json, time, zipfile, base64, argparse, io
from dotenv import load_dotenv

load_dotenv()

LEGISCAN_API_KEY = os.getenv("LEGISCAN_API_KEY")
VOYAGE_API_KEY   = os.getenv("VOYAGE_API_KEY")
CHROMA_TENANT    = os.getenv("CHROMA_TENANT")
CHROMA_DATABASE  = os.getenv("CHROMA_DATABASE")
CHROMA_API_KEY   = os.getenv("CHROMA_API_KEY")

LEGISCAN_BASE   = "https://api.legiscan.com/"
COLLECTION_NAME = "voteiq_bills"
EMBED_MODEL     = "voyage-law-2"
VOYAGE_BATCH    = 16
CHROMA_BATCH    = 100

# LegiScan session IDs for Virginia regular sessions
# Run: python ingest_votes.py --list-sessions  to refresh this
VA_SESSION_IDS = {
    "2026": None,  # populated at runtime via getSessionList
    "2025": None,
    "2024": None,
    "2023": None,
    "2022": None,
}


# ── LegiScan helpers ──────────────────────────────────────────────────────────

def legiscan(op: str, **params) -> dict:
    import urllib.request
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{LEGISCAN_BASE}?key={LEGISCAN_API_KEY}&op={op}&{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "VoteIQ/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    if data.get("status") == "ERROR":
        raise RuntimeError(f"LegiScan error: {data}")
    return data


def get_va_sessions() -> dict[str, tuple[int, str]]:
    """Return mapping of year -> (session_id, access_key) for VA regular sessions."""
    datasets = legiscan("getDatasetList", state="VA", year="")
    dataset_list = datasets.get("datasetlist", [])
    result = {}
    for ds in dataset_list:
        name = ds.get("session_name", "")
        sid  = ds.get("session_id")
        key  = ds.get("access_key")
        m = re.search(r"\b(20\d{2})\b", name)
        if m and "Regular" in name:
            year = m.group(1)
            if year not in result:
                result[year] = (sid, key)
    return result


def download_dataset(session_id: int, access_key: str) -> dict:
    """Download full LegiScan dataset for a session. Returns parsed JSON."""
    data = legiscan("getDataset", id=session_id, access_key=access_key)
    # Dataset comes as base64-encoded ZIP
    zip_b64 = data.get("dataset", {}).get("zip", "")
    if not zip_b64:
        raise RuntimeError("No zip in dataset response")
    zip_bytes = base64.b64decode(zip_b64)
    result = {"bills": {}}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            raw = json.loads(zf.read(name))
            # Bill files: VA/2026rs/bill/HB/HB0004.json
            if "/bill/" in name:
                bill = raw.get("bill", {})
                bnum = bill.get("bill_number", "")
                if bnum:
                    result["bills"][bnum] = bill
    return result


# ── Bill metadata chunk builder ──────────────────────────────────────────────

def bill_to_chunks(bill_number: str, session_label: str, bill: dict) -> list[dict]:
    """Convert LegiScan bill metadata into ChromaDB chunk dicts."""
    chunks = []

    # ── Bill summary/overview ────────────────────────────────────────────────
    title       = bill.get("title", "")
    short_title = bill.get("short_title", "")
    subject     = bill.get("subject", "")
    summary     = bill.get("summary", "")
    status      = bill.get("status", "")
    status_date = bill.get("status_date", "")

    primary_sponsor = bill.get("primary_sponsor", {})
    primary_name    = primary_sponsor.get("name", "") if isinstance(primary_sponsor, dict) else primary_sponsor

    sponsors = bill.get("sponsors", [])
    sponsor_names = []
    for s in sponsors:
        name = s.get("name", "") if isinstance(s, dict) else s
        if name:
            sponsor_names.append(name)

    action_history = bill.get("history", [])

    # Build overview chunk
    overview_lines = [
        f"Bill {bill_number} — {session_label} Virginia Regular Session",
        f"Title: {title}",
    ]
    if short_title:
        overview_lines.append(f"Short Title: {short_title}")
    if subject:
        overview_lines.append(f"Subject: {subject}")
    if primary_name:
        overview_lines.append(f"Primary Sponsor: {primary_name}")
    if sponsor_names:
        overview_lines.append(f"Co-sponsors ({len(sponsor_names)}): {', '.join(sponsor_names)}")
    if status and status_date:
        overview_lines.append(f"Status: {status} (as of {status_date})")
    elif status:
        overview_lines.append(f"Status: {status}")
    if summary:
        overview_lines.append(f"\nSummary:\n{summary}")

    chunks.append({
        "id":   f"{bill_number}_{session_label}_overview",
        "text": "\n".join(overview_lines),
        "metadata": {
            "bill_id":        bill_number,
            "session":        session_label,
            "chunk_type":     "bill_overview",
            "title":          title,
            "subject":        subject,
            "status":         status,
            "primary_sponsor": primary_name,
            "co_sponsors":    ", ".join(sponsor_names),
        },
    })

    # ── Bill action history ──────────────────────────────────────────────────
    if action_history:
        history_lines = [f"Bill {bill_number} — Action History ({session_label})"]
        for action in action_history:
            act_date = action.get("date", "")
            act_desc = action.get("action", "")
            if act_date and act_desc:
                history_lines.append(f"  {act_date}: {act_desc}")

        if len(history_lines) > 1:
            chunks.append({
                "id":   f"{bill_number}_{session_label}_history",
                "text": "\n".join(history_lines),
                "metadata": {
                    "bill_id":    bill_number,
                    "session":    session_label,
                    "chunk_type": "bill_history",
                    "subject":    subject,
                },
            })

    return chunks


# ── Vote chunk builder ────────────────────────────────────────────────────────

def votes_to_chunks(bill_number: str, session_label: str, votes: list) -> list[dict]:
    """Convert LegiScan vote records into ChromaDB chunk dicts."""
    chunks = []
    for idx, vote in enumerate(votes, 1):
        date     = vote.get("date", "")
        desc     = vote.get("desc", "")
        yeas     = vote.get("yea", 0)
        nays     = vote.get("nay", 0)
        nv       = vote.get("nv", 0)
        absent   = vote.get("absent", 0)
        result   = "PASSED" if yeas > nays else "FAILED"
        chamber  = "House of Delegates" if vote.get("chamber", "") in ("H", "House") else "Senate"
        votes_by_member = vote.get("votes", [])  # list of {name, vote_id, vote_text}

        yea_names    = [v["name"] for v in votes_by_member if v.get("vote_text","").upper() in ("YEA","YES")]
        nay_names    = [v["name"] for v in votes_by_member if v.get("vote_text","").upper() in ("NAY","NO")]
        absent_names = [v["name"] for v in votes_by_member if v.get("vote_text","").upper() in ("ABSENT","NOT VOTING","NV")]

        lines = [
            f"Bill {bill_number} — Vote Record #{idx}",
            f"Date: {date} | Chamber: {chamber}",
            f"Description: {desc}",
            f"Result: {result} ({yeas}-Y {nays}-N{f' {nv}-NV' if nv else ''})",
        ]
        if yea_names:
            lines.append(f"Yeas ({len(yea_names)}): {', '.join(yea_names)}")
        if nay_names:
            lines.append(f"Nays ({len(nay_names)}): {', '.join(nay_names)}")
        if absent_names:
            lines.append(f"Not voting ({len(absent_names)}): {', '.join(absent_names)}")

        chunk_id = f"{bill_number}_{session_label}_vote_{idx}"
        chunks.append({
            "id":   chunk_id,
            "text": "\n".join(lines),
            "metadata": {
                "bill_id":    bill_number,
                "session":    session_label,
                "chunk_type": "vote_record",
                "vote_date":  date,
                "chamber":    chamber,
                "result":     result,
                "total_yeas": yeas,
                "total_nays": nays,
                "committee":  "",
            },
        })
    return chunks


# ── Embed & upsert ────────────────────────────────────────────────────────────

def embed_batch(texts: list[str], vo) -> list:
    for attempt in range(6):
        try:
            return vo.embed(texts, model=EMBED_MODEL, input_type="document").embeddings
        except Exception as e:
            if any(k in str(e).lower() for k in ("rate", "429", "limit", "quota")):
                wait = 20 + 20 * attempt
                print(f"  [rate limit] waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Embedding failed after retries")


def get_collection():
    import chromadb
    client = chromadb.HttpClient(
        ssl=True, host="api.trychroma.com",
        tenant=CHROMA_TENANT, database=CHROMA_DATABASE,
        headers={"x-chroma-token": CHROMA_API_KEY},
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def upsert_chunks(collection, chunks: list[dict], embeddings: list):
    for i in range(0, len(chunks), CHROMA_BATCH):
        bc = chunks[i: i + CHROMA_BATCH]
        be = embeddings[i: i + CHROMA_BATCH]
        collection.upsert(
            ids=[c["id"] for c in bc],
            documents=[c["text"] for c in bc],
            embeddings=be,
            metadatas=[c["metadata"] for c in bc],
        )
        print(f"  upserted {i + len(bc)}/{len(chunks)}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session",       help="Year to ingest (e.g. 2026)")
    parser.add_argument("--all",           action="store_true", help="Ingest 2022-2026")
    parser.add_argument("--list-sessions", action="store_true", help="Print VA LegiScan session IDs and exit")
    parser.add_argument("--dry-run",       action="store_true")
    args = parser.parse_args()

    if not LEGISCAN_API_KEY:
        print("ERROR: LEGISCAN_API_KEY not set in .env")
        print("Get a free key at https://legiscan.com/legiscan")
        return

    # Resolve session IDs
    print("Fetching VA session list from LegiScan...")
    session_map = get_va_sessions()
    print("Available VA regular sessions:", session_map)

    if args.list_sessions:
        return

    years = list(range(2022, 2027)) if args.all else ([int(args.session)] if args.session else [2026])

    import voyageai as _vo
    vo = _vo.Client(api_key=VOYAGE_API_KEY) if not args.dry_run else None
    collection = get_collection() if not args.dry_run else None

    total_chunks = 0

    for year in sorted(years, reverse=True):
        session_label = str(year)
        session_info  = session_map.get(session_label)
        if not session_info:
            print(f"\n-- {year}: no LegiScan session found, skipping --")
            continue

        session_id, access_key = session_info
        print(f"\n-- {year} (LegiScan session {session_id}) --")
        print(f"  Downloading dataset...")
        try:
            dataset = download_dataset(session_id, access_key)
        except Exception as e:
            print(f"  [skip] {e}")
            continue

        bills = dataset["bills"]
        print(f"  {len(bills)} bills in dataset")

        all_chunks = []
        bills_with_votes = 0
        for bill_num, bill in bills.items():
            # Always extract bill metadata
            bill_chunks = bill_to_chunks(bill_num, session_label, bill)
            all_chunks.extend(bill_chunks)

            # Extract vote records if available
            bill_votes = bill.get("votes", [])
            if bill_votes:
                vote_chunks = votes_to_chunks(bill_num, session_label, bill_votes)
                all_chunks.extend(vote_chunks)
                bills_with_votes += 1

        print(f"  {len(all_chunks)} total chunks ({len(bills)} bills with metadata, {bills_with_votes} with vote records)")

        if args.dry_run or not all_chunks:
            continue

        # Embed
        texts = [c["text"] for c in all_chunks]
        print(f"  Embedding {len(texts)} chunks in batches of {VOYAGE_BATCH}...")
        embeddings = []
        for i in range(0, len(texts), VOYAGE_BATCH):
            batch = texts[i: i + VOYAGE_BATCH]
            embeddings.extend(embed_batch(batch, vo))
            print(f"    embedded {i + len(batch)}/{len(texts)}")
            if i + VOYAGE_BATCH < len(texts):
                time.sleep(3)

        upsert_chunks(collection, all_chunks, embeddings)
        total_chunks += len(all_chunks)
        print(f"  OK upserted {len(all_chunks)} chunks for {year}")

    if not args.dry_run and collection:
        print(f"\n[done] Total chunks ingested: {total_chunks}")
        print(f"[done] Collection now has {collection.count()} total documents.")


if __name__ == "__main__":
    main()
