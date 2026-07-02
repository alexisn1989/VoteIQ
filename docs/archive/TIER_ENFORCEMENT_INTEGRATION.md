# Tier Enforcement System — Integration Guide

## Overview

VoteIQ's tier enforcement system controls which agents users can access and enforces daily/monthly usage limits based on their subscription tier and chat vertical.

**Three Core Components:**
1. **voteiq/config/tiers.py** — Tier definitions, agent access mappings, usage limits
2. **voteiq/api/middleware/tier_check.py** — Enforcement middleware and error handling
3. **voteiq/services/usage_tracker.py** — Database tracking of user queries

---

## How It Works

### 1. User Makes a Query

```
POST /api/chat
{
  "vertical": "civic",    // Which chat (civic, news, academic, campaign)
  "agent": "analyst",     // Which agent
  "tier": "pro",          // User's subscription tier
  "query": "..."
}
```

### 2. Tier Check Middleware Validates

```python
from voteiq.api.middleware.tier_check import tier_check_middleware
from voteiq.services.usage_tracker import get_usage_tracker

# Initialize tracker
tracker = get_usage_tracker()

# Check access and get current usage
result = tier_check_middleware(
    tier="pro",
    vertical="civic",
    agent="analyst",
    current_usage=tracker.get_usage("user_123", "analyst", "daily")
)

# Returns:
# {
#   "access_granted": True,
#   "remaining_queries": 95,
#   "usage_message": "95/100 queries remaining (daily)",
#   ...
# }
```

### 3. If Access Allowed, Record the Query

```python
tracker.record_query(
    user_id="user_123",
    agent="analyst",
    vertical="civic",
    tier="pro",
    tokens_used=150,
    query_type="standard"
)
```

---

## Integration Example

Here's a complete example of integrating tier enforcement into a chat endpoint:

```python
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from voteiq.api.middleware.tier_check import tier_check_middleware
from voteiq.services.usage_tracker import get_usage_tracker

router = APIRouter()
tracker = get_usage_tracker()


class ChatRequest(BaseModel):
    vertical: str  # "civic", "news", "academic", "campaign"
    agent: str     # "analyst", "news_monitor", etc.
    tier: str      # "free", "pro", "pro_plus", "enterprise"
    query: str
    user_id: str


class ChatResponse(BaseModel):
    reply: str
    usage_info: dict


@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Chat endpoint with tier enforcement."""
    
    # Step 1: Check tier access and usage limits
    try:
        current_usage = tracker.get_usage(req.user_id, req.agent, "daily")
        check_result = tier_check_middleware(
            tier=req.tier,
            vertical=req.vertical,
            agent=req.agent,
            current_usage=current_usage,
        )
    except TierCheckException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except UsageLimitException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    
    # Step 2: Route to appropriate agent
    # (Your existing agent routing logic here)
    response = await route_to_agent(req.agent, req.query)
    
    # Step 3: Record the query
    tracker.record_query(
        user_id=req.user_id,
        agent=req.agent,
        vertical=req.vertical,
        tier=req.tier,
        tokens_used=estimate_tokens(response),
        query_type="standard"
    )
    
    # Step 4: Return response with usage info
    return ChatResponse(
        reply=response,
        usage_info={
            "usage_message": check_result["usage_message"],
            "remaining_queries": check_result["remaining_queries"],
            "reset_date": check_result["reset_date"],
        }
    )
```

---

## Tier & Agent Matrix

### Civic Chat
- **FREE**: analyst (10/day), support_drafts (unlimited)
- **PRO**: + news_monitor, field_monitor, data_analyst (10/day), visual_explainer (25/mo), deep_researcher (3/mo)
- **PRO_PLUS**: analyst (500/mo), + all Pro agents with higher limits
- **ENTERPRISE**: all agents unlimited

### News Chat
- **FREE**: analyst (25/day), news_monitor, support_drafts
- **PRO**: analyst (500/day), + field_monitor, data_analyst (unlimited), visual_explainer (100/mo)
- **PRO_PLUS**: analyst (unlimited), + deep_researcher (20/mo), visual_explainer (500/mo)
- **ENTERPRISE**: all agents unlimited

### Academic Chat
- **FREE**: analyst (unlimited), deep_researcher (1/mo), data_analyst (10/day), support_drafts
- **PRO**: analyst (unlimited), deep_researcher (10/mo), data_analyst (unlimited), visual_explainer (unlimited)
- **PRO_PLUS**: all agents unlimited
- **ENTERPRISE**: all agents unlimited + API access

