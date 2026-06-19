"""
Resolves name variants across tables into a single canonical_name.
Write to: va_name_index table.
This table is the FK substitute for the missing legislator_id column.

Source of truth: legislators.name
Matched against:
  - va_legislator_recent_votes.voter_name  (fuzzy: token_set_ratio >= 80)
  - va_cf_schedule_a.candidate_name        (fuzzy: token_sort_ratio >= 85,
                                            or override from cf_name_overrides.json)

Any legislator without a confirmed CF name match gets cf_table_name = NULL
and will return insufficient_data on donor signals — correct behavior.

Usage:
    python app/prediction/build_name_index.py            # build index
    python app/prediction/build_name_index.py --dry-run  # show matches, no writes
    python app/prediction/build_name_index.py --review   # inspect existing index
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

try:
    from rapidfuzz import fuzz
    from rapidfuzz import process as fuzz_process
except ImportError:
    raise SystemExit("rapidfuzz is required: pip install rapidfuzz")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
POLLS_DB = _DATA_DIR / "polls.db"

SCORE_CUTOFF = 85

_OVERRIDES_PATH       = Path(__file__).resolve().parent / "cf_name_overrides.json"
_DV_OVERRIDES_PATH    = Path(__file__).resolve().parent / "dv_name_overrides.json"
_VOTES_OVERRIDES_PATH = Path(__file__).resolve().parent / "votes_name_overrides.json"


def _load_cf_overrides() -> dict[str, str]:
    if not _OVERRIDES_PATH.exists():
        return {}
    with _OVERRIDES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _load_dv_overrides() -> dict[str, str]:
    if not _DV_OVERRIDES_PATH.exists():
        return {}
    with _DV_OVERRIDES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _load_votes_overrides() -> dict[str, str]:
    if not _VOTES_OVERRIDES_PATH.exists():
        return {}
    with _VOTES_OVERRIDES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS va_name_index (
    canonical_name        TEXT PRIMARY KEY,
    legislator_table_name TEXT,
    votes_table_name      TEXT,
    cf_table_name         TEXT,
    dv_table_name         TEXT,
    party                 TEXT,
    chamber               TEXT,
    district              TEXT
);
"""

# Migration: add column to existing tables built before dv_table_name was introduced.
_MIGRATE_SQL = "ALTER TABLE va_name_index ADD COLUMN dv_table_name TEXT"


def _get_legislators(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT name, party, chamber, district FROM legislators WHERE name IS NOT NULL"
    ).fetchall()
    return [{"name": r[0], "party": r[1], "chamber": r[2], "district": r[3]} for r in rows]


def _get_voter_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT voter_name FROM va_legislator_recent_votes WHERE voter_name IS NOT NULL"
    ).fetchall()
    return [r[0] for r in rows]


