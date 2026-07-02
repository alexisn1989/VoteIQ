# VoteIQ Tier System — Quick Reference Card

## Import Statements

```python
# Tier definitions
from voteiq.config.tiers import Tier, Vertical, AGENT_TIERS, USAGE_LIMITS
from voteiq.config.tiers import can_access_agent, get_agent_limit, get_limit_period, get_available_agents

# Tier enforcement
from voteiq.api.middleware.tier_check import tier_check_middleware, TierCheckException, UsageLimitException

# Usage tracking
from voteiq.services.usage_tracker import get_usage_tracker, UsageTracker
```

---

## Common Tasks

### Check if user can access an agent

```python
if can_access_agent("pro", "civic", "analyst"):
    print("Access granted")
else:
    print("Upgrade required")
```

### Get agent limit

```python
limit = get_agent_limit("pro", "civic", "analyst")
# Returns: 100 (int), "unlimited" (str), or None

period = get_limit_period("pro", "civic", "analyst")
# Returns: "daily" or "monthly"
```

### List available agents for a tier

```python
agents = get_available_agents("pro", "civic")
# Returns: ["analyst", "news_monitor", "field_monitor", ...]
```

### Check tier + usage in one call

```python
from voteiq.services.usage_tracker import get_usage_tracker

tracker = get_usage_tracker()
current_usage = tracker.get_daily_usage(user_id, "analyst")

try:
    result = tier_check_middleware("pro", "civic", "analyst", current_usage)
    print(result["usage_message"])  # "95/100 queries remaining (daily)"
except TierCheckException as e:
    print("No access:", e.detail)  # 403 Forbidden
except UsageLimitException as e:
    print("Over limit:", e.detail)  # 429 Too Many Requests
```

### Record a query

```python
tracker = get_usage_tracker()
tracker.record_query(
    user_id="user_123",
    agent="analyst",
    vertical="civic",
    tier="pro",
    tokens_used=150,
    query_type="standard"
)
```

### Get usage count

```python
tracker = get_usage_tracker()

# Get count in current period
daily = tracker.get_daily_usage(user_id, "analyst")
monthly = tracker.get_monthly_usage(user_id, "analyst")
current = tracker.get_usage(user_id, "analyst", "daily")

# Get summary for entire vertical
summary = tracker.get_user_usage_summary(user_id, "civic", "pro")
# Returns: {"analyst": {"usage": 42, "period": "daily"}, ...}
```

---

## Tier & Vertical Enum Values

```python
# Tiers
Tier.FREE = "free"
Tier.PRO = "pro"
Tier.PRO_PLUS = "pro_plus"
Tier.ENTERPRISE = "enterprise"

# Verticals
Vertical.CIVIC = "civic"
Vertical.NEWS = "news"
Vertical.ACADEMIC = "academic"
Vertical.CAMPAIGN = "campaign"

# Or use strings directly
can_access_agent("pro", "civic", "analyst")  # ✓ Works
can_access_agent(Tier.PRO, Vertical.CIVIC, "analyst")  # ✓ Also works
```

---

## Tier/Agent Cheat Sheet

| Tier | Civic | News | Academic | Campaign |
|------|-------|------|----------|----------|
| FREE | analyst(10/day) | analyst(25/day) | analyst(∞) | ✗ None |
| PRO | analyst(100/day) + 6 | analyst(500/day) + | analyst(∞) | analyst(1000/day) |
| PRO+ | analyst(500/mo) | analyst(∞) | analyst(∞) | analyst(∞) |
| ENT | all ∞ | all ∞ | all ∞ | all ∞ |

**Legend:**
- `10/day` = 10 queries per day
- `100/mo` = 100 queries per month
- `∞` = unlimited
- `+ 6` = plus 6 other agents (news_monitor, field_monitor, data_analyst, visual_explainer, deep_researcher, support_drafts)
- `✗ None` = no free tier

---

## Error Codes & Messages

### 403 Forbidden — Tier doesn't have access
```
Agent 'deep_researcher' is not available in CIVIC chat on FREE tier.
Please upgrade your subscription or choose a different agent.
```

**Fix:** Upgrade user or choose different agent

### 429 Too Many Requests — Over limit
```
You've reached your daily limit for the analyst agent in civic chat (100/100 queries used).
Your limit resets on the first day of next daily.
Upgrade to a higher tier for increased limits.
```

**Fix:** Wait for reset or upgrade tier

### 500 Internal Server Error — Database issue
Check that `/data/voteiq.db` exists and is writable

---

## Tier Enforcement in Chat Endpoint (Template)

```python
from fastapi import APIRouter, HTTPException
from voteiq.api.middleware.tier_check import tier_check_middleware, TierCheckException, UsageLimitException
from voteiq.services.usage_tracker import get_usage_tracker

router = APIRouter()
tracker = get_usage_tracker()

@router.post("/api/chat")
async def chat(req):
    # Step 1: Get current usage
    try:
        current = tracker.get_usage(req.user_id, req.agent, "daily")
    except Exception:
        current = 0
    
    # Step 2: Check tier + limits
    try:
        check = tier_check_middleware(req.tier, req.vertical, req.agent, current)
    except (TierCheckException, UsageLimitException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    
    # Step 3: Route to agent (your existing logic)
    response = await route_to_agent(req.agent, req.query)
    
    # Step 4: Record query
    tracker.record_query(req.user_id, req.agent, req.vertical, req.tier)
    
    # Step 5: Return response
    return {
        "reply": response,
        "usage": {
            "message": check["usage_message"],
            "remaining": check["remaining_queries"],
            "resets": check["reset_date"]
        }
    }
```

---

