import json
import argparse
import os
import time
from pathlib import Path
import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
import voyageai
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

COLLECTION_NAME = "voteiq_bills"
EMBED_MODEL     = "voyage-law-2"
TARGET_TOKENS   = 250
OVERLAP_TOKENS  = 40
TARGET_DETAIL_CHUNKS = 250
MAX_DETAIL_CHUNKS = 300

vo = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))

# ── Voyage AI embedding function ──────────────────────────────────────────────

class VoyageEmbeddingFunction(EmbeddingFunction):
    def name(self) -> str:
        return "voyageai"

    def __call__(self, input: Documents) -> Embeddings:
        result = vo.embed(list(input), model=EMBED_MODEL, input_type="document")
        return result.embeddings

# ── ChromaDB setup ────────────────────────────────────────────────────────────

def get_collection(reset: bool = False):
    client = chromadb.HttpClient(
        ssl=True,
        host="api.trychroma.com",
        tenant=os.getenv("CHROMA_TENANT"),
        database=os.getenv("CHROMA_DATABASE"),
        headers={"x-chroma-token": os.getenv("CHROMA_API_KEY")},
    )

    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"[reset] Deleted existing collection '{COLLECTION_NAME}'")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=VoyageEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"}
    )
    return collection

# ── Chunking helpers ──────────────────────────────────────────────────────────

def _count_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)

def _split_text(text: str, base_id: str, base_meta: dict) -> list[dict]:
    """Split text into ~TARGET_TOKENS chunks with OVERLAP_TOKENS overlap at line boundaries."""
    lines = [l for l in text.split("\n") if l.strip()]
    result, current, cur_tokens, idx = [], [], 0, 0

    for line in lines:
        lt = _count_tokens(line)
        if cur_tokens + lt > TARGET_TOKENS and current:
            result.append({
                "id":       f"{base_id}_{idx}",
                "text":     "\n".join(current),
                "metadata": {**base_meta, "chunk_index": idx},
            })
            idx += 1
            overlap, ov_tokens = [], 0
            for l in reversed(current):
                t = _count_tokens(l)
                if ov_tokens + t > OVERLAP_TOKENS:
                    break
                overlap.insert(0, l)
                ov_tokens += t
            current, cur_tokens = overlap, ov_tokens
        current.append(line)
        cur_tokens += lt

    if current:
        result.append({
            "id":       f"{base_id}_{idx}",
            "text":     "\n".join(current),
            "metadata": {**base_meta, "chunk_index": idx},
        })
    return result or [{"id": f"{base_id}_0", "text": text, "metadata": {**base_meta, "chunk_index": 0}}]

# ── Chunking ──────────────────────────────────────────────────────────────────

