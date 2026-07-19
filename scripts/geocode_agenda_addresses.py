"""
Geocode street addresses embedded in upcoming-agenda item titles so chat can
answer "what's being proposed near me?" -- the actual civic-action question
CUP/rezoning items exist to let residents weigh in on.

Address extraction is deliberately conservative: only titles with a leading
house number + street-suffix pattern are extracted (e.g. "113 Mallard Drive"),
never a bare street name alone ("Old Myrtle Road") -- a road-only mention
geocodes to some arbitrary point along the road, not the actual parcel, which
would put a confidently-wrong pin on a map. Skipping those is better than a
plausible-looking but wrong location. Confirmed via a spot-check across 2,516
real scraped titles: ~9% carry a numbered address, and every regex hit
inspected was a clean, correct extraction.

After extraction, each address is geocoded via geo_lite.geocode_lite() (the
same Census-then-Nominatim two-pass address_lookup.find_district() uses, but
without loading that function's dozen geopandas shapefiles -- this only needs
lat/lng, not district/precinct overlay, and running as a short-lived cron
subprocess should not pay that load cost). The geocoded locality is sanity-
checked against the expected city before storing -- a mismatch (wrong city
entirely) is skipped rather than stored, since a wrong pin is worse than no
pin.

Usage:
    python scripts/geocode_agenda_addresses.py
    python scripts/geocode_agenda_addresses.py --city suffolk --dry-run
    python scripts/geocode_agenda_addresses.py --limit 20
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from voteiq.services.geo_lite import extract_address, geocode_lite  # noqa: E402

DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"

# table prefix -> (display city name, locality name expected in the geocode
# result, used both as an "is this even worth trying" filter and a
# post-geocode sanity check)
CITIES = {
    "chesapeake": "Chesapeake",
    "suffolk": "Suffolk",
    "hampton": "Hampton",
    "newport_news": "Newport News",
    "portsmouth": "Portsmouth",
}

def _locality_matches(matched_address: str, city_display: str) -> bool:
    return city_display.lower() in (matched_address or "").lower()


def ensure_columns(conn: sqlite3.Connection, table: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, coltype in (("lat", "REAL"), ("lng", "REAL"), ("geocoded_address", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    conn.commit()


def geocode_table(conn: sqlite3.Connection, prefix: str, city_display: str,
                   limit: int | None, dry_run: bool) -> tuple[int, int]:
    table = f"{prefix}_upcoming_agenda"
    ensure_columns(conn, table)

    rows = conn.execute(f"""
        SELECT id, title FROM {table}
        WHERE lat IS NULL AND category != 'procedural'
        ORDER BY id
    """).fetchall()

    attempted = 0
    geocoded = 0
    for row_id, title in rows:
        addr = extract_address(title)
        if not addr:
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1

        full_addr = f"{addr}, {city_display}, VA"
        print(f"  [{table}#{row_id}] {addr!r} -> geocoding...")
        if dry_run:
            continue

        result = geocode_lite(full_addr)
        time.sleep(0.5)  # light pacing across Census/Nominatim calls
        if not result:
            print("    no geocode result -- skipping")
            continue
        if not _locality_matches(result["matched_address"], city_display):
            print(f"    geocoded outside {city_display} ({result['matched_address']!r}) -- skipping")
            continue

        conn.execute(
            f"UPDATE {table} SET lat=?, lng=?, geocoded_address=? WHERE id=?",
            (result["lat"], result["lng"], result["matched_address"], row_id),
        )
        conn.commit()
        geocoded += 1
        print(f"    OK: ({result['lat']:.5f}, {result['lng']:.5f})")

    return attempted, geocoded


def main() -> None:
    parser = argparse.ArgumentParser(description="Geocode addresses in upcoming-agenda item titles")
    parser.add_argument("--city", choices=list(CITIES), default=None,
                        help="Only process one city (default: all 5)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max addresses to attempt per city this run")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be geocoded without calling the geocoder or writing")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    targets = {args.city: CITIES[args.city]} if args.city else CITIES

    total_attempted = total_geocoded = 0
    for prefix, display in targets.items():
        print(f"\n{display}:")
        attempted, geocoded = geocode_table(conn, prefix, display, args.limit, args.dry_run)
        total_attempted += attempted
        total_geocoded += geocoded
        print(f"  {attempted} address(es) attempted, {geocoded} geocoded")

    conn.close()
    print(f"\nDone -- {total_geocoded}/{total_attempted} addresses geocoded this run.")


if __name__ == "__main__":
    main()
