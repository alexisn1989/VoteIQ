# Diagnostic and scratch scripts

Ad-hoc checks, debug probes, and experiment scripts moved out of the repo root
(2026-07-01). None are referenced by production code, CI, or build tooling —
the real test suite lives in `tests/` and runs via `pytest tests/`.

Run from the repo root when needed:

    PYTHONPATH=. python scripts/diagnostics/<name>.py
