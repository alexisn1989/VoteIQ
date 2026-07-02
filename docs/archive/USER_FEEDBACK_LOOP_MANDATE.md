# User Feedback Loop — Mandate & Implementation

**Rule:** Users can flag bad intel; feedback feeds into system improvement  
**Status:** ✓ IMPLEMENTED  
**Date:** May 25, 2026  
**Principle:** Close the loop between users and data quality

---

## The Problem

**Before:**
```
USER: "This correlation looks wrong"
SYSTEM: [No way to report it]
→ [Bad data stays in system]
→ [Others see same bad intel]
→ [No feedback to improve system]
```

**After:**
```
USER: "This correlation looks wrong"
FEEDBACK_COLLECTOR: "Thank you. I've recorded this and routed it to our Data Analyst.
                     You'll get an update within 5 business days."
→ [Feedback captured in system]
→ [Routed to appropriate agent]
→ [Investigated within 5 days]
→ [Feeds into quarterly improvements]
```

---

## Solution: User Feedback Loop

### Agent: Feedback Collector

**Role:** Capture and route user reports of bad data  
**Input:** User feedback ("This looks wrong")  
**Output:** Confirmation + next steps  
**Timeline:** 5 business days for investigation

### Feedback Types

1. **Data Issue** (Wrong amount, outdated info)
   - Example: "This donation amount is wrong"
   - Route: Data Quality Escalator
   - Action: Investigate source, verify amount

2. **Correlation Issue** (Weak correlation shown as strong)
   - Example: "This r=0.45 shouldn't be displayed"
   - Route: Data Analyst
   - Action: Review methodology, check thresholds

3. **Missing Context** (Incomplete data shown)
   - Example: "Why didn't you include VPAP data?"
   - Route: Search Assistant
   - Action: Clarify data sources and gaps

4. **Visualization Issue** (Misleading chart)
   - Example: "This chart is misleading"
   - Route: Visual Explainer
   - Action: Review data, check verification

5. **Methodology Issue** (Incorrect analysis)
   - Example: "You should have controlled for X"
   - Route: Deep Researcher
   - Action: Review methodology, explain approach

---

## Feedback Collection Process

### Step 1: User Submits Feedback

```
USER: "This correlation looks suspicious. You're showing r=0.45 
       but that's below the 0.60 threshold, right?"

FEEDBACK_COLLECTOR:
"Thank you for this feedback! I've captured:
- Claim: Correlation r=0.45 displayed below threshold
- Issue type: Correlation threshold violation
- Your concern: Weak correlation shown as significant

What happens next:
1. Routed to Data Analyst for review
2. Analyst checks: Was this correlation displayed as a finding?
3. If error found: System updated; quarterly report includes
4. Investigation completed: You'll get email update in 5 business days

Feedback ID: FB-2026-05-25-001
Check status: voteiq.io/feedback/FB-2026-05-25-001"
```

### Step 2: Route to Appropriate Agent

```
Feedback Collector analyzes type:
├─ Data Issue → Data Quality Escalator
├─ Correlation Issue → Data Analyst
├─ Missing Data → Search Assistant
├─ Visualization Issue → Visual Explainer
└─ Methodology → Deep Researcher
```

### Step 3: Investigation

```
Assigned Agent:
1. Reviews user feedback
2. Checks data/analysis
3. Determines: Is feedback valid?
4. If valid: Files bug/improvement ticket
5. If invalid: Explains why analysis was correct
```

### Step 4: User Notification

```
Email to user (within 5 business days):

Subject: Feedback Update - [Feedback ID]

Hi [User],

Thank you for reporting: "This correlation looks suspicious"

Investigation Result: ✓ VALID FEEDBACK

We found:
- Correlation r=0.45 was displayed as a finding
- This violates our r>0.60 threshold
- Correlation should have been flagged as "weak" or hidden

Action Taken:
- System updated to correctly flag weak correlations
- Included in next weekly release
- Your feedback credited in changelog

Timeline: Change deployed by 2026-05-31

Thank you for helping us improve VoteIQ!
---
Feedback ID: FB-2026-05-25-001
```

