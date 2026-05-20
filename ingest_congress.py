#!/usr/bin/env python3
"""
Ingest Virginia congressional member profiles and sponsored legislation
from the Congress.gov API v3.

Stores data in polls.db:
  congress_members  — all 13 current VA members (House + Senate)
  congress_bills    — bills sponsored or cosponsored by VA members

Examples:
    python ingest_congress.py
    python ingest_congress.py --dry-run
    python ingest_congress.py --congress 119
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
CONGRESS_API_BASE = "https://api.congress.gov/v3"
USER_AGENT = "VoteIQ/1.0 (civic data; alexisnieuwenhuys89@gmail.com)"
CURRENT_CONGRESS = 119


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_members (
            bioguide_id     TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            party           TEXT,
            chamber         TEXT,
            state           TEXT,
            district        TEXT,
            website         TEXT,
            fetched_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS congress_bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            congress        INTEGER NOT NULL,
            bill_type       TEXT NOT NULL,
            bill_number     TEXT NOT NULL,
            title           TEXT,
            introduced_date TEXT,
            policy_area     TEXT,
            latest_action   TEXT,
            latest_action_date TEXT,
            sponsor_id      TEXT,
            role            TEXT,
            api_url         TEXT,
            fetched_at      TEXT NOT NULL,
            UNIQUE(congress, bill_type, bill_number, sponsor_id, role)
        );

        CREATE INDEX IF NOT EXISTS idx_congress_bills_sponsor
            ON congress_bills(sponsor_id);
        CREATE INDEX IF NOT EXISTS idx_congress_bills_date
            ON congress_bills(introduced_date DESC);

        CREATE TABLE IF NOT EXISTS congress_bill_texts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            congress        INTEGER NOT NULL,
            bill_type       TEXT NOT NULL,
            bill_number     TEXT NOT NULL,
            version_name    TEXT,
            version_type    TEXT,
            version_date    TEXT,
            source_url      TEXT,
            text            TEXT,
            fetched_at      TEXT NOT NULL,
            UNIQUE(congress, bill_type, bill_number)
        );

        CREATE TABLE IF NOT EXISTS congress_floor_statements (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id     TEXT NOT NULL,
            member_name     TEXT,
            congress        INTEGER,
            chamber         TEXT,
            statement_date  TEXT,
            title           TEXT,
            text            TEXT,
            source_url      TEXT,
            fetched_at      TEXT NOT NULL,
            UNIQUE(bioguide_id, statement_date, title)
        );
        CREATE INDEX IF NOT EXISTS idx_cfs_member ON congress_floor_statements(bioguide_id);
        CREATE INDEX IF NOT EXISTS idx_cfs_date   ON congress_floor_statements(statement_date DESC);
    """)
    conn.commit()


