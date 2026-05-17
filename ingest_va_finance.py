#!/usr/bin/env python3
"""
Ingest Virginia state campaign finance resources from ELECT landing page.
Uses Gemini to structure the links and descriptions for the VoteIQ knowledge base.
"""
import hashlib
import os
import json
import re
import sqlite3
import unicodedata
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv
from google import genai
from google.genai import types
from io import BytesIO
from pathlib import Path
import pdfplumber
from urllib.parse import urljoin

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
PDF_CACHE_DIR = BASE_DIR / ".pdf_cache"
USER_AGENT = "VoteIQ/1.0 (Virginia Election Research)"
GEMINI_MODEL = "gemini-2.5-flash"
MAX_PDF_COUNT = 8
MAX_PDF_BYTES = 6_000_000
MAX_TOTAL_PDF_BYTES = 18_000_000
MAX_PDF_TEXT_CHARS = 5000

FINANCE_URL = "https://www.elections.virginia.gov/candidatepac-info/campaign-finance/"
OPENSTATES_DB = BASE_DIR / "openstates_va.db"

# Hard allowlist — anything outside this is rejected before touching the DB
VALID_STATE_OFFICES = {
    "governor", "lieutenant governor", "attorney general",
    "house of delegates", "state senate",
}
# Substrings that indicate a federal office leaked through
FEDERAL_KEYWORDS = {
    "u.s. house", "u.s. senate", "congress", "representative",
    "house of representatives", "senate of the united states",
}

PEOPLE_QUERIES = [
    "Virginia 2025 election results Governor Lieutenant Governor Attorney General winner party Spanberger",
    "Virginia 2025 House of Delegates election results winners by district party Democrat Republican site:vpap.org OR site:elections.virginia.gov",
    "Virginia 2025 State Senate election results winners by district party Democrat Republican site:vpap.org OR site:elections.virginia.gov",
    "Virginia House of Delegates members 2025 current serving full list name district party",
    "Virginia State Senate members 2025 current serving full list name district party",
]

SYSTEM_PROMPT = """You are a Virginia campaign finance expert. 
Extract a structured list of resources from the provided website text.
Focus on:
1. Search tools (like COMET)
2. Filing schedules and deadlines
3. Disclosure reports (like Large Contributions)
4. Policy manuals and summaries of laws

Return ONLY valid JSON in this format:
[
  {"title": "Resource Name", "url": "full_url", "description": "1 sentence explanation", "category": "Search/Reporting/Legal"}
]

When a linked PDF is attached, read the PDF and use its actual contents for the description.
"""

PEOPLE_PROMPT = """Search the web for Virginia 2025 state election winners and current officeholders.
Focus ONLY on Virginia STATE-level offices. DO NOT include any federal offices.

Allowed offices (exact strings only):
  Governor | Lieutenant Governor | Attorney General | House of Delegates | State Senate

Rules:
- House of Delegates districts are 1-100 only. State Senate districts are 1-40 only.
- Only include people you are CERTAIN won their race. Omit anyone uncertain.
- Use null for any field you cannot verify from an authoritative source.
- Prefer vpap.org, elections.virginia.gov, lis.virginia.gov, official .gov bios.

Return JSON with this exact structure:
{
  "people": [
    {
      "person_name": "Full Name",
      "office": "House of Delegates",
      "district": "42",
      "party": "Democratic",
      "role": "officeholder",
      "incumbent": true,
      "committee_name": null,
      "finance_url": null,
      "source_url": "https://vpap.org/...",
      "data_confidence": "high"
    }
  ]
}
"""

