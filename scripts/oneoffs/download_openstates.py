#!/usr/bin/env python3
"""
Robust OpenStates bill downloader with resume support.

- Appends to JSONL file as each page arrives (survives crashes)
- Tracks last-completed page in a .progress file
- Handles 429 (rate-limit) and 5xx with exponential backoff
- Runs one session at a time, resumes automatically

Usage:
    python download_openstates.py                       # 2026, 2025, 2024
    python download_openstates.py --session 2026        # one session
    python download_openstates.py --session 2024 --reset  # start fresh
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OPENSTATES_API_KEY = os.getenv("OPENSTATES_API_KEY")
BASE = "https://v3.openstates.org"
PAGE_SIZE = 20
INTER_PAGE_SLEEP = 2.0   # seconds between pages to avoid rate limits
INCLUDE = "sponsorships,actions,abstracts"


def _get(path, params, max_retries=10):
    headers = {
        "X-API-KEY": OPENSTATES_API_KEY,
        "User-Agent": "VoteIQ/1.0",
    }
    url = BASE + path
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=90)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            wait = min(20 * (2 ** attempt), 240)
            print(f"  [net-err attempt {attempt+1}] {e} — wait {wait}s", flush=True)
            time.sleep(wait)
            continue

        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 60)) + 15
            print(f"  [429 attempt {attempt+1}] rate-limited — wait {wait}s", flush=True)
            time.sleep(wait)
            continue

        if r.status_code in (500, 502, 503, 504):
            wait = min(15 * (2 ** attempt), 180)
            print(f"  [{r.status_code} attempt {attempt+1}] server error — wait {wait}s", flush=True)
            time.sleep(wait)
            continue

        r.raise_for_status()
        return r.json()

    raise RuntimeError(f"Failed after {max_retries} retries: {url}")


def _norm_bill_id(v):
    return re.sub(r"\s+", "", str(v or "").upper())


def _clean(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def bill_to_chunk(bill, session):
    bid = _norm_bill_id(bill.get("identifier") or bill.get("id") or "")
    title = _clean(bill.get("title") or bill.get("short_title") or "")
    subj = ", ".join(bill.get("subject") or bill.get("subjects") or [])
    clf  = ", ".join(bill.get("classification") or [])
    abstracts = bill.get("abstracts") or []
    summary = next(
        (_clean(a.get("abstract") or a.get("note") or "")
         for a in abstracts if isinstance(a, dict) and (a.get("abstract") or a.get("note"))),
        ""
    )
    sponsors = [
        str(s.get("name") or (s.get("person") or {}).get("name") or "")
        for s in (bill.get("sponsorships") or bill.get("sponsors") or [])
    ]
    actions = bill.get("actions") or []
    last_action = actions[-1] if actions else {}
    la_desc = _clean(bill.get("latest_action_description") or last_action.get("description") or "")
    la_date = _clean(bill.get("latest_action_date") or last_action.get("date") or "")
    url = _clean(bill.get("openstates_url") or bill.get("web_url") or "")

    lines = [f"Bill {bid} ({session} Virginia legislative session)"]
    if title:     lines.append(f"Title: {title}")
    if clf:       lines.append(f"Classification: {clf}")
    if subj:      lines.append(f"Subjects: {subj}")
    if sponsors:  lines.append(f"Sponsors: {', '.join(sponsors)}")
    if la_desc:
        lines.append(f"Latest action ({la_date}): {la_desc}" if la_date else f"Latest action: {la_desc}")
    if summary:   lines.append(f"Summary: {summary}")
    if url:       lines.append(f"OpenStates URL: {url}")

    return {
        "chunk_id": f"openstates_{bid}_{session}_summary_0",
        "text": "\n".join(lines),
        "bill_id": bid,
        "session_id": session,
        "state": "VA",
        "year": session,
        "chunk_index": 0,
        "chunk_type": "bill_summary",
        "metadata": {
            "source": "openstates",
            "jurisdiction": "Virginia",
            "title": title,
            "subject": subj,
            "classification": clf,
            "openstates_url": url,
            "latest_action": la_desc,
            "latest_action_date": la_date,
        },
    }


def download_session(session: str, out_path: Path, reset: bool = False):
    progress_path = out_path.with_suffix(".progress")

    if reset and out_path.exists():
        out_path.unlink()
    if reset and progress_path.exists():
        progress_path.unlink()

    # Load resume state
    start_page = 1
    known_ids = set()
    if progress_path.exists():
        try:
            prog = json.loads(progress_path.read_text())
            start_page = prog.get("next_page", 1)
            known_ids  = set(prog.get("seen_ids", []))
            print(f"  [resume] session={session} starting from page {start_page}, "
                  f"{len(known_ids)} bills already saved", flush=True)
        except Exception:
            pass

    # Load existing chunk IDs from JSONL to avoid duplicates
    if out_path.exists() and not reset:
        existing = 0
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    if obj.get("session_id") == session:
                        known_ids.add(obj.get("bill_id", ""))
                        existing += 1
                except Exception:
                    pass
        if existing:
            print(f"  [existing] {existing} chunks for {session} already in {out_path.name}", flush=True)

    # Fetch pages
    page = start_page
    total_saved = len(known_ids)

    with out_path.open("a", encoding="utf-8") as fout:
        while True:
            params = {
                "jurisdiction": "Virginia",
                "session": session,
                "per_page": PAGE_SIZE,
                "page": page,
                "include": ["sponsorships", "actions", "abstracts"],
            }
            print(f"  [page {page}] fetching …", flush=True)
            payload = _get("/bills", params)
            items   = payload.get("results", [])

            new_this_page = 0
            for bill in items:
                bid = _norm_bill_id(bill.get("identifier") or bill.get("id") or "")
                if bid in known_ids:
                    continue
                chunk = bill_to_chunk(bill, session)
                fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                known_ids.add(bid)
                new_this_page += 1
                total_saved += 1

            print(f"  [page {page}] {len(items)} bills | {new_this_page} new | "
                  f"{total_saved} total saved", flush=True)

            pagination = payload.get("pagination", {})
            max_page   = pagination.get("max_page", 1)

            # Save progress after every page
            progress_path.write_text(json.dumps({
                "session": session,
                "next_page": page + 1,
                "seen_ids": list(known_ids),
                "total_saved": total_saved,
            }))

            if page >= max_page or len(items) == 0:
                print(f"  [done] session={session} — {total_saved} chunks saved to {out_path.name}",
                      flush=True)
                # Clear progress on successful completion
                if progress_path.exists():
                    progress_path.unlink()
                break

            page += 1
            time.sleep(INTER_PAGE_SLEEP)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="all",
                        help="Session year (2024/2025/2026) or 'all'")
    parser.add_argument("--out",  default="openstates_va_{session}.jsonl",
                        help="Output JSONL pattern (use {session})")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing output and start fresh")
    args = parser.parse_args()

    if not OPENSTATES_API_KEY:
        sys.exit("Error: OPENSTATES_API_KEY not found in .env")

    sessions = ["2026", "2025", "2024"] if args.session == "all" else [args.session]

    for sess in sessions:
        out = Path(args.out.replace("{session}", sess))
        print(f"\n{'='*60}", flush=True)
        print(f"Downloading session {sess} -> {out}", flush=True)
        print(f"{'='*60}", flush=True)
        try:
            download_session(sess, out, reset=args.reset)
        except Exception as e:
            print(f"  [FAILED] session={sess}: {e}", flush=True)
            print("  Progress saved — re-run to resume", flush=True)


if __name__ == "__main__":
    main()
