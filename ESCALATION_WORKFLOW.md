# VoteIQ Escalation Workflow

## Overview

The VoteIQ support agent detects issues that require human review and drafts escalation tickets. This document describes how to handle, triage, and resolve escalations.

## Escalation Types

### 1. DATA-QUALITY Escalations

**When to escalate:**
- User reports a record is missing from VoteIQ
- User reports a record is wrong or outdated
- VoteIQ data doesn't match official source
- Identity match fails (person not found when they should be)
- Address/location data is stale or incorrect
- Bill status shows "unknown" when official source has status
- Donation total doesn't match official filing
- Vote record is missing when official source has it

**Triage checklist:**
- [ ] Verify the issue against official source (Congress.gov, Virginia LIS, FEC.gov, etc.)
- [ ] Determine if it's a sync delay or permanent data issue
- [ ] Check when the source was last refreshed in VoteIQ
- [ ] Identify the exact record ID or bill number
- [ ] Check if the official source has the data or if it's not yet published

**Resolution paths:**
- **Sync issue**: Flag for data team to manually trigger refresh on that source
- **Stale data**: Update the record from official source; add to next scheduled data refresh
- **Missing record**: Add record to appropriate VoteIQ table; notify user when added
- **Wrong record**: Investigate root cause (identity mismatch, data corruption, API parsing error); fix and backfill if needed

**SLA:** Medium priority. Resolve within 1-2 business days.

**Follow-up:**
- Comment on escalation with resolution
- Test that the fix works (query should return correct result)
- If issue will recur, add to FAQ or known-issues doc
- Update data refresh schedule if pattern emerges

---

### 2. PRIVACY Escalations

**When to escalate:**
- User reports PII exposure or doxxing concern
- User requests data removal (right to be forgotten)
- User flags that VoteIQ is publishing sensitive personal data
- Potential data breach or unauthorized access

**Triage checklist:**
- [ ] Assess severity: Is PII currently exposed? To whom?
- [ ] Preserve evidence (screenshot, URL, timestamp)
- [ ] Do not post sensitive details in public Slack channels
- [ ] Check if the data is from official public records (Congress.gov, FEC, LIS, etc.)
- [ ] Consult privacy@voteiq.io or legal team immediately if unsure

**Resolution paths:**
- **Public record**: Confirm source is official. Respond: "This data is published by [official source], not VoteIQ. To request removal, contact [official source]."
- **VoteIQ publishing private data**: Immediate removal required. Flag for audit of other similar records.
- **Doxxing concern**: Report to safety team; may require legal review.
- **Data removal request**: Consult legal; follow GDPR/CCPA procedures if applicable.

**SLA:** High priority. Initial review within same day.

**Follow-up:**
- Document the incident
- If removal was needed, verify removal is complete
- Audit similar records to prevent recurrence
- Update data governance docs if new policy needed

---

### 3. LEGAL/ETHICS Escalations

**When to escalate:**
- User claims a donation is evidence of corruption
- User infers illegal behavior from voting record
- User asks VoteIQ to verify a legal violation
- User reports a potential conflict of interest or ethics violation

