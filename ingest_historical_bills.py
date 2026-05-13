#!/usr/bin/env python3
"""
Ingest historical Virginia GA bills (1994–2021) filtered to only bills
where a currently-serving member is a patron.

This keeps the ChromaDB collection focused — we get the full legislative
career of every current member without loading irrelevant old bills.

Usage:
    python ingest_historical_bills.py              # all sessions 1994-2021
    python ingest_historical_bills.py --start 2010 # from 2010 backward
    python ingest_historical_bills.py --dry-run    # counts only, no embed
"""
import os, re, json, time, argparse
from html.parser import HTMLParser
from dotenv import load_dotenv

load_dotenv()

LIS_API_KEY     = os.getenv("LIS_API_KEY")
VOYAGE_API_KEY  = os.getenv("VOYAGE_API_KEY")
CHROMA_TENANT   = os.getenv("CHROMA_TENANT")
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE")
CHROMA_API_KEY  = os.getenv("CHROMA_API_KEY")

LIS_BASE        = "https://lis.virginia.gov"
COLLECTION_NAME = "voteiq_bills"
EMBED_MODEL     = "voyage-law-2"
VOYAGE_BATCH    = 16
CHROMA_BATCH    = 100

# ── Current member IDs (MemberNumber in LIS Patrons array) ───────────────────
# HOD: all 100 current delegates
HOD_IDS = {
    "H0219","H0375","H0239","H0208","H0406","H0269","H0370","H0344","H0294","H0317",
    "H0403","H0351","H0264","H0108","H0355","H0281","H0405","H0305","H0365","H0340",
    "H0382","H0297","H0404","H0227","H0343","H0385","H0380","H0301","H0374","H0395",
    "H0377","H0329","H0398","H0231","H0321","H0350","H0253","H0266","H0357","H0308",
    "H0393","H0333","H0224","H0242","H0056","H0390","H0348","H0384","H0401","H0136",
    "H0383","H0325","H0364","H0354","H0371","H0362","H0397","H0327","H0259","H0328",
    "H0247","H0394","H0342","H0388","H0314","H0389","H0369","H0238","H0392","H0323",
    "H0386","H0124","H0396","H0335","H0391","H0361","H0402","H0212","H0356","H0372",
    "H0207","H0399","H0347","H0336","H0284","H0400","H0173","H0322","H0387","H0262",
    "H0285","H0353","H0349","H0366","H0311","H0295","H0360","H0407","H0345","H0267",
}

# Senate: all 40 current senators
SENATE_IDS = {
    "S121","S68","S122","S101","S112","S111","S82","S105","S131","S132",
    "S62","S99","S115","S114","S134","S129","S116","S19","S118","S96",
    "S130","S113","S67","S119","S78","S69","S120","S88","S98","S126",
    "S125","S133","S117","S100","S80","S124","S127","S106","S135","S86",
}

# Former VA GA members now serving in US Congress or statewide office
# (Warner, Kaine, Spanberger never served in the VA General Assembly — no LIS records)
FORMER_MEMBERS = {
    "H0039",   # H. Morgan Griffith  (VA-09, US House) — VA House 1994–2011
    "H0161",   # Benjamin L. Cline   (VA-06, US House) — VA House 2002–2019
    "H0185",   # Jennifer McClellan  (VA-04, US House) — VA House 2006–2017
    "S0104",   # Jennifer McClellan  (VA-04, US House) — VA Senate 2017–2023
    "H0191",   # Robert J. Wittman   (VA-01, US House) — VA House 2006–2007
    "H0300",   # John J. McGuire III (VA-05, US House) — VA House 2020–2022
    "H0324",   # Suhas Subramanyam   (VA-10, US House) — VA House 2020–2024
    "S0109",   # Jennifer Kiggans    (VA-02, US House) — VA Senate 2020–2023
    "H0299",   # Jerrauld "Jay" Jones               — VA House 2018–2022
    "H0159",   # Winsome Earle Sears                 — VA House 2002–2004
}

CURRENT_MEMBERS = HOD_IDS | SENATE_IDS | FORMER_MEMBERS

# Sessions already fully ingested — skip them
ALREADY_DONE = {str(y) for y in range(2022, 2027)}

# All regular sessions LIS has data for (1994 is earliest available)
ALL_SESSIONS = [str(y) for y in range(1994, 2022)]  # 1994–2021


# ── HTML stripper ─────────────────────────────────────────────────────────────

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
    def handle_data(self, d):
        self._parts.append(d)
    def get_text(self):
        return " ".join(self._parts).strip()


def strip_html(html: str) -> str:
    if not html:
        return ""
    import html as _h
    text = _h.unescape(html)
    s = _HTMLStripper()
    s.feed(text)
    text = s.get_text()
    text = re.sub(r"\s+", " ", text).strip()
    try:
        return text.encode("latin-1").decode("utf-8")
    except Exception:
        return text


