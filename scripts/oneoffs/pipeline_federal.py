#!/usr/bin/env python3
"""
pipeline_federal.py
End-to-end ingest pipeline for Virginia federal delegation data.

Steps (run in dependency order):
  members     — ingest_congress.py           Congress.gov API v3
                → congress_members, congress_bills, congress_bill_texts
  votes       — ingest_congress_votes.py     House Clerk + Senate.gov XML (no key)
                → congress_votes
  floor       — ingest_floor_statements.py   GovInfo API
                → congress_floor_statements
  hearings    — ingest_hearings.py           GovInfo API
                → congress_hearings
  correlation — ingest_federal_intelligence.py  Congress.gov API
                → federal_bills, federal_votes, three-way correlation view
  rag         — build_congress_bills_rag.py  local (no key)
                → congress_bills_text_rag.jsonl

Required env vars:
  CONGRESS_API_KEY   — https://api.congress.gov/sign-up/  (free)
  GOVINFO_API_KEY    — https://api.govinfo.gov/docs/       (free)

Usage:
    python pipeline_federal.py                       # all steps
    python pipeline_federal.py --steps members,votes
    python pipeline_federal.py --skip-slow           # skip floor/hearings/rag (faster)
    python pipeline_federal.py --dry-run             # pass --dry-run to each script
    python pipeline_federal.py --congress 119
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON   = sys.executable

# ── Step registry ─────────────────────────────────────────────────────────────
# (key, script, extra_flags, required_env_vars, stamps_tables)
STEP_DEFS = [
    (
        "members",
        "ingest_congress.py",
        [],
        ["CONGRESS_API_KEY"],
        ["congress_members", "congress_bills", "congress_bill_texts"],
    ),
    (
        "votes",
        "ingest_congress_votes.py",
        [],
        [],
        ["congress_votes"],
    ),
    (
        "floor",
        "ingest_floor_statements.py",
        [],
        ["GOVINFO_API_KEY"],
        ["congress_floor_statements"],
    ),
    (
        "hearings",
        "ingest_hearings.py",
        [],
        ["GOVINFO_API_KEY"],
        ["congress_hearings"],
    ),
    (
        "correlation",
        "ingest_federal_intelligence.py",
        ["--view-only"],
        ["CONGRESS_API_KEY"],
        ["federal_bills", "federal_votes"],
    ),
    (
        "rag",
        "build_congress_bills_rag.py",
        [],
        [],
        [],          # outputs a .jsonl file, not a DB table
    ),
]

SLOW_STEPS = {"floor", "hearings", "rag"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _db_path() -> Path:
    data_dir = os.getenv("DATA_DIR", str(BASE_DIR))
    return Path(data_dir) / "polls.db"


def _stamp(table: str, row_count: int | None = None) -> None:
    """Write a data_meta row for *table* — swallowed on any error."""
    try:
        conn = sqlite3.connect(str(_db_path()), timeout=15)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS __data_meta (
                table_name TEXT PRIMARY KEY, last_ingested TEXT,
                row_count INTEGER, source_url TEXT
            )
        """)
        conn.execute(
            """
            INSERT INTO __data_meta (table_name, last_ingested, row_count, source_url)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(table_name) DO UPDATE SET
                last_ingested = excluded.last_ingested,
                row_count     = COALESCE(excluded.row_count, row_count)
            """,
            (table, datetime.now(timezone.utc).isoformat(timespec="seconds"), row_count),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _live_count(table: str) -> int | None:
    """Return current row count for *table* from polls.db, or None if unavailable."""
    try:
        conn = sqlite3.connect(str(_db_path()), timeout=10)
        n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        conn.close()
        return n
    except Exception:
        return None


def _check_env(keys: list[str]) -> list[str]:
    """Return list of missing required env vars."""
    return [k for k in keys if not os.getenv(k)]


def _run_step(
    key: str,
    script: str,
    extra_flags: list[str],
    required_env: list[str],
    stamps: list[str],
    dry_run: bool,
    congress: int,
    verbose: bool,
) -> dict:
    """Run one pipeline step. Returns result dict."""
    result = {"step": key, "status": "skipped", "elapsed": 0.0, "notes": ""}

    # env check
    missing = _check_env(required_env)
    if missing:
        result["status"] = "skipped"
        result["notes"] = f"missing env: {', '.join(missing)}"
        return result

    script_path = BASE_DIR / script
    if not script_path.exists():
        result["status"] = "error"
        result["notes"] = f"script not found: {script}"
        return result

    cmd = [PYTHON, str(script_path), *extra_flags]
    if dry_run:
        cmd.append("--dry-run")
    if congress and "--congress" not in extra_flags:
        # only pass --congress if the script accepts it (best-effort)
        cmd += ["--congress", str(congress)]

    print(f"\n[{_now()}] -- {key} ------------------------------------")
    print(f"  cmd: {' '.join(str(c) for c in cmd)}")

    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        capture_output=not verbose,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    result["elapsed"] = elapsed

    if verbose and proc.returncode != 0:
        # stderr already printed; no need to reprint
        pass
    elif not verbose:
        if proc.stdout:
            # print last 20 lines to stay concise
            lines = proc.stdout.strip().splitlines()
            for line in lines[-20:]:
                print(f"  {line}")
        if proc.returncode != 0 and proc.stderr:
            for line in proc.stderr.strip().splitlines()[-10:]:
                print(f"  STDERR: {line}")

    if proc.returncode == 0:
        result["status"] = "ok"
        # stamp data_meta for each table this step owns
        for tbl in stamps:
            count = _live_count(tbl)
            _stamp(tbl, count)
            if count is not None:
                print(f"  stamped {tbl}: {count:,} rows")
    else:
        result["status"] = "error"
        result["notes"] = f"exit code {proc.returncode}"

    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Federal data ingest pipeline for VoteIQ.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--steps",
        help="Comma-separated list of steps to run (default: all). "
             "Choices: members,votes,floor,hearings,correlation,rag",
    )
    parser.add_argument(
        "--skip-slow",
        action="store_true",
        help="Skip floor, hearings, and rag steps (faster daily refresh).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to each script (no DB writes).",
    )
    parser.add_argument(
        "--congress",
        type=int,
        default=119,
        help="Congress number to ingest (default: 119).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Stream each script's output in real time.",
    )
    args = parser.parse_args()

    # Resolve which steps to run
    all_keys = [s[0] for s in STEP_DEFS]
    if args.steps:
        requested = [k.strip() for k in args.steps.split(",")]
        invalid = [k for k in requested if k not in all_keys]
        if invalid:
            print(f"ERROR: unknown steps: {', '.join(invalid)}")
            print(f"Valid steps: {', '.join(all_keys)}")
            sys.exit(1)
        selected = requested
    else:
        selected = [k for k in all_keys if not (args.skip_slow and k in SLOW_STEPS)]

    print("=" * 60)
    print("  VoteIQ — Federal Data Pipeline")
    print(f"  Started:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Steps:    {', '.join(selected)}")
    print(f"  Congress: {args.congress}")
    print(f"  Dry run:  {args.dry_run}")
    print(f"  DB:       {_db_path()}")
    print("=" * 60)

    results = []
    pipeline_start = time.perf_counter()

    for key, script, extra_flags, req_env, stamps in STEP_DEFS:
        if key not in selected:
            continue
        res = _run_step(
            key=key,
            script=script,
            extra_flags=extra_flags,
            required_env=req_env,
            stamps=stamps,
            dry_run=args.dry_run,
            congress=args.congress,
            verbose=args.verbose,
        )
        results.append(res)

    total_elapsed = time.perf_counter() - pipeline_start

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  PIPELINE SUMMARY")
    print("=" * 60)
    col_w = 14
    print(f"  {'Step':<{col_w}}  {'Status':<8}  {'Time':>7}  Notes")
    print(f"  {'-'*col_w}  {'-'*8}  {'-'*7}  -----")
    ok_count = err_count = skip_count = 0
    for r in results:
        icon = "+" if r["status"] == "ok" else ("x" if r["status"] == "error" else "-")
        t = f"{r['elapsed']:.1f}s" if r["elapsed"] else "--"
        print(f"  {icon} {r['step']:<{col_w}}  {r['status']:<8}  {t:>7}  {r['notes']}")
        if r["status"] == "ok":       ok_count += 1
        elif r["status"] == "error":  err_count += 1
        else:                         skip_count += 1

    print(f"\n  {ok_count} ok  /  {err_count} errors  /  {skip_count} skipped")
    print(f"  Total time: {total_elapsed:.1f}s")
    print(f"  Finished:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    sys.exit(1 if err_count else 0)


if __name__ == "__main__":
    main()
