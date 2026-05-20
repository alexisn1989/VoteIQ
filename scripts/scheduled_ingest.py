"""Daily scheduled ingest pipeline — runs at 2am UTC via Render cron."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

STEPS: list[tuple[str, list[str], int]] = [
    # (script, extra_args, timeout_seconds)
    ("ingest_va_legislators.py",  [],                          120),
    ("ingest_openstates.py",      [],                          300),
    ("ingest_bill.py",            [],                          300),
    ("ingest_va_polls.py",        ["--source", "fivethirtyeight",
                                   "--source", "votehub",
                                   "--source", "news"],        180),
    ("ingest_vpap.py",            [],                          180),
    ("ingest_va_finance.py",      [],                          300),
    ("ingest_committees.py",      [],                          120),
]

# Optional heavy steps — only run when env vars are set
OPTIONAL: list[tuple[str, list[str], int, str]] = [
    # (script, extra_args, timeout, required_env_var)
    ("ingest_congress.py",        ["--congress", "119"],       600,  "CONGRESS_API_KEY"),
    ("ingest_congress_votes.py",  ["--house-limit", "300"],    600,  "CONGRESS_API_KEY"),
    ("ingest_fec_pacs.py",        [],                          1800, "FEC_API_KEY"),
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
