# One-off data pipeline scripts

Historical build/ingest/backfill/migration scripts moved out of the repo root
(2026-07-01). Every script here was verified to have **zero references** from
main.py, build.sh, render.yaml, admin routes, scheduled ingests, or any other
script at move time — anything still referenced stayed at the repo root.

These scripts were written to run from the repo root, so their imports of root
modules (`config.db`, etc.) resolve via the working directory. Run them like:

    PYTHONPATH=. python scripts/oneoffs/<name>.py       # Git Bash / Linux
    $env:PYTHONPATH='.'; python scripts/oneoffs/<name>.py   # PowerShell

If one of these graduates back into an automated pipeline, move it back to the
repo root (or into scripts/ with fixed imports) and reference it explicitly
from build.sh / render.yaml / an admin route.