### Campaign Chat
- **No FREE tier**
- **PRO**: analyst (1000/day), field_monitor, data_analyst (unlimited), visual_explainer (100/mo), support_drafts
- **PRO_PLUS**: analyst (unlimited), field_monitor, deep_researcher (30/mo)
- **ENTERPRISE**: all agents unlimited + API access

---

## API Reference

### Tier Enforcement Middleware

```python
from voteiq.api.middleware.tier_check import tier_check_middleware

result = tier_check_middleware(
    tier="pro",              # User's tier
    vertical="civic",        # Chat vertical
    agent="analyst",         # Agent name
    current_usage=42         # Queries already used this period
)

# Returns dict with:
# - access_granted (bool)
# - tier, vertical, agent, limit, period
# - current_usage, remaining_queries, reset_date
# - usage_message (human-readable)
```

### Usage Tracker

```python
from voteiq.services.usage_tracker import get_usage_tracker

tracker = get_usage_tracker()

# Record a query
tracker.record_query(
    user_id="user_123",
    agent="analyst",
    vertical="civic",
    tier="pro",
    tokens_used=150,
    query_type="standard"
)

# Get usage counts
daily = tracker.get_daily_usage("user_123", "analyst")
monthly = tracker.get_monthly_usage("user_123", "analyst")
current = tracker.get_usage("user_123", "analyst", "daily")

# Get summary for all agents in a vertical
summary = tracker.get_user_usage_summary("user_123", "civic", "pro")
# Returns: {"analyst": {"usage": 42, "period": "daily"}, ...}

# Clear usage (for testing)
tracker.reset_daily_usage("user_123", "analyst")
tracker.reset_monthly_usage("user_123", "analyst")
```

### Helper Functions

```python
from voteiq.config.tiers import (
    can_access_agent,      # Check if tier can use agent
    get_agent_limit,       # Get numeric limit or "unlimited"
    get_limit_period,      # Get "daily" or "monthly"
    get_available_agents,  # List all agents for tier/vertical
)

# Check access without exceptions
if not can_access_agent("free", "civic", "deep_researcher"):
    print("Upgrade required")

# Get limit info
limit = get_agent_limit("pro", "civic", "analyst")  # Returns 100
period = get_limit_period("pro", "civic", "analyst")  # Returns "daily"

# List available agents
agents = get_available_agents("pro", "civic")
# Returns ["analyst", "news_monitor", "field_monitor", ...]
```

---

## Usage Limit Details

### Limit Periods

**Daily Limits:**
- Reset at midnight UTC
- Good for agents with high usage (analyst in News tier: 500/day)
- User sees "X/Y queries remaining (today)"

**Monthly Limits:**
- Reset on 1st of month at midnight UTC
- Used for lower-frequency agents (visual_explainer: 25/mo)
- User sees "X/Y queries remaining (this month)"

### Special Cases

**Unlimited Agents:**
- `support_drafts` is unlimited across all tiers (customer support)
- Enterprise tier agents are unlimited
- Academic Free tier `analyst` is unlimited (no limit for students)

**No Free Tier for Campaign:**
- Campaign Chat requires paid subscription (minimum Pro at $649/yr)
- Rationale: Campaigns have budgets and are serious-use-only

---

## Error Handling

### TierCheckException (403 Forbidden)

```python
try:
    tier_check_middleware("free", "civic", "deep_researcher")
except TierCheckException as e:
    # "Agent 'deep_researcher' is not available in CIVIC chat on FREE tier..."
    return HTTPException(status_code=403, detail=str(e))
```

### UsageLimitException (429 Too Many Requests)

```python
try:
    tier_check_middleware("pro", "civic", "analyst", current_usage=100)
except UsageLimitException as e:
    # "You've reached your daily limit for the analyst agent (100/100 queries used)..."
    return HTTPException(status_code=429, detail=str(e))
```

---

## Testing

### Unit Test Example

```python
import pytest
from voteiq.config.tiers import can_access_agent, get_agent_limit
from voteiq.api.middleware.tier_check import tier_check_middleware, TierCheckException


def test_free_tier_can_access_analyst():
    """Free tier can access analyst in civic chat."""
    assert can_access_agent("free", "civic", "analyst")


def test_free_tier_cannot_access_deep_researcher():
    """Free tier cannot access deep_researcher."""
    assert not can_access_agent("free", "civic", "deep_researcher")


def test_analyst_limit_in_civic_pro():
    """Pro tier has 100 daily analyst queries in civic chat."""
    limit = get_agent_limit("pro", "civic", "analyst")
    assert limit == 100


def test_usage_limit_enforcement():
    """Exceeding usage limit raises exception."""
    with pytest.raises(UsageLimitException):
        tier_check_middleware("pro", "civic", "analyst", current_usage=100)


def test_unlimited_agent_no_limit():
    """Unlimited agents don't raise limit exception."""
    result = tier_check_middleware("free", "civic", "support_drafts", current_usage=1000)
    assert result["access_granted"]
```

