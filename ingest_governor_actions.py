"""
ingest_governor_actions.py

Reads bill governor action data from openstates_va.db (bills.latest_action)
and writes to polls.db governor_actions table.

Replaces the legislative_intelligence.db ATTACH pattern so governor action
data is available on Render (which only has polls.db and openstates_va.db).

Run directly or via scripts/scheduled_ingest.py:
    python ingest_governor_actions.py
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OPENSTATES_DB = os.environ.get("OPENSTATES_DB", str(BASE_DIR / "openstates_va.db"))
POLLS_DB = os.environ.get("POLLS_DB", str(BASE_DIR / "polls.db"))

ACTION_LABELS = {
    "signed":          "Signed into law",
    "vetoed":          "Vetoed",
    "pocket_veto":     "Pocket veto",
    "amended":         "Amended / returned",
    "veto_overridden": "Veto overridden",
    "veto_sustained":  "Veto sustained",
    "pending":         "Pending governor action",
}


def _governor_for_session(session: str) -> str:
    return "Spanberger" if str(session) >= "2026" else "Youngkin"


def _classify(latest_action: str) -> str:
    s = (latest_action or "").lower()
    if any(t in s for t in ["passed over veto", "veto overridden", "overridden"]):
        return "veto_overridden"
    if any(t in s for t in ["veto sustained", "sustained governor", "override failed"]):
        return "veto_sustained"
    if "pocket" in s and "veto" in s:
        return "pocket_veto"
    if any(t in s for t in ["recommendation received", "governor's recommendation",
                              "governor recommendation", "amendment"]):
        return "amended"
    if "veto" in s:
        return "vetoed"
    if any(t in s for t in ["chapter", "approved by governor", "signed by governor", "enacted"]):
        return "signed"
    if any(t in s for t in ["sent to governor", "enrolled", "presented to governor"]):
        return "pending"
    return "other"


def _parse_chapter(action: str) -> str:
    m = re.search(r"chapter\s+(\d+)", (action or "").lower())
    return m.group(1) if m else ""


def _parse_effective_date(action: str) -> str:
    m = re.search(r"effective\s+([\d/]+)", (action or "").lower())
    return m.group(1) if m else ""


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS governor_actions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number    TEXT,
            session        TEXT,
            title          TEXT,
            raw_status     TEXT,
            action         TEXT,
            action_key     TEXT,
            action_label   TEXT,
            action_date    TEXT,
            chapter_number TEXT,
            effective_date TEXT,
            governor       TEXT,
            sponsor_name   TEXT,
            sponsor_party  TEXT,
            source_url     TEXT,
            fetched_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bill_number, session)
        );
        CREATE INDEX IF NOT EXISTS idx_gov_action_session
            ON governor_actions(session, action);
        CREATE INDEX IF NOT EXISTS idx_gov_action_date
            ON governor_actions(action_date);
    """)
    conn.commit()


def run(sessions: list[str] | None = None) -> int:
    if sessions is None:
        sessions = ["2025", "2026"]

    if not os.path.exists(OPENSTATES_DB):
        print(f"[skip] openstates_va.db not found at {OPENSTATES_DB}")
        return 0

    os_conn = sqlite3.connect(OPENSTATES_DB)
    os_conn.row_factory = sqlite3.Row

    ph = ",".join("?" for _ in sessions)
    rows = os_conn.execute(
        f"""
        SELECT bill_id, session, title, latest_action, latest_date,
               sponsors, openstates_url
        FROM bills
        WHERE session IN ({ph})
          AND (
               LOWER(latest_action) LIKE '%governor%'
            OR LOWER(latest_action) LIKE '%chapter%'
            OR LOWER(latest_action) LIKE '%veto%'
            OR LOWER(latest_action) LIKE '%enrolled%'
            OR LOWER(latest_action) LIKE '%presented to governor%'
          )
        """,
        sessions,
    ).fetchall()
    os_conn.close()

    print(f"Found {len(rows)} bills with governor actions in openstates_va.db")

    polls_conn = sqlite3.connect(POLLS_DB)
    _ensure_table(polls_conn)

    # Build a party lookup from openstates legislators table if available
    party_map: dict[str, str] = {}
    try:
        leg_rows = sqlite3.connect(OPENSTATES_DB).execute(
            "SELECT name, party FROM legislators"
        ).fetchall()
        for name, party in leg_rows:
            party_map[name.strip().lower()] = party or ""
    except Exception:
        pass

    count = 0
    for row in rows:
        action_key = _classify(row["latest_action"])
        if action_key == "other":
            continue

        label = ACTION_LABELS.get(action_key, "Other governor action")
        chapter = _parse_chapter(row["latest_action"])
        eff_date = _parse_effective_date(row["latest_action"])
        governor = _governor_for_session(row["session"])
        primary_sponsor = (row["sponsors"] or "").split(",")[0].strip()
        sponsor_party = party_map.get(primary_sponsor.lower(), "")
        source_url = row["openstates_url"] or ""
        action_date = row["latest_date"] or ""

        polls_conn.execute(
            """
            INSERT INTO governor_actions (
                bill_number, session, title, raw_status,
                action, action_key, action_label, action_date,
                chapter_number, effective_date, governor,
                sponsor_name, sponsor_party, source_url, fetched_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(bill_number, session) DO UPDATE SET
                raw_status     = excluded.raw_status,
                action         = excluded.action,
                action_key     = excluded.action_key,
                action_label   = excluded.action_label,
                action_date    = excluded.action_date,
                chapter_number = excluded.chapter_number,
                effective_date = excluded.effective_date,
                governor       = excluded.governor,
                sponsor_name   = excluded.sponsor_name,
                sponsor_party  = excluded.sponsor_party,
                source_url     = excluded.source_url,
                fetched_at     = datetime('now')
            """,
            (
                row["bill_id"], row["session"], row["title"], row["latest_action"],
                action_key, action_key, label, action_date,
                chapter, eff_date, governor,
                primary_sponsor, sponsor_party, source_url,
            ),
        )
        count += 1

    polls_conn.commit()
    polls_conn.close()
    print(f"Upserted {count} governor action records into polls.db")
    return count


if __name__ == "__main__":
    run(sessions=["2025", "2026"])
