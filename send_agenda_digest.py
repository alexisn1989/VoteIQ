"""
send_agenda_digest.py

Scans all 14 Council/Planning-Commission upcoming-agenda tables for items not
yet sent to each confirmed agenda_alert_subscriptions row, matches by radius
(haversine, only for tables that carry lat/lng), city, and/or keyword, and
emails a digest via Resend for anyone with at least one new match.

A subscription matches an item when:
  - its `cities` filter (if any) includes the item's city, AND
  - it has an address+radius and the item falls inside that radius, OR
  - it has keywords and one appears in the item's title, OR
  - it has neither (city-only subscription) — everything in-city matches.

Already-sent items are tracked per (subscription, table, item id) in
agenda_alert_sent so re-running never double-sends.

Run by the Render cron job "voteiq-agenda-digest" (POSTs to
/api/agenda-alerts/run-digest on the running web service) or directly:
    python send_agenda_digest.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from voteiq.services.email import send_email
from voteiq.services.geo_lite import haversine_miles

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
SERVICE_URL = os.getenv("VOTEIQ_SERVICE_URL", "https://voteiq.io").rstrip("/")

# (table, city_display). lat/lng presence varies per table (checked
# dynamically in _fetch_candidates) -- newport_news/portsmouth/norfolk/vb's
# council-level tables predate the geocoding backfill; their PC counterparts
# do have it.
TABLES: list[tuple[str, str]] = [
    ("chesapeake_upcoming_agenda", "Chesapeake"),
    ("chesapeake_pc_upcoming_agenda", "Chesapeake"),
    ("suffolk_upcoming_agenda", "Suffolk"),
    ("suffolk_pc_upcoming_agenda", "Suffolk"),
    ("hampton_upcoming_agenda", "Hampton"),
    ("hampton_pc_upcoming_agenda", "Hampton"),
    ("newport_news_upcoming_agenda", "Newport News"),
    ("newport_news_pc_upcoming_agenda", "Newport News"),
    ("portsmouth_upcoming_agenda", "Portsmouth"),
    ("portsmouth_pc_upcoming_agenda", "Portsmouth"),
    ("norfolk_upcoming_agenda", "Norfolk"),
    ("norfolk_pc_upcoming_agenda", "Norfolk"),
    ("vb_upcoming_agenda", "Virginia Beach"),
    ("vb_pc_upcoming_agenda", "Virginia Beach"),
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _fetch_candidates(conn: sqlite3.Connection, table: str) -> list[dict]:
    # Column presence varies slightly per city (e.g. vb_upcoming_agenda has no
    # agenda_url) -- select only what actually exists, matching the
    # defensive PRAGMA table_info pattern used in _add_pc_agenda_context.
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    wanted = ["id", "meeting_date", "item_ref", "title", "agenda_url", "lat", "lng"]
    select_cols = [c for c in wanted if c in existing]
    rows = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM {table} ORDER BY meeting_date"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for c in ("agenda_url", "lat", "lng"):
            d.setdefault(c, None)
        out.append(d)
    return out


def _already_sent(conn: sqlite3.Connection, sub_id: int, table: str) -> set[int]:
    rows = conn.execute(
        "SELECT item_id FROM agenda_alert_sent WHERE subscription_id=? AND source_table=?",
        (sub_id, table),
    ).fetchall()
    return {r[0] for r in rows}


def _item_matches(item: dict, sub: sqlite3.Row, city: str) -> bool:
    if sub["cities"]:
        allowed = {c.strip() for c in sub["cities"].split(",")}
        if city not in allowed:
            return False

    radius_hit = False
    if sub["lat"] is not None and item.get("lat") is not None:
        dist = haversine_miles(sub["lat"], sub["lng"], item["lat"], item["lng"])
        radius_hit = dist <= (sub["radius_miles"] or 2.0)

    keyword_hit = False
    if sub["keywords"]:
        terms = [k.strip().lower() for k in sub["keywords"].split(",") if k.strip()]
        title = (item.get("title") or "").lower()
        keyword_hit = any(t in title for t in terms)

    if sub["lat"] is not None and sub["keywords"]:
        return radius_hit or keyword_hit
    if sub["lat"] is not None:
        return radius_hit
    if sub["keywords"]:
        return keyword_hit
    # Neither address nor keywords -- the city filter above is the only gate
    return True


def run(dry_run: bool = False) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agenda_alert_sent (
            subscription_id INTEGER NOT NULL,
            source_table    TEXT NOT NULL,
            item_id         INTEGER NOT NULL,
            sent_at         TEXT DEFAULT (datetime('now')),
            UNIQUE(subscription_id, source_table, item_id)
        );
    """)
    conn.commit()

    if not _table_exists(conn, "agenda_alert_subscriptions"):
        print("agenda_alert_subscriptions table doesn't exist yet — nothing to do")
        conn.close()
        return 0

    subs = conn.execute(
        "SELECT * FROM agenda_alert_subscriptions WHERE confirmed=1"
    ).fetchall()
    print(f"{len(subs)} confirmed subscriptions")

    emails_sent = 0
    for sub in subs:
        matches: list[tuple[str, str, dict]] = []  # (table, city, item)
        for table, city in TABLES:
            if not _table_exists(conn, table):
                continue
            items = _fetch_candidates(conn, table)
            sent_ids = _already_sent(conn, sub["id"], table)
            for item in items:
                if item["id"] in sent_ids:
                    continue
                if _item_matches(item, sub, city):
                    matches.append((table, city, item))

        if not matches:
            continue

        print(f"  {sub['email']}: {len(matches)} new matching item(s)")
        rows_html = "".join(
            f"<li><strong>{city}</strong> — {item['meeting_date']}: "
            f"{item['title']} "
            f"(<a href=\"{item['agenda_url']}\">agenda</a>)</li>"
            for table, city, item in matches
        )
        unsub_url = f"{SERVICE_URL}/api/agenda-alerts/unsubscribe?token={sub['unsubscribe_token']}"
        html = (
            f"<p>New agenda items matching your VoteIQ subscription"
            f"{' (' + sub['address'] + ')' if sub['address'] else ''}:</p>"
            f"<ul>{rows_html}</ul>"
            f'<p style="font-size:12px;color:#666"><a href="{unsub_url}">Unsubscribe</a></p>'
        )

        if dry_run:
            print(f"    [dry-run] would email {sub['email']}: {len(matches)} items")
            continue

        result = send_email(sub["email"], "VoteIQ: new agenda items near you", html)
        if result["ok"]:
            emails_sent += 1
            for table, city, item in matches:
                conn.execute(
                    "INSERT OR IGNORE INTO agenda_alert_sent (subscription_id, source_table, item_id) "
                    "VALUES (?, ?, ?)",
                    (sub["id"], table, item["id"]),
                )
            conn.commit()
        else:
            print(f"    send failed: {result['error']}")

    conn.close()
    print(f"\nDone — {emails_sent} digest email(s) sent")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Send VoteIQ agenda-alert digest emails")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
