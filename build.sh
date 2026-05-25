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
    echo "  Using cached data - deployment will be instant"
    echo "  To refresh: render exec bash scripts/refresh_data.sh"

    # Copy cached data to working directory
    cp /data/polls.db polls.db
    echo "✓ Cached data loaded"
else
    echo "✗ No cached data found - building Virginia SBE database..."
    bash scripts/build_production_data.sh

    # Cache the data for future deployments
    cp polls.db /data/polls.db
    echo "✓ Data cached to persistent disk for future deployments"
fi

echo ""
echo "=========================================="
echo "Build Complete - Ready for Render Deployment"
echo "=========================================="
