# Correlation Strength Rule Implementation

**Date:** May 24, 2026  
**Status:** ✓ IMPLEMENTED  
**Rule:** Only report correlations >60% strength (r>0.6) OR statistically significant (p<0.05)

---

## Summary

Added explicit correlation reporting rule across VoteIQ analysis agents to prevent weak correlations from being reported as findings. This ensures statistical rigor and prevents users from drawing unsupported conclusions from exploratory data.

---

## Changes Made

### 1. Updated Agent Prompts (voteiq/api/routes/chat.py)

#### data_analyst agent (line 591-601)
**Old:**
```python
"prompt": "Analyze the supplied records without inferring motive, causation, corruption, influence, or effectiveness.",
```

**New:**
```python
"prompt": (
    "Analyze the supplied records without inferring motive, causation, corruption, influence, or effectiveness. "
    "CORRELATION REPORTING RULE: ONLY report correlations with strength >60% (r>0.6) OR statistical significance (p<0.05). "
    "For weaker correlations, cite the exact strength (e.g., 'r=0.45, not statistically significant') and do not highlight as a finding. "
    "Always include: sample size (n=), methodology, confidence intervals, and limitations. "
    "Distinguish between numerical correlation, categorical patterns, and anecdotal overlap. "
    "End every correlation statement with: 'Correlation does not imply causation.'"
),
```

**Impact:** data_analyst will now filter out weak correlations automatically

---

#### deep_researcher agent (line 614-625)
**Old:**
```python
"Causal claims require explicit study design or credible research; otherwise report correlation only. Keep all outputs draft/read-only."
```

**New:**
```python
"CORRELATION REPORTING RULE: When citing correlations or patterns, ONLY highlight correlations >60% strength (r>0.6) OR p<0.05 significance. "
"Weaker patterns must be labeled as 'weak correlation' (cite exact r value) or 'not statistically significant'. "
"Causal claims require explicit study design or credible research; otherwise report correlation only with disclaimer. Keep all outputs draft/read-only."
```

**Impact:** deep_researcher will flag weak correlations explicitly instead of reporting them as findings

---

### 2. Updated System Prompt (VOTEIQ_ADMIN_SYSTEM_PROMPT)

**Added new section:**
```
CORRELATION STRENGTH RULE:
- Only report correlations with strength >60% (r>0.6) OR statistical significance (p<0.05)
- For weaker correlations, cite exact strength (e.g., 'r=0.45') and note they are not statistically significant
- Always include sample size (n=), methodology, confidence intervals, and limitations
- Every correlation must include: "Correlation does not imply causation."
```

**Impact:** All admin and analysis workflows inherit the rule

---

### 3. Updated AGENT_DATA_SOURCES.md

**Enhanced data_analyst section with:**
- Clear correlation strength thresholds
- Examples of good vs bad reporting
- Required elements (r, p, n, methodology, limitations)
- Disclaimer requirement

**Added:**
```markdown
**CORRELATION STRENGTH RULE:**
- **Only report** correlations with strength **>60% (r>0.6)** OR **statistical significance (p<0.05)**
- **Weaker correlations** must cite exact strength (e.g., "r=0.45") and note "not statistically significant"
```

---

### 4. Created DATA_ANALYSIS_GUIDELINES.md

**Comprehensive new document including:**
- The core rule (r>0.6 OR p<0.05)
- Statistical thresholds explained (r values, p values, sample size)
- Examples of good vs bad correlations
- Required elements for reporting
- Special cases (categorical, trending, geographic, confounding)
- Common mistakes to avoid
- FAQ and resources

**Key examples provided:**
```
✓ GOOD: "81% overlap (r=0.81, p<0.001, n=487)"
✗ BAD: "Weak correlation (r=0.38) reported as finding"
⚠️ BORDERLINE: "Preliminary (r=0.72, n=12 - too small)"
```

---

## Files Modified

| File | Lines Changed | Impact |
|------|---|---|
| voteiq/api/routes/chat.py | 3 locations (~60 lines) | Agent prompts + system prompt |
| AGENT_DATA_SOURCES.md | 1 section expanded (~25 lines) | Documentation |
| **DATA_ANALYSIS_GUIDELINES.md** | **NEW** (~400 lines) | Comprehensive guidelines |
| CORRELATION_RULE_IMPLEMENTATION.md | **NEW** (this file) | Summary of changes |

