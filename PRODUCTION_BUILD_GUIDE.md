# VoteIQ Production Build Guide

## Overview

When you deploy VoteIQ to Render, the build process automatically:
1. Installs Python dependencies
2. Downloads Virginia SBE campaign finance data (2024-2026)
3. Populates the campaign finance database
4. Verifies data integrity

---

## Build Flow

```
git push
    ↓
Render receives push
    ↓
Runs: bash build.sh
    ↓
  [STEP 1] pip install -r requirements.txt
  [STEP 2] bash scripts/build_production_data.sh
    ↓
      [1/3] Download & process 1103 Virginia SBE CSV files
      [2/3] Verify campaign finance database
      [3/3] Check state officials
    ↓
Render starts Flask app
    ↓
VoteIQ live with campaign finance data
```

---

## Files Involved

| File | Purpose |
|------|---------|
| `build.sh` | Main Render build script (installs deps + runs data build) |
| `scripts/build_production_data.sh` | Data population script (downloads CSVs + loads DB) |
| `build_va_state_finance.py` | Python script that loads Virginia SBE data |
| `render.yaml` | Render configuration (tells Render to use build.sh) |

---

## Deployment Timeline

| Step | Time |
|------|------|
| Git push | Instant |
| Render pulls code | 10 seconds |
| pip install dependencies | 30 seconds |
| Download 1103 CSV files | 2-3 minutes |
| Process & load database | 2-3 minutes |
| Verify data | 30 seconds |
| Start Flask app | 10 seconds |
| **Total deployment** | **~5-7 minutes** |

---

## What Gets Built

### Campaign Finance Database (polls.db)
- **Source**: Virginia State Board of Elections (SBE)
- **Data**: 2024-2026 election cycle contributions
- **Coverage**: All Virginia candidates (state + federal)
- **Size**: ~500-600 MB
- **Records**: 2+ million contributions
- **Key data**: Governor Spanberger ($102.8M), state legislators, etc.

### State Officials Database (legislative_intelligence.db)
- **Records**: 143 Virginia state officials
- **Contents**: Governor, LTG, AG, 40 Senators, 100 House Delegates
- **Size**: ~50 KB (already committed to git)
- **Status**: Pre-loaded, ready for queries

---

## Local Development vs Production

### Local Development
```bash
# Run build script locally to populate databases
bash scripts/build_production_data.sh

# Start Flask app
python app.py
```

### Production (Render)
```
# Push code
git push origin main

# Render automatically:
# 1. Runs build.sh
# 2. Installs dependencies
# 3. Runs build_production_data.sh
# 4. Starts Flask app
```

---

## Monitoring Deployment

### In Render Dashboard
1. Go to your VoteIQ service
2. Click "Logs"
3. Watch for:
   - `[STEP 1] Installing Python dependencies...`
   - `[STEP 2] Building production data...`
   - `✓ Campaign finance data loaded successfully`

### Expected Output
```
========================================
VoteIQ Render Build Process
========================================
[STEP 1] Installing Python dependencies...
✓ Dependencies installed

[STEP 2] Building production data...
[1/3] Building Virginia State Finance Database...
✓ Campaign finance data loaded successfully
  Database size: 578M
[2/3] Verifying campaign finance data...
  Total contributions loaded: 2,311,202
  Sample candidates: Abigail Spanberger, ...
✓ Campaign finance database verified
[3/3] Checking Virginia state officials...
  State officials in database: 143
✓ State officials database verified

==========================================
Production Data Build Complete
==========================================
Status: READY FOR DEPLOYMENT
```

---

## Troubleshooting

### Build takes longer than expected
- **Cause**: Downloading 1103 CSV files is network-dependent
- **Solution**: Normal, expected 5-10 minutes total

### "Build failed: pip install"
- **Cause**: Missing `requirements.txt`
- **Solution**: Ensure `requirements.txt` exists in root directory

### "Build failed: Campaign finance data"
- **Cause**: Virginia SBE server unavailable
- **Solution**: Retry deployment after 1 hour

### Campaign finance data not appearing in chat
- **Cause**: Database built but integration not in chat.py
- **Solution**: Add CampaignFinanceService to chat.py (see CAMPAIGN_FINANCE_CHAT_INTEGRATION.md)

---

## Updating Data

To refresh campaign finance data without code changes:

**Option 1**: Trigger Rebuild
```bash
# Force Render to rebuild
# In Render Dashboard → Service → Deployments → Rebuild Latest
```

**Option 2**: Code Commit
```bash
# Push any code change (even comment)
git add . && git commit -m "Trigger data rebuild" && git push
```

Both automatically run build.sh and refresh the campaign finance database.

---

## Configuration Reference

### render.yaml Fields
- `buildCommand: bash build.sh` — Runs before starting app
- `startCommand: gunicorn app:app` — Starts the Flask app
- `autoDeploy: true` — Auto-deploys on git push to main

### Environment Variables (set in Render Dashboard)
- `FLASK_ENV=production` — Production mode
- `DATABASE_URL` — Connection string (if using external DB)
- `PYTHONUNBUFFERED=1` — Real-time log output

---

## Performance Notes

- **First deploy**: 5-7 minutes (downloads CSVs)
- **Subsequent deploys**: 5-7 minutes (rebuilds fresh data each time)
- **Chat response time**: Not affected by build time
- **Data freshness**: Always current (pulls latest SBE data on each deploy)

---

**Last Updated**: May 25, 2026  
**Status**: Production Ready  
**Build Script Version**: 1.0
