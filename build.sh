#!/bin/bash
# Render Build Script for VoteIQ
# Steps are independently failure-tolerant so a flaky SBE download
# cannot abort the build before governor data is seeded.

echo "=========================================="
echo "VoteIQ Render Build Process"
echo "=========================================="

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
mkdir -p /data

NEED_REBUILD=0

if [ -f /data/polls.db ]; then
    echo "  Found cached /data/polls.db ($(ls -lh /data/polls.db | awk '{print $5}'))"
    cp /data/polls.db polls.db

    # Check if Spanberger 2025 finance data is present
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

    echo "  Spanberger 2025 finance records: $SPANBGR_COUNT"
    if [ "${SPANBGR_COUNT:-0}" -lt 1000 ] 2>/dev/null; then
        echo "  ⚠ Cache stale — scheduling full rebuild"
        NEED_REBUILD=1
        rm -f polls.db /data/polls.db
    else
        echo "  ✓ Cache valid"
    fi
else
    echo "  No cache found — scheduling full rebuild"
    NEED_REBUILD=1
fi

if [ "$NEED_REBUILD" = "1" ]; then
    echo "  Running build_production_data.sh..."
    bash scripts/build_production_data.sh
    BUILD_EXIT=$?
    if [ $BUILD_EXIT -eq 0 ]; then
        cp polls.db /data/polls.db
        echo "  ✓ Finance data built and cached"
    else
        echo "  ⚠ build_production_data.sh exited $BUILD_EXIT — continuing with empty polls.db"
        # Create a minimal polls.db so the app can still start
        python3 -c "import sqlite3; sqlite3.connect('polls.db').close()"
    fi
fi

echo "✓ polls.db ready"

# ── STEP 3: Seed governor tables (always runs, never skipped) ─────────────────
echo ""
echo "[STEP 3] Seeding governor tables (governor_actions + governor_executive_orders)..."

if [ ! -f data/governor_seed.sql ]; then
    echo "✗ data/governor_seed.sql not found — governor data will be missing"
else
    python3 - <<'PYEOF'
import sqlite3, os, sys

db = os.path.join(os.getcwd(), "polls.db")
sql_file = os.path.join(os.getcwd(), "data", "governor_seed.sql")

conn = sqlite3.connect(db)
try:
    with open(sql_file, "r", encoding="utf-8") as f:
        script = f.read()
    conn.executescript(script)
    conn.commit()
    ga = conn.execute("SELECT COUNT(*) FROM governor_actions").fetchone()[0]
    geo = conn.execute("SELECT COUNT(*) FROM governor_executive_orders").fetchone()[0]
    signed = conn.execute(
        "SELECT COUNT(*) FROM governor_actions WHERE action='signed' AND lower(governor) LIKE '%spanberger%'"
    ).fetchone()[0]
    print(f"  governor_actions:         {ga} rows")
    print(f"  governor_executive_orders:{geo} rows")
    print(f"  Spanberger signed bills:  {signed}")
    print("✓ Governor seed data imported")
except Exception as e:
    print(f"✗ Governor seed import failed: {e}", file=sys.stderr)
    sys.exit(1)
finally:
    conn.close()
PYEOF

    SEED_EXIT=$?
    if [ $SEED_EXIT -ne 0 ]; then
        echo "✗ Governor seed failed (exit $SEED_EXIT) — aborting build"
        exit 1
    fi

    # Write seeded DB back to persistent cache
    cp polls.db /data/polls.db
    echo "✓ Persistent cache updated with governor data"
fi

echo ""
echo "=========================================="
echo "Build Complete - Ready for Render Deployment"
echo "=========================================="