# ── LIS fetch ─────────────────────────────────────────────────────────────────

def fetch_session(session_code: str) -> list[dict]:
    import urllib.request
    url = f"{LIS_BASE}/AdvancedLegislationSearch/api/GetLegislationListAsync"
    body = json.dumps({"sessionCode": session_code}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "WebAPIKey": LIS_API_KEY, "User-Agent": "VoteIQ/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    bills = data.get("Legislations") if isinstance(data, dict) else data
    return bills or []


def is_current_member_bill(bill: dict) -> bool:
    for p in (bill.get("Patrons") or []):
        mn = p.get("MemberNumber") or ""
        if mn in CURRENT_MEMBERS:
            return True
    return False


# ── Chunk builder ─────────────────────────────────────────────────────────────

def bill_to_chunk(bill: dict, session_label: str) -> dict | None:
    num = bill.get("LegislationNumber", "")
    if not num:
        return None
    summary = strip_html(bill.get("LegislationSummary") or "")
    description = (bill.get("Description") or "").strip()
    try:
        description = description.encode("latin-1").decode("utf-8")
    except Exception:
        pass
    title = (bill.get("LegislationTitle") or "").strip()
    try:
        title = title.encode("latin-1").decode("utf-8")
    except Exception:
        pass
    status = (bill.get("LegislationStatus") or "").strip()
    if not summary and not description:
        return None

    patrons = bill.get("Patrons") or []
    chief = next((p["MemberDisplayName"] for p in patrons if p.get("Name") == "Chief Patron"), None)
    co_patrons = [p["MemberDisplayName"] for p in patrons if p.get("Name") != "Chief Patron"]

    lines = [f"Bill {num} ({session_label} Virginia Regular Session)"]
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

    return {
        "id":       f"{num}_{session_label}_summary_0",
        "text":     "\n".join(lines),
        "metadata": {
            "bill_id":    num,
            "session":    session_label,
            "chunk_type": "bill_summary",
            "subject":    description,
            "status":     status,
            "approved":   "",
        },
    }


# ── Embed & upsert ────────────────────────────────────────────────────────────

def embed_batch(texts, vo):
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


def ingest_chunks(collection, chunks, embeddings):
    for i in range(0, len(chunks), CHROMA_BATCH):
        bc = chunks[i: i + CHROMA_BATCH]
        be = embeddings[i: i + CHROMA_BATCH]
        collection.upsert(
            ids=[c["id"] for c in bc],
            documents=[c["text"] for c in bc],
            embeddings=be,
            metadatas=[c["metadata"] for c in bc],
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",   default="2021", help="Start year (works backwards, default 2021)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sessions = [y for y in ALL_SESSIONS if int(y) <= int(args.start)]
    sessions.sort(reverse=True)  # newest first

    total_ingested = 0

    import voyageai as _vo
    vo = _vo.Client(api_key=VOYAGE_API_KEY) if not args.dry_run else None
    collection = get_collection() if not args.dry_run else None

    for year in sessions:
        session_code = f"{year}1"
        print(f"\n-- {year} (session {session_code}) --")
        try:
            all_bills = fetch_session(session_code)
        except Exception as e:
            print(f"  [skip] {e}")
            continue

        # Deduplicate
        seen, deduped = set(), []
        for b in all_bills:
            bid = b.get("LegislationID")
            if bid not in seen:
                seen.add(bid)
                deduped.append(b)

        # Filter to tracked members only
        filtered = [b for b in deduped if is_current_member_bill(b)]
        print(f"  {len(deduped)} total bills -> {len(filtered)} involve tracked members")
        if not filtered:
            continue

        chunks = [c for b in filtered if (c := bill_to_chunk(b, year))]
        print(f"  {len(chunks)} chunks to ingest")

        if args.dry_run:
            continue

        # Embed
        texts = [c["text"] for c in chunks]
        embeddings = []
        for i in range(0, len(texts), VOYAGE_BATCH):
            batch = texts[i: i + VOYAGE_BATCH]
            embeddings.extend(embed_batch(batch, vo))
            print(f"  embedded {i + len(batch)}/{len(texts)}")
            if i + VOYAGE_BATCH < len(texts):
                time.sleep(3)

        # Upsert
        ingest_chunks(collection, chunks, embeddings)
        total_ingested += len(chunks)
        print(f"  OK upserted {len(chunks)} chunks")

    if not args.dry_run and collection:
        print(f"\n[done] Total ingested this run: {total_ingested}")
        print(f"[done] Collection now has {collection.count()} total documents.")


if __name__ == "__main__":
    main()
