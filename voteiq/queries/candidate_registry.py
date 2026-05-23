from __future__ import annotations

import sqlite3

from voteiq.config.finance_rules import POLLS_DB


DDL = """
CREATE TABLE IF NOT EXISTS candidate_registry (
    candidate_key TEXT PRIMARY KEY,
    fec_candidate_id TEXT,
    bioguide_id TEXT,
    lis_id TEXT,
    name TEXT,
    party TEXT,
    level TEXT,
    office TEXT,
    state TEXT,
    cycle INTEGER
);

CREATE INDEX IF NOT EXISTS idx_candidate_registry_fec
    ON candidate_registry(fec_candidate_id);

CREATE INDEX IF NOT EXISTS idx_candidate_registry_bioguide
    ON candidate_registry(bioguide_id);

CREATE INDEX IF NOT EXISTS idx_candidate_registry_lis
    ON candidate_registry(lis_id);

CREATE INDEX IF NOT EXISTS idx_candidate_registry_level_cycle
    ON candidate_registry(level, cycle);
"""


def ensure_candidate_registry() -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(DDL)
        conn.commit()
    finally:
        conn.close()


def seed_candidate_registry() -> int:
    rows = [
        {
            "candidate_key": "state:va:governor:spanberger:2026",
            "fec_candidate_id": None,
            "bioguide_id": None,
            "lis_id": None,
            "name": "Abigail D. Spanberger",
            "party": "D",
            "level": "state",
            "office": "Governor",
            "state": "VA",
            "cycle": 2026,
        },
        {
            "candidate_key": "fec:P80001571:2024",
            "fec_candidate_id": "P80001571",
            "bioguide_id": None,
            "lis_id": None,
            "name": "Donald J. Trump",
            "party": "R",
            "level": "presidential",
            "office": "President",
            "state": "US",
            "cycle": 2024,
        },
        {
            "candidate_key": "fec:H2VA02064:2024",
            "fec_candidate_id": "H2VA02064",
            "bioguide_id": "K000400",
            "lis_id": None,
            "name": "Jennifer Kiggans",
            "party": "R",
            "level": "federal",
            "office": "U.S. House VA-02",
            "state": "VA",
            "cycle": 2024,
        },
    ]

    ensure_candidate_registry()
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    try:
        conn.executemany("""
            INSERT INTO candidate_registry (
                candidate_key,
                fec_candidate_id,
                bioguide_id,
                lis_id,
                name,
                party,
                level,
                office,
                state,
                cycle
            )
            VALUES (
                :candidate_key,
                :fec_candidate_id,
                :bioguide_id,
                :lis_id,
                :name,
                :party,
                :level,
                :office,
                :state,
                :cycle
            )
            ON CONFLICT(candidate_key) DO UPDATE SET
                fec_candidate_id = excluded.fec_candidate_id,
                bioguide_id = excluded.bioguide_id,
                lis_id = excluded.lis_id,
                name = excluded.name,
                party = excluded.party,
                level = excluded.level,
                office = excluded.office,
                state = excluded.state,
                cycle = excluded.cycle
        """, rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _party_code(party: str | None) -> str | None:
    party = (party or "").strip().lower()
    if party.startswith("dem"):
        return "D"
    if party.startswith("rep"):
        return "R"
    return party.upper()[:1] or None


def _office(chamber: str | None, district: str | None) -> str:
    chamber = chamber or ""
    district = district or ""
    if chamber.lower().startswith("senate"):
        return "U.S. Senate"
    if district and district != "S":
        return f"U.S. House VA-{int(district):02d}" if district.isdigit() else f"U.S. House VA-{district}"
    return "U.S. House"


def seed_virginia_federal_registry(cycle: int = 2024) -> int:
    """
    Seed candidate_registry from current Virginia federal member records.

    Uses congress_members plus bioguide_fec_bridge. Members without a FEC bridge
    are skipped because candidate_key is FEC-based for this registry seed.
    """
    ensure_candidate_registry()
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT
                m.bioguide_id,
                b.fec_candidate_id,
                m.name,
                m.party,
                m.chamber,
                m.district
            FROM congress_members m
            JOIN bioguide_fec_bridge b
                ON b.bioguide_id = m.bioguide_id
            WHERE m.state IN ('VA', 'Virginia')
              AND b.fec_candidate_id IS NOT NULL
              AND b.fec_candidate_id != ''
            ORDER BY m.chamber, m.district, m.name
        """).fetchall()

        registry_rows = [
            {
                "candidate_key": f"fec:{row['fec_candidate_id']}:{cycle}",
                "fec_candidate_id": row["fec_candidate_id"],
                "bioguide_id": row["bioguide_id"],
                "lis_id": None,
                "name": row["name"],
                "party": _party_code(row["party"]),
                "level": "federal",
                "office": _office(row["chamber"], row["district"]),
                "state": "VA",
                "cycle": cycle,
            }
            for row in rows
        ]

        conn.executemany("""
            INSERT INTO candidate_registry (
                candidate_key,
                fec_candidate_id,
                bioguide_id,
                lis_id,
                name,
                party,
                level,
                office,
                state,
                cycle
            )
            VALUES (
                :candidate_key,
                :fec_candidate_id,
                :bioguide_id,
                :lis_id,
                :name,
                :party,
                :level,
                :office,
                :state,
                :cycle
            )
            ON CONFLICT(candidate_key) DO UPDATE SET
                fec_candidate_id = excluded.fec_candidate_id,
                bioguide_id = excluded.bioguide_id,
                lis_id = excluded.lis_id,
                name = excluded.name,
                party = excluded.party,
                level = excluded.level,
                office = excluded.office,
                state = excluded.state,
                cycle = excluded.cycle
        """, registry_rows)
        conn.commit()
        return len(registry_rows)
    finally:
        conn.close()
