# VoteIQ Escalation Review Guide

## Overview

When a user reports a data issue, the **Escalation Agent**:
1. **Investigates** by querying official sources and our database
2. **Drafts** an Asana task, Slack message, and support reply
3. **Waits for human approval** before posting anything

This guide explains how to review and approve escalations.

---

## The Review Process

### Step 1: Receive Draft in Slack

When an escalation is ready for review, you'll see a Slack message in **#voteiq-data-issues**:

```
📊 **Data Quality Escalation**

**User:** jane@virginiamercury.com (journalist)
**Type:** data_quality
**Root Cause:** stale_data
**Confidence:** HIGH

📎 **Asana Task:** [LINK or placeholder]

---
Review the Asana task and approve before posting to support thread.

[Approve & Post] [Edit & Repost] [Reject]
```

### Step 2: Review the Asana Draft

Click the task link to see the full investigation details:

```
## Support Ticket
User: jane@virginiamercury.com
Type: data_quality
Reported: 2026-05-23

## Complaint
Council votes for HB 456 not showing. 
I found 5 votes recorded on May 15 but VoteIQ returns zero results.

## Evidence
https://legistar.com/example/HB456

## Investigation Results
### Bill Search: HB456
Found 0 matching bills in our database
Warning: No bills found matching HB456

### Primary Source Check
Legistar shows 5 votes on May 15, 2026
Last sync of Legistar data: May 15, 06:00 UTC
Votes cast at: May 15, 14:30 UTC
Gap identified: Sync occurred BEFORE votes were cast

## Root Cause Assessment
**Root Cause:** stale_data
**Confidence:** HIGH

## Next Steps
1. Review investigation results above
2. Query official source if needed
3. Confirm or adjust root cause
4. Plan fix or response
5. Mark as "Ready for Response"
```

### Step 3: Decide

**Three Options:**

#### ✅ APPROVE & POST
- Investigation is correct
- Asana task description is accurate
- Ready to notify user

Click **Approve & Post**, and:
- Asana task is created with the draft content
- Slack notification is posted to #voteiq-data-issues
- User is replied to with the support reply draft
- You're added to the Asana task for visibility

#### ✏️ EDIT & REPOST
- Investigation is good, but wording needs adjustment
- You want to add context or change the severity
- You need to adjust the support message tone

Click **Edit & Repost**:
1. Edit the Asana task description (clarify findings, add notes)
2. Edit the support reply (adjust tone, add resources)
3. Re-submit for approval
4. Agent will incorporate edits and return for final review

#### ❌ REJECT
- Investigation is incomplete or wrong
- Agent misidentified the root cause
- You need to investigate further

Click **Reject**:
1. Add a comment explaining why (e.g., "Agent didn't check the May 15 sync time")
2. Agent will re-investigate with your feedback
3. New draft will be returned for review

---

## Root Cause Quick Reference

### ✓ STALE DATA
**Signs:** 
- Official source has newer data than our DB
- Last sync timestamp is before the event
- Data appears "old" compared to official source

**Typical fix:** Resync from official source, then notify user

**Example:**
- Legistar shows votes at May 15 14:30
- Our sync was May 15 06:00
- Votes are missing → sync_lag issue
- Fix: Resync now, data appears within 1 hour

### ✓ MISSING RECORD
**Signs:**
- Record exists in official source
- Completely absent from our database
- Not a sync timing issue

**Typical fix:** Add record to DB (manual or via import), backfill if old

**Example:**
- FEC shows donation that's nowhere in VPAP
- Check if it's a federal-vs-state scope difference
- If legitimate: Add to DB, notify user

### ✓ USER ERROR
**Signs:**
- Record exists and is correct
- User searched wrong, or expectation was wrong
- Tool is working as designed

**Typical fix:** Educate user, update FAQ

**Example:**
- User searched "John Smith" (first name)
- Tool shows "Smith, John" (last name match)
- Educate: "Try searching by last name"
- Consider FAQ: "Search tips"

### ✓ API/SYNC ERROR
**Signs:**
- Sync failed with an error
- API returned 500, timeout, or rate limit
- Data is incomplete

**Typical fix:** Re-run sync, check API status

**Example:**
- Legistar API returned 500 on May 15 14:00
- Data from that hour is missing
- Fix: Resync 14:00-15:00 window

### ✓ IDENTITY ISSUE
**Signs:**
- Record is in DB but under wrong name/ID
- Multiple records for same person
- Match failed between databases

**Typical fix:** Correct the ID mapping, merge/update records

**Example:**
- Jane Smith appears as "JaneSmith" vs "Smith, Jane"
- Votes are split across two IDs
- Fix: Merge IDs, deduplicate