# Gemini structured output schema — enforces valid office names and confidence values
PEOPLE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "people": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "person_name":     types.Schema(type=types.Type.STRING),
                    "office":          types.Schema(
                        type=types.Type.STRING,
                        enum=["Governor", "Lieutenant Governor", "Attorney General",
                              "House of Delegates", "State Senate"],
                    ),
                    "district":        types.Schema(type=types.Type.STRING, nullable=True),
                    "party":           types.Schema(
                        type=types.Type.STRING,
                        enum=["Democratic", "Republican", "Independent"],
                        nullable=True,
                    ),
                    "role":            types.Schema(type=types.Type.STRING),
                    "incumbent":       types.Schema(type=types.Type.BOOLEAN),
                    "committee_name":  types.Schema(type=types.Type.STRING, nullable=True),
                    "finance_url":     types.Schema(type=types.Type.STRING, nullable=True),
                    "source_url":      types.Schema(type=types.Type.STRING, nullable=True),
                    "data_confidence": types.Schema(
                        type=types.Type.STRING,
                        enum=["high", "medium", "low"],
                    ),
                },
                required=["person_name", "office", "data_confidence"],
            ),
        )
    },
)

def setup_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS va_finance_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            url TEXT UNIQUE,
            description TEXT,
            category TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS va_finance_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name TEXT NOT NULL,
            office TEXT,
            district TEXT,
            party TEXT,
            role TEXT,
            incumbent INTEGER DEFAULT 0,
            committee_name TEXT,
            finance_url TEXT,
            source_url TEXT,
            data_confidence TEXT,
            raw_json TEXT,
            fetched_at TEXT,
            UNIQUE(person_name, office, district, role)
        );
    """)
    conn.commit()
    conn.close()


def pdf_link(link):
    return link["url"].lower().split("?", 1)[0].endswith(".pdf")


def _fetch_pdf(url: str) -> bytes | None:
    PDF_CACHE_DIR.mkdir(exist_ok=True)
    cache_key = hashlib.md5(url.encode()).hexdigest()
    cache_path = PDF_CACHE_DIR / cache_key
    if cache_path.exists():
        print(f"  [cache] {url}")
        return cache_path.read_bytes()
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        cache_path.write_bytes(resp.content)
        return resp.content
    except requests.RequestException as exc:
        print(f"  PDF fetch failed: {exc}")
        return None


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract structured text from PDF using pdfplumber (tables first, then prose)."""
    parts = []
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:8]:
                # Try table extraction first — preserves rows/columns
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            cleaned = [str(cell or "").strip() for cell in row]
                            if any(cleaned):
                                parts.append(" | ".join(cleaned))
                else:
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(text.strip())
    except Exception as exc:
        return f"[pdfplumber error: {exc}]"

    full = "\n".join(parts)
    return full[:MAX_PDF_TEXT_CHARS] if full else ""


def build_gemini_contents(prompt, links):
    contents = [prompt]
    total_pdf_bytes = 0
    attached = 0
    extracted_text = []

    for link in links:
        if not pdf_link(link) or attached >= MAX_PDF_COUNT:
            continue

        pdf_bytes = _fetch_pdf(link["url"])
        if pdf_bytes is None:
            continue
        if len(pdf_bytes) > MAX_PDF_BYTES:
            print(f"  PDF too large, skipping: {link['title']}")
            continue
        if total_pdf_bytes + len(pdf_bytes) > MAX_TOTAL_PDF_BYTES:
            print("  PDF attachment total limit reached; stopping.")
            break

        local_text = extract_pdf_text(pdf_bytes)
        is_scanned = len(local_text.strip()) < 150

        if is_scanned:
            # Scanned/image PDF — skip garbled local text, let Gemini read natively
            print(f"  [scanned] {link['title']} — sending to Gemini natively")
            contents.append(f"Attached PDF (image-based): {link['title']}\nURL: {link['url']}")
            contents.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
        else:
            # Text-based PDF — provide structured local extraction + native attachment
            extracted_text.append(
                f"PDF: {link['title']}\nURL: {link['url']}\n{local_text}"
            )
            contents.append(f"Attached PDF: {link['title']}\nURL: {link['url']}")
            contents.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))

        total_pdf_bytes += len(pdf_bytes)
        attached += 1

    if extracted_text:
        contents.insert(1, "\n\n---\n\n".join(extracted_text))
    if attached:
        print(f"Extracted and attached {attached} PDF(s) for Gemini to read.")
    return contents


