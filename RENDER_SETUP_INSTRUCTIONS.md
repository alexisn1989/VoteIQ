# Render Deployment Setup for VoteIQ

## ✓ Build System Ready

The production build system is now committed to git. Here's how to enable it on Render:

---

## Step 1: Connect to Render

1. Go to [render.com](https://render.com)
2. Sign in with GitHub
3. Click "New +" → "Web Service"
4. Select your VoteIQ repository

---

## Step 2: Configure Build Settings

### Service Name
```
voteiq-api
```

### Environment
```
Python 3.11
```

### Build Command
Render will **automatically detect** `build.sh`:
```
bash build.sh
```
(No need to manually enter — Render looks for build.sh in root)

### Start Command
```
gunicorn app:app
```

### Plan
```
Standard (starts with 0.5 CPU, auto-scales)
```

---

## Step 3: Set Environment Variables

In Render Dashboard → Settings → Environment:

| Key | Value |
|-----|-------|
| `FLASK_ENV` | `production` |
| `PYTHONUNBUFFERED` | `1` |

---

## Step 4: Deploy

Click **"Deploy Service"**

Render will:
1. Clone your repo
2. Run `bash build.sh`
3. Install dependencies
4. Download Virginia SBE data (~5-7 min)
5. Load campaign finance database
6. Start Flask app

---

## What Happens When You Push

Every time you push to `main`:

```
git push origin main
    ↓
Render webhook triggered
    ↓
Render re-runs build.sh
    ↓
  - pip install -r requirements.txt
  - bash scripts/build_production_data.sh
    ↓
    Downloads latest Virginia SBE data
    Refreshes polls.db with 2.3M+ contributions
    Verifies Governor Spanberger data is loaded
    ↓
Restart Flask app with fresh data
```

---

## Monitoring First Deploy

1. **In Render Dashboard**: Go to your service
2. **Click "Logs"** to watch the build
3. **Look for**:
   ```
   [STEP 1] Installing Python dependencies...
   ✓ Dependencies installed

   [STEP 2] Building production data...
   ✓ Campaign finance data loaded successfully
   Database size: 578M
   ```

4. **Expected time**: 5-7 minutes

---

## Success Indicators

✓ **Build succeeded** if you see:
```
==============================
Build Complete - Ready for Render Deployment
==============================
```

✓ **App is running** if you see:
```
Running on https://your-domain.onrender.com
```

❌ **Build failed** if you see:
```
Error: pip install failed
Error: CSV download failed
```

(See PRODUCTION_BUILD_GUIDE.md for troubleshooting)

---

## Accessing Campaign Finance Data

Once deployed, the app will respond to:

```bash
# Test in browser or curl
https://your-domain.onrender.com/api/candidate/Spanberger/campaign-finance

# Response:
{
  "status": "success",
  "candidate_name": "Abigail Spanberger",
  "total_raised": 102786646.62,
  "total_contributions": 292565,
  "donor_sectors": {
    "Individual/Other": {...},
    "PAC/Committee": {...},
    "Technology": {...}
  },
  "top_individual_donors": [
    ["Glen Tullman", 1100000],
    ["Robert Hardie", 710140],
    ...
  ]
}
```

---

## Production URLs

After deployment, your app will be at:
```
https://voteiq-api.onrender.com
```

(Render assigns a free domain automatically)

---

## Cost & Scaling

- **Free tier**: $0/month (includes 750 free hours)
- **Auto-scaling**: Disabled by default, enable in Settings if needed
- **Database**: Included (SQLite loaded in build)
- **Data refreshes**: Daily (every git push rebuilds data)

---

## Common Next Steps

1. **Add custom domain** (Settings → Custom Domains)
2. **Enable auto-deploy** (Settings → Auto-Deploy = On)
3. **Set up health checks** (Settings → Health Check Path = `/health`)
4. **View logs** (Logs tab for debugging)

---

## Still Having Issues?

1. Check **Logs** in Render Dashboard
2. Review **PRODUCTION_BUILD_GUIDE.md**
3. Verify **build.sh** and **scripts/build_production_data.sh** exist
4. Confirm **render.yaml** is in root directory

---

**Status**: ✓ Ready to deploy to Render  
**Build System**: Fully configured and committed  
**Campaign Finance Data**: Auto-loads on every deployment  
**Next Step**: Click "Deploy Service" in Render dashboard
