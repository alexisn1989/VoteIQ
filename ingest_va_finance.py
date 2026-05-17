#!/usr/bin/env python3
"""
Ingest Virginia state campaign finance resources from ELECT landing page.
Uses Gemini to structure the links and descriptions for the VoteIQ knowledge base.
"""
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
from pypdf import PdfReader
from urllib.parse import urljoin

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "polls.db")
USER_AGENT = "VoteIQ/1.0 (Virginia Election Research)"
GEMINI_MODEL = "gemini-2.5-flash"
MAX_PDF_COUNT = 8
MAX_PDF_BYTES = 6_000_000
MAX_TOTAL_PDF_BYTES = 18_000_000
MAX_PDF_TEXT_CHARS = 5000

FINANCE_URL = "https://www.elections.virginia.gov/candidatepac-info/campaign-finance/"

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
Focus ONLY on Virginia state-level offices — NOT federal (no U.S. House, no U.S. Senate).

Target offices:
- Governor, Lieutenant Governor, Attorney General (2025 winners)
- Virginia House of Delegates (all 100 districts, 2025 winners)
- Virginia State Senate (all 40 districts, 2025 winners)

Prefer official sources: vpap.org, elections.virginia.gov, lis.virginia.gov, official .gov bios.
Return ONLY valid JSON in this format:
{
  "people": [
    {
      "person_name": "Full Name",
      "office": "Governor / Lieutenant Governor / Attorney General / House of Delegates / State Senate",
      "district": "district number as string or null for statewide",
      "party": "Democratic / Republican / Independent / null",
      "role": "officeholder",
      "incumbent": true or false,
      "committee_name": "campaign committee name or null",
      "finance_url": "COMET or VPAP campaign finance URL or null",
      "source_url": "best source URL",
      "data_confidence": "high / medium / low"
    }
  ]
}

Only include people you are confident won their race. Use null for fields you cannot verify.
"""

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


def extract_pdf_text(pdf_bytes):
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception as exc:
        return f"[PDF parser could not open file: {exc}]"

    page_texts = []
    for page in reader.pages[:8]:
        try:
            page_texts.append(page.extract_text() or "")
        except Exception:
            page_texts.append("")

    text = "\n\n".join(part.strip() for part in page_texts if part.strip())
    return text[:MAX_PDF_TEXT_CHARS] if text else "[No extractable PDF text found.]"


def build_gemini_contents(prompt, links):
    contents = [prompt]
    total_pdf_bytes = 0
    attached = 0
    extracted_text = []

    for link in links:
        if not pdf_link(link) or attached >= MAX_PDF_COUNT:
            continue

        try:
            resp = requests.get(link["url"], headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"PDF fetch skipped for {link['title']}: {exc}")
            continue

        pdf_bytes = resp.content
        if len(pdf_bytes) > MAX_PDF_BYTES:
            print(f"PDF fetch skipped for {link['title']}: file is too large")
            continue
        if total_pdf_bytes + len(pdf_bytes) > MAX_TOTAL_PDF_BYTES:
            print("PDF attachment limit reached; skipping remaining PDFs.")
            break

        extracted_text.append(
            f"PDF text extracted locally: {link['title']}\n"
            f"URL: {link['url']}\n"
            f"{extract_pdf_text(pdf_bytes)}"
        )
        contents.append(f"Attached PDF: {link['title']}\nURL: {link['url']}")
        contents.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
        total_pdf_bytes += len(pdf_bytes)
        attached += 1

    if extracted_text:
        contents.insert(1, "\n\n".join(extracted_text))
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


def extract_people():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=api_key)
    people_by_key = {}

    for query in PEOPLE_QUERIES:
        print(f"Searching people: {query}")
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"{PEOPLE_PROMPT}\n\nQuery: {query}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        payload = parse_json_object(response.text)
        for person in payload.get("people", []):
            name = str(person.get("person_name") or "").strip()
            office = str(person.get("office") or "").strip()
            if not name or not office:
                continue
            person["finance_url"] = clean_url(person.get("finance_url"))
            person["source_url"] = clean_url(person.get("source_url"))
            person["data_confidence"] = clean_confidence(person.get("data_confidence"))
            key = normalize_person_key(person)
            people_by_key[key] = merge_people(people_by_key.get(key), person)

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