### ❓ SOURCE DISAGREEMENT
**Signs:**
- Two official sources show different data
- Both sources are legitimate
- Unclear which is "correct"

**Typical fix:** Investigate, document difference, ask for clarification

**Example:**
- FEC shows $5,000 donation
- VPAP shows $0
- FEC might include federal; VPAP might filter it out
- Fix: Clarify scope in methodology, document in FAQ

---

## Approval Checklist

Before clicking **Approve & Post**, verify:

- [ ] **Investigation is accurate**
  - Agent checked official source (Legistar, VPAP, FEC)
  - Compared to what we have in our DB
  - Timestamps and amounts match official source

- [ ] **Root cause is identified**
  - One of: stale_data, missing_record, user_error, sync_error, identity_issue, source_disagreement
  - Not a guess or vague "unknown"

- [ ] **Confidence level matches findings**
  - HIGH: Clear evidence, official source verification
  - MEDIUM: Likely but needs minor confirmation
  - LOW: Needs triage, user needs to provide more info

- [ ] **Asana task is complete**
  - Title is clear and specific
  - Description includes complaint, investigation, root cause
  - Severity is appropriate (high for journalists, critical for data loss)
  - Labels are correct

- [ ] **Support reply is respectful**
  - Tone is helpful, not defensive
  - Explains what went wrong in user-friendly language
  - Provides next steps or ETA
  - Includes tracking link

---

## Common Issues & Solutions

### Agent Says "Confidence: Low"
**Meaning:** Agent couldn't find enough info to be sure.

**What to do:**
1. Reach out to user with clarifying questions (ask agent for suggestions)
2. Once you have more info, resubmit investigation
3. Or close ticket if user doesn't respond

### Agent Found Multiple Possible Root Causes
**Meaning:** Investigation narrowed it down but needs human judgment.

**What to do:**
1. Click **Edit & Repost**
2. Add a note in the Asana task with your judgment
3. Resubmit

### User Complaint Seems Like User Error
**Meaning:** Data is correct, user just didn't search right.

**What to do:**
1. Click **Approve & Post** to create Asana task
2. In support reply, politely explain the search method
3. Point them to FAQ or search tips
4. Mark Asana task as "Won't Fix — User Education"

---

## After Approval

### In Asana
1. Task is created with `pending_review` status
2. Add yourself as assignee
3. Investigate/fix the issue as needed
4. Update status: `in_progress` → `fixed` or `won't_fix`
5. Add a comment summarizing what you did
6. Asana task auto-links to Slack thread

### In Slack
1. Original message updates: "Status: Approved"
2. Data team thread starts with agent's findings
3. You post updates as you work
4. When fixed, post: "Fixed! Votes now appear in VoteIQ."

### Notify User
1. In Asana, mention user's email (if available)
2. Or reply to original support channel
3. Message: "We found and fixed the issue. Votes for HB 456 now appear in VoteIQ."

---

## SLA & Severity

| Severity | Who | SLA | Examples |
|----------|-----|-----|----------|
| **CRITICAL** | Data loss, widespread outage | 4 hours | 500 error on search, missing large batch |
| **HIGH** | Key feature broken, journalist reporting | 24 hours | Missing votes for major bill, stale donation data |
| **MEDIUM** | Single user affected, can be worked around | 48 hours | User can't find councilmember (but can search by title) |
| **LOW** | Edge case, user error, minor missing data | 1 week | Typo in name, old historical data, search refinement needed |

Set severity based on impact + user type:
- Journalist reporting a time-sensitive story → at least HIGH
- Missing data affecting only 1 person → MEDIUM max
- System broken for everyone → CRITICAL

---

## WHRO Transparency

We publish a **public dashboard** at `/api/escalation-transparency` that shows:
- Count of issues resolved (30-day rolling)
- Root cause breakdown
- SLA compliance metrics
- Redacted summaries (no user emails, just types: journalist, general, researcher)

This builds trust by showing:
✓ We track data issues seriously
✓ We resolve them quickly
✓ We're transparent about problems & fixes
✓ We cite official sources

---

## Questions?

If an escalation is unclear or you need more investigation:
1. Click **Edit & Repost**
2. Add a comment with your question
3. Agent will re-investigate and add to the task

Example comment:
```
"Agent: Can you verify the last Legistar sync timestamp? 
I want to confirm the gap before we notify the user."
```

Agent will add a response and resubmit.

---

## Metrics to Track

Every time you approve/reject, the system logs:
- Time to approve (should be <30 min)
- Root cause confirmed or corrected
- SLA met or missed
- User type (journalist, general, researcher)

Monthly review:
- Are we resolving issues within SLA?
- What are the top root causes? (prioritize fixes there)
- Are specific user types reporting more issues? (investigate why)
- Is accuracy high? (measure reject rate — aim for <10%)
