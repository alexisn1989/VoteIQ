#!/bin/bash
# Render Build Script for VoteIQ
# This script runs automatically when you deploy to Render
# It installs dependencies and builds the production data

set -e  # Exit on any error

echo "=========================================="
echo "VoteIQ Render Build Process"
echo "=========================================="

# Install Python dependencies
echo "[STEP 1] Installing Python dependencies..."
pip install -r requirements.txt

# Build production data
echo ""
echo "[STEP 2] Building production data..."
bash scripts/build_production_data.sh

echo ""
echo "=========================================="
echo "Build Complete - Ready for Render Deployment"
echo "=========================================="