## Daily vs Monthly Limits

**Daily Limits:**
- Reset at midnight UTC
- Used for high-frequency agents
- Example: analyst in News tier = 500 queries/day

**Monthly Limits:**
- Reset on 1st of month at midnight UTC
- Used for low-frequency agents
- Example: visual_explainer = 25 queries/month

**Unlimited:**
- Never reset, never limit
- Example: support_drafts, enterprise agents

Check via:
```python
period = get_limit_period("pro", "civic", "analyst")
# Returns "daily" or "monthly" or None (if unlimited)
```

---

## Testing Queries

```python
# Test free tier access
can_access_agent("free", "civic", "analyst")  # True
can_access_agent("free", "civic", "deep_researcher")  # False

# Test limits
get_agent_limit("pro", "civic", "analyst")  # 100
get_limit_period("pro", "civic", "analyst")  # "daily"

# Test all agents for pro tier civic
agents = get_available_agents("pro", "civic")
# ['analyst', 'news_monitor', 'field_monitor', 'data_analyst', 'visual_explainer', 'deep_researcher', 'support_drafts']

# Test usage tracking
tracker = get_usage_tracker()
tracker.record_query("user_1", "analyst", "civic", "pro")
count = tracker.get_daily_usage("user_1", "analyst")
# Returns 1
```

---

## Database Schema

```sql
-- Users
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  tier TEXT NOT NULL,           -- "free", "pro", "pro_plus", "enterprise"
  vertical TEXT NOT NULL,       -- "civic", "news", "academic", "campaign"
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Usage tracking
CREATE TABLE usage (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  agent TEXT NOT NULL,          -- "analyst", "news_monitor", etc.
  vertical TEXT NOT NULL,
  tier TEXT NOT NULL,
  timestamp TEXT NOT NULL,      -- ISO 8601 timestamp
  tokens_used INTEGER,
  query_type TEXT,              -- "standard", "bulk", "realtime"
  FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Index for fast lookups
CREATE INDEX idx_usage_user_agent_date
ON usage(user_id, agent, timestamp);
```

---

## Common Queries

```sql
-- Get daily usage
SELECT COUNT(*) FROM usage
WHERE user_id = 'user_123'
  AND agent = 'analyst'
  AND timestamp >= '2026-05-24T00:00:00'
  AND timestamp < '2026-05-25T00:00:00';

-- Get monthly usage
SELECT COUNT(*) FROM usage
WHERE user_id = 'user_123'
  AND agent = 'analyst'
  AND timestamp >= '2026-05-01T00:00:00'
  AND timestamp < '2026-06-01T00:00:00';

-- Get all agents usage for user
SELECT agent, COUNT(*) as count
FROM usage
WHERE user_id = 'user_123'
GROUP BY agent;
```

---

## Configuration Files

**Location:** `voteiq/config/tiers.py`

**To modify:**
1. Agent access: Edit `AGENT_TIERS` dict
2. Limits: Edit `USAGE_LIMITS` dict
3. Periods: Edit `LIMIT_PERIOD` dict

**Example: Add new agent to Pro tier in Civic**
```python
AGENT_TIERS[Vertical.CIVIC][Tier.PRO].append("new_agent")
USAGE_LIMITS[Vertical.CIVIC][Tier.PRO]["new_agent"] = 50
LIMIT_PERIOD[Vertical.CIVIC][Tier.PRO]["new_agent"] = "daily"
```

---

## Useful One-Liners

```python
# Check entire tier config
from voteiq.config.tiers import AGENT_TIERS, Vertical, Tier
print(AGENT_TIERS[Vertical.CIVIC][Tier.PRO])

# Get all tiers that can access an agent
from voteiq.config.tiers import AGENT_TIERS, Vertical
allowed = [t for t, agents in AGENT_TIERS[Vertical.CIVIC].items() if "deep_researcher" in agents]

# Get all agents
agents = set()
for vertical in AGENT_TIERS.values():
    for tier_agents in vertical.values():
        agents.update(tier_agents)
print(sorted(agents))

# Check if tier can access multiple agents
tier, vertical = "pro", "civic"
for agent in ["analyst", "deep_researcher", "news_monitor"]:
    status = "✓" if can_access_agent(tier, vertical, agent) else "✗"
    print(f"{status} {agent}")
```

---

## FAQ

**Q: What happens if usage tracking fails?**
A: Errors are caught, current_usage defaults to 0, user can proceed

**Q: Can limits be suspended for testing?**
A: Yes, use `tracker.reset_daily_usage()` or `tracker.reset_monthly_usage()`

**Q: How do I export usage data?**
A: Query SQLite directly or use `tracker.get_user_usage_summary()`

**Q: Can I use different limits per user?**
A: Currently no — limits are tier-based. Custom limits require code changes.

**Q: What if user changes tiers mid-month?**
A: Usage carries over. Old period's usage counts toward new period's limit.

**Q: Can I make an agent unlimited?**
A: Yes, set limit to `"unlimited"` in USAGE_LIMITS

---

## Resources

- **Full Guide:** `TIER_ENFORCEMENT_INTEGRATION.md`
- **Summary:** `TIER_SYSTEM_SUMMARY.md`
- **Pricing:** `VOTEIQ_PRICING_TIERS.md`
- **Checklist:** `IMPLEMENTATION_CHECKLIST.md`
- **Code:**
  - `voteiq/config/tiers.py` — Tier definitions (600 lines)
  - `voteiq/api/middleware/tier_check.py` — Enforcement (240 lines)
  - `voteiq/services/usage_tracker.py` — Database (300 lines)

---

**Phase 1 Complete — Ready for Integration! ✓**
