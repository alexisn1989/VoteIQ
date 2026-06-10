#!/bin/bash
# Render Build Script for VoteIQ
# Uses DATA_DIR env var (set on Render dashboard) for all disk paths.
# Falls back to /data if DATA_DIR is not set.

echo "=========================================="
echo "VoteIQ Render Build Process"
echo "=========================================="

# ── Resolve disk path from Render env var ─────────────────────────────────────
DISK_PATH="${DATA_DIR:-/var/data}"
echo "  DATA_DIR env: ${DATA_DIR:-(not set)}"
echo "  Using disk path: $DISK_PATH"
mkdir -p "$DISK_PATH"

# ── STEP 1: Python dependencies (hard failure — app can't run without these) ──
echo ""
echo "[STEP 1] Installing Python dependencies..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "✗ pip install failed — aborting"
    exit 1
fi
echo "✓ Dependencies installed"

# ── STEP 2: Ensure polls.db exists with campaign finance data ─────────────────
echo ""
echo "[STEP 2] Setting up polls.db (campaign finance)..."

NEED_REBUILD=0

if [ -f "$DISK_PATH/polls.db" ]; then
    echo "  Found cached $DISK_PATH/polls.db ($(ls -lh "$DISK_PATH/polls.db" | awk '{print $5}'))"
    cp "$DISK_PATH/polls.db" polls.db

    # Validate: must have Spanberger 2025 finance data
    SPANBGR_COUNT=$(python3 -c "
import sqlite3
try:
    n = sqlite3.connect('polls.db').execute(
        \"SELECT COUNT(*) FROM va_cf_schedule_a WHERE lower(candidate_name) LIKE '%spanberger%' AND election_cycle='2025'\"
    ).fetchone()[0]
    print(n)
except Exception:
    print(0)
" 2>/dev/null || echo "0")

    echo "  Spanberger 2025 finance records: ${SPANBGR_COUNT:-0}"
    if [ "${SPANBGR_COUNT:-0}" -lt 1000 ] 2>/dev/null; then
        echo "  ⚠ Cache stale — scheduling full rebuild"
        NEED_REBUILD=1
        rm -f polls.db "$DISK_PATH/polls.db"
    else
        echo "  ✓ Cache valid"
    fi
else
    echo "  No cache found at $DISK_PATH/polls.db — scheduling full rebuild"
    NEED_REBUILD=1
fi

if [ "$NEED_REBUILD" = "1" ]; then
    echo "  Running build_production_data.sh..."
    bash scripts/build_production_data.sh
    BUILD_EXIT=$?
    if [ $BUILD_EXIT -eq 0 ]; then
        cp polls.db "$DISK_PATH/polls.db"
        echo "  ✓ Finance data built and cached to $DISK_PATH/polls.db"
    else
        echo "  ⚠ build_production_data.sh exited $BUILD_EXIT — creating empty polls.db fallback"
        python3 -c "import sqlite3; sqlite3.connect('polls.db').close()"
    fi
fi

echo "✓ polls.db ready"

# ── STEP 2a: Schema migration — add missing columns to existing tables ─────────
echo ""
echo "[STEP 2a] Running schema migrations (safe ALTER TABLE ADD COLUMN)..."
python3 - <<PYEOF
import sqlite3, os, sys

data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
conn = sqlite3.connect(db)

migrations = [
    # (table, column, type, default)
    ("campaign_finance_summary", "overall_va_pct",  "REAL", "NULL"),
    ("campaign_finance_summary", "alignment_json",  "TEXT", "NULL"),
    ("campaign_finance_summary", "cycles_json",     "TEXT", "NULL"),
    ("va_committee_assignments", "chamber",         "TEXT", "NULL"),
    ("va_committee_assignments", "session",         "TEXT", "NULL"),
]

ran = []
skipped = []
for table, col, coltype, default in migrations:
    try:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col in existing:
            skipped.append(f"{table}.{col}")
        else:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype} DEFAULT {default}")
            conn.commit()
            ran.append(f"{table}.{col}")
    except Exception as e:
        print(f"  WARN: {table}.{col}: {e}", file=sys.stderr)