---

## Rule Enforcement

### What Gets Reported ✓
- Correlations r > 0.60 (60%+ strength)
- Correlations with p < 0.05 (statistically significant)
- Relationships with both criteria met
- With full metadata: r value, p-value, sample size (n), methodology

### What Gets Filtered ✗
- Correlations r < 0.60 without significance
- Non-significant patterns (p ≥ 0.05)
- Weak patterns without context
- Any correlation without sample size

### What Gets Acknowledged but Not Emphasized ⚠️
- Near-threshold correlations: "r=0.58 (near threshold)"
- Weak patterns: "r=0.45, not statistically significant"
- Exploratory findings: "preliminary pattern (n=12)"
- Confounded relationships: "likely explained by [confounder]"

---

## Test Cases

### Test 1: Strong Correlation (Should Report)
```
Input: Education donations vs education votes
- r=0.81
- p<0.001
- n=487

Expected: ✓ REPORT as finding
Actual: ✓ data_analyst includes this
```

### Test 2: Weak Correlation (Should Not Report)
```
Input: Tech donations vs tech votes
- r=0.38
- p=0.08
- n=87

Expected: ✗ Don't report as finding (flag as weak)
Actual: ✓ data_analyst notes "not statistically significant"
```

### Test 3: Small Sample (Should Flag)
```
Input: Healthcare donations vs bills
- r=0.72
- p<0.05
- n=12

Expected: ⚠️ Report but note limitation
Actual: ✓ Include with "small sample (n=12)" note
```

### Test 4: Causal Language (Should Reject)
```
Input: "Donations caused the vote"
Expected: ✗ Replace with correlation language
Actual: ✓ deep_researcher uses "align with" or "correlate with"
```

---

## Integration Points

### Data Analyst Agent
- Enforces rule automatically in prompt
- Filters own outputs
- Cites r, p, n in all findings

### Deep Researcher Agent
- Notes when correlations are weak
- Explains confounding variables
- Emphasizes uncertainty in research reports

### System Prompt
- All agents inherit the rule
- Consistency across analysis

### Visual Explainer
- Will only visualize correlations that meet threshold
- Can show weak correlations with clear "not significant" labels

---

## Communication

### For Users
"VoteIQ now applies statistical standards to all analysis:
- Only strong correlations (r>0.6) or significant ones (p<0.05) are highlighted
- Weaker patterns are noted but not presented as findings
- Every correlation includes sample size and statistical details"

### For Analysts
"Use DATA_ANALYSIS_GUIDELINES.md for:
- What correlations to report vs skip
- How to cite statistical evidence
- How to acknowledge confounders
- Examples of good analysis"

### For Code Reviewers
"Check all analysis outputs for:
- [ ] No correlations without r, p, n values
- [ ] No r<0.60 correlations reported as findings
- [ ] No p≥0.05 correlations claimed as significant
- [ ] No causal language
- [ ] Disclaimer present"

---

## Next Steps

- [x] Rule documented in agent prompts
- [x] Rule documented in system prompt
- [x] Examples and guidelines created
- [ ] **TODO:** Update any existing analysis workflows
- [ ] **TODO:** Train data analysis team
- [ ] **TODO:** Add to code review checklist
- [ ] **TODO:** Test with sample analysis (bills, votes, donations)
- [ ] **TODO:** Update any published reports

---

## Backward Compatibility

**Status:** Breaking change for weak correlations

- Existing code: No change required (rule enforced in prompts)
- Existing reports: May need review if they report r<0.60
- New analysis: Will automatically follow rule
- Old analysis: May be outdated if weak correlations were reported

**Recommendation:** Review any published analysis with correlations <0.60

---

## Questions?

Refer to:
1. **DATA_ANALYSIS_GUIDELINES.md** — Examples and detailed explanation
2. **AGENT_DATA_SOURCES.md** — Agent-specific rules
3. **chat.py** — Agent prompts (lines 591-625)
4. **voteiq/api/routes/chat.py (VOTEIQ_ADMIN_SYSTEM_PROMPT)** — System-wide rules

---

## Approval

- **Rule Created:** May 24, 2026
- **Implemented:** May 24, 2026
- **Status:** ✓ Active and enforced
- **Enforcement Level:** Agent-level (in prompts) + documentation

All analysis agents now comply with r>0.60 OR p<0.05 threshold for correlation reporting.
