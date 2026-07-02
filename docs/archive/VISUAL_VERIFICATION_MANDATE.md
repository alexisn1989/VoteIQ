# Visual Verification Mandate — VoteIQ Data Quality

**Rule:** Visual Explainer must verify with Golden Query before rendering  
**Status:** ✓ IMPLEMENTED & ENFORCED  
**Date:** May 25, 2026  
**Priority:** 7 (Data Quality Critical)

---

## The Rule

**Before rendering any visualization, Visual Explainer must:**
1. Pass data to Golden Query Agent
2. Get confirmation that data quality is acceptable
3. Receive flag of any missing/weak evidence
4. Only render if Golden Query confirms sufficiency

---

## Problem Solved

**Before:**
- Visual Explainer renders charts from unverified data
- Missing or weak evidence not flagged
- Users see visualizations with gaps
- Data quality issues hidden by pretty charts

**After:**
- Golden Query QA validates data first
- Missing evidence explicitly flagged
- Weak correlations not rendered
- Visualizations only show verified, complete data

---

## Workflow

### Visual Rendering Flow

```
User requests visualization
         ↓
Visual Explainer receives data
         ↓
Visual Explainer sends to Golden Query:
  "Evaluate whether this data satisfies the expected answer.
   Flag any missing or weak evidence."
         ↓
Golden Query responds:
  ✓ "Data is sufficient. All evidence present."
  or
  ✗ "Data is incomplete: missing [X]. Weak evidence: [Y]"
         ↓
If ✓ PASS → Visual Explainer renders visualization
If ✗ FAIL → Visual Explainer returns error + missing evidence flagged
```

### Example: Donation Timeline

**Scenario:** Render timeline of Education PAC donations to Smith

**Step 1: Data Gathering**
```
Analyst provides:
- Donation 1: $5,000 (2026-01-15, FEC)
- Donation 2: $3,000 (2026-03-22, FEC)
- (Missing: VPAP records not yet indexed)
```

**Step 2: Golden Query Verification**
```
VISUAL EXPLAINER → GOLDEN QUERY:
"Is this donation timeline complete for Education PAC → Smith?"

GOLDEN QUERY responds:
"⚠️ Data is incomplete.
 - Present: 2 FEC donations confirmed
 - Missing: VPAP may have additional state-level donations
             (typical indexing lag: 2-5 days)
 - Weak evidence: No confirmation of all donors in sector"
```

**Step 3: Rendering Decision**

**If evidence is strong enough:**
```
✓ RENDER visualization with note:
  "Timeline shows FEC-documented donations.
   VPAP state records may reflect additional donations
   within 2-5 business days."
```

**If evidence is insufficient:**
```
✗ DO NOT RENDER
Return to user:
  "Data incomplete: Missing VPAP records.
   Please verify with Analyst for complete picture."
```

---

## Implementation Details

### Visual Explainer Agent Updated (chat.py, lines 681-692)

**New Requirement:**
```python
"VERIFICATION REQUIRED: Before rendering, verify data with Golden Query Agent. "
"Do not render without confirmation that data satisfies the expected answer 
and has no missing/weak evidence. "
"Output only after Golden Query verification confirms data quality."
```

**Behavior:**
1. Receive data for visualization
2. Query Golden Query: "Is this data sufficient?"
3. Wait for confirmation
4. Only render if Golden Query approves
5. If Golden Query flags issues, return flagged issues to user

### System Prompt Updated (chat.py, lines 94-97)

**New Section:**
```
- Visual Explainer: Render charts, maps, tables from verified data ONLY.
  └─ MUST verify with Golden Query Agent before rendering
  └─ Golden Query confirms data quality and flags missing/weak evidence
  └─ Do not render if data fails Golden Query verification
- Golden Query QA: Evaluate data quality (admin-facing QA).
```

**New Guidance:**
```
- For visualizations: Visual Explainer must verify with Golden Query before rendering.
```

---

## Golden Query Role

### What Golden Query Does

