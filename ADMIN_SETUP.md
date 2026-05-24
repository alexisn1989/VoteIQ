# VoteIQ Admin Setup Guide

## Overview

The VoteIQ Admin System provides internal-only access to specialized agents for operational tasks:
- Code help & debugging
- Script writing & automation
- Documentation & guides
- Deployment & operations
- Testing & QA
- Data analysis & reporting
- Communication drafting

**IMPORTANT: This is INTERNAL-ONLY. All responses are DRAFT-ONLY unless explicitly approved.**

---

## Agent #12: General Admin Utility

**Purpose:** Internal admin assistant for coding, scripts, documentation, deployment, testing, and operational tasks.

**Not for:** Public records, source contradictions, data issues, structured extraction, or complex research (route to specialized agents instead).

---

## Setup

### Step 1: Deploy the Agent

```bash
cd /path/to/VoteIQ

# Make the deployment script executable
chmod +x deploy-general-admin.sh

# Deploy the agent
./deploy-general-admin.sh
```

This creates the agent via Anthropic API and returns an agent ID.

### Step 2: Store the Agent ID

Add the returned agent ID to your `.env` file:

```bash
VOTEIQ_GENERAL_ADMIN_AGENT_ID=agent_xyz123...
```

### Step 3: Enable Admin Mode (Optional)

To use the dashboard API endpoints, add:

```bash
VOTEIQ_ADMIN_MODE=true
```

---

## Usage

### Option A: Command Line (admin_cli.py)

**Single query:**
```bash
python3 admin_cli.py 12 "Help me debug this Python error"
```

**Interactive mode:**
```bash
python3 admin_cli.py 12
# admin> Help me write a deployment script
# admin> How do I parse JSON in Python?
# admin> exit
```

### Option B: HTTP API (Dashboard)

**List available agents:**
```bash
curl -H "X-Admin-Token: YOUR_TOKEN" \
  http://localhost:8000/admin/agents
```

**Call an agent:**
```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: YOUR_TOKEN" \
  -d '{
    "mode": "general_admin",
    "query": "Help me debug this Python error"
  }' \
  http://localhost:8000/admin/chat
```

**Get agent details:**
```bash
curl -H "X-Admin-Token: YOUR_TOKEN" \
  http://localhost:8000/admin/agents/general_admin
```

**Check admin status:**
```bash
curl -H "X-Admin-Token: YOUR_TOKEN" \
  http://localhost:8000/admin/status
```

---

## What the Agent Does

### ✅ You Can Ask For

**Code & Debugging**
- "Help me debug this Python error: ..."
- "How do I parse JSON in Python?"
- "Review this code for security issues"

**Scripts & Automation**
- "Write a deployment script that..."
- "Create a cron job to..."
- "Generate a data migration for..."

**Documentation**
- "Write a README for this project"
- "Create API documentation for..."
- "Draft a deployment runbook for..."

**Deployment & Operations**
- "How do I deploy to production?"
- "Design a system architecture for..."
- "Plan a database migration"

**Testing & QA**
- "Create test cases for..."
- "Write unit tests for..."
- "Build a test data setup for..."

**Data Analysis**
- "Analyze these metrics and generate a report"
- "Calculate statistics for..."
- "Build a visualization for..."

**Drafting Communications**
- "Draft a Slack message about..."
- "Write a grant proposal for..."
- "Create an RFP response for..."

### ❌ You Cannot Ask For

- **Public record facts:** "Who voted for HB 456?" → Route to Analyst
- **Source contradictions:** "FEC shows $5K, VPAP shows $0" → Route to Debugger
- **Data quality issues:** "Votes aren't showing up" → Route to Escalator
- **Data extraction:** "Extract votes from this meeting" → Route to Extractor
- **Complex research:** "What impact did redistricting have?" → Route to Deep Researcher
- **Inferences:** "Did donation X cause vote Y?" → Cannot infer causation
- **Automatic actions:** Agent will NOT post/send/push without explicit approval

---

## Draft-Only & Approval Rules

### ALL RESPONSES ARE DRAFT-ONLY

The agent will:
- Show you drafts BEFORE sending/posting/publishing
- Ask "Shall I send this?" before external actions
- Wait for explicit "yes", "proceed", or "go ahead" before executing
- Log all approvals for audit trail

### NEVER AUTO-EXECUTES:
- ❌ Posts to Slack
- ❌ Sends emails
- ❌ Files Asana tasks
- ❌ Publishes to websites
- ❌ Pushes code to production
- ❌ Deletes data
- ❌ Modifies production systems

