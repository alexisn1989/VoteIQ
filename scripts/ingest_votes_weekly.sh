#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ingest_votes_weekly.sh
#
# Weekly wrapper for ingest_congress_votes.py.
# Designed to be called by cron; handles logging, error detection, and
# log rotation so the log file never grows unbounded.
#
# Crontab entry (runs every Sunday at 03:00):
#   0 3 * * 0 /path/to/project/scripts/ingest_votes_weekly.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PYTHON="${PROJECT_DIR}/.venv/bin/python"          # venv python
INGEST="${PROJECT_DIR}/ingest_congress_votes.py"

LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/ingest_votes.log"
LOG_MAX_BYTES=5242880   # 5 MB — rotate when log exceeds this

# Optional: set DATA_DIR if polls.db lives outside the project root
# export DATA_DIR="/data"

# Optional: set CONGRESS_API_KEY to enrich bill titles via Congress.gov API
# export CONGRESS_API_KEY="your-key-here"

# ── Helpers ───────────────────────────────────────────────────────────────────

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

rotate_log() {
    if [[ -f "$LOG_FILE" ]]; then
        local size
        size=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
        if (( size > LOG_MAX_BYTES )); then
            mv "$LOG_FILE" "${LOG_FILE}.1"
            log "Log rotated (was ${size} bytes)" >> "$LOG_FILE"
        fi
    fi
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────

mkdir -p "$LOG_DIR"
rotate_log

# Redirect all output (stdout + stderr) to the log file, still shown in
# terminal if run manually.
exec > >(tee -a "$LOG_FILE") 2>&1

log "=== ingest_votes_weekly.sh START ==="
log "Project dir : $PROJECT_DIR"
log "Python      : $PYTHON"
log "Script      : $INGEST"

if [[ ! -x "$PYTHON" ]]; then
    log "ERROR: Python not found at $PYTHON"
    log "       Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

if [[ ! -f "$INGEST" ]]; then
    log "ERROR: Ingest script not found at $INGEST"
    exit 1
fi

# ── Determine current Congress session ───────────────────────────────────────
# 119th Congress: Session 1 = 2025, Session 2 = 2026, Session 3 = 2027 ...
# Adjust this logic when the 120th Congress begins (Jan 2027).

CURRENT_YEAR=$(date '+%Y')
CURRENT_MONTH=$(date '+%m')

CONGRESS=119
if   (( CURRENT_YEAR == 2025 )); then SESSION=1; YEAR=2025
elif (( CURRENT_YEAR == 2026 )); then SESSION=2; YEAR=2026
elif (( CURRENT_YEAR == 2027 && CURRENT_MONTH < 3 )); then
    SESSION=2; YEAR=2026   # lame-duck period — still 119th
else
    log "WARNING: Year $CURRENT_YEAR may need a Congress/session update in this script."
    SESSION=2; YEAR=$CURRENT_YEAR
fi

log "Congress=$CONGRESS  Session=$SESSION  Year=$YEAR"

# ── Run ingestion ─────────────────────────────────────────────────────────────

INGEST_ARGS=(
    --congress "$CONGRESS"
    --session  "$SESSION"
    --year     "$YEAR"
    --house-limit  500
    --senate-limit 300
)

# Add --enrich-titles only if the API key is set
if [[ -n "${CONGRESS_API_KEY:-}" ]]; then
    INGEST_ARGS+=(--enrich-titles)
    log "Bill title enrichment: ENABLED (CONGRESS_API_KEY set)"
else
    log "Bill title enrichment: DISABLED (set CONGRESS_API_KEY to enable)"
fi

log "Running: $PYTHON $INGEST ${INGEST_ARGS[*]}"

EXIT_CODE=0
"$PYTHON" "$INGEST" "${INGEST_ARGS[@]}" || EXIT_CODE=$?

# ── Result ────────────────────────────────────────────────────────────────────

if (( EXIT_CODE == 0 )); then
    log "=== ingest_votes_weekly.sh DONE (exit 0) ==="
else
    log "=== ingest_votes_weekly.sh FAILED (exit $EXIT_CODE) ==="

    # ── Optional email alert on failure ───────────────────────────────────────
    # Uncomment and configure if you want failure emails.
    # Requires: apt install mailutils   (or msmtp / ssmtp)
    #
    # ALERT_EMAIL="alexisnieuwenhuys89@gmail.com"
    # if command -v mail &>/dev/null; then
    #     tail -40 "$LOG_FILE" | mail \
    #         -s "[VoteIQ] ingest_votes_weekly FAILED (exit $EXIT_CODE)" \
    #         "$ALERT_EMAIL"
    # fi

    exit $EXIT_CODE
fi
