# VoteIQ Truth Governance Layer — Deploy Checklist

Four modules added. None modify existing pipeline logic. Each is safe to call
standalone or to wire into CI/Render cron.

---

## Modules

| File | Role | Output files |
|---|---|---|
| `truth_test_suite.py` | Regression harness for ground-truth Q&A | optional `report.json` |
| `ingest_health_check.py` | Post-ingest anomaly detection | `health_baseline.json` |
| `query_evaluator.py` | Per-response scoring wrapper | `eval_log.jsonl` |
| `drift_monitor.py` | Daily production drift detection | `drift_log.jsonl` |

All output files write to `DATA_DIR` (matches Render persistent disk at `/var/data`).

---

## Environment variables

| Var | Used by | Purpose |
|---|---|---|
| `DATA_DIR` | all four | Output path for `.json`/`.jsonl` files; mirrors existing `config/db.py` pattern |
| `DB_PATH` | `ingest_health_check`, `drift_monitor` | Override polls.db path |
| `ALERT_WEBHOOK_URL` | `drift_monitor` | Slack/Discord/Render webhook for drift alerts |

These are already set on Render via `render.yaml`. No new Render env vars are required
unless you want `ALERT_WEBHOOK_URL`.

---

## Step 1 — Install new dependency

`pyyaml` was added to `requirements.txt`. Run on next deploy or locally:

```bash
pip install pyyaml>=6.0
```

---

## Step 2 — Wire `ingest_health_check.py` into every ingest script

Add at the end of each ingest script (e.g. `ingest_va_finance.py`, `ingest_votes.py`):

```python
import subprocess, sys
result = subprocess.run([sys.executable, "ingest_health_check.py"], capture_output=True)
if result.returncode != 0:
    print(result.stderr.decode(), file=sys.stderr)
    # Optionally raise to abort the deploy:
    # raise RuntimeError("Post-ingest health check failed — aborting")
```

Or add a line to `build.sh` after each ingest call:

```bash
python ingest_health_check.py || { echo "[FATAL] Health check failed"; exit 1; }
```

### First run — write baseline

Run once on a known-clean DB to establish the baseline:

```bash
python ingest_health_check.py --write-baseline
```

This writes `DATA_DIR/health_baseline.json`. Subsequent ingest runs diff against it.

---

## Step 3 — Wire `query_evaluator.py` into the chat pipeline

In `voteiq/api/routes/chat.py`, after the final answer is assembled, add:

```python
from query_evaluator import QueryEvaluator
_evaluator = QueryEvaluator()   # module-level singleton — no DB calls

# Inside the route handler, after building final_answer:
eval_result = _evaluator.evaluate(
    raw_query=query,
    sql_used=sql_string or "",
    rows_returned=db_rows or [],
    rag_chunks=rag_context or [],
    final_answer=final_answer,
)
# eval_result.passed, eval_result.hallucination_risk, etc. are available for logging
```

The evaluator appends to `DATA_DIR/eval_log.jsonl` automatically. It adds <50 ms
overhead and never raises — logging failures are silently swallowed.

---

## Step 4 — Schedule `drift_monitor.py` as a Render cron job

In `render.yaml`, add a cron service:

```yaml
- type: cron
  name: voteiq-drift-monitor
  env: python
  schedule: "0 6 * * *"          # 06:00 UTC daily
  buildCommand: pip install -r requirements.txt
  startCommand: python drift_monitor.py
  envVars:
    - key: DATA_DIR
      value: /var/data
    - key: ALERT_WEBHOOK_URL
      sync: false                 # set in Render dashboard
```

The monitor reads `DATA_DIR/drift_log.jsonl` for its 7-day window and appends a
new snapshot on each run. Alert payload is sent to `ALERT_WEBHOOK_URL` (Slack
`/incoming-webhooks/` URL or any JSON-accepting endpoint).

---

## Step 5 — Wire `truth_test_suite.py` into CI

Add to your CI pipeline (or run manually before a demo):

```bash
python truth_test_suite.py --yaml truth_tests.yaml --threshold 0.90 --output truth_report.json
```

Exit code is 0 on pass, 1 on fail. The `truth_tests.yaml` ships with 10 cases
covering: vote lookups, WinRed/ActBlue awareness, PAC correlation, known-abstain
cases, and entity disambiguation.

Add new test cases to `truth_tests.yaml` as regressions are discovered. The YAML
schema is:

```yaml
tests:
  - query: "..."
    expected_entity: "Name"          # empty string if not applicable
    expected_sql_table: "table_name" # empty string if abstain expected
    expected_answer_contains:
      - "keyword1"
    expected_abstain: false
    expected_hallucination_risk_max: 0.3
```

---

## Risk check

| Module | Demo path touched? | DB writes? | Regression risk |
|---|---|---|---|
| `truth_test_suite.py` | No — read-only pipeline call | No | Low |
| `ingest_health_check.py` | No — read-only DB queries | No (baseline JSON only) | Low |
| `query_evaluator.py` | Yes — wraps chat route output | No (JSONL append only) | Low |
| `drift_monitor.py` | No — daily cron, not in request path | No (JSONL append only) | Low |

The PAC-vote correlation context (`_add_pac_vote_correlation_context`) and vote
retrieval waterfall are untouched by all four modules.
