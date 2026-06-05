"""
scrape_vec_disclosures.py

Scrapes Virginia Statement of Economic Interests (SOEI / Form 801) for
all active VA General Assembly members from:
  https://ethicssearch.dls.virginia.gov

Access pattern (no CAPTCHA required programmatically):
  1. GET / to obtain ASP.NET_SessionId cookie
  2. GET /Search.aspx to get __VIEWSTATE
  3. POST /Search.aspx with legislator first/last name + year
  4. Extract filingids from results HTML
  5. GET /ViewFormBinary.aspx?filingid=N&type=SOEI → HTML disclosure
  6. Parse HTML to extract employer/business/real estate/securities sections

Extracted fields per legislator:
  - Schedule A: Outside employment (employer name, compensation level)
  - Schedule B: Business officer/director positions
  - Schedule D: Real estate interests (location, type)
  - Schedule E: Securities (company, type)

Output: polls.db → legislator_financial_disclosures

Usage:
    python scrape_vec_disclosures.py --year 2024
    python scrape_vec_disclosures.py --year 2024 --name "Emily Jordan"
    python scrape_vec_disclosures.py --year 2024 --dry-run
"""
from __future__ import annotations

import argparse
import http.cookiejar
import io
import json
import re
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    _GEMINI_CLIENT = None  # initialized lazily
except ImportError:
    _genai = None
    _genai_types = None
    _GEMINI_CLIENT = None

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"
BASE_URL = "https://ethicssearch.dls.virginia.gov"

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS legislator_financial_disclosures (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    legislator_name  TEXT NOT NULL,
    filing_year      TEXT,
    filing_id        TEXT,
    part             TEXT,   -- 'employment'|'business_officer'|'real_estate'|'securities'
    entity_name      TEXT,
    role_or_type     TEXT,
    sector           TEXT,
    amount_range     TEXT,
    raw_text         TEXT,
    source           TEXT DEFAULT 'vec_soei',
    scraped_at       TEXT,
    UNIQUE(legislator_name, filing_year, part, entity_name)
);
CREATE INDEX IF NOT EXISTS idx_lfd_name   ON legislator_financial_disclosures(legislator_name);
CREATE INDEX IF NOT EXISTS idx_lfd_sector ON legislator_financial_disclosures(sector);
CREATE INDEX IF NOT EXISTS idx_lfd_year   ON legislator_financial_disclosures(filing_year);
"""

BUSINESS_SECTOR_KWS: dict[str, list[str]] = {
    "Energy/Utilities": ["energy", "electric", "utility", "solar", "gas", "oil",
                         "coal", "pipeline", "dominion", "appalachian", "nuclear"],
    "Healthcare":       ["health", "hospital", "medical", "physician", "dental",
                         "pharma", "clinic", "nursing"],
    "Finance":          ["bank", "financ", "insurance", "mortgage", "credit",
                         "investment", "capital", "securities", "wealth"],
    "Real Estate":      ["real estate", "realty", "property", "construction",
                         "developer", "apartment", "land", "homebuilder"],
    "Agriculture":      ["farm", "agri", "livestock", "crop", "timber"],
    "Transportation":   ["transport", "transit", "highway", "vehicle", "trucking",
                         "aviation", "airline"],
    "Tech/Data":        ["technology", "software", "data", "cyber", "digital",
                         "computer", "cloud"],
    "Legal":            ["law firm", "attorney", "legal", "counsel"],
    "Retail/Hospitality": ["retail", "restaurant", "hotel", "casino", "hospitality"],
}


def classify_sector(text: str) -> str | None:
    t = (text or "").lower()
    for sector, kws in BUSINESS_SECTOR_KWS.items():
        if any(kw in t for kw in kws):
            return sector
    return None


# ── HTTP session ──────────────────────────────────────────────────────────────

def _make_opener():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cj),
    )
    return opener, cj


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(opener, url: str, referer: str = BASE_URL) -> str:
    req = urllib.request.Request(url, headers={**_HEADERS, "Referer": referer})
    with opener.open(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _post(opener, url: str, data: dict, referer: str) -> str:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, headers={
        **_HEADERS,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": referer,
    })
    with opener.open(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _init_session(opener) -> str:
    """Get session cookie + __VIEWSTATE from Search.aspx."""
    opener.open(urllib.request.Request(f"{BASE_URL}/", headers=_HEADERS), timeout=10).read()
    html = _get(opener, f"{BASE_URL}/Search.aspx", referer=f"{BASE_URL}/")
    vs  = re.search(r"name=[\"']__VIEWSTATE[\"'][^>]*value=[\"']([^\"']+)", html).group(1)
    vsg = re.search(r"name=[\"']__VIEWSTATEGENERATOR[\"'][^>]*value=[\"']([^\"']+)", html).group(1)
    return vs, vsg


# ── Search for a legislator ───────────────────────────────────────────────────

def search_legislator(
    opener, vs: str, vsg: str, first: str, last: str, year: str
) -> list[dict]:
    """Return list of {filing_id, type} for a legislator."""
    data = {
        "__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg,
        "__SCROLLPOSITIONX": "0", "__SCROLLPOSITIONY": "0",
        "__EVENTTARGET": "", "__EVENTARGUMENT": "",
        "hdnSelectedTab": "1",
        "query": "", "txtLobbyistName": "", "txtPrincipalName": "",
        "txtPrincipalOfficerName": "",
        "txtFirstName": first, "txtLastName": last,
        "txtAgency": "", "lstYearCOI": year,
        "cmdSearchCOI": "Search",
    }
    html = _post(opener, f"{BASE_URL}/Search.aspx", data, referer=f"{BASE_URL}/Search.aspx")

    filings = []
    # Extract all filingid links
    for m in re.finditer(
        r'href=["\']ViewFormBinary\.aspx\?filingid=(\d+)&contentType=[^"\']+&type=(\w+)["\']',
        html, re.I
    ):
        filings.append({"filing_id": m.group(1), "type": m.group(2)})
    return filings


# ── Download and parse a disclosure ──────────────────────────────────────────

def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _extract_yes_no_table(section_text: str, after_yes: bool = True) -> list[str]:
    """Return rows from a YES/NO table section."""
    yes_match = re.search(r"Yes\s*\[X\]", section_text, re.I)
    no_match  = re.search(r"No\s*\[X\]",  section_text, re.I)
    if no_match and (not yes_match or no_match.start() < yes_match.start()):
        return []  # answered No
    if not yes_match:
        return []
    # Text after the Yes answer — look for the table content
    after = section_text[yes_match.end():]
    # Split by common delimiters; filter empty
    rows = [r.strip() for r in re.split(r"\s{3,}|\t{2,}|\n{2,}", after) if r.strip()]
    return rows[:20]


_SECTION_PATTERNS = {
    # (part_key, start_anchor, end_anchor_patterns)
    "employment": (
        r"SCHEDULE\s*A",
        [r"SCHEDULE\s*B", r"PART\s*I\s*\-?\s*", r"Do you receive"],
    ),
    "business_officer": (
        r"SCHEDULE\s*B",
        [r"SCHEDULE\s*C", r"SCHEDULE\s*D", r"Did you or"],
    ),
    "real_estate": (
        r"SCHEDULE\s*D",
        [r"SCHEDULE\s*E", r"SCHEDULE\s*F", r"Did you"],
    ),
    "securities": (
        r"SCHEDULE\s*E",
        [r"SCHEDULE\s*F", r"PART\s*II", r"Did you"],
    ),
}


_GEMINI_PROMPT = """Extract financial disclosures from this Virginia SOEI (Statement of Economic Interests) Form 801.
Return ONLY valid JSON with this structure:
{
  "legislator_name": "...",
  "employment": [{"employer": "...", "role": "...", "compensation": "..."}],
  "business_interests": [{"entity": "...", "role": "...", "ownership_pct": "...", "compensation": "..."}],
  "real_estate": [{"description": "...", "location": "...", "type": "..."}],
  "securities": [{"entity": "...", "type": "...", "value": "..."}]
}
Only include items actually disclosed (answered Yes). Use [] if nothing disclosed. No instruction text.

