#!/usr/bin/env python3
"""
Download Virginia bills from OpenStates API v3 and prepare VoteIQ ingest chunks.

Usage:
    python ingest_openstates.py --session 2026 --dry-run --limit 5
    python ingest_openstates.py --session 2026 --save
    python ingest_openstates.py --session 2026 --upsert

OpenStates API v3 uses OPENSTATES_API_KEY from .env.
The generated JSONL can be ingested by ingest.py.
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OPENSTATES_API_KEY = os.getenv("OPENSTATES_API_KEY")
OPENSTATES_BASE = "https://v3.openstates.org"
DEFAULT_JURISDICTION = "Virginia"
DEFAULT_PAGE_SIZE = 20
DEFAULT_INCLUDE = "sponsorships,actions,abstracts,votes"


def _norm_bill_id(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").upper())


def _clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _first_text(*values) -> str:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return ""


def _names(items: list[dict]) -> list[str]:
    names = []
    for item in items or []:
        name = (
            item.get("name")
            or item.get("person", {}).get("name")
            or item.get("primary_name")
            or item.get("entity", {}).get("name")
        )
        if name:
            names.append(str(name))
    return names


def _request(path: str, params: dict | None = None, _max_retries: int = 8) -> dict:
    if not OPENSTATES_API_KEY:
        raise RuntimeError("OPENSTATES_API_KEY is not set in .env")

    url = f"{OPENSTATES_BASE}{path}"
    headers = {
        "X-API-KEY": OPENSTATES_API_KEY,
        "User-Agent": "VoteIQ/1.0 (+https://voteiq.io)",
    }
    for attempt in range(_max_retries):
        try:
            response = requests.get(url, headers=headers, params=params or {}, timeout=90)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            wait = min(30 * (2 ** attempt), 300)
            print(f"  [retry {attempt+1}/{_max_retries}] network error: {exc} — sleeping {wait}s")
            time.sleep(wait)
            continue
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", 60)) + 10
            print(f"  [rate-limit] 429 — sleeping {wait}s (attempt {attempt+1}/{_max_retries})")
            time.sleep(wait)
            continue
        if response.status_code in (500, 502, 503, 504):
            wait = min(15 * (2 ** attempt), 120)
            print(f"  [server-error] {response.status_code} — sleeping {wait}s (attempt {attempt+1}/{_max_retries})")
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"OpenStates request failed after {_max_retries} retries: {url}")


def _include_values(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _result_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _has_next_page(payload, page: int, page_size: int, item_count: int) -> bool:
    if not isinstance(payload, dict):
        return item_count >= page_size
    pagination = payload.get("pagination") or {}
    if pagination.get("next_url") or pagination.get("next"):
        return True
    total_pages = pagination.get("total_pages") or pagination.get("max_page")
    if total_pages:
        return page < int(total_pages)
    total_items = pagination.get("total_items") or pagination.get("total")
    if total_items:
        return page * page_size < int(total_items)
    return item_count >= page_size


def search_bills(
    jurisdiction: str,
    session: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    limit: int = 0,
    include: str = DEFAULT_INCLUDE,
) -> list[dict]:
    bills = []
    page = 1
    while True:
        params = {
            "jurisdiction": jurisdiction,
            "session": session,
            "per_page": page_size,
            "page": page,
        }
        include_values = _include_values(include)
        if include_values:
            params["include"] = include_values

        payload = _request("/bills", params=params)
        items = _result_items(payload)
        bills.extend(items)
        print(f"[openstates] page {page}: {len(items)} bills ({len(bills)} total)")

        if limit and len(bills) >= limit:
            return bills[:limit]
        if not _has_next_page(payload, page, page_size, len(items)):
            return bills
        page += 1
        time.sleep(1.5)  # stay well under OpenStates rate limit


def fetch_bill_detail(jurisdiction: str, session: str, identifier: str) -> dict:
    # OpenStates accepts the bill identifier in the path. URL encoding is handled by requests.
    path = f"/bills/{jurisdiction}/{session}/{identifier}"
    return _request(path)


def enrich_bills(jurisdiction: str, session: str, bills: list[dict], detail: bool) -> list[dict]:
    if not detail:
        return bills

    enriched = []
    for index, bill in enumerate(bills, 1):
        identifier = bill.get("identifier") or bill.get("bill_id") or bill.get("id")
        if not identifier:
            enriched.append(bill)
            continue
        try:
            detail_bill = fetch_bill_detail(jurisdiction, session, identifier)
            enriched.append(detail_bill)
            print(f"[detail] {index}/{len(bills)} {identifier}")
        except Exception as exc:
            print(f"[warn] detail fetch failed for {identifier}: {exc}")
            enriched.append(bill)
        time.sleep(0.2)
    return enriched


def bill_to_rows(bill: dict, session: str, jurisdiction: str) -> list[dict]:
    identifier = bill.get("identifier") or bill.get("bill_id") or bill.get("id") or "UNKNOWN"
    bill_id = _norm_bill_id(identifier)
    title = _first_text(bill.get("title"), bill.get("short_title"))
    subject = ", ".join(bill.get("subject") or bill.get("subjects") or [])
    classification = ", ".join(bill.get("classification") or [])
    abstracts = bill.get("abstracts") or []
    summary = _first_text(
        bill.get("summary"),
        *(a.get("abstract") or a.get("note") or a.get("text") for a in abstracts if isinstance(a, dict)),
    )
    sponsors = _names(bill.get("sponsorships") or bill.get("sponsors") or [])
    actions = bill.get("actions") or []
    latest_action = actions[-1] if actions else {}
    latest_action_desc = _first_text(
        bill.get("latest_action_description"),
        latest_action.get("description"),
        latest_action.get("action"),
    )
    latest_action_date = _first_text(bill.get("latest_action_date"), latest_action.get("date"))
    openstates_url = _first_text(bill.get("openstates_url"), bill.get("web_url"))
    state_url = _first_text(bill.get("state_link"), bill.get("from_organization", {}).get("url"))

    lines = [f"Bill {bill_id} ({session} {jurisdiction} legislative session)"]
    if title:
        lines.append(f"Title: {title}")
    if classification:
        lines.append(f"Classification: {classification}")
    if subject:
        lines.append(f"Subjects: {subject}")
    if sponsors:
        lines.append(f"Sponsors: {', '.join(sponsors)}")
    if latest_action_desc:
        date_note = f" ({latest_action_date})" if latest_action_date else ""
        lines.append(f"Latest action{date_note}: {latest_action_desc}")
    if summary:
        lines.append(f"Summary: {summary}")
    if openstates_url:
        lines.append(f"OpenStates URL: {openstates_url}")
    if state_url:
        lines.append(f"State URL: {state_url}")

    rows = [{
        "chunk_id": f"openstates_{bill_id}_{session}_summary_0",
        "text": "\n".join(lines),
        "bill_id": bill_id,
        "session_id": session,
        "state": "VA",
        "year": session,
        "chunk_index": 0,
        "chunk_type": "bill_summary",
        "metadata": {
            "source": "openstates",
            "jurisdiction": jurisdiction,
            "title": title,
            "subject": subject,
            "classification": classification,
            "openstates_url": openstates_url,
            "state_url": state_url,
            "latest_action": latest_action_desc,
            "latest_action_date": latest_action_date,
        },
    }]

    votes = bill.get("votes") or []
    for idx, vote in enumerate(votes, 1):
        motion = _first_text(vote.get("motion_text"), vote.get("motion"), vote.get("description"))
        result = _first_text(vote.get("result"))
        date = _first_text(vote.get("start_date"), vote.get("date"))
        organization = _first_text(
            vote.get("organization", {}).get("name") if isinstance(vote.get("organization"), dict) else vote.get("organization")
        )
        counts = vote.get("counts") or []
        count_line = ", ".join(
            f"{c.get('option') or c.get('vote_type')}: {c.get('value') or c.get('count')}"
            for c in counts
            if isinstance(c, dict)
        )
        vote_lines = [f"Bill {bill_id} - Vote Record #{idx}"]
        if date or organization:
            vote_lines.append(f"Date: {date} | Chamber: {organization}".strip())
        if motion:
            vote_lines.append(f"Motion: {motion}")
        if result:
            vote_lines.append(f"Result: {result}")
        if count_line:
            vote_lines.append(f"Counts: {count_line}")
        rows.append({
            "chunk_id": f"openstates_{bill_id}_{session}_vote_{idx}",
            "text": "\n".join(vote_lines),
            "bill_id": bill_id,
            "session_id": session,
            "state": "VA",
            "year": session,
            "chunk_index": idx,
            "chunk_type": "vote_record",
            "metadata": {
                "source": "openstates",
                "jurisdiction": jurisdiction,
                "vote_date": date,
                "result": result,
                "chamber": organization,
            },
        })
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="VoteIQ OpenStates bill ingest")
    parser.add_argument("--session", default="2026", help="OpenStates session label, e.g. 2026")
    parser.add_argument("--jurisdiction", default=DEFAULT_JURISDICTION, help="OpenStates jurisdiction name")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--limit", type=int, default=0, help="Limit bills for testing")
    parser.add_argument(
        "--include",
        default=DEFAULT_INCLUDE,
        help="Comma-separated OpenStates include values",
    )
    parser.add_argument("--detail", action="store_true", help="Fetch bill detail records after search")
    parser.add_argument("--out", default="", help="Output JSONL path")
    parser.add_argument("--save", action="store_true", help="Write JSONL output")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/build only; no Chroma upsert")
    parser.add_argument("--upsert", action="store_true", help="Ingest generated JSONL into ChromaDB")
    parser.add_argument("--reset", action="store_true", help="Reset Chroma collection before upsert")
    args = parser.parse_args()

    bills = search_bills(
        jurisdiction=args.jurisdiction,
        session=args.session,
        page_size=args.page_size,
        limit=args.limit,
        include=args.include,
    )
    bills = enrich_bills(args.jurisdiction, args.session, bills, args.detail)

    rows = []
    for bill in bills:
        rows.extend(bill_to_rows(bill, args.session, args.jurisdiction))
    print(f"[chunk] Built {len(rows)} OpenStates chunks from {len(bills)} bills")

    out_path = Path(args.out or f"openstates_va_{args.session}.jsonl")
    if args.save or args.upsert:
        write_jsonl(out_path, rows)
        print(f"[save] Wrote {out_path}")

    if rows and args.dry_run:
        print("[dry-run] Sample chunk:")
        print(rows[0]["text"][:700])

    if args.upsert:
        from ingest import ingest_jsonl
        ingest_jsonl(str(out_path), reset=args.reset)


if __name__ == "__main__":
    main()