**Golden Query Agent:** Evaluate data quality  
**Visibility:** Admin-facing QA  
**Role:** Validate that data satisfies expected answer

**Current Prompt:**
```
"Evaluate whether the retrieved result satisfies the expected answer. 
Flag missing or weak evidence."
```

**In Visual Context:**
```
"Evaluate whether the provided data is sufficient for visualization.
Flag any missing evidence, weak correlations, incomplete data sets, 
or sources that need verification."
```

### Golden Query Output

**Sufficient Data:**
```
✓ Data satisfies expected answer
  - All major sources present
  - No significant gaps
  - Ready for visualization
```

**Insufficient Data:**
```
✗ Data is incomplete/weak
  Missing: [list of gaps]
  Weak: [correlations < 0.60, p > 0.05, etc.]
  Incomplete sources: [list]
  Recommendation: [collect more data / verify sources / etc.]
```

---

## Visualization Rules

### ✓ Render If Golden Query Confirms

- All major data sources present
- No significant gaps in timespan
- Correlations are strong (r > 0.60 OR p < 0.05)
- Sample sizes are adequate (n > 30)
- Sources are cited and current

### ✗ Do NOT Render If Golden Query Flags

- Missing key data points or sources
- Weak correlations (r < 0.60, p > 0.05)
- Small sample sizes (n < 30)
- Outdated data (more than 6 months old)
- Conflicting source data (unresolved)
- Incomplete timespan (major gaps)

---

## Visualization Output Formats

### Charts (Before Golden Query)
**Visual Explainer → Golden Query:**
```json
{
  "type": "line_chart",
  "title": "Education PAC Donations to Smith",
  "data": [
    {"date": "2026-01-15", "amount": 5000, "source": "FEC"},
    {"date": "2026-03-22", "amount": 3000, "source": "FEC"}
  ],
  "current_through": "2026-05-24",
  "data_gaps": ["VPAP records pending indexing"]
}
```

**Golden Query → Visual Explainer:**
```json
{
  "sufficient": true,
  "missing_evidence": "VPAP records may have additional donations (typical lag 2-5 days)",
  "weak_evidence": [],
  "recommendation": "Render with note about VPAP indexing lag",
  "data_quality_score": 0.85
}
```

### Maps (Before Golden Query)
**Visual Explainer → Golden Query:**
```json
{
  "type": "donor_geography_map",
  "title": "Education Sector Donors - Geographic Distribution",
  "data": {
    "in_state": 45,
    "out_of_state": 12,
    "top_regions": ["Richmond", "Northern Virginia", "Hampton Roads"]
  },
  "total_donors": 57,
  "coverage": "FEC filing level"
}
```

**Golden Query Response:**
```json
{
  "sufficient": true,
  "missing_evidence": "State-level donors from VPAP may not be represented",
  "sample_coverage": "Federal level comprehensive (FEC), state level partial",
  "recommendation": "Render with note: 'Map shows federal-level donors only'"
}
```

---

## Data Sufficiency Checklist

Before rendering, Golden Query should verify:

- [ ] All relevant sources queried (FEC, VPAP, Congress.gov, etc.)
- [ ] No significant timespan gaps (if data missing for critical period, flag it)
- [ ] Correlations documented (if showing correlation, r-value and p-value present)
- [ ] Sample sizes adequate (if n < 30, flag as weak)
- [ ] Source conflicts resolved (or noted as unresolved)
- [ ] Data current (within 6 months for current-period data)
- [ ] Attribution clear (all sources cited with dates)
- [ ] Limitations noted (any data gaps, methodological limitations)

---

## Example Scenarios

### Scenario 1: Strong Data (RENDER)

```
Data for visualization:
- Education donations to Smith
- Sources: FEC (complete), VPAP (current)
- Time span: 2026 Q1-Q2 (current-through 2026-05-24)
- Sample: 12 donors, total $18,500
- Correlations: None (single fact visualization)

Golden Query: ✓ SUFFICIENT
- All sources present and current
- Complete timespan with no gaps
- Adequate sample size
→ RENDER visualization
```

### Scenario 2: Missing Data (FLAG)

