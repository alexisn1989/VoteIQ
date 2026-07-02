#!/usr/bin/env python3
"""
Fetch current Virginia General Assembly member list from OpenStates API.

Saves va_members_2026.json with all delegates and senators.

Usage:
    python fetch_va_members.py
    python fetch_va_members.py --out va_members_custom.json
"""
import argparse
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OPENSTATES_API_KEY = os.getenv("OPENSTATES_API_KEY")
BASE = "https://v3.openstates.org"


def _get(path: str, params: dict = None, _retries: int = 5) -> dict:
    for attempt in range(_retries):
        r = requests.get(
            BASE + path,
            headers={"X-API-KEY": OPENSTATES_API_KEY, "User-Agent": "VoteIQ/1.0"},
            params=params or {},
            timeout=60,
        )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 30)) + 5
            print(f"  [rate limit] waiting {wait}s …")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Failed after {_retries} retries")


_VA_JURISDICTION = "ocd-jurisdiction/country:us/state:va/government"


def fetch_people(jurisdiction: str = _VA_JURISDICTION) -> list[dict]:
    people, page = [], 1
    while True:
        data = _get("/people", {
            "jurisdiction": jurisdiction,
            "per_page": 20,
            "page": page,
        })
        items = data.get("results", [])
        people.extend(items)
        print(f"  [page {page}] {len(items)} people — {len(people)} total")
        pagination = data.get("pagination", {})
        if page >= (pagination.get("max_page") or 1):
            break
        page += 1
        time.sleep(0.5)
    return people


def normalize(p: dict) -> dict:
    role = (p.get("current_role") or {})
    party = (p.get("party") or [{}])[0].get("name", "") if isinstance(p.get("party"), list) else p.get("party", "")
    return {
        "id":        p.get("id", ""),
        "name":      p.get("name", ""),
        "party":     party,
        "chamber":   role.get("org_classification", ""),
        "district":  str(role.get("district", "")),
        "title":     role.get("title", ""),
        "email":     next((c["value"] for c in (p.get("contact_details") or []) if c.get("type") == "email"), ""),
        "url":       (p.get("links") or [{}])[0].get("url", "") if p.get("links") else "",
        "image":     p.get("image", ""),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jurisdiction", default=_VA_JURISDICTION)
    parser.add_argument("--out", default="va_members_2026.json")
    args = parser.parse_args()

    print(f"Fetching {args.jurisdiction} members from OpenStates...")
    raw = fetch_people(args.jurisdiction)
    members = [normalize(p) for p in raw]

    out = Path(args.out)
    out.write_text(json.dumps(members, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {len(members)} members to {out}")

    house  = [m for m in members if "lower" in m["chamber"].lower() or "house" in m["chamber"].lower()]
    senate = [m for m in members if "upper" in m["chamber"].lower() or "senate" in m["chamber"].lower()]
    dem    = [m for m in members if "democrat" in m["party"].lower()]
    rep    = [m for m in members if "republican" in m["party"].lower()]
    print(f"  House:      {len(house)}")
    print(f"  Senate:     {len(senate)}")
    print(f"  Democrats:  {len(dem)}")
    print(f"  Republicans:{len(rep)}")
    if members:
        print("\nSample:")
        for m in members[:4]:
            print(f"  {m['name']} ({m['party']}, {m['title']} District {m['district']})")


if __name__ == "__main__":
    main()
