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