if ran:
    print(f"  Migrations applied: {', '.join(ran)}")
if skipped:
    print(f"  Already present:    {', '.join(skipped)}")
print("  Schema migration complete")
conn.close()
PYEOF
echo "✓ Schema migration done"

# ── STEP 2b0: Seed campaign_finance_summary (168 rows — required for profiles) ─
echo ""
echo "[STEP 2b0] Seeding campaign_finance_summary..."

if [ ! -f data/campaign_finance_summary_seed.sql ]; then
    echo "⚠ data/campaign_finance_summary_seed.sql not found — legislator profiles will be incomplete"
else
    python3 - <<PYEOF
import sqlite3, os, sys

data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
conn = sqlite3.connect(db)
try:
    existing = conn.execute("SELECT COUNT(*) FROM campaign_finance_summary").fetchone()[0]
    # Re-seed if columns might be stale (old builds lacked alignment_json)
    has_align = any(r[1] == "alignment_json"
                    for r in conn.execute("PRAGMA table_info(campaign_finance_summary)").fetchall())
    if existing >= 100 and has_align:
        print(f"  Already populated ({existing} rows, alignment_json present) — skipping")
        sys.exit(0)
    print(f"  Existing rows: {existing}, has alignment_json: {has_align} — reseeding")
except Exception:
    pass
try:
    with open("data/campaign_finance_summary_seed.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM campaign_finance_summary").fetchone()[0]
    print(f"  campaign_finance_summary: {n} rows")
except Exception as e:
    print(f"  WARNING: campaign_finance_summary seed failed: {e}", file=sys.stderr)
finally:
    conn.close()
PYEOF
    [ $? -ne 0 ] && echo "⚠ campaign_finance_summary seed failed" || echo "✓ campaign_finance_summary seeded"
fi

# ── STEP 2b: Seed legislator vote/bill summary tables ────────────────────────
echo ""
echo "[STEP 2b] Seeding legislator vote summary tables..."
if python3 -c "import sqlite3; sqlite3.connect('polls.db').execute('SELECT 1 FROM va_legislator_vote_summary LIMIT 1')" 2>/dev/null; then
    echo "  va_legislator_vote_summary already present — skipping seed"
else
    echo "  Building legislator seed data..."
    python3 generate_legislators_seed.py 2>&1 | tail -5
    echo "  ✓ Legislator tables seeded"
fi

# ── STEP 3: Seed governor tables — always runs, writes to DATA_DIR ────────────
echo ""
echo "[STEP 3] Seeding governor tables (governor_actions + governor_executive_orders)..."

if [ ! -f data/governor_seed.sql ]; then
    echo "✗ data/governor_seed.sql not found — governor data will be missing"
else
    python3 - <<PYEOF
import sqlite3, os, sys

# Write directly to DATA_DIR so the running app reads the seeded data
data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
sql_file = os.path.join(os.getcwd(), "data", "governor_seed.sql")

print(f"  Seeding DB at: {db}")
print(f"  Seed file:     {sql_file}")

conn = sqlite3.connect(db)
try:
    with open(sql_file, "r", encoding="utf-8") as f:
        script = f.read()
    conn.executescript(script)
    conn.commit()
    ga  = conn.execute("SELECT COUNT(*) FROM governor_actions").fetchone()[0]
    geo = conn.execute("SELECT COUNT(*) FROM governor_executive_orders").fetchone()[0]
    signed = conn.execute(
        "SELECT COUNT(*) FROM governor_actions WHERE action='signed' AND lower(governor) LIKE '%spanberger%'"
    ).fetchone()[0]
    print(f"  governor_actions:          {ga} rows")
    print(f"  governor_executive_orders: {geo} rows")
    print(f"  Spanberger signed bills:   {signed}")
    print("  Governor seed data imported OK")