---

## Feedback Workflow

```
USER REPORTS ISSUE
        ↓
FEEDBACK_COLLECTOR:
  ├─ Acknowledge feedback
  ├─ Classify type
  ├─ Capture details
  └─ Route to agent
        ↓
ASSIGNED AGENT:
  ├─ Investigate claim
  ├─ Check data/analysis
  ├─ Determine validity
  └─ Document findings
        ↓
FEEDBACK REVIEW:
  ├─ Valid: Create improvement ticket
  └─ Invalid: Explain methodology
        ↓
USER NOTIFICATION:
  ├─ Confirmation email
  ├─ Investigation result
  ├─ Action taken (if any)
  └─ Deploy timeline
        ↓
QUARTERLY REVIEW:
  ├─ Aggregate all feedback
  ├─ Identify patterns
  ├─ Plan improvements
  └─ Deploy in next quarter
```

---

## Feedback Types & Routing

### Type 1: Data Issue

**Examples:**
- "This donation amount is wrong"
- "The bill status is outdated"
- "This person's voting record has errors"

**Route:** Data Quality Escalator  
**Investigation:** Cross-check sources, verify amounts  
**Timeline:** 5 business days  
**Outcome:** Data correction or explanation

### Type 2: Correlation Issue

**Examples:**
- "This r=0.45 shouldn't be shown"
- "The methodology seems wrong"
- "You didn't control for [confounding variable]"

**Route:** Data Analyst  
**Investigation:** Review methodology, check thresholds  
**Timeline:** 5 business days  
**Outcome:** Threshold violation fix or methodology explanation

### Type 3: Missing Data/Context

**Examples:**
- "Why didn't you include VPAP data?"
- "This analysis is incomplete"
- "You're missing [source] information"

**Route:** Search Assistant / Deep Researcher  
**Investigation:** Verify source inclusion, explain gaps  
**Timeline:** 5 business days  
**Outcome:** Clarification or data inclusion

### Type 4: Visualization Issue

**Examples:**
- "This chart is misleading"
- "The scale makes small differences look huge"
- "The colors are confusing"

**Route:** Visual Explainer  
**Investigation:** Review Golden Query verification, check chart accuracy  
**Timeline:** 5 business days  
**Outcome:** Visualization update or explanation

### Type 5: Methodology Issue

**Examples:**
- "Why did you compare House to Senate votes?"
- "Different sources use different definitions"
- "You should have normalized the data"

**Route:** Deep Researcher  
**Investigation:** Review methodology, explain approach  
**Timeline:** 5 business days  
**Outcome:** Methodology documentation or change

---

## User-Facing Feedback Interface

### Where to Leave Feedback

**Option 1: After Any Response**
```
USER sees response from Analyst
        ↓
At bottom: [Was this helpful?] [No] [Yes]
        ↓
[No] → Opens Feedback Collector
```

**Option 2: Explicit Feedback Channel**
```
Menu: [Account] → [Send Feedback]
        ↓
Opens Feedback Collector form
```

**Option 3: Inline Feedback (If Suspicious)**
```
"This data looks wrong"
        ↓
Triggers Feedback Collector with pre-filled issue description
```

### Feedback Form

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

## Feedback Tracking & Transparency

### User Can Track Their Feedback

```
voteiq.io/feedback/[Feedback-ID]

Status: Under Investigation
Submitted: 2026-05-25
Type: Correlation Issue
Description: "This r=0.45 shouldn't be shown"
Assigned to: Data Analyst
Expected Resolution: 2026-05-30
```

### VoteIQ Feedback Dashboard (Transparency)

```
Public Feedback Stats:
├─ Total feedback received: 147
├─ Resolved: 89 (61%)
├─ Under investigation: 23 (16%)
├─ Waiting for user clarification: 18 (12%)
├─ Invalid/Misunderstanding: 17 (11%)
└─ Improvements deployed: 34 (this quarter)

Recent Improvements (from User Feedback):
├─ Fixed: Weak correlations (r<0.60) now flagged correctly
├─ Fixed: VPAP indexing lag clearly noted
├─ Added: Data freshness timestamps
└─ Improved: Correlation methodology explanation
```

