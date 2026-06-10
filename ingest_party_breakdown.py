#!/usr/bin/env python3
"""
ingest_party_breakdown.py

Fetches per-party vote tallies from official congressional XML feeds for
every unique roll-call vote in congress_votes, then stores results in
congress_vote_party_breakdown and computes party-unity / bipartisan-rate
stats per member into congress_member_party_stats.

Sources (no API key required):
  House:  https://clerk.house.gov/evs/{year}/roll{vote_number}.xml
  Senate: https://www.senate.gov/legislative/LIS/roll_call_votes/
            vote119{session}/vote_119_{session}_{vote_number:05d}.xml

Usage:
    python ingest_party_breakdown.py              # fetch all missing votes
    python ingest_party_breakdown.py --stats-only # skip fetch, recompute stats
    python ingest_party_breakdown.py --limit 50   # fetch at most N votes
    python ingest_party_breakdown.py --dry-run    # show counts, no writes
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
_dd = os.environ.get("DATA_DIR", str(BASE_DIR))
POLLS_DB = Path(_dd if Path(_dd).is_dir() else str(BASE_DIR)) / "polls.db"

HOUSE_URL  = "https://clerk.house.gov/evs/{year}/roll{vote_number}.xml"
SENATE_URL = ("https://www.senate.gov/legislative/LIS/roll_call_votes/"
              "vote119{session}/vote_119_{session}_{vote_number:05d}.xml")

HEADERS = {"User-Agent": "VoteIQ/1.0 (alexisnieuwenhuys89@gmail.com)"}
DELAY   = 0.25   # seconds between requests — be a good citizen
TIMEOUT = 15


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS congress_vote_party_breakdown (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    congress         INTEGER NOT NULL,
    session          INTEGER NOT NULL,
    chamber          TEXT    NOT NULL,
    vote_number      INTEGER NOT NULL,
    vote_date        TEXT,
    vote_question    TEXT,
    vote_desc        TEXT,
    rep_yea          INTEGER DEFAULT 0,
    rep_nay          INTEGER DEFAULT 0,
    rep_present      INTEGER DEFAULT 0,
    dem_yea          INTEGER DEFAULT 0,
    dem_nay          INTEGER DEFAULT 0,
    dem_present      INTEGER DEFAULT 0,
    ind_yea          INTEGER DEFAULT 0,
    ind_nay          INTEGER DEFAULT 0,
    majority_party   TEXT,
    majority_position TEXT,
    fetched_at       TEXT,
    UNIQUE(congress, session, chamber, vote_number)
);
CREATE INDEX IF NOT EXISTS idx_vpb_chamber
    ON congress_vote_party_breakdown(chamber, congress, session, vote_number);

CREATE TABLE IF NOT EXISTS congress_member_party_stats (
    bioguide_id      TEXT PRIMARY KEY,
    name             TEXT,
    party            TEXT,
    chamber          TEXT,
    total_votes      INTEGER DEFAULT 0,
    with_party       INTEGER DEFAULT 0,
    against_party    INTEGER DEFAULT 0,
    not_classified   INTEGER DEFAULT 0,
    party_unity_pct  REAL,
    bipartisan_pct   REAL,
    missed_votes          INTEGER DEFAULT 0,
    missed_votes_pct      REAL,
    missed_substantive    INTEGER DEFAULT 0,
    missed_procedural     INTEGER DEFAULT 0,
    updated_at            TEXT
);
"""

_MIGRATIONS = [
    "ALTER TABLE congress_member_party_stats ADD COLUMN missed_votes INTEGER DEFAULT 0",
    "ALTER TABLE congress_member_party_stats ADD COLUMN missed_votes_pct REAL",
    "ALTER TABLE congress_member_party_stats ADD COLUMN missed_substantive INTEGER DEFAULT 0",
    "ALTER TABLE congress_member_party_stats ADD COLUMN missed_procedural INTEGER DEFAULT 0",
]

# Vote question strings that represent substantive congressional decisions.
# Missing these is newsworthy; missing procedural votes is routine.
_SUBSTANTIVE_QUESTIONS = frozenset({
    "On Passage",
    "On the Nomination",
    "On the Amendment",
    "On Agreeing to the Amendment",
    "On the Joint Resolution",
    "On the Concurrent Resolution",
    "On Retaining Division A",
    "On Retaining Divisions B and C",
})

