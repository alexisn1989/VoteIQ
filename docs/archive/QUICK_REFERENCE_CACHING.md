# Quick Reference: Data Caching

## TL;DR

- **First deploy**: Takes 5-7 minutes (builds and caches data)
- **Next deploys**: Takes <1 minute (uses cache)
- **New SBE data**: Run `bash scripts/refresh_data.sh` to update cache (3-5 min)

---

## Common Tasks

### Deploy Code Change (No Data Update)
```bash
git push origin main
# Takes: <1 minute
# Uses: Cached data from previous build
```

### Refresh Campaign Finance Data
```bash
# In Render Shell or via CLI:
bash scripts/refresh_data.sh

# Takes: 3-5 minutes
# Does: Downloads latest SBE data, verifies, updates cache
```

### Check Cache Status
```bash
# In Render Shell:
ls -lh /data/polls.db
# Shows: File size and last update time

du -sh /data/
# Shows: Total disk space used
```

### Restore Previous Data Version
```bash
# List backups:
ls -lh /data/polls.db.backup.*

# Restore specific backup:
cp /data/polls.db.backup.20260525_143022 /data/polls.db
```

---

## When to Refresh Data

| Event | Action |
|-------|--------|
| Virginia SBE posts new data | Run `refresh_data.sh` |
| Monthly (1st, 15th) | Check if refresh needed |
| Deploy code changes | Do nothing (use cache) |
| Next election | Build new cache automatically |

---

## Deployment Times

| Action | Time |
|--------|------|
| Deploy code (cache exists) | <1 min |
| Deploy code (first time) | 5-7 min |
| Refresh campaign data | 3-5 min |

---

## Render Shell Commands

```bash
# Check cache status
ls -lh /data/polls.db

# Verify data freshness
sqlite3 /data/polls.db "SELECT MAX(filed_date) FROM va_cf_schedule_a"

# Refresh data
bash scripts/refresh_data.sh

# Restore backup
cp /data/polls.db.backup.TIMESTAMP /data/polls.db

# Clean up old backups (keep last 3)
ls -1t /data/polls.db.backup.* | tail -n +4 | xargs rm
```

---

## Cost Savings

| Before | After | Savings |
|--------|-------|---------|
| 5-7 min per deploy | <1 min per deploy | ~90% compute reduction |
| Network: 500 MB+ | Network: 0 | ~100% per subsequent deploy |
| High CPU usage | Minimal CPU | ~90% reduction |

---

## Troubleshooting

**Q: Why is deployment still slow?**
A: First deploy builds data (5-7 min). Second+ deploys are <1 min. Check: `ls -lh /data/polls.db`

**Q: How do I get the latest SBE data?**
A: Run `bash scripts/refresh_data.sh` in Render Shell

**Q: Can I delete old backups?**
A: Yes, safely delete backups older than 60 days

**Q: What if refresh fails?**
A: Auto-rolls back to previous backup. See logs for details.

---

**Docs**: See `DATA_CACHING_STRATEGY.md` for complete details
