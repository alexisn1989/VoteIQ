from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import requests


SOURCE_URL = (
    "https://www.elections.virginia.gov/media/registration-statistics/"
    "2025-November-General-Election-Day-Polling-Locations-20251022.xlsx"
)
OUT_PATH = Path("polling_place_addresses.json")
NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _text(node):
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _col_index(cell_ref):
    letters = re.sub(r"[^A-Z]", "", cell_ref or "")
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _precinct_number(value):
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return ""
    return str(int(match.group(0)))


def _read_xlsx_rows(raw):
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("a:si", NS)]

        sheet_name = next(
            name for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        root = ET.fromstring(zf.read(sheet_name))

    rows = []
    for row in root.findall(".//a:sheetData/a:row", NS):
        values = []
        for cell in row.findall("a:c", NS):
            idx = _col_index(cell.get("r"))
            while len(values) <= idx:
                values.append("")
            cell_type = cell.get("t")
            if cell_type == "s":
                raw_value = _text(cell.find("a:v", NS))
                values[idx] = shared[int(raw_value)] if raw_value else ""
            elif cell_type == "inlineStr":
                values[idx] = _text(cell.find("a:is", NS))
            else:
                values[idx] = _text(cell.find("a:v", NS))
        rows.append(values)
    return rows


def build():
    response = requests.get(SOURCE_URL, timeout=60)
    response.raise_for_status()
    rows = _read_xlsx_rows(response.content)
    if not rows:
        raise RuntimeError("No rows found in polling-place workbook")

    headers = rows[0]
    records = []
    for row in rows[1:]:
        row = row + [""] * (len(headers) - len(row))
        item = dict(zip(headers, row))
        locality = item.get("Locality Name", "").strip()
        precinct = item.get("Voting Precinct Name", "").strip()
        if not locality or not precinct:
            continue

        address_parts = [
            item.get("Address Line 1", "").strip(),
            item.get("Address Line 2", "").strip(),
            item.get("City", "").strip(),
            item.get("State", "").strip(),
            item.get("Zip Code", "").strip(),
        ]
        full_address = ", ".join(part for part in address_parts if part)
        records.append({
            "election_name": item.get("Election Name", "").strip(),
            "locality": locality,
            "locality_key": _norm(locality),
            "precinct": precinct,
            "precinct_key": _norm(precinct),
            "precinct_number": _precinct_number(precinct),
            "location": item.get("Location", "").strip(),
            "address_line_1": item.get("Address Line 1", "").strip(),
            "address_line_2": item.get("Address Line 2", "").strip(),
            "city": item.get("City", "").strip(),
            "state": item.get("State", "").strip(),
            "zip_code": item.get("Zip Code", "").strip(),
            "room": item.get("Poll Location Voting Room", "").strip(),
            "full_address": full_address,
        })

    OUT_PATH.write_text(
        json.dumps({
            "source_url": SOURCE_URL,
            "record_count": len(records),
            "records": records,
        }, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(records)} polling-place records to {OUT_PATH}")


if __name__ == "__main__":
    build()