except Exception as e:
    print(f"  WARNING: Governor seed import failed: {e}", file=sys.stderr)
    print("  NOTE: App lifespan will re-seed governor data at startup — build continues.")
finally:
    conn.close()
PYEOF

    SEED_EXIT=$?
    if [ $SEED_EXIT -ne 0 ]; then
        echo "⚠ Governor seed failed (exit $SEED_EXIT) — continuing build (lifespan will retry at startup)"
    else
        echo "✓ Governor seed complete"
    fi
fi

# ── STEP 4: Seed VEC financial disclosures ────────────────────────────────────
echo ""
echo "[STEP 4] Seeding VEC legislator financial disclosures..."

if [ ! -f data/vec_disclosures_seed.sql ]; then
    echo "⚠ data/vec_disclosures_seed.sql not found — disclosures data will be missing"
else
    python3 - <<PYEOF
import sqlite3, os, sys

data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
sql_file = os.path.join(os.getcwd(), "data", "vec_disclosures_seed.sql")

print(f"  Seeding DB at: {db}")
conn = sqlite3.connect(db)
try:
    # Skip if already populated
    existing = conn.execute("SELECT COUNT(*) FROM legislator_financial_disclosures").fetchone()[0]
    if existing >= 3000:
        print(f"  Already populated ({existing:,} rows) — skipping")
        sys.exit(0)
except Exception:
    pass  # table doesn't exist yet, proceed

try:
    with open(sql_file, "r", encoding="utf-8") as f:
        script = f.read()
    conn.executescript(script)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM legislator_financial_disclosures").fetchone()[0]
    print(f"  legislator_financial_disclosures: {n:,} rows")
    print("  VEC disclosures seed imported OK")
except Exception as e:
    print(f"  WARNING: VEC disclosures seed failed: {e}", file=sys.stderr)
finally:
    conn.close()
PYEOF

    SEED_EXIT=$?
    if [ $SEED_EXIT -ne 0 ]; then
        echo "⚠ VEC disclosures seed failed (exit $SEED_EXIT) — continuing build"
    else
        echo "✓ VEC disclosures seed complete"
    fi
fi

# ── STEP 4a: Seed lobbyist registrations (required for testimony proxy) ───────
echo ""
echo "[STEP 4a] Seeding lobbyist_registrations..."

if [ ! -f data/lobbyist_registrations_seed.sql ]; then
    echo "⚠ data/lobbyist_registrations_seed.sql not found — testimony proxy will produce 0 rows"
else
    python3 - <<PYEOF
import sqlite3, os, sys

data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
conn = sqlite3.connect(db)
try:
    existing = conn.execute("SELECT COUNT(*) FROM lobbyist_registrations").fetchone()[0]
    if existing >= 5000:
        print(f"  Already populated ({existing:,} rows) — skipping")
        sys.exit(0)
except Exception:
    pass
try:
    with open("data/lobbyist_registrations_seed.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM lobbyist_registrations").fetchone()[0]
    print(f"  lobbyist_registrations: {n:,} rows")
except Exception as e:
    print(f"  WARNING: {e}", file=sys.stderr)
finally:
    conn.close()
PYEOF
    [ $? -ne 0 ] && echo "⚠ Lobbyist seed failed — testimony proxy may be empty" || echo "✓ Lobbyist registrations seeded"
fi

# ── STEP 4a2: Seed legiscan_va_bills (bill titles for testimony proxy) ────────
echo ""
echo "[STEP 4a2] Seeding legiscan_va_bills..."

if [ ! -f data/legiscan_va_bills_seed.sql ]; then
    echo "⚠ data/legiscan_va_bills_seed.sql not found — testimony proxy bill titles will be missing"
else
    python3 - <<PYEOF
import sqlite3, os, sys

