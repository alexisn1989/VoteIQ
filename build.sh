#!/bin/bash
# Render Build Script for VoteIQ
# Installs dependencies and optionally builds campaign finance data
# Uses persistent disk caching to avoid rebuilding on every deploy

set -e  # Exit on any error

echo "=========================================="
echo "VoteIQ Render Build Process"
echo "=========================================="

# Create persistent data directory
mkdir -p /data

# Install Python dependencies
echo "[STEP 1] Installing Python dependencies..."
pip install -r requirements.txt

# Check if campaign finance data is cached
echo ""
echo "[STEP 2] Checking for cached campaign finance data..."

if [ -f /data/polls.db ]; then
    echo "✓ Found cached polls.db ($(ls -lh /data/polls.db | awk '{print $5}'))"
    cp /data/polls.db polls.db
    echo "✓ Cached data loaded"

    # Validate cache: must have Spanberger finance data (2025 cycle)
    SPANBGR_COUNT=$(python3 -c "
import sqlite3, sys
try:
    c = sqlite3.connect('polls.db').execute(\"SELECT COUNT(*) FROM va_cf_schedule_a WHERE lower(candidate_name) LIKE '%spanberger%' AND election_cycle='2025'\").fetchone()[0]
    print(c)
except:
    print(0)
")
    echo "  Spanberger 2025 finance records in cache: $SPANBGR_COUNT"
    if [ "$SPANBGR_COUNT" -lt 1000 ]; then
        echo "  ⚠ Cache is stale (missing Spanberger finance data) — forcing full rebuild..."
        rm -f polls.db /data/polls.db
        bash scripts/build_production_data.sh
        cp polls.db /data/polls.db
        echo "✓ Full rebuild complete, cache refreshed"
    else
        echo "  ✓ Cache validated — finance data present"
    fi
else
    echo "✗ No cached data found - building Virginia SBE database..."
    bash scripts/build_production_data.sh

    # Cache the data for future deployments
    cp polls.db /data/polls.db
    echo "✓ Data cached to persistent disk for future deployments"
fi

echo ""
echo "[STEP 3] Seeding governor tables (governor_actions + governor_executive_orders)..."
if [ -f data/governor_seed.sql ]; then
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
    ga_count = conn.execute("SELECT COUNT(*) FROM governor_actions").fetchone()[0]
    geo_count = conn.execute("SELECT COUNT(*) FROM governor_executive_orders").fetchone()[0]
    print(f"  governor_actions: {ga_count} rows")
    print(f"  governor_executive_orders: {geo_count} rows")
    print("✓ Governor seed data imported")
except Exception as e:
    print(f"✗ Governor seed import failed: {e}", file=sys.stderr)
    sys.exit(1)
finally:
    conn.close()
PYEOF
    # Refresh the persistent cache so next deploy gets governor data too
    cp polls.db /data/polls.db
    echo "✓ Persistent cache updated with governor data"
else
    echo "✗ data/governor_seed.sql not found — skipping governor seed"
fi

echo ""
echo "=========================================="
echo "Build Complete - Ready for Render Deployment"
echo "=========================================="
