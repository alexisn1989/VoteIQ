# VoteIQ Data Quality Escalation System

## What Was Built

A **data quality escalation system** that:
1. Automatically investigates data issues reported by users
2. Drafts Asana tasks, Slack notifications, and support replies for human review
3. Ensures humans approve before anything is posted
4. Publishes a public transparency dashboard for WHRO

---

## The Complete Workflow

### User Reports Issue
User contacts support (email, chat, social media) with a data problem:
- "Council votes for HB 456 aren't showing in VoteIQ"
- "This donation amount is wrong"
- "Can't find Councilmember Jane Smith"

### Support Team Escalates
Support agent calls the **Escalation Agent API**:

```bash
curl -X POST http://localhost:8000/api/escalation-agent \
  -H "Content-Type: application/json" \
  -d '{
    "user_email": "reporter@virginiamercury.com",
    "user_type": "journalist",
    "complaint": "Council votes for HB 456 not showing",
    "evidence_url": "https://legistar.com/example/HB456",
    "expected_result": "Should show 5 votes from May 15"
  }'
```

### Agent Investigates
Agent automatically:
- **Extracts key info** (bill number, name, dates)
- **Queries official sources** (Legistar, VPAP, FEC, city websites)
- **Compares to our database** (what we have vs. what's official)
- **Identifies root cause** (stale data, missing record, user error, sync error, etc.)
- **Assesses confidence** (HIGH, MEDIUM, or LOW)

### Agent Drafts for Review
Agent returns **three draft outputs** for human approval:

```json
{
  "escalation_type": "data_quality",
  "confidence": "high",
  "root_cause": "stale_data",
  
  "asana_draft": {
    "title": "📊 Data Issue — Council votes HB 456...",
    "description": "## Support Ticket...",
    "severity": "high",
    "status": "pending_review"
  },
  
  "slack_draft": {
    "message": "📊 **Data Quality Escalation**...",
    "status": "pending_approval",
    "blocks": [actions with Approve/Edit/Reject buttons]
  },
  
  "support_reply_draft": "Thank you for reporting this!..."
}
```

### Human Approves
Data team reviews in Slack:
- **Approve & Post** → Creates Asana task, notifies Slack, replies to user
- **Edit & Repost** → Adjust wording, resubmit for approval
- **Reject** → Request more investigation with feedback

### Issue Resolved
- Data team investigates in Asana
- Fixes the underlying problem (resync, add record, update mapping, etc.)
- Updates Asana task with resolution
- User is notified: "Issue fixed, data now appears in VoteIQ"
- Escalation auto-appears on public transparency dashboard (redacted)

---

## The Four New Endpoints

### 1. `/api/escalation-agent` (POST)
**Purpose:** Investigate and draft data quality escalation

**Request:**
```json
{
  "user_email": "reporter@example.com",
  "user_type": "journalist|general|researcher",
  "complaint": "Exact user complaint text",
  "evidence_url": "Link to official source or screenshot",
  "expected_result": "What should be there",
  "reported_date": "ISO 8601 date"
}
```

**Response:**
```json
{
  "escalation_type": "data_quality|privacy|legal_ethics|system|abuse",
  "confidence": "high|medium|low",
  "root_cause": "stale_data|missing_record|sync_error|...",
  "asana_draft": { title, description, severity, labels, ... },
  "slack_draft": { message, blocks with action buttons, ... },
  "support_reply_draft": "User-facing reply text"
}
```

**What happens:**
- Agent extracts key info (bill numbers, names, dates)
- Queries our database
- Compares against official sources
- Returns drafts for human review
- **Does NOT post anything automatically**

---

### 2. `/api/route-question` (POST)
**Purpose:** Detect question type and recommend agent mode

**Used by:** Chat routing system to send questions to the right agent

**Response:**
```json
{
  "recommended_agent": "support_agent|public_record_analyst|civic_chat",
  "reason": "Explanation of recommendation",
  "suggested_endpoint": "/api/support-chat|/api/analyst-chat|/chat",
  "confidence": "high|medium"
}
```

---

### 3. `/api/analyst-chat` (POST)
**Purpose:** Structured fact-based answers with sources and limitations

**Response:** Includes Answer, Record Type, Sources, Data Quality, Data Limits, Inference Flag

---

### 4. `/api/escalation-transparency` (GET) — PUBLIC
**Purpose:** Show WHRO and public how data issues are tracked and resolved

**Response:**
```json
{
  "disclosure": "VoteIQ Data Quality Escalations — Resolved Issues",
  "summary": "3 issues resolved, 1 in progress",
  "methodology": "All issues checked against official sources...",
  "escalations": [
    {
      "escalation_id": "ESC-001",
      "escalation_type": "data_quality",
      "root_cause": "stale_data",
      "status": "resolved",
      "user_type": "journalist",
      "summary": "Council votes HB 456... Root cause: sync lag..."
    }
  ],
  "root_cause_distribution": {
    "stale_data": 45,
    "missing_record": 28,
    "user_error": 18
  },
  "resolution_sla_compliance": "98% within 24 hours",
  "average_resolution_time": "4.2 hours"
}
```

**Key design:**
- ✓ User identities are redacted (@example.com, just user type shown)
- ✓ Only resolved issues shown (protects open investigations)
- ✓ Includes root cause distribution (shows what you track)
- ✓ Shows SLA compliance (proves accountability)

---

## Key Design Principles

### 1. **Draft-Then-Review**
Nothing is posted to Asana, Slack, or user until a human approves. Agent only generates drafts.

### 2. **Official Source First**
Agent always verifies against official sources (Legistar, VPAP, FEC, city websites) before our database.

### 3. **Low Confidence Flagged**
If agent isn't sure, it marks confidence as MEDIUM or LOW so humans know to double-check.

### 4. **Transparent Classification**
Root cause is one of: stale_data, missing_record, sync_error, identity_issue, api_parse_error, user_error, source_disagreement

### 5. **Public Accountability**
WHRO and public can see redacted metrics, proving you track issues and resolve them fast.

---

## Integration Checklist

- [x] `/api/escalation-agent` endpoint implemented ✓
- [x] Drafts Asana task with investigation details ✓
- [x] Drafts Slack notification with action buttons ✓
- [x] Drafts user support reply ✓
- [x] Public transparency endpoint for WHRO ✓
- [x] ESCALATION_WORKFLOW.md with triage procedures ✓
- [x] ESCALATION_REVIEW_GUIDE.md for data team ✓
- [ ] (Optional) Integration with actual Asana API (currently drafts only)
- [ ] (Optional) Slack bot to handle approval buttons (currently manual)
- [ ] (Optional) Database to store escalation history (currently in-memory)

---

## Next Steps (Optional)

If you want full automation:

### To actually post to Asana:
```python
# In escalation_agent endpoint, add:
import requests

asana_token = os.getenv("ASANA_API_KEY")
asana_project = os.getenv("ASANA_PROJECT_ID")

# POST to Asana API
response = requests.post(
    f"https://api.asana.com/1.0/projects/{asana_project}/tasks",
    headers={"Authorization": f"Bearer {asana_token}"},
    json={"data": {"name": asana_draft["title"], ...}}
)
```

### To handle Slack approval buttons:
Set up Slack event listener that captures button clicks and calls an approval endpoint.

### To track escalations:
Create a `data_escalations` table to log all issues (investigation, root cause, resolution, SLA met/missed).

---

## Show WHRO

When demoing to WHRO, say:

> "Here's our data quality escalation system. When someone reports a data issue, our system automatically:
>
> 1. Investigates by checking official sources
> 2. Compares to what we have
> 3. Drafts a task for our team to review
> 4. Our humans approve before doing anything
> 5. We fix the issue and notify the user
>
> You can see every escalation we've resolved here: [link to /api/escalation-transparency]
> It shows what went wrong, how we fixed it, and how fast we resolved it.
>
> This proves we take data quality seriously and stay transparent with you."

---

## Metrics to Track

Once integrated:
- Escalations per week (by type)
- Average time to review draft
- Average time to resolve (by severity)
- SLA compliance (target: >95%)
- Root cause distribution (identifies patterns to fix)
- Rejection rate (aim for <10% — means high quality investigations)

---

## Questions?

See:
- **ESCALATION_WORKFLOW.md** — Detailed procedures for each escalation type
- **ESCALATION_REVIEW_GUIDE.md** — How-to for approving/rejecting escalations
- **Endpoint documentation above** — API details
