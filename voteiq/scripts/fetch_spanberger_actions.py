"""
voteiq/scripts/fetch_spanberger_actions.py

Extracts governor actions from va_bills and loads them
into governor_actions for cross-session analysis.

Governor mapping:
  session < '2026'  → Youngkin
  session >= '2026' → Spanberger
"""
import re
import sqlite3

DB = "legislative_intelligence.db"

ACTION_LABELS = {
    "signed": "Signed into law",
    "vetoed": "Vetoed",
    "pocket_veto": "Pocket veto",
    "amended": "Amended / returned",
    "veto_overridden": "Veto overridden",
    "veto_sustained": "Veto sustained",
    "pending": "Pending governor action",
}

ACTION_ORDER = [
    "signed",
    "amended",
    "vetoed",
    "pocket_veto",
    "veto_overridden",
    "veto_sustained",
    "pending",
]

SUMMARY_LABELS = {
    **ACTION_LABELS,
    "pending": "Pending action",
}


# ── GOVERNOR LOOKUP ───────────────────────────────────────────────────────────

def get_governor_for_session(session: str) -> str:
    if session >= "2026":
        return "Spanberger"
    return "Youngkin"


# ── TABLE SETUP ───────────────────────────────────────────────────────────────

def create_governor_actions_table():
    conn = sqlite3.connect(DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS governor_actions (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number          TEXT,
            session              TEXT,
            title                TEXT,
            raw_status           TEXT,
            action               TEXT,
            action_key           TEXT,
            action_label         TEXT,
            action_date          TEXT,
            chapter_number       TEXT,
            effective_date       TEXT,
            governor             TEXT,
            sponsor_lis_id       TEXT,
            sponsor_name         TEXT,
            sponsor_party        TEXT,
            source_url           TEXT,
            fetched_at           TEXT DEFAULT CURRENT_TIMESTAMP,
            override_attempted   INTEGER DEFAULT 0,
            override_succeeded   INTEGER DEFAULT 0,
            override_vote_house  TEXT,
            override_vote_senate TEXT,
            override_date        TEXT,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bill_number, session)
        );

        CREATE INDEX IF NOT EXISTS idx_gov_action_session
            ON governor_actions(session, action);

        CREATE INDEX IF NOT EXISTS idx_gov_action_sponsor
            ON governor_actions(sponsor_lis_id);
    """)

    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(governor_actions)").fetchall()
    }
    migrations = {
        "action_label": "ALTER TABLE governor_actions ADD COLUMN action_label TEXT",
        "action_date": "ALTER TABLE governor_actions ADD COLUMN action_date TEXT",
        "source_url": "ALTER TABLE governor_actions ADD COLUMN source_url TEXT",
        "fetched_at": "ALTER TABLE governor_actions ADD COLUMN fetched_at TEXT",
    }
    for column, ddl in migrations.items():
        if column not in columns:
            conn.execute(ddl)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gov_action_session_key
            ON governor_actions(session, action)
    """)

    conn.commit()
    conn.close()
    print("Governor actions table ready")


# ── CLASSIFIERS ───────────────────────────────────────────────────────────────

def classify_action(status: str) -> str:
    s = (status or "").lower()

    # Override/sustained — check before generic veto
    if any(t in s for t in [
        "passed over veto",
        "veto overridden",
        "overridden",
    ]):
        return "veto_overridden"

    if any(t in s for t in [
        "veto sustained",
        "sustained governor",   # "House sustained Governor's veto"
        "override failed",
    ]):
        return "veto_sustained"

    # Pocket veto before generic veto
    if "pocket" in s and "veto" in s:
        return "pocket_veto"

    # Governor amendments / recommendations
    if any(t in s for t in [
        "recommendation received",
        "governor's recommendation",
        "governor recommendation",
        "amendment",
    ]):
        return "amended"

    # Generic veto
    if "veto" in s:
        return "vetoed"

    # Signed / enacted
    if any(t in s for t in [
        "chapter",
        "approved by governor",
        "signed by governor",
        "enacted",
    ]):
        return "signed"

    # Pending governor action
    if any(t in s for t in [
        "sent to governor",
        "enrolled",
        "presented to governor",
    ]):
        return "pending"

    return "other"


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, "Other governor action")