### Manual Testing

```bash
# Test tier access via Python REPL
python3
>>> from voteiq.api.middleware.tier_check import tier_check_middleware
>>> result = tier_check_middleware("pro", "civic", "analyst", current_usage=50)
>>> print(result["usage_message"])
50/100 queries remaining (daily)

# Test usage tracking
>>> from voteiq.services.usage_tracker import get_usage_tracker
>>> tracker = get_usage_tracker()
>>> tracker.record_query("test_user", "analyst", "civic", "pro")
>>> print(tracker.get_daily_usage("test_user", "analyst"))
1
```

---

## Next Steps (Phase 2)

1. **Authentication Integration:**
   - Wire up Supabase or similar auth to get user_id and tier from JWT token
   - Populate ChatRequest.tier and user_id from authenticated session

2. **Billing Integration:**
   - Connect Stripe subscription events to tier updates in database
   - Auto-upgrade/downgrade users based on payment status

3. **Email Digests:**
   - Send weekly usage summaries showing query counts per agent
   - Notify users when approaching limits

4. **Usage Dashboard:**
   - Show users their usage per agent per vertical
   - Display upgrade suggestions

5. **API Keys (Enterprise):**
   - Generate per-user API keys for enterprise tier
   - Implement separate rate limiting for API access

---

## Architecture Diagram

```
User Request
    ↓
ChatRequest (tier, agent, vertical, user_id)
    ↓
Tier Check Middleware
    ├─→ can_access_agent(tier, vertical, agent)?
    │   └─→ TierCheckException if no access
    ├─→ get_usage(user_id, agent, period)
    │   └─→ UsageLimitException if over limit
    └─→ Return check_result
    ↓
Route to Agent
    ↓
Agent Response
    ↓
Record Query
    └─→ tracker.record_query(user_id, agent, vertical, tier)
    ↓
Return ChatResponse
    └─→ Include usage_info
```

---

## Configuration

### Tier Definitions

Edit `voteiq/config/tiers.py` to:
- Add new tiers (e.g., "starter", "team")
- Adjust agent access per tier/vertical
- Change usage limits
- Add new agents

### Database Location

Usage tracker defaults to: `voteiq/../../data/voteiq.db`

To customize:
```python
from voteiq.services.usage_tracker import UsageTracker

tracker = UsageTracker(db_path="/custom/path/voteiq.db")
```

---

## Checklist for Implementation

- [ ] **Phase 1A:** Tier definitions (✓ DONE)
  - [x] voteiq/config/tiers.py with all tier/vertical combinations
  - [x] Agent access mappings
  - [x] Usage limit definitions

- [ ] **Phase 1B:** Enforcement middleware (✓ DONE)
  - [x] voteiq/api/middleware/tier_check.py
  - [x] TierCheckException and UsageLimitException
  - [x] Helper functions

- [ ] **Phase 1C:** Usage tracking (✓ DONE)
  - [x] voteiq/services/usage_tracker.py
  - [x] SQLite schema for usage table
  - [x] Query counting methods

- [ ] **Phase 2A:** Integration into chat endpoints
  - [ ] Wire tier_check_middleware into /api/chat
  - [ ] Wire tier_check_middleware into /api/analyst-chat, etc.
  - [ ] Integration test suite

- [ ] **Phase 2B:** Authentication
  - [ ] Add user_id extraction from JWT token
  - [ ] Add tier extraction from subscription database
  - [ ] Add session management

- [ ] **Phase 2C:** Billing integration
  - [ ] Stripe webhook handlers
  - [ ] Subscription update logic
  - [ ] Invoice generation

- [ ] **Phase 3:** User-facing features
  - [ ] Usage dashboard UI
  - [ ] Email digests
  - [ ] Upgrade prompts
  - [ ] API key management (enterprise)

---

## Questions?

Refer to individual module docstrings for detailed API documentation:
- `voteiq/config/tiers.py` — Tier definitions and helpers
- `voteiq/api/middleware/tier_check.py` — Enforcement middleware
- `voteiq/services/usage_tracker.py` — Usage tracking database