def _get_cf_candidate_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT candidate_name
        FROM va_cf_schedule_a
        WHERE candidate_name IS NOT NULL AND trim(candidate_name) != ''
        """
    ).fetchall()
    return [r[0] for r in rows]


def _get_dv_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT name FROM donor_vote_alignment WHERE name IS NOT NULL"
    ).fetchall()
    return [r[0] for r in rows]


def build_name_index(dry_run: bool = False) -> None:
    conn = sqlite3.connect(POLLS_DB)
    conn.execute(CREATE_TABLE_SQL)

    # Migration: add dv_table_name column if built before this version.
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(va_name_index)").fetchall()]
    if "dv_table_name" not in existing_cols:
        conn.execute(_MIGRATE_SQL)
        conn.commit()

    legislators = _get_legislators(conn)
    vote_names  = _get_voter_names(conn)
    cf_names    = _get_cf_candidate_names(conn)
    dv_names       = _get_dv_names(conn)
    cf_overrides   = _load_cf_overrides()
    dv_overrides   = _load_dv_overrides()
    votes_overrides = _load_votes_overrides()

    print(f"Legislators (source of truth):      {len(legislators)}")
    print(f"Distinct voter_names in votes:      {len(vote_names)}")
    print(f"Distinct candidate_names in CF:     {len(cf_names)}")
    print(f"Distinct names in donor_vote_align: {len(dv_names)}")
    print(f"Manual CF overrides loaded:         {len(cf_overrides)}")
    print(f"Manual DV overrides loaded:         {len(dv_overrides)}")
    print(f"Manual votes overrides loaded:      {len(votes_overrides)}")
    print()

    matched_rows: list[tuple] = []
    no_votes_match: list[str] = []
    no_cf_match: list[str] = []
    no_dv_match: list[str] = []
    overrides_used:       list[tuple[str, str]] = []
    dv_overrides_used:    list[tuple[str, str]] = []
    votes_overrides_used: list[tuple[str, str]] = []

    for leg in legislators:
        name = leg["name"]

        # votes_name_overrides.json covers nickname/formal mismatches below 80%
        # (e.g. "Cliff Hayes" -> "C.E. Cliff Hayes, Jr."). token_set_ratio at 80
        # handles middle-initial-only differences ("Aaron Rouse" -> "Aaron R. Rouse").
        if name in votes_overrides:
            votes_match = votes_overrides[name]
            votes_overrides_used.append((name, votes_match))
        else:
            votes_result = fuzz_process.extractOne(
                name,
                vote_names,
                scorer=fuzz.token_set_ratio,
                score_cutoff=80,
            )
            votes_match = votes_result[0] if votes_result else None

        # Manual override takes priority over fuzzy match for CF names.
        # Overrides are sourced from cf_name_overrides.json via direct SBE lookup.
        if name in cf_overrides:
            cf_match = cf_overrides[name]
            overrides_used.append((name, cf_match))
        else:
            cf_result = fuzz_process.extractOne(
                name,
                cf_names,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=SCORE_CUTOFF,
            )
            cf_match = cf_result[0] if cf_result else None

        # donor_vote_alignment uses inconsistent name formats (mix of voter_name
        # and canonical with middle initials/nicknames). token_set_ratio at 80
        # handles most cases; dv_name_overrides.json covers nickname/formal mismatches
        # below the threshold (e.g. "Buddy Fowler" -> "Hyland F. \"Buddy\" Fowler, Jr.").
        if name in dv_overrides:
            dv_match = dv_overrides[name]
            dv_overrides_used.append((name, dv_match))
        else:
            dv_result = fuzz_process.extractOne(
                name,
                dv_names,
                scorer=fuzz.token_set_ratio,
                score_cutoff=80,
            )
            dv_match = dv_result[0] if dv_result else None

        if votes_match is None:
            no_votes_match.append(name)
        if cf_match is None:
            no_cf_match.append(name)
        if dv_match is None:
            no_dv_match.append(name)

        matched_rows.append((
            name,          # canonical_name
            name,          # legislator_table_name
            votes_match,   # votes_table_name
            cf_match,      # cf_table_name
            dv_match,      # dv_table_name
            leg["party"],
            leg["chamber"],
            leg["district"],
        ))

    total        = len(legislators)
    votes_matched = sum(1 for r in matched_rows if r[2] is not None)
    cf_matched    = sum(1 for r in matched_rows if r[3] is not None)
    dv_matched    = sum(1 for r in matched_rows if r[4] is not None)
    print(f"Matched to votes table:        {votes_matched}/{total}")
    print(f"Matched to CF table:           {cf_matched}/{total}")
    print(f"Matched to donor_vote_align:   {dv_matched}/{total}")
    print()

    if votes_overrides_used:
        print(f"=== VOTES OVERRIDES APPLIED ({len(votes_overrides_used)}) ===")
        for canonical, vname in votes_overrides_used:
            print(f"  {canonical!r} -> {vname!r}")
        print()

    if overrides_used:
        print(f"=== CF OVERRIDES APPLIED ({len(overrides_used)}) ===")
        for canonical, sbe_name in overrides_used:
            print(f"  {canonical!r} -> {sbe_name!r}")
        print()

    if dv_overrides_used:
        print(f"=== DV OVERRIDES APPLIED ({len(dv_overrides_used)}) ===")
        for canonical, dv_name in dv_overrides_used:
            print(f"  {canonical!r} -> {dv_name!r}")
        print()

    if no_votes_match:
        print(f"=== NO VOTES MATCH ({len(no_votes_match)}) — manual review required ===")
        for name in sorted(no_votes_match):
            print(f"  {name}")
        print()

    if no_cf_match:
        print(
            f"=== NO CF MATCH ({len(no_cf_match)}) ==="
            " — these will return insufficient_data on donor signals"
        )
        for name in sorted(no_cf_match):
            print(f"  {name}")
        print()

    if no_dv_match:
        print(
            f"=== NO DV MATCH ({len(no_dv_match)}) ==="
            " — these will return no CoI score"
        )
        for name in sorted(no_dv_match):
            print(f"  {name}")
        print()

    if dry_run:
        print("[dry-run] No rows written to va_name_index.")
        conn.close()
        return

    conn.executemany(
        """
        INSERT INTO va_name_index
            (canonical_name, legislator_table_name, votes_table_name,
             cf_table_name, dv_table_name, party, chamber, district)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_name) DO UPDATE SET
            legislator_table_name = excluded.legislator_table_name,
            votes_table_name      = excluded.votes_table_name,
            cf_table_name         = COALESCE(va_name_index.cf_table_name, excluded.cf_table_name),
            dv_table_name         = excluded.dv_table_name,
            party                 = excluded.party,
            chamber               = excluded.chamber,
            district              = excluded.district
        """,
        matched_rows,
    )
    conn.commit()
    print(f"Inserted {len(matched_rows)} rows into va_name_index.")

    # Re-query after upsert so counts reflect COALESCE-preserved SQL mappings,
    # not just what the fuzzy pass matched.
    db_cf = conn.execute("SELECT COUNT(*) FROM va_name_index WHERE cf_table_name IS NOT NULL").fetchone()[0]
    db_dv = conn.execute("SELECT COUNT(*) FROM va_name_index WHERE dv_table_name IS NOT NULL").fetchone()[0]
    cf_pct = 100 * db_cf // total if total else 0
    dv_pct = 100 * db_dv // total if total else 0
    print(f"CF coverage: {db_cf}/{total} ({cf_pct}%) have a donor name match.")
    print(f"DV coverage: {db_dv}/{total} ({dv_pct}%) have a CoI alignment match.")
    conn.close()


def review_index() -> None:
    conn = sqlite3.connect(POLLS_DB)
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='va_name_index'"
        ).fetchone()
        if not exists:
            print("va_name_index does not exist. Run without --review to build it.")
            return

        rows = conn.execute(
            "SELECT canonical_name, votes_table_name, cf_table_name, dv_table_name FROM va_name_index ORDER BY canonical_name"
        ).fetchall()
        if not rows:
            print("va_name_index is empty.")
            return

        no_votes = [r for r in rows if r[1] is None]
        no_cf    = [r for r in rows if r[2] is None]
        no_dv    = [r for r in rows if r[3] is None]
        print(f"Index rows:          {len(rows)}")
        print(f"Missing votes match: {len(no_votes)}")
        print(f"Missing CF match:    {len(no_cf)}")
        print(f"Missing DV match:    {len(no_dv)}")

        if no_votes:
            print("\nNo votes match:")
            for r in no_votes:
                print(f"  {r[0]}")
        if no_cf:
            print("\nNo CF match (donor signals will be insufficient_data):")
            for r in no_cf:
                print(f"  {r[0]}")
        if no_dv:
            print("\nNo DV match (CoI score will be unavailable):")
            for r in no_dv:
                print(f"  {r[0]}")
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Build va_name_index — canonical name resolution across vote and finance tables"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show matches without writing to DB")
    parser.add_argument("--review", action="store_true", help="Inspect existing index (read-only)")
    args = parser.parse_args()

    if args.review:
        review_index()
    else:
        build_name_index(dry_run=args.dry_run)