def parse_source_url(description: str) -> str:
    match = re.search(r"Source:\s*(https?://\S+)", description or "")
    return match.group(1).rstrip(").,") if match else ""


def parse_chapter_number(status: str) -> str:
    match = re.search(r'chapter\s+(\d+)', (status or "").lower())
    return match.group(1) if match else ""


def parse_effective_date(status: str) -> str:
    match = re.search(r'effective\s+([\d/]+)', (status or "").lower())
    return match.group(1) if match else ""


# ── LOAD ──────────────────────────────────────────────────────────────────────

def load_governor_actions(sessions: list = None) -> int:
    if sessions is None:
        sessions = ["2025", "2026"]

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    placeholders = ",".join("?" for _ in sessions)
    cur.execute(f"""
        SELECT
            b.bill_number,
            b.session,
            b.title,
            b.description,
            b.introduced_date    AS action_date,
            b.status,
            b.introduced_by     AS sponsor_lis_id,
            l.name              AS sponsor_name,
            l.party             AS sponsor_party
        FROM va_bills b
        LEFT JOIN legislators l ON b.introduced_by = l.lis_id
        WHERE b.session IN ({placeholders})
        AND (
            LOWER(b.status) LIKE '%governor%'
            OR LOWER(b.status) LIKE '%chapter%'
            OR LOWER(b.status) LIKE '%veto%'
            OR LOWER(b.status) LIKE '%override%'
            OR LOWER(b.status) LIKE '%enacted%'
            OR LOWER(b.status) LIKE '%recommendation%'
            OR LOWER(b.status) LIKE '%sent to governor%'
            OR LOWER(b.status) LIKE '%enrolled%'
            OR LOWER(b.status) LIKE '%presented to governor%'
        )
    """, sessions)

    bills = cur.fetchall()
    print(f"Found {len(bills)} bills with governor actions")

    count = 0
    for bill in bills:
        key       = classify_action(bill["status"])
        label     = action_label(key)
        chapter   = parse_chapter_number(bill["status"])
        eff_date  = parse_effective_date(bill["status"])
        governor  = get_governor_for_session(bill["session"])
        source_url = parse_source_url(bill["description"])

        cur.execute("""
            INSERT INTO governor_actions (
                bill_number, session, title, raw_status,
                action, action_key, action_label, action_date,
                chapter_number, effective_date, governor,
                sponsor_lis_id, sponsor_name, sponsor_party,
                source_url, fetched_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(bill_number, session) DO UPDATE SET
                raw_status     = excluded.raw_status,
                action         = excluded.action,
                action_key     = excluded.action_key,
                action_label   = excluded.action_label,
                action_date    = excluded.action_date,
                chapter_number = excluded.chapter_number,
                effective_date = excluded.effective_date,
                governor       = excluded.governor,
                sponsor_lis_id = excluded.sponsor_lis_id,
                sponsor_name   = excluded.sponsor_name,
                sponsor_party  = excluded.sponsor_party,
                source_url     = excluded.source_url,
                fetched_at     = datetime('now'),
                created_at     = CURRENT_TIMESTAMP
        """, (
            bill["bill_number"], bill["session"], bill["title"],
            bill["status"], key, key, label, bill["action_date"], chapter, eff_date, governor,
            bill["sponsor_lis_id"], bill["sponsor_name"], bill["sponsor_party"],
            source_url,
        ))
        count += cur.rowcount

    conn.commit()
    conn.close()
    print(f"Upserted {count} governor actions")
    return count


# ── SUMMARY ───────────────────────────────────────────────────────────────────

