"""Daily scheduled ingest pipeline — runs at 2am UTC via Render cron."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

STEPS: list[tuple[str, list[str], int]] = [
    # (script, extra_args, timeout_seconds)
    ("ingest_va_legislators.py",       [],                          120),
    ("ingest_openstates.py",           [],                          300),
    ("ingest_bill.py",                 [],                          300),
    ("ingest_governor_actions.py",     [],                          120),  # must run after openstates
    ("ingest_governor_eos.py",         [],                          180),
    ("ingest_va_polls.py",             ["--source", "fivethirtyeight",
                                        "--source", "votehub",
                                        "--source", "news"],        180),
    ("ingest_va_news.py",              ["--limit", "50"],           300),
    ("ingest_vpap.py",                 [],                          180),
    ("ingest_va_finance.py",           [],                          300),
    ("ingest_committees.py",           [],                          120),
]

# Optional heavy steps — only run when env vars are set
OPTIONAL: list[tuple[str, list[str], int, str]] = [
    # (script, extra_args, timeout, required_env_var)
    ("scripts/build_openstates_data_dir.py", ["--session", os.getenv("OPENSTATES_BUILD_SESSION", "2026"),
                                              "--resume"],       3600, "OPENSTATES_API_KEY"),
    ("download_va_finance.py",     ["--since", os.getenv("VA_FINANCE_SINCE", "2023_01"),
                                    "--workers", os.getenv("VA_FINANCE_WORKERS", "2")],
                                                              1800, "VOTEIQ_BUILD_STATE_FINANCE"),
    ("build_va_state_finance.py",  ["--since", os.getenv("VA_FINANCE_BUILD_SINCE", "2023")],
                                                              1800, "VOTEIQ_BUILD_STATE_FINANCE"),
    # Refresh multi-cycle donor trends after any SBE finance update
    ("build_donor_cycle_trends.py", [],                       600,  "VOTEIQ_BUILD_STATE_FINANCE"),
    # Run spike watchlist — writes alerts for donors that crossed thresholds
    ("run_spike_watchlist.py",     [],                         120,  "VOTEIQ_BUILD_STATE_FINANCE"),
    ("bulk_download_va_reports.py", ["--start", os.getenv("VA_FINANCE_REPORT_START", "2023_01"),
                                     "--end", os.getenv("VA_FINANCE_REPORT_END", "2026_05")],
                                                              1800, "VOTEIQ_BUILD_STATE_FINANCE"),
    ("build_legislative_intelligence.py", ["--since", os.getenv("LEGISLATIVE_INTELLIGENCE_SINCE", "2023")],
                                                              1800, "VOTEIQ_BUILD_LEGISLATIVE_INTELLIGENCE"),
    ("ingest_congress.py",        ["--congress", "119"],       600,  "CONGRESS_API_KEY"),
    ("ingest_congress_votes.py",  ["--house-limit", "300"],    600,  "CONGRESS_API_KEY"),
    # Floor statements — runs after congress votes so member table is fresh
    ("ingest_floor_statements.py", ["--fetch-text"],           900,  "CONGRESS_API_KEY"),
    ("ingest_fec_pacs.py",        [],                          1800, "FEC_API_KEY"),
    # FEC independent expenditures (Schedule E) — current + prior cycle
    ("ingest_fec_schedule_e.py",  ["--cycle", "2026"],         600,  "FEC_API_KEY"),
    ("ingest_fec_schedule_e.py",  ["--cycle", "2025"],         600,  "FEC_API_KEY"),
    # Rebuild donor-vote alignment after any finance update
    ("build_donor_vote_alignment.py", ["--sessions", "2025", "2026"], 600, "VOTEIQ_BUILD_STATE_FINANCE"),
]


def run(script: str, args: list[str], timeout: int) -> bool:
    path = BASE_DIR / script
    if not path.exists():
        print(f"[skip] {script} not found", flush=True)
        return True
    print(f"[run]  {script}", flush=True)
    result = subprocess.run(
        [sys.executable, str(path)] + args,
        capture_output=False,
        timeout=timeout,
        cwd=str(BASE_DIR),
    )
    ok = result.returncode == 0
    print(f"[{'ok' if ok else 'FAIL'}] {script} exited {result.returncode}", flush=True)
    return ok


def main() -> int:
    failures = 0

    for script, args, timeout in STEPS:
        try:
            if not run(script, args, timeout):
                failures += 1
        except subprocess.TimeoutExpired:
            print(f"[TIMEOUT] {script}", flush=True)
            failures += 1
        except Exception as exc:
            print(f"[ERROR] {script}: {exc}", flush=True)
            failures += 1

    for script, args, timeout, env_var in OPTIONAL:
        if not os.getenv(env_var):
            print(f"[skip] {script} — {env_var} not set", flush=True)
            continue
        try:
            if not run(script, args, timeout):
                failures += 1
        except subprocess.TimeoutExpired:
            print(f"[TIMEOUT] {script}", flush=True)
            failures += 1
        except Exception as exc:
            print(f"[ERROR] {script}: {exc}", flush=True)
            failures += 1

    print(f"\n[done] {failures} failure(s)", flush=True)
    return min(failures, 1)


if __name__ == "__main__":
    raise SystemExit(main())