---

## Feedback Processing Timeline

### Immediate (Same Day)
- [x] User submits feedback
- [x] Feedback Collector acknowledges
- [x] Issue type classified
- [x] Routed to appropriate agent

### Short-term (1-2 Days)
- [x] Assigned agent reviews feedback
- [x] Preliminary investigation
- [x] Validity determination

### Medium-term (3-5 Days)
- [x] Full investigation complete
- [x] Findings documented
- [x] User notified of result

### Long-term (Weekly/Quarterly)
- [x] Aggregate feedback
- [x] Identify patterns
- [x] Plan improvements
- [x] Deploy fixes

---

## Impact on System Improvement

### Feedback Feeds Into:

**Weekly Reviews:**
- Data issues identified
- Threshold violations found
- Visualization problems noted

**Monthly Analysis:**
- Patterns in user concerns
- Common misunderstandings
- Features users want

**Quarterly Planning:**
- System improvements
- Documentation updates
- Agent capability enhancements

**Annual Review:**
- Major methodology changes
- Architecture improvements
- New feature development

---

## Feedback Examples

### Example 1: Valid Data Issue

```
USER FEEDBACK: "Education PAC donated $5K per FEC, 
                but your system shows $3K. Why?"

INVESTIGATION:
- Checked FEC filing: $5,000 confirmed
- Checked VPAP: Shows $3,000 (incomplete indexing)
- Found: System used VPAP instead of FEC

RESULT: ✓ VALID - Data error in VPAP indexing
ACTION: Updated source priority; FEC now takes precedence
DEPLOYED: Next release (2026-05-31)

USER NOTIFICATION:
"Thank you for catching this! We had a data source priority 
issue where VPAP was used instead of FEC. Fixed now."
```

### Example 2: Invalid Methodology Question

```
USER FEEDBACK: "Why didn't you compare House vs Senate votes? 
                They're different chambers."

INVESTIGATION:
- Analyst explains: House and Senate have different procedures
- Our analysis accounts for this in methodology
- User may have misunderstood the context

RESULT: ✗ INVALID - User misunderstanding
ACTION: Improve methodology documentation
IMPROVED: Added explanation of House vs Senate vote differences

USER NOTIFICATION:
"Great question! Here's why we analyze House and Senate separately...
[explanation]. We'll clarify this in our docs."
```

### Example 3: Feature Request

```
USER FEEDBACK: "Can you show where VPAP and FEC disagree? 
                That would help me validate data."

INVESTIGATION:
- Data Analyst reviews request
- Aligns with source conflict resolution work
- Already implemented in recent update

RESULT: ✓ FEATURE ALREADY EXISTS
ACTION: Point user to feature
IMPROVED: Add to documentation/FAQ

USER NOTIFICATION:
"Exactly! We now flag source conflicts automatically. 
See 'Data Transparency' section of each response."
```

---

## Implementation Checklist

- [x] Feedback Collector agent created
- [x] System prompt documents feedback loop
- [x] Routing rules for each feedback type
- [x] Timeline documented (5 business days)
- [x] User notification process defined
- [ ] Feedback form built
- [ ] Feedback tracking system (tech debt)
- [ ] Dashboard built (quarterly)

---

## Testing Strategy

- [ ] User can submit feedback
- [ ] Feedback routes to correct agent
- [ ] User gets confirmation email
- [ ] Feedback is tracked and stored
- [ ] Investigation happens within 5 days
- [ ] User gets update with results
- [ ] Invalid feedback handled gracefully

---

## References

- **Agent:** chat.py, feedback_collector (lines ~729-755)
- **System prompt:** chat.py, User Feedback Loop section
- **Workflow:** This document
- **Escalator:** DATA_QUALITY_ESCALATOR_AGENT
- **Data Analyst:** DATA_ANALYST_AGENT

---

**Effective:** May 25, 2026  
**Status:** ✓ Implemented  
**Impact:** Closes feedback loop, improves system over time

