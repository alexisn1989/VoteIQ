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
    print(f"  ERROR: Governor seed import failed: {e}", file=sys.stderr)
    sys.exit(1)
finally:
    conn.close()
PYEOF

    SEED_EXIT=$?
    if [ $SEED_EXIT -ne 0 ]; then
        echo "✗ Governor seed failed (exit $SEED_EXIT) — aborting build"
        exit 1
    fi
    echo "✓ Governor seed complete"
fi

echo ""
echo "=========================================="
echo "Build Complete - Ready for Render Deployment"
echo "=========================================="
echo "  Disk path used: $DISK_PATH"
echo "  DATA_DIR env:   ${DATA_DIR:-(not set)}"