def build_chunks(data: dict) -> list[dict]:
    bill  = data["bill"]
    votes = data["votes"]

    bill_id = bill.get("house_bill_number") or bill.get("bill_number")
    session = bill["session"]
    chunks  = []

    subject  = bill.get("subject", "")
    approved = bill.get("approved", "")
    patrons  = bill.get("all_patrons", [])
    patron_line = (
        f"Co-patrons ({bill['patron_count']} total): {', '.join(patrons)}\n"
        if patrons else ""
    )
    dates_line = " | ".join(filter(None, [
        f"Prefiled: {bill['prefiled']}"  if bill.get("prefiled")  else "",
        f"Offered: {bill['offered']}"    if bill.get("offered")   else "",
        f"Approved: {approved}"          if approved              else "",
    ]))

    # ── Bill Summary ──────────────────────────────────────────────────────────
    summary_text = (
        f"Bill {bill_id} ({session} Virginia Regular Session)\n"
        f"Title: {bill['title']}\n"
        f"Short title: {bill.get('bill_title_short') or bill.get('short_title', '')}\n"
        + (f"Subject: {subject}\n" if subject else "")
        + (f"Primary patron: {bill['primary_patron']}\n" if bill.get("primary_patron") else "")
        + patron_line
        + (f"Companion bill: {bill['companion_bill']}\n" if bill.get("companion_bill") else "")
        + (f"{dates_line}\n" if dates_line else "")
        + (f"Summary: {bill['summary']}" if bill.get("summary") else "")
    )
    chunks.extend(_split_text(
        summary_text,
        f"{bill_id}_{session}_summary",
        {"bill_id": bill_id, "session": session, "chunk_type": "bill_summary",
         "subject": subject, "status": "approved", "approved": approved},
    ))

    # ── Schedule (wage or rate) ───────────────────────────────────────────────
    if bill.get("schedule"):
        schedule_lines = "\n".join(
            f"  From {s['from']} to {s.get('until', 'ongoing')}: ${s['min_wage']}/hr"
            for s in bill["schedule"]
        )
        schedule_text = (
            f"Bill {bill_id} — Rate / Wage Schedule\n"
            f"{schedule_lines}"
        )
        chunks.extend(_split_text(
            schedule_text,
            f"{bill_id}_{session}_schedule",
            {"bill_id": bill_id, "session": session, "chunk_type": "schedule",
             "subject": bill["subject"]},
        ))

    # ── Fiscal Impact ─────────────────────────────────────────────────────────
    fi = bill.get("fiscal_impact")
    if fi:
        fy_totals = fi.get("general_fund_expenditures", {}).get("TOTAL", {})
        fy_lines  = "\n".join(
            f"  {fy}: ${fy_totals[fy]:,}"
            for fy in sorted(k for k in fy_totals if k.startswith("FY"))
        )
        fiscal_text = (
            f"Bill {bill_id} — Fiscal Impact Summary\n"
            f"{fi['summary']}\n"
            f"Impacted agencies: {', '.join(fi['impacted_agencies'])}\n"
            f"Budget amendment required: {fi['budget_amendment_necessary']}\n"
            f"General fund totals by fiscal year:\n"
            f"{fy_lines}"
        )
        chunks.extend(_split_text(
            fiscal_text,
            f"{bill_id}_{session}_fiscal",
            {"bill_id": bill_id, "session": session, "chunk_type": "fiscal_impact",
             "subject": bill["subject"]},
        ))

        dmas = fi.get("dmas_analysis")
        if dmas:
            fy2028  = dmas.get("statewide_cost_estimates", {}).get("FY2028", {})
            dmas_text = (
                f"Bill {bill_id} — DMAS (Medicaid) Fiscal Impact\n"
                f"Agency: {dmas['agency']}\n"
                f"Primary impact: {dmas['primary_impact']}\n"
                f"Current attendant wage (outside NoVA): "
                f"${dmas['current_attendant_wages']['outside_northern_virginia_per_hour']}/hr\n"
                f"Impact begins: {dmas['impact_begins']}\n"
                f"FY2028 cost: ${fy2028.get('total', 0):,} total, "
                f"${fy2028.get('general_fund', 0):,} general fund\n"
                f"Additional pressures: {', '.join(dmas['additional_pressure_noted'])}"
            )
            chunks.extend(_split_text(
                dmas_text,
                f"{bill_id}_{session}_fiscal_dmas",
                {"bill_id": bill_id, "session": session, "chunk_type": "fiscal_agency", "agency": "DMAS"},
            ))

    # ── Vote Records ──────────────────────────────────────────────────────────
    for vote in votes["vote_records"]:
        vid = vote["vote_id"]
        chamber_label = vote["chamber"]
        if vote.get("committee"):
            chamber_label += f" — {vote['committee']}"
        if vote.get("subcommittee"):
            chamber_label += f" / {vote['subcommittee']}"

        errata_note = ""
        if vote.get("errata"):
            errata_note = "\nErrata: " + "; ".join(
                f"{e['member']} recorded as {e['recorded']}, intended {e['intended']}"
                for e in vote["errata"]
            )

        vote_text = (
            f"Bill {bill_id} — Vote Record #{vid}\n"
            f"Date: {vote['date']} | Chamber: {chamber_label}\n"
            f"Description: {vote['description']}\n"
            f"Result: {vote['result']} ({vote['total_yeas']}-Y {vote['total_nays']}-N)\n"
            f"Yeas ({vote['total_yeas']}): {', '.join(vote['yeas'])}\n"
            f"Nays ({vote['total_nays']}): {', '.join(vote['nays']) if vote['nays'] else 'None'}"
            f"{errata_note}"
        )
        chunks.extend(_split_text(
            vote_text,
            f"{bill_id}_{session}_vote_{vid}",
            {
                "bill_id":    bill_id,
                "session":    session,
                "chunk_type": "vote_record",
                "vote_id":    vid,
                "chamber":    vote["chamber"],
                "committee":  vote.get("committee") or "",
                "vote_date":  vote["date"] or "",
                "result":     vote["result"],
                "total_yeas": vote["total_yeas"],
                "total_nays": vote["total_nays"],
            },
        ))

    # ── Statutory text (rag_chunks from bill JSON) ────────────────────────────
    for rc in data.get("rag_chunks", []):
        rc_text = f"Bill {bill_id} — {rc['section']}\n{rc['content']}"
        chunks.extend(_split_text(
            rc_text,
            f"{bill_id}_{session}_sec_{rc['chunk_id']}",
            {"bill_id": bill_id, "session": session, "chunk_type": "statutory_text",
             "subject": subject, "section": rc["section"]},
        ))

    return chunks

# ── Ingest ────────────────────────────────────────────────────────────────────

def build_election_chunks(data: dict) -> list[dict]:
    meta = data["meta"]
    doc_id = "election_summary_va"
    chunks = []
    for rc in data.get("rag_chunks", []):
        rc_text = f"Virginia Election Results — {rc['section']}\n{rc['content']}"
        chunks.extend(_split_text(
            rc_text,
            f"{doc_id}_sec_{rc['chunk_id']}",
            {"bill_id": doc_id, "session": "2016-2025", "chunk_type": "election_results",
             "subject": "Virginia Elections", "section": rc["section"]},
        ))
    return chunks


