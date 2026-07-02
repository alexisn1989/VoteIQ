# User Feedback Loop — Quick Reference

## The Rule

**Users can report issues → System investigates → User gets answer within 5 days**

---

## Five Feedback Types & Routing

| Type | Example | Route | Timeline |
|------|---------|-------|----------|
| **Data Issue** | "This donation is wrong" | Data Quality Escalator | 5 days |
| **Correlation Issue** | "This r=0.45 shouldn't show" | Data Analyst | 5 days |
| **Missing Context** | "Why no VPAP data?" | Search Assistant | 5 days |
| **Visualization Issue** | "Chart is misleading" | Visual Explainer | 5 days |
| **Methodology Issue** | "Wrong analysis" | Deep Researcher | 5 days |

---

## Feedback Flow

```
USER REPORTS ISSUE
        ↓
FEEDBACK_COLLECTOR:
├─ Acknowledge receipt (same day)
├─ Classify issue type
├─ Generate Feedback ID
└─ Route to appropriate agent
        ↓
ASSIGNED AGENT:
├─ Review feedback (1-2 days)
├─ Investigate thoroughly (3-5 days)
├─ Determine: Valid or Not?
└─ Document findings
        ↓
USER NOTIFICATION:
├─ Email with result
├─ Action taken (if any)
├─ Deployment timeline
└─ Feedback ID for tracking
```

---

## Response Template

```
Thank you for this feedback! I've captured:
- Claim: [User's exact issue]
- Issue type: [Category]
- Your concern: [Paraphrased impact]

What happens next:
1. Routed to [Agent Name] for review
2. [Agent] will check: [What they investigate]
3. If error found: System updated
4. Investigation complete: You'll get email update in 5 business days

Feedback ID: FB-2026-05-25-001
Track status: voteiq.io/feedback/FB-2026-05-25-001
```

---

## Where Users Submit Feedback

1. **After Any Response**
   - [Was this helpful?] buttons at bottom
   - [No] → Opens Feedback Collector

2. **Explicit Channel**
   - Menu: [Account] → [Send Feedback]

3. **Inline**
   - "This data looks wrong" (in response)
   - Triggers Feedback Collector with pre-filled description

---

## Feedback Form Fields

```
REPORT AN ISSUE

What's the problem?
[textarea: describe the issue]

What type of issue?
○ Data looks wrong
○ Correlation seems weak
○ Missing data/sources
○ Chart is misleading
○ Methodology question
○ Other

Where did you see this?
[field: which response / which agent]

What do you think should happen?
[textarea: suggestion]

Contact me about this:
[email field - optional]

[SUBMIT FEEDBACK]
```

---

## Investigation Process

### Step 1: Feedback Collector (Same Day)
- ✓ Receive feedback
- ✓ Classify type
- ✓ Assign Feedback ID
- ✓ Route to agent
- ✓ Send confirmation

### Step 2: Assigned Agent (Days 1-5)
- ✓ Review feedback
- ✓ Investigate claim
- ✓ Check data/analysis
- ✓ Determine validity

### Step 3: User Notification (Day 5)
- ✓ Email results
- ✓ Explain findings
- ✓ List actions taken
- ✓ Provide timeline

### Step 4: Quarterly Review
- ✓ Aggregate patterns
- ✓ Plan improvements
- ✓ Deploy fixes
- ✓ Credit user feedback

---

## User Benefits

**Transparency**
- Users see their feedback is taken seriously
- Know exactly when they'll hear back (5 days max)
- Can track status anytime with Feedback ID

**Improvement**
- Bad data gets fixed
- System gets better over time
- Users directly improve VoteIQ

**Accountability**
- Every report investigated
- Results explained to user
- Public dashboard shows improvements made

---

## Tracking & Status

### User Can Check Status
```
voteiq.io/feedback/[Feedback-ID]

Status: Under Investigation
Submitted: 2026-05-25
Type: Data Issue
Expected Resolution: 2026-05-30
Assigned to: Data Quality Escalator
```

### Public Dashboard
```
Total feedback received: 147
Resolved: 89 (61%)
Under investigation: 23 (16%)
Improvements deployed: 34 (this quarter)
```

---

## Timeline Promises

| Timeline | Action |
|----------|--------|
| **Same day** | ✓ Feedback received & acknowledged |
| **1-2 days** | ✓ Agent begins investigation |
| **5 days max** | ✓ User gets response via email |
| **7 days** | ✓ Fix deployed if valid |
| **Quarterly** | ✓ All improvements summarized |

---

## Example Feedback

### Valid Issue (Data)
```
USER: "Education PAC gave $5K per FEC, shows $3K in system"
RESULT: ✓ VALID — VPAP lag issue found
ACTION: Updated source priority; FEC now takes precedence
EMAIL: "Thank you for catching this! We've fixed the indexing."
```

### Invalid Issue (Methodology)
```
USER: "Why didn't you compare House vs Senate?"
RESULT: ✗ INVALID — We do analyze separately
ACTION: Improved documentation
EMAIL: "Great question! Here's why we approach it this way..."
```

### Feature Request
```
USER: "Can you show where FEC and VPAP disagree?"
RESULT: ✓ FEATURE EXISTS
ACTION: Point to feature, add to docs
EMAIL: "Exactly! We flag source conflicts automatically."
```

---

## Success Metrics

- ✓ 100% of feedback acknowledged (same day)
- ✓ 100% of feedback investigated (within 5 days)
- ✓ 100% of users notified of results
- ⏳ >50% of feedback results in improvements
- ⏳ User trust increases with each fix deployed

---

**Status:** ✓ Ready for use  
**Agent:** Feedback Collector + 5 Investigation Agents  
**Timeline:** 5 business days guaranteed  
**Impact:** Closes feedback loop, improves system over time