data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
conn = sqlite3.connect(db)
try:
    existing = conn.execute("SELECT COUNT(*) FROM legiscan_va_bills").fetchone()[0]
    if existing >= 10000:
        print(f"  Already populated ({existing:,} rows) — skipping")
        sys.exit(0)
except Exception:
    pass
try:
    with open("data/legiscan_va_bills_seed.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM legiscan_va_bills").fetchone()[0]
    print(f"  legiscan_va_bills: {n:,} rows")
except Exception as e:
    print(f"  WARNING: {e}", file=sys.stderr)
finally:
    conn.close()
PYEOF
    [ $? -ne 0 ] && echo "⚠ LegiScan bills seed failed" || echo "✓ legiscan_va_bills seeded"
fi

# ── STEP 4b: Seed committee assignments (chairs, vice-chairs, members) ────────
echo ""
echo "[STEP 4b] Seeding va_committee_assignments..."

if [ ! -f data/committee_assignments_seed.sql ]; then
    echo "⚠ data/committee_assignments_seed.sql not found — chair lookup will be unavailable"
else
    python3 - <<PYEOF
import sqlite3, os, sys

data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
sql_file = os.path.join(os.getcwd(), "data", "committee_assignments_seed.sql")

conn = sqlite3.connect(db)
try:
    existing = conn.execute("SELECT COUNT(*) FROM va_committee_assignments").fetchone()[0]
    if existing >= 400:
        print(f"  Already populated ({existing:,} rows) — skipping")
        sys.exit(0)
except Exception:
    pass

try:
    with open(sql_file, "r", encoding="utf-8") as f:
        script = f.read()
    conn.executescript(script)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM va_committee_assignments").fetchone()[0]
    chairs = conn.execute("SELECT COUNT(*) FROM va_committee_assignments WHERE role='chair'").fetchone()[0]
    print(f"  va_committee_assignments: {n:,} rows ({chairs} chairs)")
except Exception as e:
    print(f"  WARNING: committee assignments seed failed: {e}", file=sys.stderr)
finally:
    conn.close()
PYEOF

    if [ $? -ne 0 ]; then
        echo "⚠ Committee assignments seed failed — continuing build"
    else
        echo "✓ Committee assignments seed complete"
    fi
fi

# ── STEP 4c: Seed federal data (congress members, FEC totals, bills, hearings) ─
echo ""
echo "[STEP 4c] Seeding federal data tables..."

if [ ! -f data/federal_data_seed.sql ]; then
    echo "⚠ data/federal_data_seed.sql not found — Kiggans/federal queries will be incomplete"
else
    python3 - <<PYEOF
import sqlite3, os, sys

data_dir = os.environ.get("DATA_DIR", os.getcwd())
db = os.path.join(data_dir, "polls.db")
conn = sqlite3.connect(db)
try:
    existing = conn.execute("SELECT COUNT(*) FROM fec_industry_totals").fetchone()[0]
    if existing >= 400:
        print(f"  Already populated ({existing:,} fec_industry_totals rows) — skipping")
        sys.exit(0)
except Exception:
    pass