def get_action_summary(sessions: list = None) -> dict:
    if sessions is None:
        sessions = ["2025", "2026"]

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ph = ",".join("?" for _ in sessions)

    cur.execute(f"""
        SELECT action, action_label, COUNT(*) AS count,
               COUNT(DISTINCT sponsor_lis_id) AS unique_sponsors,
               COUNT(DISTINCT sponsor_party)  AS parties
        FROM governor_actions
        WHERE session IN ({ph})
        GROUP BY action, action_label ORDER BY count DESC
    """, sessions)
    action_count_rows = {r["action"]: dict(r) for r in cur.fetchall()}
    action_counts = []
    for action in ACTION_ORDER:
        row = action_count_rows.get(action, {})
        action_counts.append({
            "action": action,
            "action_label": SUMMARY_LABELS.get(action, action_label(action)),
            "count": row.get("count", 0),
            "unique_sponsors": row.get("unique_sponsors", 0),
            "parties": row.get("parties", 0),
        })

    cur.execute(f"""
        SELECT sponsor_party, action, action_label, COUNT(*) AS count
        FROM governor_actions
        WHERE session IN ({ph}) AND sponsor_party IS NOT NULL
        GROUP BY sponsor_party, action, action_label ORDER BY count DESC
    """, sessions)
    party_breakdown = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT bill_number, title, action, action_label,
               override_vote_house, override_vote_senate,
               override_date, sponsor_name, sponsor_party
        FROM governor_actions
        WHERE session IN ({ph})
        AND action IN ('veto_overridden', 'veto_sustained')
        ORDER BY override_date DESC
    """, sessions)
    overrides = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT sponsor_name, sponsor_party, COUNT(*) AS veto_count
        FROM governor_actions
        WHERE session IN ({ph})
        AND action IN ('vetoed', 'veto_sustained', 'veto_overridden', 'pocket_veto')
        GROUP BY sponsor_name, sponsor_party ORDER BY veto_count DESC LIMIT 10
    """, sessions)
    most_vetoed = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT sponsor_name, sponsor_party, COUNT(*) AS signed_count
        FROM governor_actions
        WHERE session IN ({ph}) AND action = 'signed'
        GROUP BY sponsor_name, sponsor_party ORDER BY signed_count DESC LIMIT 10
    """, sessions)
    most_signed = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT effective_date, COUNT(*) AS count
        FROM governor_actions
        WHERE session IN ({ph}) AND action = 'signed' AND effective_date != ''
        GROUP BY effective_date ORDER BY count DESC LIMIT 10
    """, sessions)
    effective_dates = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {
        "action_counts":   action_counts,
        "party_breakdown": party_breakdown,
        "overrides":       overrides,
        "most_vetoed":     most_vetoed,
        "most_signed":     most_signed,
        "effective_dates": effective_dates,
        "has_overrides":   len(overrides) > 0,
    }


# ── PIPELINE ──────────────────────────────────────────────────────────────────

def run_veto_tracker_pipeline(sessions: list = None):
    if sessions is None:
        sessions = ["2025", "2026"]

    print("Governor Veto Tracker")
    print("=" * 40)

    create_governor_actions_table()
    load_governor_actions(sessions)

    summary = get_action_summary(sessions)

    print("\nSummary:")
    for a in summary["action_counts"]:
        print(
            f"  {a['action_label']:<30} "
            f"{a['count']:>4} bills "
            f"({a['unique_sponsors']} sponsors)"
        )

    if summary["has_overrides"]:
        print("\nOVERRIDE ACTIVITY:")
        for o in summary["overrides"]:
            print(f"  {o['bill_number']} - {o['action'].upper()}")
    else:
        print("\nNo override attempts recorded")

    return summary


SESSIONS_TO_REFRESH = ["2026"]

if __name__ == "__main__":
    summary = run_veto_tracker_pipeline(
        sessions=SESSIONS_TO_REFRESH
    )

    print("Weekly governor action tracker updated")
    print(f"Sessions refreshed: {', '.join(SESSIONS_TO_REFRESH)}")
    print(f"Action categories: {len(summary.get('action_counts', []))}")