def _normalize_metadata(meta: dict) -> dict:
    """Chroma metadata values must be scalar; keep useful list/dict data searchable."""
    normalized = {}
    for key, value in meta.items():
        if value is None:
            normalized[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            normalized[key] = value
        elif isinstance(value, list):
            normalized[key] = ", ".join(str(v) for v in value)
        else:
            normalized[key] = json.dumps(value, ensure_ascii=False)
    return normalized


def ingest_jsonl(filepath: str, reset: bool = False):
    path = Path(filepath)
    if not path.exists():
        print(f"[error] File not found: {filepath}")
        return

    rows = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[warn] Skipping invalid JSONL line {line_no}: {e}")
                continue
            if row.get("chunk_id") and row.get("text"):
                rows.append(row)

    if not rows:
        print(f"[error] No JSONL rows with chunk_id/text found in {filepath}")
        return

    print(f"[ingest] Processing JSONL {filepath}...")
    sizes = [_count_tokens(r["text"]) for r in rows]
    print(f"[ingest] Built {len(rows)} chunks  "
          f"(min {min(sizes)} / avg {sum(sizes)//len(sizes)} / max {max(sizes)} tokens)")

    ids = [r["chunk_id"] for r in rows]
    documents = [r["text"] for r in rows]
    metadatas = []
    for r in rows:
        meta = {
            "bill_id": r.get("bill_id", ""),
            "session_id": r.get("session_id", ""),
            "state": r.get("state", ""),
            "year": r.get("year", ""),
            "chunk_index": r.get("chunk_index", 0),
            "chunk_type": r.get("chunk_type", ""),
            "legiscan_bill_id": r.get("legiscan_bill_id", ""),
        }
        meta.update(r.get("metadata") or {})
        metadatas.append(_normalize_metadata(meta))

    collection = get_collection(reset=reset)
    batch_size = 200
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i:i+batch_size],
            documents=documents[i:i+batch_size],
            metadatas=metadatas[i:i+batch_size],
        )
        print(f"[ingest] Upserted {min(i+batch_size, len(ids))}/{len(ids)}")
        if i + batch_size < len(ids):
            time.sleep(1)

    print(f"[ingest] OK Upserted {len(rows)} chunks into '{COLLECTION_NAME}'")
    print(f"[ingest] Collection now has {collection.count()} total documents")


def ingest(filepath: str, reset: bool = False):
    path = Path(filepath)
    if not path.exists():
        print(f"[error] File not found: {filepath}")
        return

    if path.suffix.lower() == ".jsonl":
        ingest_jsonl(filepath, reset=reset)
        return

    with open(path) as f:
        data = json.load(f)

    if data.get("meta", {}).get("type") == "election_results_summary":
        doc_id = "election_summary_va"
        print(f"[ingest] Processing election results summary...")
        chunks = build_election_chunks(data)
    else:
        bill_id = data["bill"].get("house_bill_number") or data["bill"].get("bill_number")
        session = data["bill"]["session"]
        print(f"[ingest] Processing {bill_id} ({session})...")
        chunks = build_chunks(data)
    sizes  = [_count_tokens(c["text"]) for c in chunks]
    print(f"[ingest] Built {len(chunks)} chunks  "
          f"(min {min(sizes)} / avg {sum(sizes)//len(sizes)} / max {max(sizes)} tokens)")

    collection = get_collection(reset=reset)
    collection.upsert(
        ids=       [c["id"]       for c in chunks],
        documents= [c["text"]     for c in chunks],
        metadatas= [c["metadata"] for c in chunks],
    )

    print(f"[ingest] OK Upserted {len(chunks)} chunks into '{COLLECTION_NAME}'")
    print(f"[ingest] Collection now has {collection.count()} total documents")

# ── Query helper (for testing) ────────────────────────────────────────────────

def test_query(query: str, n: int = 3, filters: dict = None):
    """Quick sanity check — run a semantic query against the collection."""
    collection = get_collection()
    for attempt in range(4):
        try:
            query_embedding = vo.embed([query], model=EMBED_MODEL, input_type="query").embeddings[0]
            break
        except voyageai.error.RateLimitError:
            wait = 22 * (attempt + 1)
            print(f"[rate limit] waiting {wait}s before retrying...")
            time.sleep(wait)
    else:
        print(f"[skip] rate limit exceeded for query: {query!r}")
        return

    kwargs = {"query_embeddings": [query_embedding], "n_results": n}
    if filters:
        kwargs["where"] = filters
    results = collection.query(**kwargs)

    print(f"\n[query] '{query}'")
    for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
        session_label = meta.get("session") or meta.get("session_name") or meta.get("session_id") or ""
        print(f"\n  Result {i+1} [{meta['chunk_type']}] — {meta['bill_id']} {session_label}")
        print(f"  {doc[:200]}...")

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoteIQ bill ingest")
    parser.add_argument("--file",  required=True, help="Path to bill JSON file")
    parser.add_argument("--reset", action="store_true", help="Wipe collection before ingest")
    parser.add_argument("--test",  action="store_true", help="Run test queries after ingest")
    args = parser.parse_args()

    ingest(args.file, reset=args.reset)

    if args.test:
        test_query("what is the minimum wage schedule")
        test_query("how did Senator Obenshain vote")
        test_query("fiscal impact on Medicaid")
        test_query("who sponsored HB1")