def scrape_and_extract():
    print(f"Fetching {FINANCE_URL}...")
    try:
        resp = requests.get(FINANCE_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Clean up text for Gemini
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        links = []
        for a in soup.find_all("a", href=True):
            label = a.get_text(" ", strip=True)
            href = urljoin(FINANCE_URL, a["href"])
            if label and href.startswith(("http://", "https://")):
                links.append({"title": label, "url": href})

        # Call Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        client = genai.Client(api_key=api_key)
        
        # Provide context about the URL to help Gemini resolve relative links
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Source URL: {FINANCE_URL}\n\n"
            f"Links found on page:\n{json.dumps(links, indent=2)}\n\n"
            f"Content:\n{text[:8000]}"
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=build_gemini_contents(prompt, links),
        )
        
        # Clean Gemini output
        raw_json = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw_json)
    except Exception as e:
        print(f"Extraction error: {e}")
        return []


def parse_json_object(raw):
    raw = raw.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def clean_url(url):
    if not url:
        return None
    match = re.search(r"https?://[^\s,\)]+", str(url))
    if not match:
        return None
    url = match.group(0).strip().rstrip(".,;")
    if not url or "vertexaisearch.cloud.google.com/grounding-api-redirect" in url:
        return None
    return url


def normalize_person_name(name):
    ascii_name = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z0-9 ]+", " ", ascii_name.lower()).split()
    tokens = [token for token in tokens if len(token) > 1]
    if len(tokens) > 2:
        return " ".join([tokens[0], tokens[-1]])
    return " ".join(tokens)


def clean_confidence(confidence):
    confidence = str(confidence or "").lower().strip()
    if "high" in confidence:
        return "high"
    if "medium" in confidence:
        return "medium"
    if "low" in confidence:
        return "low"
    return None


def normalize_person_key(person):
    return (
        normalize_person_name(person.get("person_name")),
        str(person.get("office") or "").strip().lower(),
        str(person.get("district") or "").strip().lower(),
    )


def role_rank(role):
    role = str(role or "").lower()
    if role == "candidate_and_officeholder":
        return 3
    if role == "candidate":
        return 2
    if role == "officeholder":
        return 1
    return 0


def confidence_rank(confidence):
    return {"high": 3, "medium": 2, "low": 1}.get(clean_confidence(confidence), 0)


def merge_people(existing, incoming):
    if not existing:
        return incoming

    merged = dict(existing)
    if role_rank(incoming.get("role")) > role_rank(merged.get("role")):
        merged["role"] = incoming.get("role")
    if incoming.get("incumbent"):
        merged["incumbent"] = True
    for key in ("person_name", "office", "district", "party", "committee_name", "finance_url", "source_url", "data_confidence"):
        value = incoming.get(key)
        if key in ("finance_url", "source_url"):
            value = clean_url(value)
        if value and (not merged.get(key) or confidence_rank(incoming.get("data_confidence")) > confidence_rank(merged.get("data_confidence"))):
            merged[key] = value
    return merged


def load_openstates_index() -> dict:
    """Return {normalized_name: (name, district, party, chamber)} from openstates_va.db."""
    if not OPENSTATES_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(OPENSTATES_DB)
        rows = conn.execute(
            "SELECT name, district, party, chamber FROM legislators "
            "WHERE chamber IN ('Delegate', 'Senator')"
        ).fetchall()
        conn.close()
        return {normalize_person_name(r[0]): r for r in rows}
    except Exception:
        return {}


def is_valid_state_person(person: dict) -> bool:
    """Hard filter: reject federal offices and impossible district numbers."""
    office = str(person.get("office") or "").strip().lower()
    # Block any federal keywords
    if any(kw in office for kw in FEDERAL_KEYWORDS):
        return False
    # Must be a known state office
    if office not in VALID_STATE_OFFICES:
        return False
    # Validate district ranges
    district = person.get("district")
    if district and district not in ("", "null", None):
        try:
            d = int(str(district).strip())
            if office == "house of delegates" and not (1 <= d <= 100):
                return False
            if office == "state senate" and not (1 <= d <= 40):
                return False
        except ValueError:
            pass
    return True


