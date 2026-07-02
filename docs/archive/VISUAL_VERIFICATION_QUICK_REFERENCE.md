# Visual Verification — Quick Reference

## The Rule

```
Visual Explainer wants to render visualization
         ↓
MUST call Golden Query first
         ↓
Golden Query evaluates: "Is data sufficient?"
         ↓
✓ YES → Visual Explainer renders
✗ NO  → Visual Explainer returns error + flagged issues
```

---

## Verification Checklist

**Golden Query MUST confirm:**

- [ ] All major sources present
- [ ] No significant data gaps
- [ ] Strong correlations (r > 0.60 OR p < 0.05)
- [ ] Adequate sample size (n > 30)
- [ ] Data current (< 6 months old for current period)
- [ ] Sources cited with dates
- [ ] No unresolved conflicts

---

## Render vs. Reject

| Condition | Golden Query | Visual Explainer |
|-----------|--------------|------------------|
| All sources, no gaps | ✓ Approve | Render |
| Weak correlation (r=0.35) | ✗ Reject | Don't render |
| Sample too small (n=10) | ✗ Reject | Don't render |
| Data incomplete | ✗ Flag | Return error |
| Conflicting sources | ✗ Flag | Don't render |
| Strong data, current | ✓ Approve | Render |

---

## Golden Query Output

### Approve (Render)
```json
{
  "sufficient": true,
  "missing_evidence": null,
  "weak_evidence": [],
  "recommendation": "Data is ready for visualization"
}
```

### Reject (Don't Render)
```json
{
  "sufficient": false,
  "missing_evidence": ["VPAP records pending", "Q3 2026 data"],
  "weak_evidence": ["Correlation r=0.35 (below threshold)"],
  "recommendation": "Do not render. Collect additional data."
}
```

---

## Visualization Types

### Charts
**Data needed:** Time-series, correlation, distribution  
**Before render:** Golden Query verifies data completeness, correlations > threshold

### Maps
**Data needed:** Geographic distribution, donors by region  
**Before render:** Golden Query verifies coverage (all regions represented), sample size

### Timelines
**Data needed:** Date-sorted events with amounts  
**Before render:** Golden Query verifies no gaps in critical timespan

### Tables
**Data needed:** Structured facts with sources  
**Before render:** Golden Query verifies all sources cited, data current

---

## Implementation Flow

```python
# Visual Explainer receives data
data = {
  "type": "donation_timeline",
  "data": [...],
  "sources": ["FEC", "VPAP"],
  "current_through": "2026-05-24"
}

# Step 1: Call Golden Query
verification = golden_query.evaluate(
  data=data,
  question="Is this data sufficient for visualization?"
)

# Step 2: Check result
if verification['sufficient']:
    # Render visualization
    return render_visualization(data)
else:
    # Return error with flagged issues
    return error(
      message="Data incomplete",
      missing=verification['missing_evidence'],
      weak=verification['weak_evidence']
    )
```

---

## User-Facing Scenarios

### Scenario 1: Data Approved
```
USER: "Chart education donations to Smith"
        ↓
ANALYST: Provides 12 FEC donations (2026 Q1-Q2)
        ↓
VISUAL EXPLAINER → GOLDEN QUERY: Verify data
        ↓
GOLDEN QUERY: ✓ Data sufficient
        ↓
VISUAL EXPLAINER: Renders timeline chart
USER SEES: Clean visualization with sources cited
```

### Scenario 2: Data Insufficient
```
USER: "Chart education donations to Smith"
        ↓
ANALYST: Provides 12 FEC donations, VPAP pending
        ↓
VISUAL EXPLAINER → GOLDEN QUERY: Verify data
        ↓
GOLDEN QUERY: ✗ VPAP data missing (normal lag)
        ↓
VISUAL EXPLAINER: Returns error + missing data flag
USER SEES: "Data incomplete. VPAP records pending (2-5 days).
           Try again after [date] or request from Analyst."
```

### Scenario 3: Weak Evidence
```
USER: "Chart correlation: donations vs votes"
        ↓
ANALYST: Provides correlation r=0.35, p=0.12
        ↓
VISUAL EXPLAINER → GOLDEN QUERY: Verify
        ↓
GOLDEN QUERY: ✗ Correlation below threshold
        ↓
VISUAL EXPLAINER: Rejects rendering
USER SEES: "Correlation not statistically significant (r=0.35, p=0.12).
           Does not meet VoteIQ reporting threshold (r>0.60 or p<0.05)."
```

---

## Status Codes

| Status | Meaning | Action |
|--------|---------|--------|
| **SUFFICIENT** | Data ready | Render |
| **INCOMPLETE** | Gaps present | Flag + return to analyst |
| **WEAK** | Below threshold | Don't render |
| **CONFLICT** | Sources disagree | Flag escalation |
| **TIMEOUT** | Golden Query no response | Error + escalate |

---

## Troubleshooting

### "Golden Query not responding"
- Wait up to 10 seconds
- If still no response, return error
- Do NOT render without verification
- Escalate to support

### "Data appears sufficient but Golden Query rejects"
- Trust Golden Query (data quality expert)
- Return detailed rejection reason to user
- Suggest: "Request complete data from Analyst"

### "User says data looks fine, why no render?"
- Golden Query caught issues (missing sources, weak correlations)
- Never override Golden Query for data quality
- Explain specific flagged issues to user

---

## Code Location

- **Visual Explainer:** chat.py, lines 681-692
- **Golden Query:** chat.py, lines 593-599
- **System prompt:** chat.py, lines 94-97
- **Specification:** VISUAL_VERIFICATION_MANDATE.md

---

## Summary

| Step | Owner | Action |
|------|-------|--------|
| 1 | Visual Explainer | Prepare visualization data |
| 2 | Visual Explainer | Call Golden Query to verify |
| 3 | Golden Query | Evaluate: "Is data sufficient?" |
| 4 | Golden Query | Return approval/rejection + issues |
| 5 | Visual Explainer | Render (if ✓) or error (if ✗) |

---

**Rule Effective:** May 25, 2026  
**Priority:** 7 (Data Quality Critical)  
**No visualization renders without Golden Query verification**