DISCLOSURE TEXT:
"""


def _gemini_extract(html: str) -> dict | None:
    """Send disclosure HTML to Gemini 2.5 Flash for structured extraction."""
    global _GEMINI_CLIENT
    if _genai is None:
        return None

    import os
    from dotenv import load_dotenv
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return None

    if _GEMINI_CLIENT is None:
        _GEMINI_CLIENT = _genai.Client(api_key=key)

    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

    try:
        resp = _GEMINI_CLIENT.models.generate_content(
            model="gemini-2.5-flash",
            contents=_GEMINI_PROMPT + text[:10000],
            config=_genai_types.GenerateContentConfig(response_mime_type="application/json"),
        )
        import json as _json
        return _json.loads(resp.text)
    except Exception as e:
        print(f"    Gemini error: {e}")
        return None


def _gemini_to_records(data: dict, legislator: str, filing_id: str, year: str, now: str) -> list[dict]:
    """Convert Gemini JSON output to DB records."""
    records = []
    reported_name = data.get("legislator_name") or legislator

    part_map = {
        "employment":        ("employment",       "employer"),
        "business_interests":("business_officer", "entity"),
        "real_estate":       ("real_estate",      "description"),
        "securities":        ("securities",       "entity"),
    }

    for key, (part, entity_field) in part_map.items():
        items = data.get(key, [])
        if not items:
            records.append({
                "legislator_name": reported_name, "filing_year": year,
                "filing_id": filing_id, "part": part,
                "entity_name": "NONE DISCLOSED", "role_or_type": "",
                "sector": None, "amount_range": "", "raw_text": "",
                "scraped_at": now,
            })
            continue
        for item in items:
            entity = (item.get(entity_field) or item.get("employer") or
                      item.get("entity") or item.get("description") or "")
            if not entity or entity.lower() in ("none", "n/a", ""):
                continue
            role = (item.get("role") or item.get("type") or "")
            amount = (item.get("compensation") or item.get("value") or
                      item.get("ownership_pct") or "")
            sector = classify_sector(entity + " " + role)
            records.append({
                "legislator_name": reported_name, "filing_year": year,
                "filing_id": filing_id, "part": part,
                "entity_name": str(entity)[:120],
                "role_or_type": str(role)[:80],
                "sector": sector,
                "amount_range": str(amount)[:60],
                "raw_text": str(item)[:300],
                "scraped_at": now,
            })

    return records


def parse_disclosure(html: str, legislator: str, filing_id: str, year: str) -> list[dict]:
    """Parse VEC SOEI HTML disclosure (server renders PDF as HTML) into structured records."""
    text = _strip_tags(html)
    now  = datetime.now(timezone.utc).isoformat()
    records = []

    # Extract name from disclosure
    name_m = re.search(r"NAME:\s*([A-Za-z\s\.,'-]{5,50})\s+(?:OFFICE|POSITION)", text, re.I)
    if name_m:
        reported_name = name_m.group(1).strip()
    else:
        reported_name = legislator

    for part, (start_re, end_res) in _SECTION_PATTERNS.items():
        start = re.search(start_re, text, re.I)
        if not start:
            continue
        section_start = start.start()

        # Find end of section
        section_end = len(text)
        for end_re in end_res:
            end_m = re.search(end_re, text[section_start + 20:], re.I)
            if end_m:
                section_end = min(section_end, section_start + 20 + end_m.start())

        section_text = text[section_start:section_end]

        # Check if answered Yes or No
        yes_m = re.search(r"Yes\s*\[?\s*X\s*\]", section_text, re.I)
        no_m  = re.search(r"No\s*\[?\s*X\s*\]",  section_text, re.I)

        # If No is answered (or No comes before Yes), skip this section
        if no_m and (not yes_m or no_m.start() < yes_m.start()):
            records.append({
                "legislator_name": reported_name, "filing_year": year,
                "filing_id": filing_id, "part": part,
                "entity_name": "NONE DISCLOSED", "role_or_type": "",
                "sector": None, "amount_range": "", "raw_text": "",
                "scraped_at": now,
            })
            continue

        if not yes_m:
            continue

        # Extract after Yes answer — look for actual table rows, not instructions
        after_yes = section_text[yes_m.end():]

        # Extract entity names: Look for capitalized phrases that aren't instructions
        # Pattern: Company name patterns (has & | LLC | Inc | Corp | Co. | Company, Bank, Fund, etc.)
        entity_patterns = [
            r"([A-Z][A-Za-z0-9&\s,\.''-]{8,}?(?:LLC|Inc|Corp|Co\.|Company|Bank|Group|Fund|Associates|Partners|Authority|Services|Solutions))\b",
            r"([A-Z][A-Z][A-Za-z0-9&\s,\.'-]{6,})\s+(?:Inc|LLC|Corp|Co|Ltd|LLP)",  # Multi-word entity
        ]

        entities = []
        for pattern in entity_patterns:
            entities.extend(re.findall(pattern, after_yes))

        # Filter out instruction/noise text and column headers
        SKIP = {
            "schedule", "complete", "instructions", "disclose", "virginia",
            "general assembly", "board", "commission", "statement", "exclude",
            "include", "table", "form", "office", "position", "member",
            "family", "received", "salary", "compensation", "wages",
            "do you", "if yes", "if no", "has a", "owns", "holds",
            "gross income", "entity name", "nature of", "percentage",
            "type of", "location", "address", "amount", "value",
        }

        clean_entities = []
        for e in entities:
            e = e.strip()
            e_lower = e.lower()

            # Strip column header prefixes like "GROSS INCOME", "ENTITY NAME", etc.
            for header in ["gross income", "entity name", "nature of", "percentage", "type of", "location", "address"]:
                if e_lower.startswith(header):
                    e = e[len(header):].strip()
                    e_lower = e.lower()
                    break

            # Skip if it's just noise/instructions, but NOT if it looks like a company name
            is_noise = any(skip in e_lower for skip in {"do you", "if yes", "if no", "has a", "owns", "holds",
                          "complete", "disclose", "instructions", "table", "form"})

            if is_noise:
                continue
            if len(e) > 4 and not e.isupper():
                clean_entities.append(e)

        # Remove duplicates while preserving order
        clean_entities = list(dict.fromkeys(clean_entities))

        # If no entities found and we have instruction text, it means "No" disclosure
        if not clean_entities:
            continue

        # Extract amounts from the section (values like "$10K-$50K", "$50,000-$100,000")
        amount_pattern = r"\$[\d,]+(K|M)?(?:-\$[\d,]+(K|M)?)?"
        amounts = re.findall(amount_pattern, after_yes[:1000])

        for i, entity in enumerate(clean_entities[:5]):  # Max 5 per section
            sector = classify_sector(entity)
            amount_range = amounts[i] if i < len(amounts) else ""

            records.append({
                "legislator_name": reported_name,
                "filing_year": year,
                "filing_id": filing_id,
                "part": part,
                "entity_name": entity[:120],
                "role_or_type": "",
                "sector": sector,
                "amount_range": amount_range,
                "raw_text": after_yes[:300],
                "scraped_at": now,
            })

    return records


def download_and_parse(
    opener, filing_id: str, legislator: str, year: str
) -> list[dict]:
    """Download disclosure HTML and parse with Gemini (fallback: regex)."""
    url = (f"{BASE_URL}/ViewFormBinary.aspx"
           f"?filingid={filing_id}&contentType=application/pdf&type=SOEI")
    html = _get(opener, url, referer=f"{BASE_URL}/Search.aspx")
    if not html or len(html) < 500:
        return []

    now = datetime.now(timezone.utc).isoformat()

    # Try Gemini first for accurate structured extraction
    gemini_data = _gemini_extract(html)
    if gemini_data:
        return _gemini_to_records(gemini_data, legislator, filing_id, year, now)

    # Fallback to regex parsing
    return parse_disclosure(html, legislator, filing_id, year)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _upsert_records(conn: sqlite3.Connection, records: list[dict]) -> int:
    count = 0
    for r in records:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO legislator_financial_disclosures
                    (legislator_name, filing_year, filing_id, part,
                     entity_name, role_or_type, sector, amount_range,
                     raw_text, source, scraped_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["legislator_name"], r["filing_year"], r["filing_id"],
                r["part"], r["entity_name"], r["role_or_type"],
                r["sector"], r.get("amount_range",""),
                r.get("raw_text",""), "vec_soei", r["scraped_at"],
            ))
            count += 1
        except sqlite3.Error:
            pass
    conn.commit()
    return count


# ── Conflict detection ────────────────────────────────────────────────────────

def detect_conflicts(conn: sqlite3.Connection, min_signals: int = 2) -> list[dict]:
    """
    Find triple-overlap conflicts:
    1. Business interest / employment in sector X  (financial disclosure)
    2. Top campaign donors in sector X              (campaign_finance_summary)
    3. Votes more/less than average on sector X     (donor_vote_alignment)
    """
    rows = conn.execute("""
        SELECT
            d.legislator_name,
            d.sector                        AS disclosure_sector,
            d.entity_name                   AS disclosed_entity,
            d.part                          AS disclosure_type,
            d.filing_year,
            c.top_sector                    AS donor_top_sector,
            ROUND(c.total_raised,0)         AS total_raised,
            ROUND(a.alignment_delta,1)      AS vote_delta,
            a.sector_yes_rate               AS vote_yes_rate
        FROM legislator_financial_disclosures d
        LEFT JOIN campaign_finance_summary c
               ON lower(c.name) LIKE '%' || lower(substr(d.legislator_name,
                      instr(d.legislator_name,' ')+1)) || '%'
              AND c.source = 'va_sbe'
        LEFT JOIN donor_vote_alignment a
               ON a.legislator_id = c.legislator_id
        WHERE d.sector IS NOT NULL
          AND d.entity_name != 'NONE DISCLOSED'
          AND d.source = 'vec_soei'
        ORDER BY d.legislator_name, d.sector
    """).fetchall()

    seen = set()
    conflicts = []
    for r in rows:
        key = (r[0], r[1])
        if key in seen:
            continue
        seen.add(key)

        sector_match = bool(r[1] and r[5] and
                            r[1].split("/")[0].lower() in (r[5] or "").lower())
        vote_signal  = bool(r[7] is not None and abs(r[7]) >= 5)
        donor_signal = bool(r[6] and r[6] > 50_000)

        signals = sum([int(sector_match), int(vote_signal), int(donor_signal)])
        if signals >= min_signals:
            conflicts.append({
                "legislator":        r[0],
                "disclosure_sector": r[1],
                "disclosed_entity":  r[2],
                "disclosure_type":   r[3],
                "donor_top_sector":  r[5],
                "total_raised":      r[6],
                "vote_delta":        r[7],
                "sector_overlap":    sector_match,
                "signal_count":      signals,
            })

    conflicts.sort(key=lambda x: -x["signal_count"])
    return conflicts


# ── Main ──────────────────────────────────────────────────────────────────────

def main(year: str, target_name: str | None, dry_run: bool) -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)

    # Get legislator names from DB (use campaign_finance_summary as source of truth)
    all_legs = conn.execute("""
        SELECT DISTINCT name FROM campaign_finance_summary
        WHERE source = 'va_sbe' AND name IS NOT NULL
        ORDER BY name
    """).fetchall()
    legislators = [r["name"] for r in all_legs]

    if target_name:
        filtered = [l for l in legislators if target_name.lower() in l.lower()]
        if filtered:
            legislators = filtered
        else:
            # Name not in GA finance DB — search VEC directly (statewide officials, etc.)
            legislators = [target_name]

    print(f"Scraping {len(legislators)} legislators for year {year}…")

    opener, _ = _make_opener()
    vs, vsg = _init_session(opener)

    scraped = skipped = failed = total_records = 0

    for leg_name in legislators:
        parts = leg_name.strip().split()
        if len(parts) < 2:
            continue
        first = parts[0]
        last  = parts[-1]

        try:
            filings = search_legislator(opener, vs, vsg, first, last, year)
            soei_filings = [f for f in filings if f["type"] == "SOEI"]

            if not soei_filings:
                skipped += 1
                if target_name:
                    print(f"  No SOEI found for {leg_name} in {year}")
                continue

            for filing in soei_filings[:1]:  # take most recent
                fid = filing["filing_id"]
                print(f"  {leg_name}: filing {fid}", end=" ")

                if not dry_run:
                    records = download_and_parse(opener, fid, leg_name, year)
                    n = _upsert_records(conn, records)
                    total_records += n
                    print(f"-> {n} records")
                    if target_name:
                        for r in records:
                            print(f"    [{r['part']}] {r['entity_name'][:60]} | sector={r['sector']}")
                else:
                    print(f"(dry-run)")

                scraped += 1
                time.sleep(0.5)  # polite rate limit

        except Exception as e:
            failed += 1
            if target_name:
                import traceback; traceback.print_exc()
            else:
                print(f"  WARN {leg_name}: {e}")

    conn.close()

    print(f"\nDone: {scraped} scraped, {skipped} no-SOEI, {failed} failed")
    print(f"Total disclosure records written: {total_records}")

    if not dry_run and total_records > 0:
        conn2 = sqlite3.connect(POLLS_DB)
        conn2.row_factory = sqlite3.Row
        conflicts = detect_conflicts(conn2)
        conn2.close()
        if conflicts:
            print(f"\nConflict signals detected ({len(conflicts)} legislators):")
            for c in conflicts[:10]:
                print(f"  {c['legislator']:<28} disclosure={c['disclosure_sector']:<22} "
                      f"donors={c['donor_top_sector']:<22} overlap={c['sector_overlap']} "
                      f"vote_delta={c['vote_delta']} signals={c['signal_count']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--year",     default="2024",  help="Filing year")
    p.add_argument("--name",     default=None,    help="Single legislator name to scrape")
    p.add_argument("--dry-run",  action="store_true")
    args = p.parse_args()
    main(args.year, args.name, args.dry_run)
