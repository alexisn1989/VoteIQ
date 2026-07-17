"""
build_campaign_finance_summary.py

Pre-computes campaign finance summaries for all VA legislators
(state SBE + federal FEC) and stores them in polls.db.

Run after any FEC or SBE ingest to refresh the cache:
    python build_campaign_finance_summary.py
"""
import json
import sqlite3
import os
from datetime import datetime, timezone

from voteiq.queries.sectors import get_campaign_finance
from voteiq.config.finance_rules import POLLS_DB, LEG_DB


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS campaign_finance_summary (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            legislator_id    TEXT NOT NULL,
            name             TEXT,
            chamber          TEXT,
            district         TEXT,
            party            TEXT,
            source           TEXT,          -- 'fec' or 'va_sbe'
            latest_cycle     TEXT,
            total_raised     REAL DEFAULT 0,
            top_sector       TEXT,
            top_sector_pct   REAL,
            by_sector_json   TEXT,          -- JSON array [{sector, total, donor_count, avg_donation}]
            top_donors_json  TEXT,          -- JSON array [{contributor_name, employer, sector, total, count}]
            cycles_json      TEXT,          -- JSON array of cycle strings
            updated_at       TEXT,
            UNIQUE(legislator_id, source)
        );
        CREATE INDEX IF NOT EXISTS idx_cfs_legislator
            ON campaign_finance_summary(legislator_id);
        CREATE INDEX IF NOT EXISTS idx_cfs_source
            ON campaign_finance_summary(source);
        CREATE INDEX IF NOT EXISTS idx_cfs_name
            ON campaign_finance_summary(name);
    """)
    conn.commit()


def _upsert(conn: sqlite3.Connection, result: dict) -> None:
    leg  = result["legislator"]
    sectors = result["by_sector"]
    top_sector     = sectors[0]["sector"] if sectors else None
    top_sector_pct = None
    if sectors and result["total_raised"]:
        top_sector_pct = round(sectors[0]["total"] / result["total_raised"] * 100, 1)

    conn.execute("""
        INSERT INTO campaign_finance_summary
            (legislator_id, name, chamber, district, party, source,
             latest_cycle, total_raised, top_sector, top_sector_pct,
             by_sector_json, top_donors_json, cycles_json, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(legislator_id, source) DO UPDATE SET
            name            = excluded.name,
            chamber         = excluded.chamber,
            district        = excluded.district,
            party           = excluded.party,
            latest_cycle    = excluded.latest_cycle,
            total_raised    = excluded.total_raised,
            top_sector      = excluded.top_sector,
            top_sector_pct  = excluded.top_sector_pct,
            by_sector_json  = excluded.by_sector_json,
            top_donors_json = excluded.top_donors_json,
            cycles_json     = excluded.cycles_json,
            updated_at      = excluded.updated_at
    """, (
        leg.get("lis_id") or leg.get("bioguide_id") or leg.get("id", ""),
        leg.get("name", ""),
        leg.get("chamber", ""),
        str(leg.get("district", "") or ""),
        leg.get("party", ""),
        result["source"],
        result["cycles"][0] if result["cycles"] else None,
        result["total_raised"],
        top_sector,
        top_sector_pct,
        json.dumps(result["by_sector"]),
        json.dumps(result["top_donors"]),
        json.dumps(result["cycles"]),
        datetime.now(timezone.utc).isoformat(),
    ))


def build_state(conn: sqlite3.Connection) -> int:
    """Build summaries for all VA state legislators with SBE data."""
    li_conn = sqlite3.connect(LEG_DB)
    li_conn.row_factory = sqlite3.Row
    legislators = li_conn.execute(
        """
        SELECT DISTINCT l.lis_id, l.name
        FROM legislators l
        JOIN va_sbe_contributions c ON c.legislator_id = l.lis_id
        WHERE l.active = 1
        """
    ).fetchall()
    li_conn.close()

    count = 0
    for leg in legislators:
        result = get_campaign_finance(name=leg["name"])
        if not result["by_sector"]:
            continue
        _upsert(conn, result)
        count += 1
        if count % 20 == 0:
            conn.commit()
            print(f"  state: {count}/{len(legislators)} done")

    conn.commit()
    print(f"  state: {count} legislators stored")
    return count


def build_federal(conn: sqlite3.Connection) -> int:
    """Build summaries for all VA federal members."""
    p_conn = sqlite3.connect(POLLS_DB)
    p_conn.row_factory = sqlite3.Row
    members = p_conn.execute("SELECT bioguide_id, name FROM congress_members").fetchall()
    p_conn.close()

    count = 0
    for mbr in members:
        result = get_campaign_finance(bioguide_id=mbr["bioguide_id"])
        if not result["by_sector"]:
            continue
        # Patch in bioguide_id so _upsert finds it
        result["legislator"]["bioguide_id"] = mbr["bioguide_id"]
        _upsert(conn, result)
        count += 1

    conn.commit()
    print(f"  federal: {count} members stored")
    return count


def main():
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_table(conn)

    print("Building campaign_finance_summary...")
    print("State legislators (va_sbe):")
    n_state = build_state(conn)
    print("Federal members (FEC):")
    n_federal = build_federal(conn)

    total = conn.execute("SELECT COUNT(*) FROM campaign_finance_summary").fetchone()[0]
    print(f"\nDone. {n_state} state + {n_federal} federal = {total} rows in campaign_finance_summary")
    conn.close()


if __name__ == "__main__":
    main()
