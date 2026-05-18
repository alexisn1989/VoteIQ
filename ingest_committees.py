"""
Ingest congressional committee assignments for Virginia's federal delegation.

Sources:
  committees-current.yaml      — committee names and codes
  committee-membership-current.yaml — who sits on which committee

Both from: https://github.com/unitedstates/congress-legislators
No API key required.

Usage:
  python ingest_committees.py
"""

import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml  # pip install pyyaml

DB_PATH  = Path(__file__).parent / "polls.db"
BASE_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"

VA_BIOGUIDES = {
    "B001292",  # Don Beyer
    "C001118",  # Ben Cline
    "G000568",  # Morgan Griffith
    "K000384",  # Tim Kaine
    "K000399",  # Jennifer Kiggans
    "M001227",  # Jennifer McClellan
    "M001239",  # John McGuire
    "S000185",  # Bobby Scott
    "S001230",  # Suhas Subramanyam
    "V000138",  # Eugene Vindman
    "W000804",  # Rob Wittman
    "W000805",  # Mark Warner
    # Walkinshaw has no bioguide in DB yet — will be added when congress.gov records him
}


def _fetch_yaml(filename: str) -> list | dict:
    url = f"{BASE_URL}/{filename}"
    raw = urllib.request.urlopen(url, timeout=30).read().decode()
    return yaml.safe_load(raw)


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_committees (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id    TEXT NOT NULL,
            member_name    TEXT,
            committee_code TEXT NOT NULL,
            committee_name TEXT NOT NULL,
            is_subcommittee INTEGER DEFAULT 0,
            parent_code    TEXT,
            parent_name    TEXT,
            role           TEXT,
            party          TEXT,
            rank           INTEGER,
            fetched_at     TEXT NOT NULL,
            UNIQUE(bioguide_id, committee_code)
        );
        CREATE INDEX IF NOT EXISTS idx_cc_bioguide ON congress_committees(bioguide_id);
    """)
    conn.commit()


def build_name_map(committees_raw: list) -> dict[str, dict]:
    """Return {code: {name, parent_code, parent_name, is_sub}} for all committees + subcommittees."""
    result: dict[str, dict] = {}
    for c in committees_raw:
        code = c.get("thomas_id", "")
        name = c.get("name", "").removeprefix("House Committee on ").removeprefix("Senate Committee on ")
        result[code] = {
            "name": name,
            "is_subcommittee": 0,
            "parent_code": None,
            "parent_name": None,
        }
        for sub in c.get("subcommittees", []):
            sub_code = code + sub.get("thomas_id", "")
            sub_name = sub.get("name", "")
            result[sub_code] = {
                "name": sub_name,
                "is_subcommittee": 1,
                "parent_code": code,
                "parent_name": name,
            }
    return result


def main() -> int:
    print("Fetching committee names…")
    committees_raw = _fetch_yaml("committees-current.yaml")
    name_map = build_name_map(committees_raw)
    print(f"  {len(name_map)} committee/subcommittee codes loaded")

    print("Fetching committee membership…")
    membership_raw = _fetch_yaml("committee-membership-current.yaml")
    print(f"  {len(membership_raw)} committees in membership file")

    # Build: {bioguide_id: [{code, role, party, rank}]}
    va_assignments: dict[str, list[dict]] = {b: [] for b in VA_BIOGUIDES}

    for code, members in membership_raw.items():
        info = name_map.get(code)
        if not info:
            continue
        for m in members:
            bio = m.get("bioguide", "")
            if bio not in VA_BIOGUIDES:
                continue
            va_assignments[bio].append({
                "code":     code,
                "name":     info["name"],
                "is_sub":   info["is_subcommittee"],
                "parent_code": info["parent_code"],
                "parent_name": info["parent_name"],
                "role":     m.get("title", ""),
                "party":    m.get("party", ""),
                "rank":     m.get("rank", None),
                "member_name": m.get("name", ""),
            })

    conn = sqlite3.connect(str(DB_PATH))
    setup_db(conn)
    now = datetime.now(timezone.utc).isoformat()

    total = 0
    for bio, assignments in va_assignments.items():
        if not assignments:
            print(f"  {bio}: no assignments found")
            continue
        member_name = assignments[0]["member_name"] if assignments else bio
        print(f"\n{member_name} ({bio}) — {len(assignments)} assignments:")
        for a in sorted(assignments, key=lambda x: (x["is_sub"], x["code"])):
            indent = "    └─ " if a["is_sub"] else "  • "
            role_tag = f" [{a['role']}]" if a["role"] else ""
            parent_tag = f" (under {a['parent_name']})" if a["is_sub"] and a["parent_name"] else ""
            print(f"{indent}{a['name']}{role_tag}{parent_tag}")
            conn.execute(
                """INSERT INTO congress_committees
                       (bioguide_id, member_name, committee_code, committee_name,
                        is_subcommittee, parent_code, parent_name, role, party, rank, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(bioguide_id, committee_code) DO UPDATE SET
                       member_name=excluded.member_name,
                       committee_name=excluded.committee_name,
                       is_subcommittee=excluded.is_subcommittee,
                       parent_code=excluded.parent_code,
                       parent_name=excluded.parent_name,
                       role=excluded.role,
                       party=excluded.party,
                       rank=excluded.rank,
                       fetched_at=excluded.fetched_at""",
                (bio, member_name, a["code"], a["name"],
                 a["is_sub"], a["parent_code"], a["parent_name"],
                 a["role"], a["party"], a["rank"], now),
            )
            total += 1
    conn.commit()
    conn.close()
    print(f"\nDone. Stored/updated {total} committee assignments.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