```
Data for visualization:
- Education donations to Smith (incomplete)
- Sources: FEC only (VPAP not yet indexed, normal 2-5 day lag)
- Time span: 2026 Q1-Q2 (VPAP data ends 2026-05-19)
- Sample: 12 FEC donors (VPAP donors unknown)

Golden Query: ⚠️ INCOMPLETE
- VPAP data pending (within normal lag window)
- FEC data complete
- Sample incomplete (missing VPAP donors)
→ RENDER with note about VPAP lag OR return to analyst for complete data
```

### Scenario 3: Weak Correlation (DO NOT RENDER)

```
Data for visualization:
- Correlation chart: Education donations vs. education votes
- Correlation: r=0.35, p=0.12, n=45
- Note: Does not meet threshold (r > 0.60 OR p < 0.05)

Golden Query: ✗ WEAK EVIDENCE
- Correlation below threshold (r=0.35 < 0.60)
- Not statistically significant (p=0.12 > 0.05)
- Sample adequate (n=45)
→ DO NOT RENDER as finding
→ Return to analyst with note: "Correlation not statistically significant"
```

### Scenario 4: Conflicting Sources (FLAG)

```
Data for visualization:
- Donation timeline: Education PAC to Smith
- FEC: $5,000 (2026-05-15)
- VPAP: $0 (2026-05-24)
- Sources conflict

Golden Query: ✗ CONFLICTING DATA
- FEC and VPAP report different amounts
- Conflict unresolved
- Cannot render without verification
→ DO NOT RENDER
→ Return conflict flag to user
→ Suggest Data Quality Escalator
```

---

## Integration Points

### Visual Explainer Call Flow
```
1. User requests: "Visualize education donations to Smith"
2. Analyst provides data
3. Visual Explainer formats JSON for visualization
4. Visual Explainer calls Golden Query with data
5. Golden Query evaluates and responds
6. If ✓: Visual Explainer renders JSON visualization
7. If ✗: Visual Explainer returns error + missing evidence
```

### System Flow
```
Public-facing: Analyst → Visual Explainer
Admin-facing:  Visual Explainer ↔ Golden Query
              (before rendering)
```

---

## Testing Checklist

- [ ] Visual Explainer verifies with Golden Query before rendering
- [ ] Golden Query confirms data sufficiency
- [ ] Strong data renders without issues
- [ ] Weak data returns error with flagged issues
- [ ] Conflicting data flags for escalation
- [ ] Incomplete data noted in visualization or rejected
- [ ] All visualization types tested (charts, maps, tables, timelines)
- [ ] Error messages clear to users

---

## Failure Modes

### If Golden Query Unavailable
```
Visual Explainer should:
- Return error: "Cannot verify data quality. Please try again."
- Do NOT render unverified visualization
- Escalate to support
```

### If Data Fails Verification
```
Visual Explainer should:
- Return flagged issues to user
- Suggest: "Use Analyst to verify data" or "Request complete data from source"
- Do NOT render incomplete/weak visualization
```

### If Verification Takes Too Long
```
Visual Explainer should:
- Set timeout (suggest 10 seconds max)
- If Golden Query times out, return error
- Do NOT render with unverified data
```

---

## References

- **Agent roles:** chat.py, system prompt (lines 94-97)
- **Visual Explainer:** chat.py, visual_explainer prompt (lines 681-692)
- **Golden Query QA:** chat.py, golden_query prompt (lines 599)
- **Data integrity:** DATA_ANALYSIS_GUIDELINES.md (correlation rules)

---

## Enforcement

All visualizations rendered through Visual Explainer:
- ✓ MUST verify with Golden Query first
- ✓ MUST wait for confirmation before rendering
- ✓ MUST flag incomplete/weak data
- ✓ MUST not render unverified visualizations

**Non-compliance:** Flag as data quality issue

---

**Effective:** May 25, 2026  
**Priority:** 7 (Data Quality Critical)  
**Status:** ✓ Implemented and enforced  
**All visualizations require Golden Query verification**

