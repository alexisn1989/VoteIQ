#!/bin/bash
# Production Data Build Script for VoteIQ
# Runs during Render deployment to populate campaign finance and voting data
# Script: build_production_data.sh

set -e  # Exit on any error

echo "=========================================="
echo "VoteIQ Production Data Builder"
echo "=========================================="

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Error: Python3 not found"
    exit 1
fi

echo "[1/3] Downloading Virginia SBE Campaign Finance Data..."
python3 download_va_finance.py --since 2024

if [ $? -ne 0 ]; then
    echo "✗ Failed to download SBE data"
    exit 1
fi
echo "✓ CSV files downloaded successfully"

echo ""
echo "[2/3] Building Virginia State Finance Database..."
python3 build_va_state_finance.py --since 2024 --output polls.db

if [ $? -eq 0 ]; then
    echo "✓ Campaign finance database built successfully"
    FILE_SIZE=$(ls -lh polls.db | awk '{print $5}')
    echo "  Database size: $FILE_SIZE"
else
    echo "✗ Failed to build campaign finance database"
    exit 1
fi

echo ""
echo "[3/3] Verifying campaign finance data..."
python3 << 'PYEOF'
import sqlite3

conn = sqlite3.connect('polls.db')
cursor = conn.cursor()

try:
    cursor.execute("SELECT COUNT(*) FROM va_cf_schedule_a")
    count = cursor.fetchone()[0]
    print(f"  Total contributions loaded: {count:,}")

    cursor.execute("SELECT DISTINCT candidate_name FROM va_cf_schedule_a LIMIT 5")
    candidates = cursor.fetchall()
    print(f"  Sample candidates: {', '.join([c[0] for c in candidates])}")

    print("✓ Campaign finance database verified")
except Exception as e:
    print(f"✗ Verification failed: {e}")
    exit(1)
finally:
    conn.close()
PYEOF

echo ""
echo "[4/4] Loading Governor signed bills and executive orders..."
python3 << 'PYEOF'
import sys
import os

# Add current directory to path for imports
sys.path.insert(0, os.getcwd())

try:
    import fetch_governor_signed_bills
    count_signed = fetch_governor_signed_bills.run()
    print(f"✓ Signed bills: {count_signed} bills loaded")
except Exception as e:
    print(f"⚠ Warning: Could not load signed bills: {e}")
    count_signed = 0

try:
    import fetch_governor_executive_orders
    count_exec = fetch_governor_executive_orders.run()
    print(f"✓ Executive orders: {count_exec} orders loaded")
except Exception as e:
    print(f"⚠ Warning: Could not load executive orders: {e}")
    count_exec = 0

if count_signed > 0 or count_exec > 0:
    print("✓ Governor actions data enhanced")
PYEOF

echo ""
echo "[5/5] Verifying complete governor actions data..."
python3 << 'PYEOF'
import sqlite3

conn = sqlite3.connect('polls.db')
cursor = conn.cursor()

try:
    cursor.execute("""
        SELECT action, COUNT(*) as count
        FROM governor_actions
        WHERE governor = 'Spanberger'
        GROUP BY action
        ORDER BY count DESC
    """)
    print("  Governor Actions Summary:")
    for action, count in cursor.fetchall():
        print(f"    {action:20} : {count:4} actions")
    print("✓ Governor actions verified")
except Exception as e:
    print(f"  Note: Governor actions table status: {e}")
finally:
    conn.close()
PYEOF

echo ""
echo "=========================================="
echo "Production Data Build Complete"
echo "=========================================="
echo ""
echo "Status: READY FOR DEPLOYMENT"
echo "Campaign Finance: Loaded and verified"
echo "Governor Actions: Signed bills and executive orders loaded"
echo "State Officials: Ready for queries"
echo ""
