#!/usr/bin/env python3
"""
generate_legislators_seed.py
Exports pre-aggregated VA legislator data into data/legislators_seed.sql.

Sources:
  openstates_va.db  — votes (individual per-legislator), bills, legislators
  polls.db          — legiscan_va_bill_sponsors, legiscan_va_bills,
                      campaign_finance_summary

Run once locally; commit the SQL file. Never runs on Render.
"""
from __future__ import annotations
import sqlite3
import os
from pathlib import Path
from collections import defaultdict

BASE_DIR  = Path(__file__).resolve().parent
OS_DB     = BASE_DIR / "openstates_va.db"
POLLS_DB  = BASE_DIR / "polls.db"
SEED_OUT  = BASE_DIR / "data" / "legislators_seed.sql"
SESSION   = "2026"
RECENT_VOTES_LIMIT = 30


def esc(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def main():
    if not OS_DB.exists():
        print(f"ERROR: {OS_DB} not found")
        return
    if not POLLS_DB.exists():
        print(f"ERROR: {POLLS_DB} not found")
        return

    os_conn = sqlite3.connect(str(OS_DB))
    os_conn.row_factory = sqlite3.Row
    p_conn = sqlite3.connect(str(POLLS_DB))
    p_conn.row_factory = sqlite3.Row

    # ── 1. All distinct legislators from votes (use votes table — most complete) ──
    print("Building legislator roster from votes...")
    legislator_meta = {}  # name -> {chamber, party}

    # Get chamber/party from the votes table itself (most populated source)
    for r in os_conn.execute(f"""
        SELECT voter_name,
               MAX(CASE WHEN chamber != '' THEN chamber END) AS chamber,
               MAX(CASE WHEN party   != '' THEN party   END) AS party
        FROM votes
        WHERE session = '{SESSION}'
        GROUP BY voter_name
    """).fetchall():
        legislator_meta[r["voter_name"]] = {
            "chamber": r["chamber"] or "",
            "party":   r["party"]   or "",
        }

    # Supplement from legislators table
    for r in os_conn.execute("SELECT name, chamber, party, district FROM legislators").fetchall():
        if r["name"] not in legislator_meta:
            legislator_meta[r["name"]] = {"chamber": r["chamber"] or "", "party": r["party"] or ""}
        else:
            meta = legislator_meta[r["name"]]
            if not meta["chamber"] and r["chamber"]:
                meta["chamber"] = r["chamber"]
            if not meta["party"] and r["party"]:
                meta["party"] = r["party"]

    print(f"  {len(legislator_meta)} legislators found")

    # ── 2. Vote summaries (2026 session) ─────────────────────────────────────────
    print("Computing vote summaries...")
    vote_summary_rows = os_conn.execute(f"""
        SELECT voter_name,
               SUM(CASE WHEN option = 'yes'         THEN 1 ELSE 0 END) AS yes_count,
               SUM(CASE WHEN option = 'no'          THEN 1 ELSE 0 END) AS no_count,
               SUM(CASE WHEN option = 'not voting'  THEN 1 ELSE 0 END) AS not_voting,
               SUM(CASE WHEN option = 'abstain'     THEN 1 ELSE 0 END) AS abstain,
               COUNT(*) AS total_votes
        FROM votes
        WHERE session = '{SESSION}'
        GROUP BY voter_name
    """).fetchall()
    print(f"  {len(vote_summary_rows)} legislators with vote data")

    # ── 3. Recent votes per legislator (with bill titles) ────────────────────────
    print(f"Fetching {RECENT_VOTES_LIMIT} most recent votes per legislator...")
    recent_votes: list[tuple] = []
    unique_names = [r["voter_name"] for r in vote_summary_rows]
    for i, name in enumerate(unique_names):
        if i % 20 == 0:
            print(f"  {i}/{len(unique_names)}...")
        rows = os_conn.execute(f"""
            SELECT v.voter_name, v.bill_id, v.vote_date, v.option,
                   v.chamber, v.motion,
                   COALESCE(b.title, v.bill_id) AS title
            FROM votes v
            LEFT JOIN bills b ON v.bill_id = b.bill_id AND v.session = b.session
            WHERE v.session = '{SESSION}'
              AND v.voter_name = ?
            ORDER BY v.vote_date DESC
            LIMIT {RECENT_VOTES_LIMIT}
        """, (name,)).fetchall()
        for r in rows:
            recent_votes.append((
                r["voter_name"], r["bill_id"], r["vote_date"],
                r["option"], r["chamber"], r["motion"], r["title"],
            ))
    print(f"  {len(recent_votes)} recent vote rows total")

    # ── 4. Sponsored bills (primary, order=1) ────────────────────────────────────
    print("Fetching sponsored bills (primary sponsors)...")
    sponsored = p_conn.execute(f"""
        SELECT s.legislator_name, s.bill_id, s.session,
               b.bill_number, b.title, b.status_label, b.subject, b.status_date
        FROM legiscan_va_bill_sponsors s
        JOIN legiscan_va_bills b ON s.bill_id = b.bill_id
        WHERE s.session = '{SESSION}'
          AND s.sponsor_order = 1
        ORDER BY s.legislator_name, b.status_date DESC
    """).fetchall()
    print(f"  {len(sponsored)} primary-sponsored bill rows")

    # ── 5. Co-sponsored bills (order >= 2) ───────────────────────────────────────
    print("Fetching co-sponsored bills...")
    cosponsored = p_conn.execute(f"""
        SELECT s.legislator_name, s.bill_id, s.session, s.sponsor_order,
               b.bill_number, b.title, b.status_label, b.subject, b.status_date
        FROM legiscan_va_bill_sponsors s
        JOIN legiscan_va_bills b ON s.bill_id = b.bill_id
        WHERE s.session = '{SESSION}'
          AND s.sponsor_order >= 2
        ORDER BY s.legislator_name, b.status_date DESC
    """).fetchall()
    print(f"  {len(cosponsored)} co-sponsored bill rows")

    os_conn.close()
    p_conn.close()

    # ── Write SQL seed ────────────────────────────────────────────────────────────
    print(f"\nWriting {SEED_OUT}...")
    lines = [
        "-- legislators_seed.sql",
        "-- Pre-aggregated VA legislator data: vote summaries, recent votes,",
        "-- sponsored/co-sponsored bills.",
        "-- Sources: openstates_va.db (votes, bills), polls.db (legiscan tables)",
        "-- DO NOT EDIT BY HAND -- regenerate with generate_legislators_seed.py",
        "",
        "-- ── va_legislator_vote_summary ──────────────────────────────────────────────",
        "CREATE TABLE IF NOT EXISTS va_legislator_vote_summary (",
        "    voter_name   TEXT,",
        "    session      TEXT,",
        "    chamber      TEXT,",
        "    party        TEXT,",
        "    yes_count    INTEGER DEFAULT 0,",
        "    no_count     INTEGER DEFAULT 0,",
        "    not_voting   INTEGER DEFAULT 0,",
        "    abstain      INTEGER DEFAULT 0,",
        "    total_votes  INTEGER DEFAULT 0,",
        "    PRIMARY KEY (voter_name, session)",
        ");",
        f"DELETE FROM va_legislator_vote_summary WHERE session = '{SESSION}';",
        "",
    ]
    for r in vote_summary_rows:
        name = r["voter_name"]
        meta = legislator_meta.get(name, {"chamber": "", "party": ""})
        yes_rate = round(int(r["yes_count"] or 0) / max(int(r["total_votes"] or 1), 1) * 100, 1)
        lines.append(
            f"INSERT INTO va_legislator_vote_summary VALUES("
            f"{esc(name)},{esc(SESSION)},"
            f"{esc(meta['chamber'])},{esc(meta['party'])},"
            f"{r['yes_count'] or 0},{r['no_count'] or 0},"
            f"{r['not_voting'] or 0},{r['abstain'] or 0},"
            f"{r['total_votes'] or 0});"
        )

    lines += [
        "",
        "-- ── va_legislator_recent_votes ──────────────────────────────────────────────",
        "CREATE TABLE IF NOT EXISTS va_legislator_recent_votes (",
        "    voter_name TEXT,",
        "    session    TEXT,",
        "    bill_id    TEXT,",
        "    vote_date  TEXT,",
        "    option     TEXT,",
        "    chamber    TEXT,",
        "    motion     TEXT,",
        "    title      TEXT",
        ");",
        f"DELETE FROM va_legislator_recent_votes WHERE session = '{SESSION}';",
        "",
    ]
    for (name, bill_id, vote_date, option, chamber, motion, title) in recent_votes:
        # Truncate title to 120 chars to keep seed manageable
        title_short = (title or "")[:120]
        lines.append(
            f"INSERT INTO va_legislator_recent_votes VALUES("
            f"{esc(name)},{esc(SESSION)},{esc(bill_id)},{esc(vote_date)},"
            f"{esc(option)},{esc(chamber)},{esc(motion)},{esc(title_short)});"
        )

    lines += [
        "",
        "-- ── va_legislator_sponsored_bills ───────────────────────────────────────────",
        "CREATE TABLE IF NOT EXISTS va_legislator_sponsored_bills (",
        "    legislator_name TEXT,",
        "    session         TEXT,",
        "    bill_id         TEXT,",
        "    bill_number     TEXT,",
        "    title           TEXT,",
        "    status_label    TEXT,",
        "    subject         TEXT,",
        "    status_date     TEXT",
        ");",
        f"DELETE FROM va_legislator_sponsored_bills WHERE session = '{SESSION}';",
        "",
    ]
    for r in sponsored:
        lines.append(
            f"INSERT INTO va_legislator_sponsored_bills VALUES("
            f"{esc(r['legislator_name'])},{esc(r['session'])},{esc(r['bill_id'])},"
            f"{esc(r['bill_number'])},{esc(r['title'])},{esc(r['status_label'])},"
            f"{esc(r['subject'])},{esc(r['status_date'])});"
        )

    lines += [
        "",
        "-- ── va_legislator_cosponsor_bills ───────────────────────────────────────────",
        "CREATE TABLE IF NOT EXISTS va_legislator_cosponsor_bills (",
        "    legislator_name TEXT,",
        "    session         TEXT,",
        "    bill_id         TEXT,",
        "    bill_number     TEXT,",
        "    title           TEXT,",
        "    status_label    TEXT,",
        "    subject         TEXT,",
        "    sponsor_order   INTEGER",
        ");",
        f"DELETE FROM va_legislator_cosponsor_bills WHERE session = '{SESSION}';",
        "",
    ]
    for r in cosponsored:
        lines.append(
            f"INSERT INTO va_legislator_cosponsor_bills VALUES("
            f"{esc(r['legislator_name'])},{esc(r['session'])},{esc(r['bill_id'])},"
            f"{esc(r['bill_number'])},{esc(r['title'])},{esc(r['status_label'])},"
            f"{esc(r['subject'])},{r['sponsor_order'] or 2});"
        )

    lines.append("")
    SEED_OUT.write_text("\n".join(lines), encoding="utf-8")

    size_mb = SEED_OUT.stat().st_size / 1_048_576
    print(f"\nDone.")
    print(f"  File:                  {SEED_OUT}")
    print(f"  Size:                  {size_mb:.1f} MB")
    print(f"  vote_summary rows:     {len(vote_summary_rows)}")
    print(f"  recent_vote rows:      {len(recent_votes)}")
    print(f"  sponsored_bill rows:   {len(sponsored)}")
    print(f"  cosponsor_bill rows:   {len(cosponsored)}")


if __name__ == "__main__":
    main()
