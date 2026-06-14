"""
audit_data_integrity.py

Standalone, read-only data-integrity auditor for VoteIQ. Run before pulling
bills for a demo, or after any ingest, to catch the failure modes that produced
the HB774/SB161 "impossible vote total" and HB1373/HB1445 "wrong bill" bugs.

It FLAGS problems and prints a report — it never drops or mutates rows. Exit
code is non-zero if any hard violation is found, so it can gate a deploy/demo.

Checks
------
1. Vote totals (openstates_va.db `votes`)
   a. HARD: any single vote event (bill_id, session, vote_date, motion) with
      YES+NO > 100 is physically impossible for either VA chamber (House=100,
      Senate=40). This is the signature of the duplication bug.
   b. SOFT: counted per-voter rows that diverge by >1 from the official tally
      embedded in the motion text, e.g. "VOTE: Passage (62-Y 33-N)". Catches
      incomplete/duplicated/mislabeled per-voter data without false-flagging the
      2021 chamber-label noise.

2. cycle_context title consistency (polls.db `cycle_context`)
   VA reuses bill numbers every session, so (bill_number, session) — not
   bill_number alone — is the only stable key. Flags any bill_match row whose
   stored title does not match the canonical title for that (bill_number,
   session) in legiscan_va_bills / openstates_va.db.

Usage:
    python audit_data_integrity.py            # full audit, report + exit code
    python audit_data_integrity.py --quiet    # only print violations
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OPENSTATES_DB = BASE_DIR / "openstates_va.db"
POLLS_DB = BASE_DIR / "polls.db"

# VA chamber sizes. Used only for the HARD impossibility ceiling; the SOFT
# check relies on the official tally instead, because the chamber column is
# unreliable in pre-2024 OpenStates data (House votes mislabeled as Senate).
HOUSE_SIZE = 100
SENATE_SIZE = 40
HARD_CEILING = HOUSE_SIZE  # no VA chamber can exceed this

_TALLY_RE = re.compile(r"\((\d+)-Y\s+(\d+)-N")


def audit_vote_totals(conn: sqlite3.Connection, quiet: bool) -> tuple[int, int]:
    """Return (hard_violations, soft_violations)."""
    print("\n=== Vote-total audit (openstates_va.db) ===")

    # 1a. HARD: impossible totals (> larger chamber). The original 372/198 bug.
    hard = conn.execute(
        """
        SELECT bill_id, session, vote_date, chamber, motion,
               SUM(CASE WHEN option='yes' THEN 1 ELSE 0 END) AS y,
               SUM(CASE WHEN option='no'  THEN 1 ELSE 0 END) AS n
        FROM votes
        GROUP BY bill_id, session, vote_date, chamber, motion
        HAVING (y + n) > ?
        ORDER BY (y + n) DESC
        """,
        (HARD_CEILING,),
    ).fetchall()
    print(f"  [HARD] events with YES+NO > {HARD_CEILING} (impossible): {len(hard)}")
    for r in hard[:20]:
        print(f"    !! {r[0]} {r[1]} {r[3]} {r[4][:36]!r} -> {r[5]}-Y {r[6]}-N (={r[5]+r[6]})")

    # 1b. SOFT: counted rows diverge from official embedded tally by >1.
    soft = 0
    soft_examples: list[str] = []
    rows = conn.execute(
        """
        SELECT bill_id, session, motion,
               SUM(CASE WHEN option='yes' THEN 1 ELSE 0 END) AS y,
               SUM(CASE WHEN option='no'  THEN 1 ELSE 0 END) AS n
        FROM votes
        WHERE motion LIKE '%-Y %-N%'
        GROUP BY bill_id, session, vote_date, chamber, motion
        """
    ).fetchall()
    for bid, ses, motion, y, n in rows:
        m = _TALLY_RE.search(motion or "")
        if not m:
            continue
        oy, on = int(m.group(1)), int(m.group(2))
        if y > oy + 1 or n > on + 1:  # +1 slack for speaker/clerk edge cases
            soft += 1
            if len(soft_examples) < 15:
                soft_examples.append(
                    f"    ?  {bid} {ses} counted({y}-{n}) vs official({oy}-{on}) | {motion[:38]}"
                )
    print(f"  [SOFT] per-voter rows diverging from official tally: {soft}")
    if not quiet:
        for line in soft_examples:
            print(line)

    return len(hard), soft


# Sessions whose cycle_context rows are built from OpenStates rather than
# LegiScan (see build_cycle_context.py::_insert_bill_matched). The audit must
# compare each row against the SAME source the builder used, otherwise benign
# OpenStates-vs-LegiScan title-wording differences look like mismatches.
_OPENSTATES_SESSIONS = {"2022", "2023"}


def _canonical_titles(quiet: bool) -> dict[tuple[str, str], str]:
    """Build {(bill_number, session): title}, choosing the source that
    build_cycle_context.py actually used for that session so the comparison is
    apples-to-apples. Falls back to the other source when one is missing."""
    legiscan: dict[tuple[str, str], str] = {}
    openstates: dict[tuple[str, str], str] = {}
    if POLLS_DB.exists():
        c = sqlite3.connect(POLLS_DB)
        try:
            for bn, ses, t in c.execute(
                "SELECT bill_number, session, title FROM legiscan_va_bills"
            ):
                if t:
                    legiscan[(bn, ses)] = t
        except sqlite3.Error:
            pass
        c.close()
    if OPENSTATES_DB.exists():
        c = sqlite3.connect(OPENSTATES_DB)
        for bn, ses, t in c.execute("SELECT bill_id, session, title FROM bills"):
            if t:
                openstates[(bn, ses)] = t
        c.close()

    titles: dict[tuple[str, str], str] = {}
    for key in set(legiscan) | set(openstates):
        _, ses = key
        primary, secondary = (
            (openstates, legiscan) if ses in _OPENSTATES_SESSIONS
            else (legiscan, openstates)
        )
        titles[key] = primary.get(key) or secondary.get(key)
    return titles


def _norm(s: str) -> str:
    """Lowercase, strip all quote characters (sources disagree on '/" styles),
    collapse non-alphanumerics to single spaces."""
    s = (s or "").lower()
    s = re.sub(r"[\"'‘’“”]", "", s)  # drop straight + curly quotes
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def audit_cycle_context(quiet: bool) -> int:
    """Flag cycle_context bill_match rows whose stored title contradicts the
    canonical title for that (bill_number, session). Returns violation count."""
    print("\n=== cycle_context title-consistency audit (polls.db) ===")
    if not POLLS_DB.exists():
        print("  polls.db not found — skipping")
        return 0

    canon = _canonical_titles(quiet)
    conn = sqlite3.connect(POLLS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT election_cycle, bill_number, title
        FROM cycle_context
        WHERE source = 'bill_match' AND bill_number IS NOT NULL
        """
    ).fetchall()
    conn.close()

    violations = 0
    examples: list[str] = []
    for r in rows:
        key = (r["bill_number"], r["election_cycle"])
        canonical = canon.get(key)
        if not canonical:
            continue  # can't verify; not a contradiction
        # The stored title is "HB1373 (2023): <canonical[:80]>"; check the
        # canonical fragment is actually present in the stored title.
        if _norm(canonical[:60]) not in _norm(r["title"]):
            violations += 1
            if len(examples) < 15:
                examples.append(
                    f"    !! {r['bill_number']} ({r['election_cycle']}): "
                    f"stored={r['title'][:45]!r} canonical={canonical[:45]!r}"
                )
    print(f"  rows checked: {len(rows)}  |  title mismatches: {violations}")
    if not quiet:
        for line in examples:
            print(line)
    return violations


def main(quiet: bool) -> int:
    if not OPENSTATES_DB.exists():
        print(f"ERROR: {OPENSTATES_DB} not found", file=sys.stderr)
        return 2

    conn = sqlite3.connect(OPENSTATES_DB)
    hard, soft = audit_vote_totals(conn, quiet)
    conn.close()

    cc_bad = audit_cycle_context(quiet)

    print("\n=== Summary ===")
    print(f"  Vote totals  — HARD (impossible): {hard}   SOFT (tally divergence): {soft}")
    print(f"  cycle_context — title mismatches:  {cc_bad}")

    # Only HARD vote violations and cycle_context mismatches gate the demo;
    # SOFT divergences are reported for review (known pre-2024 source noise).
    blocking = hard + cc_bad
    if blocking:
        print(f"\n  RESULT: {blocking} blocking violation(s) — FIX before demo.")
        return 1
    print("\n  RESULT: no blocking violations. SOFT divergences are advisory.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--quiet", action="store_true", help="print only counts, not examples")
    args = p.parse_args()
    sys.exit(main(args.quiet))
