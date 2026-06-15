#!/bin/bash
# Refresh Campaign Finance Data Cache
# Manually trigger this script when new Virginia SBE data is available
# Usage: render exec bash scripts/refresh_data.sh

set -e

echo "=========================================="
echo "VoteIQ Campaign Finance Data Refresh"
echo "=========================================="
echo ""
echo "This will rebuild the campaign finance database"
echo "from latest Virginia SBE data."
echo ""
echo "Note: The app will continue running during refresh"
echo "=========================================="
echo ""

# Resolve persistent disk path (matches build.sh and render.yaml)
DISK="${DATA_DIR:-/var/data}"
echo "  Using disk path: $DISK"

# Ensure data directory exists
mkdir -p "$DISK"

# Create backup of current cache
if [ -f "$DISK/polls.db" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    echo "[BACKUP] Creating backup: $DISK/polls.db.backup.$TIMESTAMP"
    cp "$DISK/polls.db" "$DISK/polls.db.backup.$TIMESTAMP"
    echo "✓ Backup created"
else
    echo "Note: No existing cache to backup"
fi

echo ""
echo "[1/3] Checking Python dependencies..."
if ! python3 -c "import sqlite3" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi
echo "✓ Dependencies ready"

echo ""
echo "[2/3] Downloading and processing Virginia SBE data..."
echo "This may take 3-5 minutes..."
python3 build_va_state_finance.py --since 2024 --output polls.db

if [ $? -ne 0 ]; then
    echo ""
    echo "✗ Build failed - restoring previous cache"
    if [ -f "$DISK/polls.db.backup.$TIMESTAMP" ]; then
        cp "$DISK/polls.db.backup.$TIMESTAMP" "$DISK/polls.db"
    fi
    exit 1
fi

echo "✓ Data downloaded and processed"

echo ""
echo "[3/3] Verifying and caching new data..."
python3 << 'PYEOF'
import sqlite3

conn = sqlite3.connect('polls.db')
cursor = conn.cursor()

try:
    cursor.execute("SELECT COUNT(*) FROM va_cf_schedule_a")
    count = cursor.fetchone()[0]
    print(f"  Total contributions: {count:,}")

    cursor.execute("SELECT SUM(amount) FROM va_cf_schedule_a WHERE candidate_name LIKE '%Spanberger%'")
    spanberger_total = cursor.fetchone()[0]
    print(f"  Spanberger total: ${spanberger_total:,.2f}")

    print("✓ Data verified")
except Exception as e:
    print(f"✗ Verification failed: {e}")
    exit(1)
finally:
    conn.close()
PYEOF

if [ $? -ne 0 ]; then
    echo "✗ Verification failed"
    exit 1
fi

# Move to persistent disk
cp polls.db "$DISK/polls.db"
echo "✓ New data cached to persistent disk"

echo ""
echo "=========================================="
echo "Data Refresh Complete"
echo "=========================================="
echo ""
echo "The app will use the new data on next request"
echo "No restart needed!"
echo ""
echo "Cached data location: $DISK/polls.db"
echo "Backup location: $DISK/polls.db.backup.$TIMESTAMP"
echo ""
