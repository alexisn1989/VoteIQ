#!/usr/bin/env python3
"""
migrate_bill_number_canonical.py
================================

One-time migration that adds a ``bill_number_canonical`` column to the
``va_bills`` table, backfills it using ``va_bill_number.normalize_bill_number``,
and builds an index for fast lookups.

Why a separate column (rather than rewriting ``bill_number``)
-------------------------------------------------------------
The raw ``bill_number`` is kept untouched so source provenance is preserved
(the value as it arrived from LegiScan / LIS). Queries should match against
``bill_number_canonical``; display can still show the raw value.

Safety
------
* Defaults to DRY RUN. Pass ``--apply`` to write changes.
* Reports any collisions (two different raw numbers normalizing to the same
  canonical within one session) WITHOUT merging them -- you decide.
* Verifies the SJ2 / SJR2 case resolves after applying.
* Always run against a copy first:  cp polls.db polls.db.bak

Usage
-----
    python migrate_bill_number_canonical.py                 # dry run, /var/data/polls.db
    python migrate_bill_number_canonical.py --apply
    python migrate_bill_number_canonical.py --db ./polls.db --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict

from va_bill_number import normalize_bill_number

DEFAULT_DB = "/var/data/polls.db"
TABLE = "va_bills"
RAW_COL = "bill_number"
CANON_COL = "bill_number_canonical"
SESSION_COL = "session"


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    return column in cols


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite path (default {DEFAULT_DB})")
    ap.add_argument("--apply", action="store_true",
                    help="Write changes. Omit for a dry run.")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    if not _column_exists(con, TABLE, RAW_COL):
        print(f"ERROR: {TABLE}.{RAW_COL} not found. Check table/column names "
              f"at the top of this script.", file=sys.stderr)
        return 2
    has_session = _column_exists(con, TABLE, SESSION_COL)

    rows = con.execute(f"SELECT rowid AS _rowid, {RAW_COL}"
                       + (f", {SESSION_COL}" if has_session else "")
                       + f" FROM {TABLE}").fetchall()

    updates: list[tuple[str, int]] = []   # (canonical, rowid)
    unparseable: list[str] = []
    changed = 0
    # collision key: (session, canonical) -> set of distinct raw values
    collisions: dict[tuple, set] = defaultdict(set)

    for r in rows:
        raw = r[RAW_COL]
        canon = normalize_bill_number(raw)
        if canon is None:
            unparseable.append(raw)
            continue
        updates.append((canon, r["_rowid"]))
        if canon != raw:
            changed += 1
        session = r[SESSION_COL] if has_session else None
        collisions[(session, canon)].add(raw)

    real_collisions = {k: v for k, v in collisions.items() if len(v) > 1}

    print(f"Scanned {len(rows):,} rows in {TABLE}")
    print(f"  would change : {changed:,}  (raw != canonical)")
    print(f"  unparseable  : {len(unparseable):,}")
    if unparseable:
        print("    e.g.", unparseable[:10])
    if real_collisions:
        print(f"  COLLISIONS   : {len(real_collisions)} canonical keys map from "
              f"multiple raw values (NOT merged):")
        for (sess, canon), raws in list(real_collisions.items())[:20]:
            print(f"    session={sess} {canon} <- {sorted(raws)}")
    else:
        print("  collisions   : none")

    if not args.apply:
        print("\nDRY RUN -- no changes written. Re-run with --apply.")
        con.close()
        return 0

    if not _column_exists(con, TABLE, CANON_COL):
        con.execute(f"ALTER TABLE {TABLE} ADD COLUMN {CANON_COL} TEXT")
    con.executemany(f"UPDATE {TABLE} SET {CANON_COL} = ? WHERE rowid = ?", updates)
    # Non-unique index (collisions, if any, are intentional duplicate rows).
    idx_cols = f"{SESSION_COL}, {CANON_COL}" if has_session else CANON_COL
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_canon ON {TABLE} ({idx_cols})")
    con.commit()
    print(f"\nApplied: {CANON_COL} backfilled, index idx_{TABLE}_canon created.")

    # Verify the bug that started this.
    where = f"{CANON_COL} = 'SJ2'" + (f" AND {SESSION_COL} = '2026'" if has_session else "")
    hit = con.execute(f"SELECT {RAW_COL}, {CANON_COL} FROM {TABLE} WHERE {where}").fetchall()
    print("\nVerification -- lookup canonical 'SJ2' (2026):")
    if hit:
        for h in hit:
            print(f"  found raw={h[RAW_COL]!r} canonical={h[CANON_COL]!r}")
    else:
        print("  STILL ZERO -- the bill may not be ingested at all; "
              "check the raw 2026 SJ/SJR series directly.")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