# Quorum calls and pure housekeeping — virtually never substantive.
_PROCEDURAL_QUESTIONS = frozenset({
    "Call of the House",
    "Call by States",
    "On Motion to Adjourn",
    "On Motion to Table",
    "On Ordering the Previous Question",
    "On Motion to Recommit",
    "On Motion to Refer",
    "On Motion to Discharge",
    "On the Point of Order",
    "On the Motion to Discharge",
})


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists
    conn.commit()


# ── Fetchers ──────────────────────────────────────────────────────────────────

def _get(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except Exception as exc:
        print(f"    WARN fetch {url}: {exc}")
        return None


def parse_house_xml(data: bytes) -> dict | None:
    """Parse House Clerk XML → party breakdown dict."""
    try:
        tree = ET.fromstring(data)
    except ET.ParseError:
        return None
    meta = tree.find("vote-metadata")
    if meta is None:
        return None

    result: dict = {
        "vote_question": (meta.findtext("vote-question") or "").strip(),
        "vote_desc":     (meta.findtext("vote-desc") or "").strip(),
        "rep_yea": 0, "rep_nay": 0, "rep_present": 0,
        "dem_yea": 0, "dem_nay": 0, "dem_present": 0,
        "ind_yea": 0, "ind_nay": 0,
    }

    totals_node = meta.find("vote-totals")
    if totals_node is None:
        return None

    for party_node in totals_node.findall("totals-by-party"):
        party = (party_node.findtext("party") or "").lower()
        yea   = int(party_node.findtext("yea-total")     or 0)
        nay   = int(party_node.findtext("nay-total")     or 0)
        pres  = int(party_node.findtext("present-total") or 0)
        if party == "republican":
            result["rep_yea"] = yea; result["rep_nay"] = nay; result["rep_present"] = pres
        elif party == "democratic":
            result["dem_yea"] = yea; result["dem_nay"] = nay; result["dem_present"] = pres
        elif party == "independent":
            result["ind_yea"] = yea; result["ind_nay"] = nay

    return result


def parse_senate_xml(data: bytes) -> dict | None:
    """Parse Senate XML → party breakdown dict (aggregated from member votes)."""
    try:
        tree = ET.fromstring(data)
    except ET.ParseError:
        return None

    result: dict = {
        "vote_question": (tree.findtext("vote_question_text") or "").strip(),
        "vote_desc":     (tree.findtext("vote_title") or "").strip(),
        "rep_yea": 0, "rep_nay": 0, "rep_present": 0,
        "dem_yea": 0, "dem_nay": 0, "dem_present": 0,
        "ind_yea": 0, "ind_nay": 0,
    }

    members = tree.find("members")
    if members is None:
        return None

    for m in members:
        party = (m.findtext("party") or "").upper()
        vote  = (m.findtext("vote_cast") or "").lower()
        is_yea  = vote in ("yea", "aye", "yes")
        is_nay  = vote in ("nay", "no")
        is_pres = vote == "present"

        if party == "R":
            if is_yea:  result["rep_yea"]     += 1
            elif is_nay: result["rep_nay"]     += 1
            elif is_pres: result["rep_present"] += 1
        elif party == "D":
            if is_yea:  result["dem_yea"]     += 1
            elif is_nay: result["dem_nay"]     += 1
            elif is_pres: result["dem_present"] += 1
        else:
            if is_yea:  result["ind_yea"] += 1
            elif is_nay: result["ind_nay"] += 1

    return result


def _majority_position(yea: int, nay: int) -> str | None:
    """Return 'Yea', 'Nay', or None if tied / no votes."""
    if yea == nay or (yea + nay) == 0:
        return None
    return "Yea" if yea > nay else "Nay"


def _majority_party(rep_yea: int, rep_nay: int,
                    dem_yea: int, dem_nay: int) -> str | None:
    """Return 'R' or 'D' for the party whose majority position won the vote."""
    rep_pos = _majority_position(rep_yea, rep_nay)
    dem_pos = _majority_position(dem_yea, dem_nay)
    if rep_pos and dem_pos and rep_pos != dem_pos:
        # Parties split — whichever had more yeas wins
        return "R" if rep_yea > dem_yea else "D"
    # Parties agree (or one has no data)
    return None


# ── Main fetch loop ───────────────────────────────────────────────────────────

def fetch_missing(conn: sqlite3.Connection,
                  limit: int | None,
                  dry_run: bool) -> int:
    """Fetch party breakdown for votes not yet in congress_vote_party_breakdown."""
    # Get all unique votes from congress_votes
    all_votes = conn.execute("""
        SELECT DISTINCT chamber, congress, session, vote_number, MIN(vote_date) AS vote_date
        FROM congress_votes
        GROUP BY chamber, congress, session, vote_number
    """).fetchall()

    # Get already-fetched vote keys
    fetched = set(
        conn.execute(
            "SELECT chamber, congress, session, vote_number "
            "FROM congress_vote_party_breakdown"
        ).fetchall()
    )

    missing = [r for r in all_votes if (r[0], r[1], r[2], r[3]) not in fetched]
    print(f"Votes total: {len(all_votes)}  already fetched: {len(fetched)}  missing: {len(missing)}")

    if not missing or dry_run:
        return 0

    if limit:
        missing = missing[:limit]
        print(f"  (limited to {limit})")

    now = datetime.now(timezone.utc).isoformat()
    ok = err = 0

    for chamber, congress, session, vote_number, vote_date in missing:
        year = (vote_date or "")[:4] or "2025"

        if chamber == "house":
            url = HOUSE_URL.format(year=year, vote_number=vote_number)
            data = _get(url)
            parsed = parse_house_xml(data) if data else None
        else:
            url = SENATE_URL.format(session=session, vote_number=vote_number)
            data = _get(url)
            parsed = parse_senate_xml(data) if data else None

        if not parsed:
            # Record a null row so this vote isn't retried on every run
            conn.execute("""
                INSERT INTO congress_vote_party_breakdown
                    (congress, session, chamber, vote_number, vote_date,
                     vote_question, fetched_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(congress, session, chamber, vote_number) DO NOTHING
            """, (congress, session, chamber, vote_number, vote_date,
                  "UNAVAILABLE", now))
            err += 1
            if err % 25 == 0:
                conn.commit()
            time.sleep(DELAY)
            continue

        rep_pos = _majority_position(parsed["rep_yea"], parsed["rep_nay"])
        dem_pos = _majority_position(parsed["dem_yea"], parsed["dem_nay"])

        # Majority party = the one whose position carried the vote
        # For our purposes: store both parties' positions
        maj_party = _majority_party(
            parsed["rep_yea"], parsed["rep_nay"],
            parsed["dem_yea"], parsed["dem_nay"]
        )

        conn.execute("""
            INSERT INTO congress_vote_party_breakdown
                (congress, session, chamber, vote_number, vote_date,
                 vote_question, vote_desc,
                 rep_yea, rep_nay, rep_present,
                 dem_yea, dem_nay, dem_present,
                 ind_yea, ind_nay,
                 majority_party, majority_position, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(congress, session, chamber, vote_number) DO NOTHING
        """, (
            congress, session, chamber, vote_number, vote_date,
            parsed["vote_question"], parsed["vote_desc"],
            parsed["rep_yea"], parsed["rep_nay"], parsed["rep_present"],
            parsed["dem_yea"], parsed["dem_nay"], parsed["dem_present"],
            parsed["ind_yea"], parsed["ind_nay"],
            maj_party,
            rep_pos if maj_party == "R" else dem_pos,
            now,
        ))
        ok += 1
        if ok % 50 == 0:
            conn.commit()
            print(f"  fetched {ok}/{len(missing)}…")
        time.sleep(DELAY)

    conn.commit()
    print(f"Fetch done — ok={ok}  errors={err}")
    return ok


# ── Stats computation ─────────────────────────────────────────────────────────

def compute_stats(conn: sqlite3.Connection) -> None:
    """
    For each VA federal member in congress_votes, compute:
      - party_unity_pct  : % of votes where member voted with their party majority
      - bipartisan_pct   : % of votes where member voted AGAINST their party majority
    Only counts votes where both the member voted Yea/Nay AND their party had
    a clear majority position (Yea or Nay).
    """
    members = conn.execute(
        "SELECT bioguide_id, name, party, chamber FROM congress_members"
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()

    for bio, name, party, chamber in members:
        # Join member votes to party breakdown
        party_col = "rep_yea, rep_nay" if party in ("Republican", "R") else "dem_yea, dem_nay"
        rows = conn.execute(f"""
            SELECT cv.member_vote,
                   vpb.rep_yea, vpb.rep_nay,
                   vpb.dem_yea, vpb.dem_nay
            FROM congress_votes cv
            JOIN congress_vote_party_breakdown vpb
              ON vpb.congress  = cv.congress
             AND vpb.session   = cv.session
             AND vpb.chamber   = cv.chamber
             AND vpb.vote_number = cv.vote_number
            WHERE cv.bioguide_id = ?
              AND cv.member_vote IN ('Yea','Nay','Yes','No','Aye','Naye')
        """, (bio,)).fetchall()

        if not rows:
            continue

        total = with_party = against_party = unclassified = 0

        is_rep = party in ("Republican", "R")
        is_dem = party in ("Democratic", "Democrat", "D")

        for member_vote, rep_yea, rep_nay, dem_yea, dem_nay in rows:
            total += 1
            mv_yea = member_vote.upper() in ("YEA", "YES", "AYE")
            mv_nay = member_vote.upper() in ("NAY", "NO", "NAYE")

            # Determine party's majority position
            if is_rep:
                party_pos = _majority_position(rep_yea or 0, rep_nay or 0)
            elif is_dem:
                party_pos = _majority_position(dem_yea or 0, dem_nay or 0)
            else:
                party_pos = None

            if party_pos is None:
                unclassified += 1
                continue

            member_pos = "Yea" if mv_yea else ("Nay" if mv_nay else None)
            if member_pos is None:
                unclassified += 1
                continue

            if member_pos == party_pos:
                with_party += 1
            else:
                against_party += 1

        classifiable = with_party + against_party
        unity_pct = round(with_party / classifiable * 100, 1) if classifiable else None
        bip_pct   = round(against_party / classifiable * 100, 1) if classifiable else None

        # ── Missed votes: classify by question type ──────────────────────────
        missed_rows = conn.execute(
            """SELECT question, COUNT(*) as cnt
               FROM congress_votes
               WHERE bioguide_id = ?
                 AND member_vote IN ('Not Voting', 'Present')
               GROUP BY question""",
            (bio,),
        ).fetchall()
        total_row = conn.execute(
            "SELECT COUNT(*) FROM congress_votes WHERE bioguide_id = ?",
            (bio,),
        ).fetchone()
        total_all   = total_row[0] if total_row else 0
        missed      = sum(r[1] for r in missed_rows)
        missed_subst = sum(
            r[1] for r in missed_rows if r[0] in _SUBSTANTIVE_QUESTIONS
        )
        missed_proc  = sum(
            r[1] for r in missed_rows if r[0] in _PROCEDURAL_QUESTIONS
        )
        missed_pct  = round(missed / total_all * 100, 1) if total_all else None

        print(
            f"  {name:<35s} party={party[:1]}  "
            f"unity={unity_pct}%  bipartisan={bip_pct}%  "
            f"missed={missed_pct}% ({missed}/{total_all})"
            f"  subst={missed_subst}  proc={missed_proc}  "
            f"({classifiable} classified / {total} total)"
        )

        conn.execute("""
            INSERT INTO congress_member_party_stats
                (bioguide_id, name, party, chamber,
                 total_votes, with_party, against_party, not_classified,
                 party_unity_pct, bipartisan_pct,
                 missed_votes, missed_votes_pct,
                 missed_substantive, missed_procedural, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(bioguide_id) DO UPDATE SET
                name=excluded.name,
                party=excluded.party,
                chamber=excluded.chamber,
                total_votes=excluded.total_votes,
                with_party=excluded.with_party,
                against_party=excluded.against_party,
                not_classified=excluded.not_classified,
                party_unity_pct=excluded.party_unity_pct,
                bipartisan_pct=excluded.bipartisan_pct,
                missed_votes=excluded.missed_votes,
                missed_votes_pct=excluded.missed_votes_pct,
                missed_substantive=excluded.missed_substantive,
                missed_procedural=excluded.missed_procedural,
                updated_at=excluded.updated_at
        """, (bio, name, party, chamber, total,
              with_party, against_party, unclassified,
              unity_pct, bip_pct,
              missed, missed_pct,
              missed_subst, missed_proc, now))

    conn.commit()
    print("Stats written to congress_member_party_stats.")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(stats_only: bool, limit: int | None, dry_run: bool) -> None:
    print(f"ingest_party_breakdown  db={POLLS_DB}  stats_only={stats_only}  limit={limit}  dry_run={dry_run}")
    if not POLLS_DB.exists():
        print("ERROR: polls.db not found.")
        return

    conn = sqlite3.connect(str(POLLS_DB))
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)

    if not stats_only:
        fetched = fetch_missing(conn, limit=limit, dry_run=dry_run)
        if dry_run:
            conn.close()
            return

    print("\nComputing party unity / bipartisan stats…")
    compute_stats(conn)
    conn.close()
    print("Done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ingest congressional party breakdown data")
    ap.add_argument("--stats-only", action="store_true",
                    help="Skip XML fetch; recompute stats from existing breakdown rows")
    ap.add_argument("--limit", type=int, metavar="N",
                    help="Fetch at most N new votes (for testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show counts without fetching or writing")
    args = ap.parse_args()
    run(stats_only=args.stats_only, limit=args.limit, dry_run=args.dry_run)
