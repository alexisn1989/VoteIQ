"""
assign_testimony_wards.py
Geocode norfolk_council_testimony addresses via the free Census batch API
and assign a ward number using point-in-polygon against Wards.shp.

Usage:
    python assign_testimony_wards.py           # backfill all rows
    python assign_testimony_wards.py --dry-run # print only
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sqlite3
import time
import urllib.request
import urllib.parse
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

BASE_DIR  = Path(__file__).resolve().parent
DB_PATH   = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
WARDS_SHP = BASE_DIR / "Wards.shp"
SUPERWARDS_GEOJSON = BASE_DIR / "Superwards.geojson"

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BATCH_SIZE = 100   # Census allows up to 1000; keep smaller for reliability

# Cities that are NOT Norfolk — geocode would succeed but point won't land in a ward
_NON_NORFOLK = {"virginia beach", "chesapeake", "portsmouth", "hampton",
                "suffolk", "newport news", "hampton roads", "cape charles",
                "richmond", "williamsburg", "yorktown", "smithfield"}


# Trailing role/affiliation clauses that aren't part of the mailing address.
# e.g. "1201 Midland Street, president of the Campostella Civic League"
_ROLE_TAIL = re.compile(
    r",\s*(president|vice president|chair|chairman|chairwoman|director|league|"
    r"association|civic|coalition|resident|residing|member|founder|owner|ceo|"
    r"pastor|reverend|rev\.?|dr\.?|representing|on behalf|of the)\b.*$",
    re.I,
)
# Unit / apartment suffixes that confuse the Census matcher but don't change the ward.
_UNIT_SUFFIX = re.compile(r"\s*#\s*\w+|\s+(?:apt|unit|suite|ste)\.?\s*\w+", re.I)


def _strip_non_norfolk_city(addr: str) -> tuple[str, str]:
    """
    Return (street, city) where city defaults to 'Norfolk' unless the address
    explicitly names another city. Returns ('', '') if non-Norfolk city detected.

    Also strips trailing role/affiliation clauses ("..., president of X League")
    and unit suffixes ("... #4B") so Census geocoding can match the street.
    """
    addr = addr.strip().rstrip(",")
    # Drop role/affiliation tail before anything else (it looks like a city otherwise)
    addr = _ROLE_TAIL.sub("", addr).strip().rstrip(",")
    # Check for explicit non-Norfolk city suffix: "123 Main St, Virginia Beach"
    m = re.search(r",\s*([A-Za-z ]+)$", addr)
    if m:
        city = m.group(1).strip().lower()
        if city in _NON_NORFOLK:
            return "", ""          # skip — outside Norfolk
        street = _UNIT_SUFFIX.sub("", addr[: m.start()].strip()).strip(", ")
        return street, m.group(1).strip()
    street = _UNIT_SUFFIX.sub("", addr).strip(", ")
    return street, "Norfolk"


def _census_geocode_batch(rows: list[tuple[int, str]]) -> dict[int, tuple[float, float]]:
    """
    rows: list of (testimony_id, raw_address)
    Returns dict of {testimony_id: (lon, lat)} for matched addresses.
    """
    lines = ["Id,Street,City,State,ZIP"]
    id_map: dict[str, int] = {}
    for tid, raw in rows:
        street, city = _strip_non_norfolk_city(raw)
        if not street:
            continue
        row_id = str(tid)
        lines.append(f'{row_id},"{street}","{city}",VA,')
        id_map[row_id] = tid

    if not id_map:
        return {}

    csv_bytes = "\n".join(lines).encode("utf-8")
    boundary = "----VoteIQBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="addressFile"; filename="addrs.csv"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
    ).encode() + csv_bytes + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="benchmark"\r\n\r\n'
        f"Public_AR_Current\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        CENSUS_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_csv = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"  Census API error: {exc}")
        return {}

    result: dict[int, tuple[float, float]] = {}
    for row in csv.reader(io.StringIO(raw_csv)):
        if len(row) < 6:
            continue
        row_id, _input_addr, match, match_type, matched_addr, coords = row[:6]
        if match.strip().upper() != "MATCH":
            continue
        try:
            lon_str, lat_str = coords.strip().split(",")
            result[id_map[row_id.strip()]] = (float(lon_str), float(lat_str))
        except (ValueError, KeyError):
            continue
    return result


def load_wards() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(str(WARDS_SHP))          # EPSG:2284
    return gdf[["WARD", "geometry"]].copy()


def load_superwards() -> gpd.GeoDataFrame:
    """Norfolk's 2 superwards (6, 7) — a separate, overlapping district map.

    Wards do NOT nest cleanly into superwards (Wards 1 & 2 split across both),
    so superward must be derived from the geocoded point, not the ward number.
    """
    gdf = gpd.read_file(str(SUPERWARDS_GEOJSON))  # EPSG:4326
    return gdf[["SUPWARD", "geometry"]].copy()


def assign_ward(lon: float, lat: float, wards: gpd.GeoDataFrame) -> int | None:
    """Point-in-polygon: reproject WGS84 point to EPSG:2284, find containing ward."""
    pt_gdf = gpd.GeoDataFrame(
        geometry=[Point(lon, lat)], crs="EPSG:4326"
    ).to_crs(wards.crs)
    pt = pt_gdf.geometry.iloc[0]
    for _, row in wards.iterrows():
        if row.geometry.contains(pt):
            return int(row["WARD"])
    return None


def assign_superward(lon: float, lat: float, superwards: gpd.GeoDataFrame) -> int | None:
    """Point-in-polygon against Superwards.geojson (already WGS84 — no reprojection)."""
    pt = Point(lon, lat)
    for _, row in superwards.iterrows():
        if row.geometry.contains(pt):
            return int(row["SUPWARD"])
    return None


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(norfolk_council_testimony)")}
    if "ward" not in cols:
        conn.execute("ALTER TABLE norfolk_council_testimony ADD COLUMN ward INTEGER")
    if "superward" not in cols:
        conn.execute("ALTER TABLE norfolk_council_testimony ADD COLUMN superward INTEGER")
    if "lat" not in cols:
        conn.execute("ALTER TABLE norfolk_council_testimony ADD COLUMN lat REAL")
    if "lon" not in cols:
        conn.execute("ALTER TABLE norfolk_council_testimony ADD COLUMN lon REAL")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--reset", action="store_true", help="Re-geocode already assigned rows")
    args = parser.parse_args()

    wards = load_wards()
    print(f"Loaded {len(wards)} ward polygons (CRS: {wards.crs})")
    superwards = load_superwards()
    print(f"Loaded {len(superwards)} superward polygons (CRS: {superwards.crs})")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if not args.dry_run:
        ensure_columns(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(norfolk_council_testimony)")}
    has_ward = "ward" in cols
    if args.reset or not has_ward:
        where = "WHERE speaker_addr != ''"
    else:
        where = "WHERE ward IS NULL AND speaker_addr != ''"
    rows = conn.execute(
        f"SELECT id, speaker_addr FROM norfolk_council_testimony {where}"
    ).fetchall()
    print(f"{len(rows)} addresses to geocode")

    assigned = 0
    skipped  = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = [(r["id"], r["speaker_addr"]) for r in rows[i: i + BATCH_SIZE]]
        print(f"  Batch {i // BATCH_SIZE + 1}: {len(batch)} addresses...")

        coords = _census_geocode_batch(batch)
        print(f"    matched: {len(coords)}")

        for tid, (lon, lat) in coords.items():
            ward = assign_ward(lon, lat, wards)
            superward = assign_superward(lon, lat, superwards)
            addr = next(a for i2, a in batch if i2 == tid)
            if ward is not None:
                print(f"    [{tid:4d}] Ward {ward} / Superward {superward}  {addr}")
                if not args.dry_run:
                    conn.execute(
                        "UPDATE norfolk_council_testimony "
                        "SET ward=?, superward=?, lat=?, lon=? WHERE id=?",
                        (ward, superward, lat, lon, tid),
                    )
                assigned += 1
            else:
                print(f"    [{tid:4d}] outside Norfolk  {addr}")
                skipped += 1

        if not args.dry_run:
            conn.commit()
        if i + BATCH_SIZE < len(rows):
            time.sleep(1)   # be polite to Census API

    conn.close()
    print(f"\nDone. Assigned: {assigned}, outside-Norfolk/unmatched: {skipped}")


if __name__ == "__main__":
    main()
