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
DATA_DIR = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else BASE_DIR
OPENSTATES_DB = os.environ.get("OPENSTATES_DB", str(DATA_DIR / "openstates_va.db"))
POLLS_DB = os.environ.get("POLLS_DB", str(DATA_DIR / "polls.db"))

ACTION_LABELS = {
    "signed":          "Signed into law",
    "vetoed":          "Vetoed",
    "pocket_veto":     "Pocket veto",
    "amended":         "Amended / returned",
    "veto_overridden": "Veto overridden",
    "veto_sustained":  "Veto sustained",
    "pending":         "Pending governor action",
}


OFFICIAL_2026_VETO_OVERRIDES = {
    # April regular-session vetoes already appear correctly in OpenStates latest
    # snapshots on some builds, but keeping them here makes the outcome stable.
    "HB1288": ("2026-04-13", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Enforcement of vehicle liens; increases property value."),
    "SB17": ("2026-04-13", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Enforcement of vehicle liens; increases property value."),
    "HB86": ("2026-04-13", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Mattress Stewardship Program; established, definitions, report."),
    "SB764": ("2026-04-13", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Defendant; deferred disposition in a criminal case, license suspension."),
    "HB637": ("2026-04-14", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Possession of residue of a controlled substance unlawful; penalties exceptions."),
    "SB23": ("2026-04-13", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Plea agreements and court orders; prohibited provisions."),
    "SB661": ("2026-04-13", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Va. Small Business Economic Dev. Act; established, regulation and taxation of skill game machines."),
    "SB756": ("2026-04-11", "https://www.governor.virginia.gov/newsroom/news-releases/2026/april-releases/name-1116393-en.html", "Casino gaming; eligible host localities."),

    # Post-reconvene vetoes in the Governor's May 19, 2026 release.
    "HB61": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Small SWaM Business Procurement Enhancement Program; established, report."),
    "HB111": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Voter registration; cancellation of registration, sources of data."),
    "HB246": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Mental illness, neurocognitive disorder, etc.; affirmative defense or reduced penalty."),
    "SB335": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Mental illness, neurocognitive disorder, etc.; affirmative defense or reduced penalty."),
    "HB449": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Civil actions filed on behalf of multiple persons; class actions."),
    "SB229": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Civil actions filed on behalf of multiple persons; class actions."),
    "HB483": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Prescription Drug Affordability Board; established."),
    "SB271": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Prescription Drug Affordability Board; established."),
    "HB642": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Cannabis control; establishes framework for creation of retail marijuana market, penalties, report."),
    "SB542": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Cannabis control; establishes framework for creation of retail marijuana market, penalties, report."),
    "HB1173": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Virginia Human Rights Act; reasonable accommodation for known limitations related to menopause."),
    "SB258": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Virginia Human Rights Act; reasonable accommodation for known limitations related to menopause."),
    "HB1222": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Social services, local departments of; child abuse and neglect, recorded interviews."),
    "HB1385": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Higher educational institutions, public; membership of governing boards."),
    "SB494": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Higher educational institutions, public; membership of governing boards."),
    "HB1392": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Correctional facilities, local and regional, and courthouse security; powers & duties for operation."),
    "SB83": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "District or circuit court; possession of portable electronic devices."),
    "SB218": ("2026-05-19", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Inmates; Director of Dept. of Corrections shall continue to accept applications for confinement."),
    "HB1263": ("2026-05-14", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Public employees; repeals existing prohibition on collective bargaining, etc."),
    "SB378": ("2026-05-14", "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html", "Public employees; repeals existing prohibition on collective bargaining, etc."),
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(governor_actions)").fetchall()}
    migrations = {
        "sponsor_lis_id": "ALTER TABLE governor_actions ADD COLUMN sponsor_lis_id TEXT",
    }
    for column, ddl in migrations.items():
        if column not in columns:
            conn.execute(ddl)
    conn.commit()


def _bill_lookup(conn: sqlite3.Connection, bill_number: str, session: str) -> sqlite3.Row | None:
    try:
        return conn.execute(
            """
            SELECT bill_number, session, title, status, introduced_date
            FROM va_bills
            WHERE bill_number = ? AND session = ?
            LIMIT 1
            """,
            (bill_number, session),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _apply_official_veto_overrides(conn: sqlite3.Connection, sessions: list[str]) -> int:
    if "2026" not in {str(s) for s in sessions}:
        return 0

    count = 0
    conn.row_factory = sqlite3.Row
    for bill_number, (action_date, source_url, fallback_title) in OFFICIAL_2026_VETO_OVERRIDES.items():
        bill = _bill_lookup(conn, bill_number, "2026")
        title = bill["title"] if bill else fallback_title
        prior_status = bill["status"] if bill else ""
        existing = conn.execute(
            """
            SELECT title, raw_status, source_url
            FROM governor_actions
            WHERE bill_number = ? AND session = '2026'
            LIMIT 1
            """,
            (bill_number,),
        ).fetchone()
        if existing:
            title = title or existing["title"] or ""
            prior_status = prior_status or existing["raw_status"] or ""
            source_url = existing["source_url"] or source_url

        raw_status = "Vetoed by Governor"
        if prior_status and "veto" not in prior_status.lower():
            raw_status = f"Vetoed by Governor; previous status: {prior_status}"

        conn.execute(
            """
            INSERT INTO governor_actions (
                bill_number, session, title, raw_status,
                action, action_key, action_label, action_date,
                chapter_number, effective_date, governor,
                sponsor_name, sponsor_party, source_url, fetched_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(bill_number, session) DO UPDATE SET
                title          = COALESCE(NULLIF(excluded.title, ''), governor_actions.title),
                raw_status     = excluded.raw_status,
                action         = excluded.action,
                action_key     = excluded.action_key,
                action_label   = excluded.action_label,
                action_date    = excluded.action_date,
                chapter_number = '',
                effective_date = '',
                governor       = excluded.governor,
                source_url     = excluded.source_url,
                fetched_at     = datetime('now')
            """,
            (
                bill_number, "2026", title, raw_status,
                "vetoed", "vetoed", ACTION_LABELS["vetoed"], action_date,
                "", "", "Spanberger",
                "", "", source_url,
            ),
        )
        count += 1

    return count


def run(sessions: list[str] | None = None) -> int:
    if sessions is None:
        sessions = ["2025", "2026"]

    polls_conn = sqlite3.connect(POLLS_DB)
    _ensure_table(polls_conn)

    if not os.path.exists(OPENSTATES_DB):
        print(f"[skip] openstates_va.db not found at {OPENSTATES_DB}")
        override_count = _apply_official_veto_overrides(polls_conn, sessions)
        polls_conn.commit()
        polls_conn.close()
        print(f"Applied {override_count} official 2026 veto overrides")
        return override_count

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
    override_count = _apply_official_veto_overrides(polls_conn, sessions)
    polls_conn.commit()
    polls_conn.close()
    print(f"Applied {override_count} official 2026 veto overrides")
    print(f"Upserted {count} governor action records into polls.db")
    return count + override_count


if __name__ == "__main__":
    run(sessions=["2025", "2026"])