try:
    with open("data/federal_data_seed.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    results = {}
    for t in ["congress_members", "fec_industry_totals", "fec_independent_expenditures",
              "federal_bills", "congress_committees", "congress_hearings"]:
        try:
            results[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            results[t] = "MISSING"
    for t, n in results.items():
        print(f"  {t}: {n:,}" if isinstance(n, int) else f"  {t}: {n}")
    print("  Federal data seed imported OK")
except Exception as e:
    print(f"  WARNING: Federal data seed failed: {e}", file=sys.stderr)
finally:
    conn.close()
PYEOF
    [ $? -ne 0 ] && echo "⚠ Federal data seed failed — federal queries may be incomplete" || echo "✓ Federal data seeded"
fi

# ── STEP 4d: Ingest congress roll-call votes (VA delegation) ──────────────────
echo ""
echo "[STEP 4d] Checking congress_votes table (VA roll-call ingestion)..."

# Resolve the actual DB directory: use DATA_DIR only if it exists as a dir,
# otherwise fall back to the project source directory (cwd during build).
CV_DATA_DIR="${DATA_DIR:-}"
if [ -z "$CV_DATA_DIR" ] || [ ! -d "$CV_DATA_DIR" ]; then
    CV_DATA_DIR="$(pwd)"
fi
echo "  Resolved DB dir: $CV_DATA_DIR"

CV_STATUS=$(python3 -c "
import sqlite3, os
from datetime import datetime, timezone
db = os.path.join('${CV_DATA_DIR}', 'polls.db')
try:
    conn = sqlite3.connect(db)
    n = conn.execute('SELECT COUNT(*) FROM congress_votes').fetchone()[0]
    last = conn.execute('SELECT MAX(vote_date) FROM congress_votes').fetchone()[0]
    age = 9999
    if last:
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(last[:10])).days
    print(f'{n},{age}')
    conn.close()
except Exception:
    print('0,9999')
" 2>/dev/null || echo "0,9999")

CV_ROWS=$(echo "$CV_STATUS" | cut -d',' -f1)
CV_AGE=$(echo  "$CV_STATUS" | cut -d',' -f2)

echo "  congress_votes rows: ${CV_ROWS}  /  newest entry age: ${CV_AGE} days"

if [ "${CV_ROWS:-0}" -lt 500 ] || [ "${CV_AGE:-9999}" -gt 14 ]; then
    echo "  Running ingest_congress_votes.py ..."

    CURRENT_YEAR=$(date '+%Y')
    if   [ "$CURRENT_YEAR" = "2025" ]; then CV_SESSION=1; CV_YEAR=2025
    elif [ "$CURRENT_YEAR" = "2026" ]; then CV_SESSION=2; CV_YEAR=2026
    else CV_SESSION=2; CV_YEAR=$CURRENT_YEAR; fi

    DATA_DIR="$CV_DATA_DIR" python3 ingest_congress_votes.py \
        --congress 119 \
        --session  "$CV_SESSION" \
        --year     "$CV_YEAR" \
        --house-limit  300 \
        --senate-limit 200 \
        2>&1 | tail -10

    CV_ROWS_AFTER=$(python3 -c "
import sqlite3, os
db = os.path.join('${CV_DATA_DIR}', 'polls.db')
try:
    print(sqlite3.connect(db).execute('SELECT COUNT(*) FROM congress_votes').fetchone()[0])
except Exception:
    print(0)
" 2>/dev/null || echo "0")
    echo "  congress_votes after ingest: ${CV_ROWS_AFTER} rows"
    if [ "${CV_ROWS_AFTER:-0}" -gt 0 ] 2>/dev/null; then
        echo "✓ Congress votes ingested"
    else
        echo "⚠ Ingest produced 0 rows — vote context will be limited (non-fatal)"
    fi
else
    echo "  ✓ Already populated and fresh — skipping ingest"
fi

# ── STEP 4e: Build federal vote-donor alignment ───────────────────────────────
echo ""
echo "[STEP 4e] Building federal vote-donor alignment..."
FA_COUNT=$(python3 -c "
import sqlite3, os
_dd = os.environ.get('DATA_DIR', os.getcwd())
db = os.path.join(_dd if os.path.isdir(_dd) else os.getcwd(), 'polls.db')
try:
    n = sqlite3.connect(db).execute(
        \"SELECT COUNT(*) FROM donor_vote_alignment WHERE legislator_id LIKE 'bioguide:%'\"
    ).fetchone()[0]
    print(n)
except Exception:
    print(0)
" 2>/dev/null || echo "0")

if [ "${FA_COUNT:-0}" -lt 5 ]; then
    echo "  Running build_federal_vote_alignment.py..."
    DATA_DIR="$CV_DATA_DIR" python3 build_federal_vote_alignment.py 2>&1 | tail -5
else
    echo "  Already have ${FA_COUNT} federal alignment rows — skipping"
fi

# ── STEP 5: Build committee_testimony_proxy (derived from polls.db) ───────────
echo ""
echo "[STEP 5] Building committee testimony proxy (all sessions)..."

PROXY_COUNT=$(python3 -c "
import sqlite3, os
db = os.path.join(os.environ.get('DATA_DIR', os.getcwd()), 'polls.db')
try:
    n = sqlite3.connect(db).execute('SELECT COUNT(*) FROM committee_testimony_proxy').fetchone()[0]
    print(n)
except Exception:
    print(0)
" 2>/dev/null || echo "0")

echo "  Existing committee_testimony_proxy rows: ${PROXY_COUNT:-0}"

if [ "${PROXY_COUNT:-0}" -ge 100000 ] 2>/dev/null; then
    echo "  ✓ Already populated — skipping rebuild"
else
    echo "  Building testimony proxy for sessions 2021-2026..."
    # Point build script at the Render persistent DB
    export POLLS_DB="$DISK_PATH/polls.db"
    for SESSION in 2021 2022 2023 2024 2025 2026; do
        echo "  → Session $SESSION..."
        python3 build_testimony_proxy.py --session "$SESSION" 2>&1 | grep -E "(Total proxy|WARNING|ERROR|✗)" || true
    done
    TOTAL=$(python3 -c "
import sqlite3, os
db = os.path.join(os.environ.get('DATA_DIR', os.getcwd()), 'polls.db')
try:
    print(sqlite3.connect(db).execute('SELECT COUNT(*) FROM committee_testimony_proxy').fetchone()[0])
except Exception:
    print(0)
" 2>/dev/null || echo "0")
    echo "  Total committee_testimony_proxy rows: ${TOTAL:-0}"
    if [ "${TOTAL:-0}" -gt 0 ] 2>/dev/null; then
        echo "✓ Testimony proxy build complete"
    else
        echo "⚠ Testimony proxy build produced 0 rows — chat lobbying context will be unavailable"
    fi
fi

# ── STEP 6: Validate polls.db integrity (report only — never blocks deploy) ───
echo ""
echo "[STEP 6] Validating polls.db integrity..."
python3 -c "
import sqlite3, os, sys
db = os.path.join(os.environ.get('DATA_DIR', '.'), 'polls.db')
checks = [
    ('va_bills',                         1000, 'bills'),
    ('lobbyist_registrations',            500, 'lobbyist registrations'),
    ('committee_testimony_proxy',       50000, 'testimony proxy rows'),
    ('legislator_financial_disclosures', 3000, 'VEC disclosures'),
    ('campaign_finance_summary',          100, 'campaign finance summaries'),
    ('governor_actions',                  100, 'governor actions'),
    ('va_committee_assignments',           50, 'committee assignments'),
    ('congress_votes',                    500, 'congress roll-call votes'),
]
try:
    conn = sqlite3.connect(db)
    degraded = []
    for table, minimum, label in checks:
        try:
            n = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            ok = n >= minimum
            icon = 'OK' if ok else 'WARN'
            print(f'  [{icon}] {label}: {n:,} (min {minimum:,})')
            if not ok:
                degraded.append(label)
        except Exception as e:
            print(f'  [MISS] {label}: table missing — {e}')
            degraded.append(label)
    conn.close()
    if degraded:
        print(f'  DEGRADED features: {\", \".join(degraded)}')
        print('  NOTE: App will start but some features will return empty results.')
    else:
        print('  All features healthy.')
except Exception as e:
    print(f'  Could not open DB: {e}')
sys.exit(0)  # validation never blocks deploy
"

echo ""
echo "=========================================="
echo "Build Complete - Ready for Render Deployment"
echo "=========================================="
echo "  Disk path used: $DISK_PATH"
echo "  DATA_DIR env:   ${DATA_DIR:-(not set)}"
