"""Lightweight geocoding + distance helpers, shared by the agenda-address
backfill script and the "near me" chat context builder.

Deliberately does NOT import address_lookup.py's find_district() — that
function lazily loads a dozen geopandas shapefiles (congressional/HOD/SD
districts, VB council, 7 cities' precincts, Norfolk wards) to resolve
district/precinct info neither caller here needs. This module only needs
lat/lng, so it re-implements just the Census-then-Nominatim two-pass
(see address_lookup.find_district() for the original) to avoid paying
that memory/load cost inside a short-lived cron subprocess.
"""
from __future__ import annotations

import math
import re
import time

import requests

_CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
_NOMINATIM_UA = "voteiq_civic_platform_v1"

# A numbered street address (never a bare street name -- "Old Myrtle Road"
# alone geocodes to an arbitrary point along the road, not a parcel).
ADDRESS_RE = re.compile(
    r'\b(\d{1,6}\s+(?:[A-Z][a-zA-Z\'.]*\s+){1,4}?(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Lane|Ln|'
    r'Boulevard|Blvd|Way|Court|Ct|Place|Pl|Circle|Cir|Highway|Hwy|Parkway|Pkwy|Trail|Trl|Terrace|Ter))\b'
)


def extract_address(text: str) -> str | None:
    m = ADDRESS_RE.search(text or "")
    return m.group(1).strip() if m else None


def geocode_lite(address: str) -> dict | None:
    """Census Geocoder first (free, no key), Nominatim fallback. Returns
    {"lat": float, "lng": float, "matched_address": str} or None."""
    try:
        r = requests.get(
            _CENSUS_URL,
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=10,
        )
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0]["coordinates"]
            return {
                "lat": float(coords["y"]),
                "lng": float(coords["x"]),
                "matched_address": matches[0].get("matchedAddress", address),
            }
    except Exception:
        pass

    try:
        from geopy.geocoders import Nominatim

        time.sleep(1)  # Nominatim usage policy: max 1 req/sec
        geolocator = Nominatim(user_agent=_NOMINATIM_UA, timeout=15)
        location = geolocator.geocode(address)
        if location:
            return {
                "lat": location.latitude,
                "lng": location.longitude,
                "matched_address": location.raw.get("display_name", address),
            }
    except Exception:
        pass

    return None


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3958.8  # Earth radius, miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
