# Source Conflict Resolution — VoteIQ Data Quality

**Status:** ✓ IMPLEMENTED  
**Date:** May 25, 2026  
**Priority:** B (Secondary)

---

## Problem Statement

When FEC and state financial systems (VPAP) report conflicting data, users had no way to discover or resolve the discrepancy:

- **Before:** Analyst would report one source, users unaware of conflicts
- **After:** Analyst flags conflicts and directs users to Data Quality Escalator

### Example Conflict
```
FEC Filing (2026-05-24): Education PAC donated $5,000 to Candidate Smith
VPAP Filing (2026-05-24): Education PAC donated $0 to Candidate Smith
```

Users asking "How much did Education PAC donate?" would get only one answer with no visibility into the conflict.

---

## Solution: Multi-Level Conflict Handling

### Level 1: Public Analyst (User-Facing)

**Role:** Detect and flag conflicts  
**Visibility:** Public  
**Action:** Alert user and suggest escalation

**Behavior:**
```
USER: "How much did Education PAC donate to Smith?"
ANALYST (detects conflict):
  ⚠️ SOURCE CONFLICT DETECTED
  FEC (2026-05-24): $5,000
  VPAP (2026-05-24): $0
  
  Data conflict between sources. Contact Support or use Data Quality Escalator.
```

**Detection Logic:**
- Query FEC and VPAP for same donation
- If amounts differ → Flag as conflict
- If dates differ → Flag as timing conflict
- If one source has record, other doesn't → Flag as gap

**Flag Format:**
```
⚠️ SOURCE CONFLICT: [source 1] reports [value], [source 2] reports [value]
Contact Support or use Data Quality Escalator for resolution.
```

### Level 2: Data Quality Escalator (Admin)

**Role:** Investigate and resolve conflicts  
**Visibility:** Admin-only  
**Action:** Draft escalation summary with likely source

**Current Prompt:**
```
"Inspect records and draft an escalation summary with repro steps, 
likely source, and suggested fix. Do not write to production data or 
claim a fix was applied without separate human approval."
```

**Escalator Workflow:**
1. Receive flagged conflict from analyst
2. Cross-check both sources
3. Identify likely source of error (filing mistake, timing lag, data entry error)
4. Draft recommended fix (which source is authoritative, why)
5. Queue for human review

---

## Implementation Details

### Analyst Agent Updated (chat.py, lines 532-548)

**New Capability:**
```python
"SOURCE CONFLICTS: If different sources report different values 
(e.g., FEC says $5K, VPAP says $0), flag this explicitly and suggest: 
'Data conflict detected. Contact Support or use Data Quality Escalator 
for resolution.' Always list all conflicting sources with their reported 
values before suggesting escalation."
```

**Behavior Rules:**
1. Query all authoritative sources (FEC, VPAP, Virginia SBE, etc.)
2. If values match → Return single fact with source
3. If values differ → Flag conflict with ALL sources listed
4. Suggest escalation path (Support or Escalator)
5. Do NOT guess which source is correct

### System Prompt Updated (chat.py, lines 87-98)

**New Section:**
```
- Public Record Analyst: Exact facts ONLY from structured records
  └─ FLAG source conflicts when detected and suggest Data Quality Escalator
- Data Quality Escalator: Flag and resolve source conflicts (admin-facing)
- For source conflicts, analyst flags and users can escalate to Escalator
```

### AGENT_DATA_SOURCES.md Updated

**New Subsection:**
```
CONFLICT DETECTION & ESCALATION:
If different sources report conflicting values → Flag conflict explicitly
→ List all conflicting sources and their values
→ Suggest Data Quality Escalator for resolution

ROUTING RULES:
1. Research synthesis → Deep Researcher
2. Source conflict → Flag + suggest Data Quality Escalator
```

---

## Source Conflict Scenarios

### Scenario 1: Donation Amount Mismatch

```
Query: "How much did Education PAC donate to Smith?"

FEC: $5,000 (filed 2026-04-15)
VPAP: $0 (as of 2026-05-24)

ANALYST RESPONSE:
⚠️ SOURCE CONFLICT DETECTED
- FEC (federal filings, 2026-04-15): $5,000
- VPAP (Virginia state aggregator, 2026-05-24): $0

Possible causes: Filing delay, entry error, or record mismatch.
Contact Support or use Data Quality Escalator for resolution.
```

### Scenario 2: Timing Lag (Not a Conflict)

```
Query: "Recent donations to Smith?"

FEC: $5,000 (filed 2026-05-15, indexed 2026-05-24)
VPAP: Not yet indexed (usual lag: 2-5 days)

ANALYST RESPONSE:
OFFICIAL: FEC, 2026-05-24 — $5,000 from Education PAC (filed 2026-05-15)

Note: VPAP may reflect this donation within 2-5 business days.
```

**Decision Rule:** If one source hasn't indexed yet based on known lags → Don't flag as conflict. Only flag if both sources have full data but disagree.

