# VoteIQ Data Caching Strategy

## Overview

Campaign finance data is **cached on Render's persistent disk** to avoid rebuilding on every deployment. Since Virginia SBE data only changes monthly, this optimization reduces deployment time from **5-7 minutes to <1 minute**.

---

## How Caching Works

### First Deployment
```
Render receives code push
    ↓
build.sh runs
    ↓
Checks: Does /data/polls.db exist?
    ↓
No → Downloads 1103 Virginia SBE CSV files (2-3 min)
    ↓
Processes and loads data into polls.db (2-3 min)
    ↓
Copies to persistent disk: /data/polls.db
    ↓
App starts with fresh campaign finance data
```
**Total time**: ~5-7 minutes

### Subsequent Deployments (Code Changes Only)
```
Render receives code push
    ↓
build.sh runs
    ↓
Checks: Does /data/polls.db exist?
    ↓
Yes → Copies /data/polls.db to working directory
    ↓
App starts with cached data (no download, no processing)
```
**Total time**: <1 minute

---

## When Data Gets Refreshed

| Scenario | What Happens | Time |
|----------|-------------|------|
| Deploy code change | Uses cached data | <1 min |
| Virginia SBE updates data | Cache is stale, use old data | 0 time |
| **You manually refresh** | Downloads latest SBE data, updates cache | 3-5 min |
| Next election cycle | Create new cache for new cycle | 5-7 min |

---

## Manual Data Refresh

When Virginia SBE publishes new campaign finance data (usually monthly), manually refresh:

### In Render Dashboard

1. Go to your VoteIQ service
2. Click **"Shell"** (top right)
3. Run:
   ```bash
   bash scripts/refresh_data.sh
   ```
4. Wait 3-5 minutes for download and verification
5. Done! App uses new data on next request

### Via Command Line (if you have Render CLI)

```bash
render exec bash scripts/refresh_data.sh
```

### What the Script Does

```
[1/3] Check Python dependencies
[2/3] Download latest Virginia SBE CSV files (3-5 min)
      Process 2.3M+ contributions
      Load into polls.db
[3/3] Verify data integrity
      Check Governor Spanberger total ($102.8M)
      Update persistent cache
      Backup previous version
```

---

## Persistent Disk Details

### Configuration

In `render.yaml`:
```yaml
disks:
  - name: data
    mountPath: /data
    sizeGB: 1
```

### What's Stored

```
/data/
├── polls.db                    # Current cache (578 MB)
├── polls.db.backup.20260525_... # Previous backup
├── polls.db.backup.20260518_... # Older backups
└── polls.db.backup.20260511_... # Historical backups
```

### Storage Capacity

- **Allocated**: 1 GB
- **Currently used**: ~600 MB (polls.db + backups)
- **Available**: ~400 MB for future backups

---

## Data Freshness Timeline

### Scenario: Monthly SBE Update Cycle

```
May 1 - Initial Deploy
  └─ build.sh downloads SBE data through May 1
  └─ Cached to /data/polls.db
  └─ Campaign data current through May 1

May 15 - Code Changes (No Data Update)
  └─ deploy code change
  └─ uses cached /data/polls.db (still May 1 data)
  └─ deployment: <1 minute

May 26 - New SBE Data Released
  └─ SBE publishes updates through May 26
  └─ run: bash scripts/refresh_data.sh
  └─ downloads new SBE data (May 1-26)
  └─ updates cache
  └─ campaign data now current through May 26

June 1 - Another Code Change
  └─ deploy code change
  └─ uses cached /data/polls.db (May 26 data)
  └─ deployment: <1 minute
```

---

## Backup & Recovery

### Automatic Backups

Each time you run `refresh_data.sh`, the old cache is backed up:

```bash
bash scripts/refresh_data.sh
  ↓
  [BACKUP] Creating: /data/polls.db.backup.20260525_143022
  ↓
  Downloads new data
  ↓
  If successful → use new data
  If failed → restore from backup (automatic)
```

### Manual Recovery

If needed, restore an old cache:

```bash
# In Render Shell:
cp /data/polls.db.backup.20260525_143022 /data/polls.db

# App will use restored data on next request
```

---

## Monitoring Cache Status

### Check Current Cache

In Render Shell:
```bash
ls -lh /data/polls.db*
du -sh /data/
```

### Verify Data Currency

```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('/data/polls.db')
cursor = conn.cursor()

cursor.execute("SELECT MAX(filed_date) FROM va_cf_schedule_a")
latest = cursor.fetchone()[0]
print(f"Data current through: {latest}")
EOF
```

---

## Cost Impact

| Metric | Before Caching | After Caching |
|--------|---|---|
| Deployment time | 5-7 min | <1 min |
| CPU usage per deploy | High (CSV download + processing) | Minimal (copy cached file) |
| Network bandwidth | 500 MB+ per deploy | 0 (reuse cache) |
| Render compute cost | Higher | Lower (~90% reduction) |
| Storage cost | Minimal | +1GB persistent disk (~$0.20/month) |

**Net savings**: Deploy faster + lower costs

---

## Troubleshooting

### "Cached data is stale"
→ Run `bash scripts/refresh_data.sh` to get latest SBE data

### "Getting old candidate data"
→ Check when cache was last updated: `ls -lh /data/polls.db`
→ If >30 days old, run refresh script

### "Persistent disk full"
→ Clean up old backups: `rm /data/polls.db.backup.*`
→ Keep only recent 3-4 backups

### "Need data from specific date"
→ Restore from backup: `cp /data/polls.db.backup.DATE /data/polls.db`

---

## Best Practices

1. **Check cache age monthly** — Run `ls -lh /data/polls.db`
2. **Refresh when SBE updates** — Usually 1st and 15th of month
3. **Keep recent backups** — Delete backups older than 60 days
4. **Monitor deployment times** — Should be <1 minute after first deploy
5. **Test after refresh** — Verify campaign finance API still works

---

## Performance Impact

### Before Caching
- Every `git push` triggers 5-7 minute rebuild
- High CPU and network usage
- Slower feedback on code deployments
- Users wait longer for updates to go live

### After Caching
- Code deployments: <1 minute
- Data refresh only when needed: 3-5 minutes
- Low CPU/network for code changes
- Fresh campaign finance data when you want it

---

**Caching Strategy**: Persistent disk at `/data/polls.db`  
**Data Freshness**: Manual refresh when new SBE data available  
**Deployment Speed**: <1 minute for code changes  
**Storage Cost**: ~$0.20/month for 1GB persistent disk