def _gemini_search_structured(client, query: str) -> list[dict]:
    """Two-step extraction: search with grounding, then structure with schema."""
    # Step 1 — grounded search for raw facts
    raw_response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            f"Search for Virginia 2025 state election results and current officeholders.\n"
            f"Query: {query}\n\n"
            f"Report all names, offices, districts, and party affiliations you find. "
            f"Be comprehensive. Do NOT include federal offices."
        ),
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        ),
    )
    raw_text = raw_response.text

    # Step 2 — structure raw text into validated schema (no tools = schema works)
    structured_response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=f"{PEOPLE_PROMPT}\n\nSource material to structure:\n{raw_text[:6000]}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PEOPLE_SCHEMA,
        ),
    )
    try:
        return json.loads(structured_response.text).get("people", [])
    except Exception:
        # Fallback: try to parse free-form JSON
        return parse_json_object(structured_response.text).get("people", [])


def extract_people():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=api_key)
    os_index = load_openstates_index()
    print(f"  Loaded {len(os_index)} OpenStates legislators for cross-check")

    people_by_key: dict = {}
    query_hits: dict = {}  # key -> number of queries that returned this person

    for query in PEOPLE_QUERIES:
        print(f"Searching people: {query}")
        try:
            people_this_query = _gemini_search_structured(client, query)
        except Exception as exc:
            print(f"  Query failed: {exc}")
            continue

        for person in people_this_query:
            name = str(person.get("person_name") or "").strip()
            office = str(person.get("office") or "").strip()
            if not name or not office:
                continue

            # Hard filter — reject before any merge
            if not is_valid_state_person(person):
                print(f"  [rejected] {name} / {office} / district={person.get('district')}")
                continue

            person["finance_url"] = clean_url(person.get("finance_url"))
            person["source_url"] = clean_url(person.get("source_url"))
            person["data_confidence"] = clean_confidence(person.get("data_confidence"))

            key = normalize_person_key(person)
            query_hits[key] = query_hits.get(key, 0) + 1
            people_by_key[key] = merge_people(people_by_key.get(key), person)

    # Cross-query consistency: single-query results get demoted
    for key, person in people_by_key.items():
        if query_hits.get(key, 0) == 1 and person.get("data_confidence") == "high":
            person["data_confidence"] = "medium"
            print(f"  [demoted] {person['person_name']} seen in only 1 query")

    # OpenStates cross-check: verify GA members against authoritative source
    verified = 0
    for key, person in people_by_key.items():
        office = str(person.get("office") or "").lower()
        if office not in ("house of delegates", "state senate"):
            continue
        os_key = normalize_person_name(person["person_name"])
        if os_key in os_index:
            _, os_district, os_party, _ = os_index[os_key]
            person["district"] = os_district
            person["party"] = os_party
            person["data_confidence"] = "high"
            verified += 1
        else:
            # GA member not in OpenStates — flag as unverified
            if person.get("data_confidence") == "high":
                person["data_confidence"] = "medium"

    print(f"  OpenStates verified {verified} GA members")
    print(f"  {len(people_by_key)} people passed all filters")
    return list(people_by_key.values())


def ingest_people(conn, now):
    try:
        people = extract_people()
    except Exception as exc:
        print(f"People extraction error: {exc}")
        return 0

    written = 0
    for person in people:
        try:
            conn.execute("""
                INSERT INTO va_finance_people (
                    person_name, office, district, party, role, incumbent,
                    committee_name, finance_url, source_url, data_confidence,
                    raw_json, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_name, office, district, role) DO UPDATE SET
                    party=excluded.party,
                    incumbent=excluded.incumbent,
                    committee_name=excluded.committee_name,
                    finance_url=excluded.finance_url,
                    source_url=excluded.source_url,
                    data_confidence=excluded.data_confidence,
                    raw_json=excluded.raw_json,
                    fetched_at=excluded.fetched_at
            """, (
                person.get("person_name"),
                person.get("office"),
                None if person.get("district") in ("", "null") else person.get("district"),
                person.get("party"),
                person.get("role"),
                1 if person.get("incumbent") else 0,
                person.get("committee_name"),
                clean_url(person.get("finance_url")),
                clean_url(person.get("source_url")),
                clean_confidence(person.get("data_confidence")),
                json.dumps(person),
                now,
            ))
            written += 1
        except Exception as exc:
            print(f"DB Error for person {person.get('person_name')}: {exc}")
    return written


