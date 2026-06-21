"""
backfill_wards_nominatim.py
Second-pass ward backfill for norfolk_council_testimony rows that the Census
batch geocoder could not match. Uses OpenStreetMap / Nominatim, which has
better residential coverage, then assigns ward + superward via the SAME
point-in-polygon logic as the Census pass (Wards.shp + Superwards.geojson).

Honesty notes:
  - Only rows whose geocoded point lands inside a Norfolk ward polygon are updated.
  - Out-of-city speakers (Chesapeake, VA Beach, etc.) are skipped and stay NULL.
  - A `geocode_source` column records provenance ('nominatim'); original Census
    rows keep geocode_source = NULL. No address or ward is ever fabricated.

Usage:
    python backfill_wards_nominatim.py            # backfill in-city ward-NULL rows
    python backfill_wards_nominatim.py --dry-run  # probe only, no DB writes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

from assign_testimony_wards import (
    BASE_DIR, _NON_NORFOLK, _ROLE_TAIL, _UNIT_SUFFIX,
    load_wards, load_superwards, assign_ward, assign_superward,
)

DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "VoteIQ-civic-research/1.0 (alexisnieuwenhuys89@gmail.com)"
RATE_SECONDS = 1.1  # Nominatim usage policy: max 1 request/second


def _clean_street(addr: str) -> str:
    """Strip role/affiliation tails and unit suffixes; return street only."""
    s = (addr or "").strip().rstrip(",")
    s = _ROLE_TAIL.sub("", s).strip().rstrip(",")
    s = _UNIT_SUFFIX.sub("", s).strip(", ")
    return s


def _is_other_city(addr: str) -> bool:
    a = (addr or "").lower()
    return any(c in a for c in _NON_NORFOLK)


def geocode_nominatim(street: str) -> tuple[float, float] | None:
    """Return (lon, lat) for a Norfolk street, or None. Uses structured query."""
    params = {
        "street": street,
        "city": "Norfolk",
        "state": "Virginia",
        "country": "USA",
        "format": "json",
        "limit": 1,
    }
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        print(f"    nominatim error: {exc}")
        return None
    if not data:
        return None
    try:
        return float(data[0]["lon"]), float(data[0]["lat"])
    except (KeyError, ValueError, IndexError):
        return None


def ensure_source_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(norfolk_council_testimony)")}
    if "geocode_source" not in cols:
        conn.execute("ALTER TABLE norfolk_council_testimony ADD COLUMN geocode_source TEXT")
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    wards = load_wards()
    superwards = load_superwards()
    print(f"Loaded {len(wards)} wards, {len(superwards)} superwards")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    if not args.dry_run:
        ensure_source_column(conn)

    rows = conn.execute(
        "SELECT id, speaker_addr FROM norfolk_council_testimony "
        "WHERE ward IS NULL AND speaker_addr != ''"
    ).fetchall()

    targets = [(r["id"], r["speaker_addr"]) for r in rows if not _is_other_city(r["speaker_addr"])]
    skipped_city = len(rows) - len(targets)
    print(f"{len(rows)} ward-NULL rows; {skipped_city} out-of-city skipped; "
          f"{len(targets)} in-city to attempt\n")

    assigned = outside = nomatch = 0
    for tid, raw in targets:
        street = _clean_street(raw)
        if not street:
            nomatch += 1
            continue
        coords = geocode_nominatim(street)
        time.sleep(RATE_SECONDS)
        if not coords:
            print(f"  [{tid:4d}] no match   {street!r}")
            nomatch += 1
            continue
        lon, lat = coords
        ward = assign_ward(lon, lat, wards)
        if ward is None:
            print(f"  [{tid:4d}] outside    {street!r}")
            outside += 1
            continue
        superward = assign_superward(lon, lat, superwards)
        print(f"  [{tid:4d}] Ward {ward} / SW {superward}   {street!r}")
        if not args.dry_run:
            conn.execute(
                "UPDATE norfolk_council_testimony "
                "SET ward=?, superward=?, lat=?, lon=?, geocode_source='nominatim' "
                "WHERE id=?",
                (ward, superward, lat, lon, tid),
            )
            conn.commit()
        assigned += 1

    conn.close()
    print(f"\nDone. Assigned: {assigned}, outside-ward: {outside}, unmatched: {nomatch}")


if __name__ == "__main__":
    main()