### ALWAYS REQUIRES APPROVAL FOR:
- Delete operations
- Production pushes
- Sensitive data access
- External posts/sends
- Any destructive operation

---

## Examples

### Example 1: Code Help
```
Query: "How do I filter a list by value in Python?"

Response:
"Here are several ways:

# Method 1: List comprehension (recommended)
yes_votes = [v for v in votes if v['vote'] == 'yes']

# Method 2: Filter function
yes_votes = list(filter(lambda v: v['vote'] == 'yes', votes))

Which approach fits your use case?"
```

### Example 2: Drafting Communications
```
Query: "Draft a Slack message to the team about the new extractor agent"

Response:
"---
DRAFT - Awaiting Your Approval

Hi @data-team,

We've deployed the VoteIQ Structured Extractor agent.

It can extract structured data from unstructured civic text (meeting minutes, emails, PDFs).

Features:
- Normalizes bill IDs, dates, votes, amounts
- Confidence scores (HIGH/MEDIUM/LOW)
- Audit trail via _source_url

Docs: [link]
Try it: `/admin/chat?mode=structured_extractor`

Questions? Reply here.

---

Does this look good? Ready to post to Slack?"
```

### Example 3: Routing
```
Query: "Who are the major donors to Councilmember Smith?"

Response:
"This is a civic data question. Let me route to the Public Record Analyst, who can:
1. Query VPAP and FEC directly
2. Sort by donation amount
3. Provide official source links

Route to Analyst?"
```

---

## Guard Rails & Safety

### The agent WILL NOT:
- ❌ Infer motive or corruption from data
- ❌ Make accusations or legal claims
- ❌ Bypass approval processes
- ❌ Delete data without permission
- ❌ Push code to production without review
- ❌ Answer civic facts (routes instead)
- ❌ Make promises about timelines

### The agent WILL:
- ✅ Ask for clarification if ambiguous
- ✅ Show drafts before sending
- ✅ Explain what it's doing
- ✅ Provide multiple options
- ✅ Route to specialists when needed
- ✅ Warn about risks
- ✅ Ask for explicit approval
- ✅ Keep audit trail
- ✅ Be honest about limitations

---

## Audit Trail

All operations are logged:
- Who made the request
- What was requested
- What was recommended
- What was approved
- What was executed
- When
- Results

Example:
```
2026-05-24 14:30 | Alexis | Query: Deploy to prod | Recommendation: Run tests first | Approved: Yes | Executed: Yes | Result: v1.2.3 deployed
```

---

## Troubleshooting

### Agent ID not found
```
Error: VOTEIQ_GENERAL_ADMIN_AGENT_ID not set in environment
```

**Fix:** Run `./deploy-general-admin.sh` and add the returned ID to `.env`

### API Key not found
```
Error: ANTHROPIC_API_KEY not set in environment
```

**Fix:** Add your Anthropic API key to `.env`:
```bash
ANTHROPIC_API_KEY=sk-ant-...
```

### Admin token required
```
Error: Admin access required
```

**Fix:** Add `X-Admin-Token` header to HTTP requests (if enabled)

### Agent timeout
If the agent takes too long (>180 seconds), it will timeout. For long operations, break into smaller steps.

---

## Integration

The admin system integrates with:
- **Admin CLI** (`admin_cli.py`) — Command-line interface
- **Admin Dashboard API** (`voteiq/api/routes/admin_dashboard.py`) — HTTP API endpoints
- **Main FastAPI app** (`main.py`) — Registers admin routes

### Adding New Agents

To add a new admin agent:

1. Create the agent via Anthropic API
2. Add to `ADMIN_AGENTS` dict in `admin_dashboard.py`
3. Add mode mapping to `MODES` dict
4. Create deployment script (e.g., `deploy-new-agent.sh`)
5. Document in `ADMIN_SETUP.md`

---

## Important Notes

- **INTERNAL ONLY:** This API should not be exposed to the public
- **DRAFT-ONLY:** All responses are drafts until explicitly approved
- **AUTHORIZATION:** Implement proper admin authentication (JWT, session, etc.)
- **AUDIT:** Log all operations for compliance
- **SCOPE:** Agent routes out-of-scope questions to specialists
- **APPROVAL:** No automatic posts/sends — always show draft first

---

## Questions?

For issues, bugs, or feature requests:
1. Check this guide first
2. Check the agent's routing rules (which specialists to ask)
3. File an issue with details

For civic data questions, route to the appropriate specialist:
- **Civic facts:** Public Record Analyst
- **Source conflicts:** Source Debugger
- **Data issues:** Data Quality Escalator
- **Data extraction:** Structured Extractor
- **Research:** Deep Researcher