def api_get(path: str, api_key: str, **params) -> dict:
    params["api_key"] = api_key
    qs = urllib.parse.urlencode(params)
    url = f"{CONGRESS_API_BASE}/{path}?{qs}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _decode_bytes(data: bytes, content_type: str) -> str:
    charset = "utf-8"
    if content_type:
        parts = content_type.split("charset=")
        if len(parts) > 1:
            charset = parts[-1].split(";")[0].strip() or charset
    try:
        return data.decode(charset, errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text("\n").strip()


def _url_get(url: str, timeout: int = 30) -> tuple[str, str] | None:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json,text/html,text/plain,*/*",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read()
            return _decode_bytes(body, content_type), content_type
    except Exception:
        return None


def _normalize_text_version(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    if "billTextVersion" in item and isinstance(item["billTextVersion"], dict):
        item = item["billTextVersion"]

    version_name = str(item.get("versionName") or item.get("version") or
                        item.get("versionCode") or item.get("version_name") or "")
    version_type = str(item.get("type") or item.get("format") or
                        item.get("versionType") or item.get("version_type") or "")
    version_date = str(item.get("versionDate") or item.get("date") or "")
    source_url = str(item.get("url") or item.get("sourceUrl") or
                     item.get("billTextUrl") or item.get("source_url") or "")

    def _extract_text_value(value):
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("text", "body", "content", "value", "parsedText", "bodyText"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
        return None

    text = _extract_text_value(item.get("text"))
    if not text:
        text = _extract_text_value(item.get("parsedText"))
    if not text:
        text = _extract_text_value(item.get("bodyText"))
    if not text:
        text = _extract_text_value(item.get("body"))
    if not text:
        text = _extract_text_value(item.get("billText"))

    return {
        "version_name": version_name,
        "version_type": version_type,
        "version_date": version_date,
        "source_url": source_url,
        "text": text if isinstance(text, str) and text.strip() else None,
    }


def _parse_version_date(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        return datetime.fromisoformat(date_str).isoformat()
    except ValueError:
        return date_str.strip()


def _score_text_version(version: dict) -> tuple[int, str]:
    t = (version.get("version_type") or "").lower()
    if "xml" in t:
        score = 5
    elif "formatted" in t or "formattedtext" in t or "formatted_text" in t:
        score = 4
    elif "text" in t or "plain" in t:
        score = 3
    elif "html" in t:
        score = 2
    elif "pdf" in t:
        score = 1
    else:
        score = 0
    return score, _parse_version_date(version.get("version_date") or "")


def _fetch_text_from_url(source_url: str) -> str | None:
    if not source_url:
        return None
    if source_url.lower().endswith(".pdf"):
        return None
    fetched = _url_get(source_url)
    if not fetched:
        return None
    body, content_type = fetched
    if "application/json" in content_type:
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                normalized = _normalize_text_version(data)
                return normalized.get("text")
        except Exception:
            return None
    if "html" in content_type:
        text = _html_to_text(body)
    else:
        text = body.strip()
    # Reject soft-404 responses from govinfo.gov and similar CDNs
    preview = text[:300].lower()
    if (
        "page not found" in preview
        or "404 not found" in preview
        or "error 404" in preview
        or len(text) < 200
    ):
        return None
    return text


def _extract_bill_text_versions(data: dict) -> list[dict]:
    versions = []
    candidates = [
        data.get("billTextVersions"),
        data.get("billTextVersion"),
        data.get("bill_text_versions"),
        data.get("versions"),
        data.get("billText"),
    ]
    for raw in candidates:
        if not raw:
            continue
        if isinstance(raw, list):
            for item in raw:
                normalized = _normalize_text_version(item)
                if normalized:
                    versions.append(normalized)
        elif isinstance(raw, dict):
            normalized = _normalize_text_version(raw)
            if normalized:
                versions.append(normalized)
        elif isinstance(raw, str):
            versions.append({
                "version_name": "text",
                "version_type": "text",
                "version_date": "",
                "source_url": "",
                "text": raw.strip(),
            })
        if versions:
            break
    return versions


_GOVINFO_TYPE_MAP = {
    "h.r.": "hr", "hr": "hr",
    "s.": "s", "s": "s",
    "h.res.": "hres", "hres": "hres",
    "s.res.": "sres", "sres": "sres",
    "h.con.res.": "hconres", "hconres": "hconres",
    "s.con.res.": "sconres", "sconres": "sconres",
    "h.j.res.": "hjres", "hjres": "hjres",
    "s.j.res.": "sjres", "sjres": "sjres",
}


def _govinfo_urls(congress: int, bill_type: str, bill_number: str) -> list[str]:
    """Return govinfo.gov XML URLs to try, from most-enacted to introduced."""
    bt = _GOVINFO_TYPE_MAP.get(bill_type.lower().replace(" ", ""), bill_type.lower().replace(".", ""))
    num = bill_number.strip()
    versions = ["enr", "eh", "rh", "ih"] if bt.startswith("h") else ["enr", "es", "rs", "is"]
    urls = []
    for v in versions:
        pkg = f"BILLS-{congress}{bt}{num}{v}"
        urls.append(f"https://www.govinfo.gov/content/pkg/{pkg}/xml/{pkg}.xml")
    return urls


def _fetch_govinfo_text(congress: int, bill_type: str, bill_number: str) -> dict | None:
    """Try govinfo.gov XML for a bill — fallback when Congress.gov text API returns nothing."""
    for url in _govinfo_urls(congress, bill_type, bill_number):
        result = _url_get(url)
        if not result:
            continue
        body, content_type = result
        if not body.strip():
            continue
        # Real govinfo XML is served as application/xml or text/xml.
        # A 404/error page is always text/html — reject immediately.
        if "html" in content_type.lower():
            continue
        # Belt-and-suspenders: reject anything that doesn't look like XML
        if not body.lstrip().startswith("<"):
            continue
        try:
            text = re.sub(r"<[^>]+>", " ", body)
            text = re.sub(r"\s+", " ", text).strip()
            if not _is_real_bill_text(text):
                continue
            return {
                "version_name": "govinfo",
                "version_type": "xml",
                "version_date": "",
                "source_url": url,
                "text": text[:50000],
            }
        except Exception:
            continue
    return None


def _normalize_bill_filter(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", "", str(value or "")).upper().replace(".", "")
    match = re.match(r"^(HR|S)(\d+)$", cleaned)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def fetch_bill_text(congress: int, bill_type: str, bill_number: str, api_key: str) -> dict | None:
    try:
        data = api_get(
            f"bill/{congress}/{bill_type}/{bill_number}/text",
            api_key,
            format="json",
        )
    except Exception as exc:
        print(f"    Warning: could not fetch text for {bill_type.upper()}{bill_number} ({congress}): {exc}")
        return None

    versions = _extract_bill_text_versions(data)
    if not versions:
        # Try to extract top-level text fields if present
        normalized = _normalize_text_version(data)
        if normalized.get("text"):
            versions = [normalized]
    if not versions:
        return None

    versions = sorted(versions, key=_score_text_version, reverse=True)
    for version in versions:
        if version.get("text"):
            return version
        if version.get("source_url"):
            version["text"] = _fetch_text_from_url(version["source_url"])
            if version["text"]:
                return version

    # Last resort: return the best candidate even if text is missing
    return versions[0] if versions else None


_GARBAGE_MARKERS = ("page not found", "404 not found", "error 404", "access denied")


def _is_real_bill_text(text: str | None) -> bool:
    """Return False if text is a soft-404 or too short to be real bill text."""
    if not text or len(text) < 200:
        return False
    preview = text[:400].lower()
    return not any(m in preview for m in _GARBAGE_MARKERS)


def upsert_bill_text(conn: sqlite3.Connection, congress: int, bill_type: str,
                     bill_number: str, version: dict, dry_run: bool) -> int:
    if not _is_real_bill_text(version.get("text")):
        return 0
    if dry_run:
        print(
            f"    [dry] text for {bill_type.upper()}{bill_number} ({congress}) "
            f"version={version.get('version_name') or version.get('version_type')} "
            f"date={version.get('version_date')} source={version.get('source_url')}"
        )
        return 1

    conn.execute(
        """INSERT INTO congress_bill_texts
                   (congress, bill_type, bill_number, version_name, version_type,
                    version_date, source_url, text, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(congress, bill_type, bill_number) DO UPDATE SET
                   version_name=excluded.version_name,
                   version_type=excluded.version_type,
                   version_date=excluded.version_date,
                   source_url=excluded.source_url,
                   text=excluded.text,
                   fetched_at=excluded.fetched_at""",
        (
            congress,
            bill_type,
            bill_number,
            version.get("version_name"),
            version.get("version_type"),
            version.get("version_date"),
            version.get("source_url"),
            version.get("text"),
            now_iso(),
        ),
    )
    return 1


def fetch_bill_texts(conn: sqlite3.Connection, api_key: str, dry_run: bool,
                     refresh: bool = False, bill_filter: tuple[str, str] | None = None) -> int:
    where = ""
    params = []
    if bill_filter:
        where = "WHERE bill_type = ? AND bill_number = ?"
        params = [bill_filter[0], bill_filter[1]]

    rows = conn.execute(
        f"SELECT DISTINCT congress, bill_type, bill_number FROM congress_bills {where}"
        f" ORDER BY congress DESC, bill_number ASC",
        params,
    ).fetchall()
    if not rows:
        return 0

    fetched = 0
    for congress, bill_type, bill_number in rows:
        if not bill_type or not bill_number:
            continue
        if not refresh:
            existing = conn.execute(
                "SELECT 1 FROM congress_bill_texts WHERE congress=? AND bill_type=? AND bill_number=? LIMIT 1",
                (congress, bill_type, bill_number),
            ).fetchone()
            if existing:
                continue

        selected = fetch_bill_text(congress, bill_type, bill_number, api_key)
        if not selected or not selected.get("text"):
            # Fallback: try govinfo.gov XML directly
            selected = _fetch_govinfo_text(congress, bill_type, bill_number)
        if not selected or not selected.get("text"):
            continue
        upsert_bill_text(conn, congress, bill_type, bill_number, selected, dry_run)
        fetched += 1
        if not dry_run:
            conn.commit()
        time.sleep(0.3)
    return fetched


def fetch_va_members(api_key: str) -> list[dict]:
    """Page through all current members and return Virginia ones."""
    va = []
    offset = 0
    while True:
        data = api_get("member", api_key, currentMember="true", limit=250, offset=offset)
        members = data.get("members", [])
        if not members:
            break
        for m in members:
            if m.get("state") == "Virginia":
                va.append(m)
        total = data.get("pagination", {}).get("count", 0)
        offset += len(members)
        if offset >= total:
            break
    return va


def fetch_member_detail(bioguide_id: str, api_key: str) -> dict:
    data = api_get(f"member/{bioguide_id}", api_key)
    return data.get("member", {})


def fetch_legislation(bioguide_id: str, role: str, api_key: str,
                      limit: int = 50, congress: int | None = None) -> list[dict]:
    """role: 'sponsored' or 'cosponsored'"""
    key = "sponsoredLegislation" if role == "sponsored" else "cosponsoredLegislation"
    path = f"member/{bioguide_id}/{role}-legislation"
    params = {"limit": limit}
    if congress is not None:
        params["congress"] = congress
    try:
        data = api_get(path, api_key, **params)
        return data.get(key, [])
    except Exception as exc:
        print(f"    Warning: could not fetch {role} legislation: {exc}")
        return []


def upsert_member(conn: sqlite3.Connection, m: dict, detail: dict,
                  dry_run: bool) -> None:
    bid = m.get("bioguideId", "")
    name = m.get("name", "")
    party = m.get("partyName", "")
    state = m.get("state", "")
    district = str(m.get("district", "S"))
    terms = m.get("terms", {}).get("item", [])
    chamber = terms[-1].get("chamber", "") if terms else ""
    website = detail.get("officialWebsiteUrl", "")

    if dry_run:
        print(f"  [dry] member: {name} ({party}) VA-{district} {chamber[:5]}")
        return

    conn.execute(
        """INSERT INTO congress_members
               (bioguide_id, name, party, chamber, state, district, website, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(bioguide_id) DO UPDATE SET
               name=excluded.name, party=excluded.party, chamber=excluded.chamber,
               state=excluded.state, district=excluded.district,
               website=excluded.website, fetched_at=excluded.fetched_at""",
        (bid, name, party, chamber, state, district, website, now_iso()),
    )


def upsert_bills(conn: sqlite3.Connection, bills: list[dict], sponsor_id: str,
                 role: str, dry_run: bool, default_congress: int) -> int:
    written = 0
    for b in bills:
        congress = b.get("congress") or default_congress
        btype = (b.get("type") or "").lower()
        bnum = str(b.get("number", ""))
        title = (b.get("title") or "")[:400]
        intro = b.get("introducedDate")
        policy = (b.get("policyArea") or {}).get("name")
        action = b.get("latestAction") or {}
        action_text = (action.get("text") or "")[:300]
        action_date = action.get("actionDate")
        api_url = b.get("url", "")

        if dry_run:
            print(f"    [dry] {btype.upper()}{bnum} ({role}): {title[:60]}")
            written += 1
            continue

        conn.execute(
            """INSERT INTO congress_bills
                   (congress, bill_type, bill_number, title, introduced_date,
                    policy_area, latest_action, latest_action_date,
                    sponsor_id, role, api_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(congress, bill_type, bill_number, sponsor_id, role)
               DO UPDATE SET
                   title=excluded.title,
                   latest_action=excluded.latest_action,
                   latest_action_date=excluded.latest_action_date,
                   fetched_at=excluded.fetched_at""",
            (congress, btype, bnum, title, intro, policy,
             action_text, action_date, sponsor_id, role, api_url, now_iso()),
        )
        written += 1
    return written


def fetch_floor_statements(bioguide_id: str, api_key: str,
                           congress: int = CURRENT_CONGRESS,
                           limit: int = 50) -> list[dict]:
    """Fetch floor statements for one member from Congress.gov."""
    statements = []
    offset = 0
    while True:
        try:
            data = api_get(
                f"member/{bioguide_id}/floor-statements",
                api_key,
                congress=congress,
                limit=min(limit, 250),
                offset=offset,
            )
        except Exception as exc:
            print(f"    [floor-stmt] fetch error for {bioguide_id}: {exc}")
            break
        items = data.get("floorStatements") or []
        statements.extend(items)
        if len(items) < 250 or len(statements) >= limit:
            break
        offset += len(items)
        time.sleep(0.3)
    return statements[:limit]


def upsert_floor_statements(
    conn: sqlite3.Connection,
    bioguide_id: str,
    member_name: str,
    statements: list[dict],
    congress: int,
    dry_run: bool,
) -> int:
    written = 0
    for s in statements:
        title = (s.get("title") or "").strip()
        date  = (s.get("date") or "")[:10]
        if not title and not date:
            continue
        row = {
            "bioguide_id":    bioguide_id,
            "member_name":    member_name,
            "congress":       congress,
            "chamber":        s.get("chamber", ""),
            "statement_date": date,
            "title":          title,
            "text":           (s.get("text") or "").strip() or None,
            "source_url":     s.get("url") or s.get("congressdotgov_url") or None,
            "fetched_at":     now_iso(),
        }
        if dry_run:
            written += 1
            continue
        try:
            conn.execute(
                """INSERT INTO congress_floor_statements
                       (bioguide_id, member_name, congress, chamber,
                        statement_date, title, text, source_url, fetched_at)
                   VALUES (:bioguide_id, :member_name, :congress, :chamber,
                           :statement_date, :title, :text, :source_url, :fetched_at)
                   ON CONFLICT(bioguide_id, statement_date, title) DO UPDATE SET
                       text       = excluded.text,
                       source_url = excluded.source_url,
                       fetched_at = excluded.fetched_at""",
                row,
            )
            written += 1
        except Exception as exc:
            print(f"    [floor-stmt] upsert error: {exc}")
    return written


def ingest(conn: sqlite3.Connection, api_key: str, dry_run: bool,
           bill_limit: int = 50, fetch_text: bool = False,
           refresh_text: bool = False, congress: int = CURRENT_CONGRESS,
           bill_filter: tuple[str, str] | None = None,
           floor_statements: bool = False) -> None:
    print("Fetching current Virginia members...")
    members = fetch_va_members(api_key)
    print(f"  Found {len(members)} VA members")

    for m in members:
        bid = m.get("bioguideId", "")
        name = m.get("name", "")
        district = m.get("district", "S")
        print(f"\n  VA-{district} {name} [{bid}]")

        detail = fetch_member_detail(bid, api_key)
        upsert_member(conn, m, detail, dry_run)
        time.sleep(0.3)

        for role in ("sponsored", "cosponsored"):
            bills = fetch_legislation(bid, role, api_key, limit=bill_limit, congress=congress)
            n = upsert_bills(conn, bills, bid, role, dry_run, default_congress=congress)
            print(f"    {role}: {n} bills")
            time.sleep(0.3)

        if floor_statements:
            stmts = fetch_floor_statements(bid, api_key, congress=congress)
            n = upsert_floor_statements(conn, bid, name, stmts, congress, dry_run)
            print(f"    floor statements: {n}")
            time.sleep(0.3)

    if fetch_text:
        print("\nFetching bill text from Congress.gov for stored bills...")
        fetched = fetch_bill_texts(conn, api_key, dry_run, refresh_text, bill_filter=bill_filter)
        print(f"  Bill text fetched for {fetched} bills")

    if not dry_run:
        conn.commit()
        total_bills = conn.execute("SELECT COUNT(*) FROM congress_bills").fetchone()[0]
        total_members = conn.execute("SELECT COUNT(*) FROM congress_members").fetchone()[0]
        print(f"\nDone. {total_members} members, {total_bills} bill records in DB.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest VA congressional members and bills from Congress.gov."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--congress", type=int, default=CURRENT_CONGRESS)
    parser.add_argument("--bill-limit", type=int, default=50,
                        help="Max sponsored/cosponsored bills to fetch per member")
    parser.add_argument("--fetch-text", action="store_true",
                        help="Fetch latest bill text versions from Congress.gov /text endpoint")
    parser.add_argument("--refresh-text", action="store_true",
                        help="Refresh bill text even when a text record already exists")
    parser.add_argument("--bill", help="Optional bill filter for text fetch, e.g. HR1 or S2")
    parser.add_argument("--floor-statements", action="store_true",
                        help="Also fetch floor statements for each member")
    parser.add_argument("--api-key", default=os.getenv("CONGRESS_API_KEY", ""),
                        help="Congress.gov API key (or set CONGRESS_API_KEY env var)")
    args = parser.parse_args(argv)

    if not args.api_key:
        print("ERROR: CONGRESS_API_KEY not set.", file=sys.stderr)
        return 1

    bill_filter = _normalize_bill_filter(args.bill)
    if args.bill and bill_filter is None:
        print(f"ERROR: invalid bill format: {args.bill}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    setup_db(conn)
    ingest(
        conn,
        args.api_key,
        args.dry_run,
        bill_limit=args.bill_limit,
        fetch_text=args.fetch_text,
        refresh_text=args.refresh_text,
        congress=args.congress,
        bill_filter=bill_filter,
        floor_statements=args.floor_statements,
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