### Scenario 3: One Source Missing Record

```
Query: "All donations to Smith from Education Sector?"

FEC: Education PAC $5,000, Green Future Fund $3,000
VPAP: Education PAC $5,000 (Green Future Fund not yet indexed)

ANALYST RESPONSE:
OFFICIAL: FEC, 2026-05-24
- Education PAC: $5,000
- Green Future Fund: $3,000

Note: VPAP shows Education PAC only; Green Future Fund may not yet be indexed
(typical lag: 2-5 business days from FEC filing).
```

**Decision Rule:** If one source has fewer records due to known indexing lag → Note as "pending" not "conflict." Only flag if both sources claim authoritative data but disagree.

---

## When to Flag as Conflict

### ✓ Flag as Conflict
- Different dollar amounts from same sources on same date
- One source says $0, other says amount > $0 (after normal indexing lag)
- Conflicting donor names/IDs for same transaction
- Different filing dates that should match

### ✗ Do NOT Flag as Conflict
- VPAP slightly behind FEC due to normal indexing lag (2-5 days)
- FEC records still processing (showing "pending" status)
- Different levels of detail (FEC may have granular data, VPAP aggregated)
- Historical changes (source updated record after initial filing)

---

## User Escalation Flow

### Path 1: Contact Support
**Action:** User contacts support@voteiq.io  
**Escalator Role:** Support team triages, may route to escalator

### Path 2: Direct to Data Quality Escalator (Advanced)
**Action:** User or support routes directly to escalator  
**Input:** Conflicting values from analyst output  
**Output:** Escalation summary with recommended resolution

### Path 3: Public Reporting
**Action:** User reports via "Report Data Issue" flow  
**Escalator Role:** Public reports queue for escalator review  
**Priority:** Based on impact (public figure vs. local candidate, etc.)

---

## Implementation Checklist

- [x] Analyst agent updated to detect source conflicts
- [x] System prompt documents conflict handling
- [x] AGENT_DATA_SOURCES.md includes conflict examples
- [x] Escalator role clarified (admin resolution)
- [ ] Query logic implements conflict detection across FEC/VPAP
- [ ] "Data Quality Escalator" link provided to users
- [ ] Testing: Verify conflict scenarios flagged correctly
- [ ] Documentation: Source conflict examples published

---

## Testing & Verification

### Test Case 1: FEC-VPAP Amount Mismatch
```
Input: "How much did Education PAC donate to Smith?"
Sources: FEC=$5K, VPAP=$0 (both dated 2026-05-24)
Expected: ⚠️ Flag conflict, list both values, suggest escalation
```

### Test Case 2: Normal Indexing Lag
```
Input: "Recent donations to Smith?"
Sources: FEC=$5K (filed 2026-05-23), VPAP not yet indexed (lag expected)
Expected: Return FEC value, note VPAP lag, do NOT flag as conflict
```

### Test Case 3: Single Source Missing (Due to Lag)
```
Input: "All Education sector donations?"
Sources: FEC=[A, B, C], VPAP=[A] (B, C not yet indexed)
Expected: Return FEC, note VPAP may be incomplete, do NOT flag as conflict
```

### Test Case 4: Conflicting Record Availability
```
Input: "Did Green Future Fund donate to Smith?"
Sources: FEC=YES ($3K), VPAP=NO
Expected: ⚠️ Flag conflict (both authoritative, disagree on existence)
```

---

## System Integration Points

### Analyst Agent (User-Facing)
- Detects: Queries both FEC and VPAP for same data
- Flags: Explicitly alerts user when values differ
- Routes: Suggests Data Quality Escalator

### Data Quality Escalator (Admin)
- Receives: Conflict flags from analyst
- Investigates: Cross-checks sources, filing history
- Resolves: Drafts escalation summary (admin review required before action)

### Escalation Workflow
- Input: User reports or analyst flags conflict
- Processing: Escalator investigates
- Output: Escalation summary (no production writes without human approval)

---

## References

- **AGENT_DATA_SOURCES.md** — Agent scope and routing
- **voteiq/api/routes/chat.py** — Agent registry with prompts (analyst + escalator)
- **ESCALATION_WORKFLOW.md** — Escalation system details
- **DATA_INTEGRITY.md** — Data quality standards

---

## Notes

- **Priority:** B (Secondary) — Affects user transparency but not core functionality
- **User Impact:** Medium — Users with conflicting data now get visibility + resolution path
- **Admin Impact:** Low — Escalator already handles conflicts; now receives flagged cases
- **Data Changes:** None — Analyst reports what sources say; escalator recommends, doesn't write
- **Backward Compatibility:** ✓ Yes — analyst still returns facts, now with conflict flags

---

**Effective Date:** May 25, 2026  
**Status:** ✓ Implemented and ready for testing  
**Next Phase:** Query logic implementation and integration testing