def cleanup_people(conn):
    rows = conn.execute("""
        SELECT *
        FROM va_finance_people
        ORDER BY person_name, office, district, role
    """).fetchall()
    groups = {}
    for row in rows:
        item = dict(zip([col[0] for col in conn.execute("SELECT * FROM va_finance_people LIMIT 0").description], row))
        key = (
            normalize_person_name(item.get("person_name")),
            str(item.get("office") or "").strip().lower(),
            str(item.get("district") or "").strip().lower(),
        )
        groups.setdefault(key, []).append(item)

    removed = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(
            key=lambda item: (
                confidence_rank(item.get("data_confidence")),
                role_rank(item.get("role")),
                1 if clean_url(item.get("source_url")) else 0,
                1 if clean_url(item.get("finance_url")) else 0,
            ),
            reverse=True,
        )
        keep = group[0]
        merged = keep
        for item in group[1:]:
            merged = merge_people(merged, item)
            conn.execute("DELETE FROM va_finance_people WHERE id = ?", (item["id"],))
            removed += 1
        conn.execute("""
            UPDATE va_finance_people
            SET person_name = ?, party = ?, role = ?, incumbent = ?,
                committee_name = ?, finance_url = ?, source_url = ?,
                data_confidence = ?
            WHERE id = ?
        """, (
            merged.get("person_name"),
            merged.get("party"),
            merged.get("role"),
            1 if merged.get("incumbent") else 0,
            merged.get("committee_name"),
            clean_url(merged.get("finance_url")),
            clean_url(merged.get("source_url")),
            clean_confidence(merged.get("data_confidence")),
            keep["id"],
        ))

    conn.execute("""
        UPDATE va_finance_people
        SET source_url = NULL
        WHERE source_url NOT LIKE 'http%' OR source_url LIKE '%vertexaisearch.cloud.google.com/grounding-api-redirect%'
    """)
    conn.execute("""
        UPDATE va_finance_people
        SET finance_url = NULL
        WHERE finance_url NOT LIKE 'http%' OR finance_url LIKE '%vertexaisearch.cloud.google.com/grounding-api-redirect%'
    """)
    for row_id, source_url, finance_url, confidence in conn.execute("""
        SELECT id, source_url, finance_url, data_confidence
        FROM va_finance_people
    """).fetchall():
        conn.execute("""
            UPDATE va_finance_people
            SET source_url = ?, finance_url = ?, data_confidence = ?
            WHERE id = ?
        """, (
            clean_url(source_url),
            clean_url(finance_url),
            clean_confidence(confidence),
            row_id,
        ))
    return removed


def ingest():
    setup_db()
    data = scrape_and_extract()

    conn = sqlite3.connect(DB_PATH)
    written = 0
    now = datetime.now(timezone.utc).isoformat()
    
    for item in data or []:
        try:
            conn.execute("""
                INSERT INTO va_finance_info (title, url, description, category, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    category=excluded.category,
                    fetched_at=excluded.fetched_at
            """, (item['title'], item['url'], item['description'], item['category'], now))
            written += 1
        except Exception as e:
            print(f"DB Error for {item.get('title')}: {e}")

    people_written = ingest_people(conn, now)
    people_removed = cleanup_people(conn)
    conn.commit()
    conn.close()
    if not data:
        print("No finance resource data extracted.")
    print(f"Ingested {written} finance resources into polls.db")
    print(f"Ingested {people_written} finance people into polls.db")
    if people_removed:
        print(f"Cleaned up {people_removed} duplicate finance people rows")

if __name__ == "__main__":
    ingest()
