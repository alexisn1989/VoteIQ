#!/usr/bin/env python3
"""Import Suffolk precinct GIS into VoteIQ SQL and GeoJSON assets."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pyogrio import raw
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform
from shapely.wkb import loads as load_wkb


BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / "SUFFOLK_CITY.shp"
GEOJSON_OUT = BASE_DIR / "suffolk_precincts.geojson"
POLLS_DB = BASE_DIR / "polls.db"

KEEP_FIELDS = (
    "LocalityNa",
    "LocalityFI",
    "ElectionDi",
    "Election_1",
    "PrecinctNu",
    "PrecinctFI",
    "PrecinctNa",
    "Precinct_1",
    "GEOID",
    "PollingLoc",
    "PrecinctDi",
)


def _read_features() -> list[dict]:
    meta, _fids, geometries, field_data = raw.read(str(SOURCE))
    fields = [str(field) for field in meta["fields"]]
    transformer = Transformer.from_crs(meta["crs"], "EPSG:4326", always_xy=True)
    features = []

    for idx, geom_wkb in enumerate(geometries):
        props = {}
        for field, values in zip(fields, field_data):
            if field not in KEEP_FIELDS:
                continue
            value = values[idx]
            props[field] = None if value is None else str(value)

        geom = load_wkb(bytes(geom_wkb))
        geom_4326 = transform(transformer.transform, geom)
        features.append(
            {
                "type": "Feature",
                "id": props.get("GEOID") or props.get("PrecinctFI") or str(idx + 1),
                "properties": props,
                "geometry": mapping(geom_4326),
            }
        )

    return features


def write_geojson(features: list[dict]) -> None:
    payload = {
        "type": "FeatureCollection",
        "name": "suffolk_precincts",
        "features": features,
    }
    GEOJSON_OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_sql(features: list[dict]) -> None:
    conn = sqlite3.connect(POLLS_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS locality_gis_precincts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            locality TEXT NOT NULL,
            source_file TEXT NOT NULL,
            precinct_number TEXT,
            precinct_name TEXT,
            precinct_fips TEXT,
            geoid TEXT,
            election_district TEXT,
            polling_location TEXT,
            geometry_geojson TEXT NOT NULL,
            properties_json TEXT NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(locality, precinct_fips, geoid)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_locality_gis_precincts_locality ON locality_gis_precincts(locality)"
    )
    conn.execute(
        "DELETE FROM locality_gis_precincts WHERE lower(locality) = 'suffolk city'"
    )
    for feature in features:
        props = feature["properties"]
        conn.execute(
            """
            INSERT OR REPLACE INTO locality_gis_precincts
            (locality, source_file, precinct_number, precinct_name, precinct_fips,
             geoid, election_district, polling_location, geometry_geojson, properties_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                props.get("LocalityNa") or "SUFFOLK CITY",
                SOURCE.name,
                props.get("PrecinctNu"),
                props.get("PrecinctNa") or props.get("Precinct_1"),
                props.get("PrecinctFI"),
                props.get("GEOID"),
                props.get("ElectionDi") or props.get("Election_1"),
                props.get("PollingLoc"),
                json.dumps(feature["geometry"], ensure_ascii=False),
                json.dumps(props, ensure_ascii=False),
            ),
        )
    conn.commit()
    conn.close()


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source shapefile: {SOURCE}")
    features = _read_features()
    write_geojson(features)
    write_sql(features)
    print(f"Imported Suffolk GIS precincts: {len(features)}")
    print(f"GeoJSON: {GEOJSON_OUT.name}")
    print("SQL table: polls.db.locality_gis_precincts")


if __name__ == "__main__":
    main()