**Triage checklist:**
- [ ] Do not speculate on legality or wrongdoing
- [ ] Preserve the exact user question
- [ ] Note the official records cited (don't infer)
- [ ] Do not conclude whether a violation occurred
- [ ] Forward to legal@voteiq.io immediately

**Resolution paths:**
- **Factual inquiry**: Confirm VoteIQ can provide facts (who voted, how much donated, etc.). Do not assess legality.
- **Legal conclusion requested**: Forward to legal team. Respond: "VoteIQ does not make legal conclusions. Legal team will review."
- **Ethics concern**: If it's about VoteIQ's own practices, escalate to leadership. If it's about a public official, forward facts to legal; legal decides if external reporting is needed.

**SLA:** Medium-high priority. Legal team responds within 1-2 business days.

**Follow-up:**
- Legal team documents conclusion
- If it's a known pattern, update FAQ
- If it requires external reporting, follow legal guidance
- Inform user of outcome (without revealing legal analysis)

---

### 4. SYSTEM Escalations

**When to escalate:**
- Chat endpoint returns 500 error
- Search returns no results for a query that should work
- API timeout or "service unavailable"
- Feature is broken or not responding
- Data appears to be corrupted or malformed

**Triage checklist:**
- [ ] Check status page for known outages
- [ ] Check upstream API status (Congress.gov, FEC, OpenStates, etc.)
- [ ] Try to reproduce the error
- [ ] Gather: exact error message, query, timestamp, user account
- [ ] Check app logs and database health
- [ ] Determine if it affects other users or just this user

**Resolution paths:**
- **Source API down**: Inform user; provide official source link; estimate when VoteIQ will resume.
- **VoteIQ bug**: Assign to engineering; prioritize by impact (how many users, data loss risk, etc.)
- **User error**: Clarify with user; guide to correct usage.
- **Database issue**: Page on-call DBA; may require rollback or recovery.

**SLA:** Varies by impact.
- Critical (data loss, widespread outage): resolve within 4 hours
- High (feature broken for some users): resolve within 1 business day
- Medium (intermittent, single user): resolve within 2 business days

**Follow-up:**
- Engineer confirms fix is deployed
- Verify fix works (test query should succeed)
- Update status page if it was a known issue
- Post-mortem if it was a serious incident
- Add test case if appropriate

---

### 5. ABUSE / SAFETY Escalations

**When to escalate:**
- User sends threatening or harassing messages
- User attempts to manipulate or hack VoteIQ
- User publishes other users' private info via VoteIQ
- VoteIQ is being used to facilitate harassment

**Triage checklist:**
- [ ] Preserve evidence (screenshot, exact timestamp, message)
- [ ] Do not engage with the abusive content
- [ ] Do not respond in kind
- [ ] Note user account if logged in
- [ ] Escalate to safety team or on-call lead immediately

**Resolution paths:**
- **Warn user**: Issue one warning about acceptable use policy
- **Suspend account**: Disable login for 24-48 hours
- **Ban user**: Permanently disable account if warnings were ignored or severity is high
- **Report to law enforcement**: If threatening violence or other crime, consult legal; may need to involve police

**SLA:** Urgent. Respond within 1 hour.

**Follow-up:**
- Document the incident
- Update abuse filters if needed
- Monitor for repeat offenders
- If banned, inform user why

---

## Escalation Ticket Template

```json
{
  "escalation_id": "[auto-generated UUID]",
  "escalation_type": "data_quality|privacy|legal_ethics|system|abuse",
  "created_at": "[ISO 8601 timestamp]",
  "created_by": "support_agent",
  "severity": "low|medium|high|urgent",
  "status": "draft_pending_review|assigned|in_progress|resolved|closed",
  "assigned_to": "[human reviewer email or null]",
  "user_email": "[if available]",
  "user_question": "[exact user message]",
  
  "data_quality_fields": {
    "record_type": "person|bill|vote|donation|executive_order",
    "entity_name": "[name, bill number, etc.]",
    "entity_id": "[database ID, bill number, FEC ID, etc.]",
    "what_appears_wrong": "[plain language description]",
    "suspected_root_cause": "[missing_record|stale_data|sync_error|identity_issue|api_parse_error|unknown]",
    "official_source": "[Congress.gov, Virginia LIS, FEC, etc.]",
    "supporting_evidence": "[URLs, screenshots]",
    "proposed_fix": "[null until assigned]"
  },
  
  "privacy_fields": {
    "issue_type": "PII_exposure|doxxing|data_removal_request|potential_breach",
    "affected_data": "[brief description, minimize sensitive info]",
    "evidence": "[internal screenshot/log reference]",
    "severity_reason": "[why urgent or not]"
  },
  
  "legal_fields": {
    "issue": "[user's observation]",
    "entities_involved": "[official, bill, donor names]",
    "primary_sources": "[official record URLs only]",
    "unsupported_inferences_to_avoid": "[list of conclusions not supported by facts]"
  },
  
  "system_fields": {
    "route_or_feature": "[chat|search|bill page|login|api]",
    "error_message": "[if available]",
    "reproducible": "true|false",
    "steps_to_reproduce": "[if reproducible]",
    "affected_users": "single_user|multiple_users|widespread"
  },
  
  "notes": "[internal discussion thread]",
  "resolution_summary": "[filled in when resolved]",
  "resolution_date": "[ISO 8601 when resolved]"
}
```

---

## Escalation Review Process

### Daily Review (Every AM)

1. **Triage new escalations** (5-10 min)
   - Categorize by type
   - Assess severity
   - Assign to appropriate team
   - Set initial SLA

2. **Check stale escalations** (5-10 min)
   - Find any older than SLA
   - Bump priority if needed
   - Add comment if status unchanged

### Assignment

- **Data quality** → Data team lead
- **Privacy** → Privacy officer or legal
- **Legal/ethics** → Legal team
- **System** → Engineering lead / on-call
- **Abuse** → Safety team or on-call lead

### Closure

Escalation is **RESOLVED** only when:
- [ ] Root cause identified and documented
- [ ] Fix deployed (if applicable)
- [ ] Fix tested and verified working
- [ ] User informed (if applicable)
- [ ] FAQ or docs updated (if recurrence risk)

---

## Response Templates for Users

### Data Quality Confirmed

"Thank you for reporting this. We found [issue] and have [fixed|scheduled a fix]. VoteIQ will be updated [when/ASAP]. We appreciate your help in making VoteIQ accurate."

### Privacy Issue Being Reviewed

"We take your concern seriously. Our privacy team is reviewing this and will be in touch within [SLA]. In the meantime, please do not share additional sensitive information here."

### Legal/Ethics Escalation

"Thank you for flagging this. VoteIQ does not make legal conclusions from public records. Our legal team is reviewing this for any follow-up needed."

### System Issue Being Fixed

"We identified the issue on our end. Our engineering team is fixing it and we expect [feature/endpoint] to be back online by [time]. We'll follow up once it's resolved."

---

## Metrics to Track

- Escalation volume by type per week
- Mean time to triage
- Mean time to assign
- Mean time to resolve (by severity)
- Closure rate (how many are actually resolved vs. abandoned)
- Recurrence rate (same issue escalated twice)

Review these metrics monthly. If any metric degrades, investigate why and adjust process.

---

## Related Documents

- [Agent Personas](./voteiq/config/agent_personas.yaml)
- [VoteIQ Methodology](./METHODOLOGY.md)
- [Data Sources](./DATA_SOURCES.md)
- [Known Issues](./KNOWN_ISSUES.md)
