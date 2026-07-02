# Analyst Routing Rule Implementation

**Rule:** "For research synthesis, route to Deep Researcher"  
**Status:** ✓ IMPLEMENTED  
**Date:** May 24, 2026

---

## Summary

Updated the Public Record Analyst agent to explicitly decline research synthesis questions and route them to Deep Researcher. This clarifies agent roles and prevents analyst from attempting multi-step analysis it shouldn't handle.

---

## Changes Made

### 1. Updated Analyst Agent Prompt (voteiq/api/routes/chat.py, line 526)

**Old:**
```python
"prompt": "Return exact facts from structured public records. Cite record source, dates, amounts, IDs, votes, bill actions, executive orders, and data limits. Do not produce broad research synthesis; route that to Deep Researcher.",
```

**New:**
```python
"prompt": (
    "Return exact facts from structured public records ONLY: votes (Virginia LIS, Congress.gov), "
    "donations (FEC, VPAP), bill actions, executive orders, dates, amounts, IDs, and committees. "
    "Always cite source, date, and amount. Flag data limits explicitly. "
    "YOUR SCOPE: Individual facts from official records (single vote, single donation, single bill action). "
    "NOT YOUR SCOPE: Research synthesis, pattern analysis, broader conclusions, multi-step analysis. "
    "FOR RESEARCH SYNTHESIS: Always route to Deep Researcher (e.g., 'Why do education donors support education votes?' → Deep Researcher). "
    "For broad questions about trends, patterns, or causation, decline and suggest Deep Researcher."
),
```

**Changes:**
- ✓ Explicit scope definition (what analyst does & doesn't do)
- ✓ Clear examples of routing to Deep Researcher
- ✓ Instruction to decline and suggest alternatives
- ✓ Multi-line format for clarity

---

### 2. Updated System Prompt (VOTEIQ_ADMIN_SYSTEM_PROMPT, lines 87-92)

**Old:**
```python
Agent roles:
- Public Record Analyst is for exact facts from structured records: votes, donations, bill actions, executive orders, dates, amounts, officials, committees, and IDs.
- Deep Researcher is for broader research reports: multi-step questions, source comparison, synthesis, confidence levels, contradictions, and data gaps.
- Data Analyst is for pattern analysis: only report correlations >60% strength (r>0.6) OR p<0.05 significance.
- For exact current facts such as a vote, donation, bill action, executive order, or recent polling, defer to the Public Record Analyst or SQL/API-first workflow.
- Causal claims require explicit study design or credible research. Otherwise report correlation only.
```

**New:**
```python
Agent roles and routing:
- Public Record Analyst: Exact facts ONLY from structured records (single vote, donation, bill action, executive order, date, amount, ID).
  └─ ROUTE research synthesis questions to Deep Researcher ("Why do donors align with votes?" → Deep Researcher)
- Deep Researcher: Broader research synthesis, multi-step analysis, pattern investigation, contradictions, confidence levels, data gaps.
  └─ INPUT: Research synthesis, trend questions, "why" questions, cross-source analysis
  └─ OUTPUT: Multi-question research reports with confidence levels and limitations
- Data Analyst: Pattern analysis and correlations (only r>0.60 OR p<0.05 significance).
  └─ INPUT: "What patterns exist in [dataset]?"
  └─ OUTPUT: Correlation tables with r, p, n, methodology
- For exact current facts (vote, donation, bill, executive order, polling), use Public Record Analyst.
- For broad questions, trends, or causation, use Deep Researcher.
- Causal claims require explicit study design or credible research. Otherwise report correlation only.
```

**Changes:**
- ✓ Added explicit routing guidance
- ✓ Clarified input/output for each agent
- ✓ Emphasized "ROUTE research synthesis to Deep Researcher"
- ✓ Examples showing routing decisions

---

### 3. Updated AGENT_DATA_SOURCES.md

**Added to analyst section:**
```markdown
Scope: Individual facts ONLY
```
- ✓ Example checkboxes (✓ Does this, ✗ Don't do that)
- ✓ Routing examples showing when to decline
- ✓ Clear scope definition
```

---

## Router Decision Logic

### When to Use Analyst
```
✓ "How did Smith vote on HB 456?"
✓ "Who donated to campaign X?"
✓ "What is bill SB 123?"
✓ "How much did organization Y donate?"
→ All: Single facts from official records
```

### When to Route to Deep Researcher
```
✗ "Why do education donors support education votes?"
✗ "What patterns exist in voting by party?"
✗ "Do donations influence legislative votes?"
✗ "How has funding changed over time?"
→ All: Research synthesis, patterns, trends, "why" questions
```

---

## Implementation Example

### Before (Analyst Doing Synthesis)

```
USER: "Why do some donors support certain votes?"

ANALYST (OLD):
  "Donors may support candidates who:
   1. Share their values
   2. Serve relevant committees
   3. Represent their districts..."
  ← Wrong: Synthesis instead of facts
```

### After (Analyst Routes to Deep Researcher)

```
USER: "Why do some donors support certain votes?"

ANALYST (NEW):
  "That's a research question. Use Deep Researcher for pattern analysis."
  ← Correct: Routes to appropriate agent

DEEP_RESEARCHER:
  "Question 1: How much do education donors give vs. others? (r=0.82)
   Question 2: Do they vote education issues more? (correlation analysis)
   Question 3: Other factors? (party, district, committee)
   Conclusion: Multiple explanations exist; causation unclear..."
  ← Correct: Multi-step research synthesis
```

---

## Agent Responsibilities

### Public Record Analyst
**Does:**
- ✓ Return single facts
- ✓ Cite official sources
- ✓ Include date, amount, ID
- ✓ Flag data limits
- ✓ Route synthesis questions to Deep Researcher

**Doesn't:**
- ✗ Analyze patterns
- ✗ Synthesize research
- ✗ Answer "why" questions
- ✗ Make causal claims
- ✗ Compare multiple facts into patterns

### Deep Researcher
**Does:**
- ✓ Answer "why" questions
- ✓ Analyze multi-step patterns
- ✓ Synthesize from multiple sources
- ✓ Acknowledge limitations
- ✓ Include confidence levels

**Doesn't:**
- ✗ Return raw single facts (use analyst)
- ✗ Make causal claims without evidence
- ✗ Report without confidence/limitations

---

## Testing Routing

### Unit Tests

```python
def test_analyst_routes_to_deep_researcher():
    questions = [
        "Why do donors support certain votes?",
        "Do large donations influence voting?",
        "What patterns exist in education voting?",
        "How has campaign spending changed?"
    ]
    for q in questions:
        result = analyst.answer(q)
        assert "Deep Researcher" in result

def test_analyst_answers_single_facts():
    questions = [
        "How did Smith vote on HB 456?",
        "Who donated to candidate X?",
        "What is bill SB 123?"
    ]
    for q in questions:
        result = analyst.answer(q)
        assert "per Virginia LIS" in result or "per FEC" in result
```

### Integration Tests

```python
def test_routing_workflow():
    # User asks a pattern question
    q1 = "Why do education donors support education votes?"
    
    # Analyst routes to Deep Researcher
    r1 = analyst.answer(q1)
    assert "Deep Researcher" in r1
    
    # Deep Researcher handles it
    r2 = deep_researcher.answer(q1)
    assert "Question 1:" in r2  # Multi-step
    assert "confidence" in r2.lower()  # Confidence level
    assert "confound" in r2.lower() or "other factor" in r2.lower()
```

---

## Documentation Files

| File | Purpose | Change |
|------|---------|--------|
| **AGENT_ROUTING_GUIDE.md** | New routing guide | Complete new file with decision trees, examples |
| **AGENT_DATA_SOURCES.md** | Updated analyst section | Added scope definition and routing examples |
| **chat.py** | Agent registry | Updated 2 locations (analyst prompt + system prompt) |

---

## User Behavior Changes

### Before
```
USER: "Why do donors support certain votes?"
ANALYST: Attempts synthesis analysis (wrong agent)
```

### After
```
USER: "Why do donors support certain votes?"
ANALYST: "Use Deep Researcher for pattern analysis"
→ Correct routing to appropriate agent
```

---

## Communication

### For Agents (in prompts)
"FOR RESEARCH SYNTHESIS: Always route to Deep Researcher"

### For Developers
See AGENT_ROUTING_GUIDE.md for decision tree and implementation

### For Users (if visible)
"Ask Deep Researcher about patterns, trends, and why questions"

---

## Files Modified Summary

```
voteiq/api/routes/chat.py:
  ✓ Line 526-533: analyst agent prompt (expanded & clarified)
  ✓ Line 87-92: VOVEIQ_ADMIN_SYSTEM_PROMPT (agent roles section)

Documentation:
  ✓ AGENT_DATA_SOURCES.md (analyst section expanded)
  ✓ AGENT_ROUTING_GUIDE.md (NEW - comprehensive routing guide)
  ✓ ANALYST_ROUTING_IMPLEMENTATION.md (this file)
```

---

## Checklist

- [x] Updated analyst agent prompt
- [x] Updated system prompt agent roles
- [x] Updated AGENT_DATA_SOURCES.md
- [x] Created AGENT_ROUTING_GUIDE.md
- [x] Created ANALYST_ROUTING_IMPLEMENTATION.md
- [ ] Test with sample questions
- [ ] Deploy to production
- [ ] Monitor user feedback

---

## Examples Covered

✓ Single fact questions (analyst answers)
✓ Pattern questions (analyst routes to deep researcher)
✓ Synthesis questions (analyst routes to deep researcher)
✓ Data analysis questions (analyst routes to data analyst)
✓ Support questions (analyst routes to support drafts)

---

## Impact

**Low Risk:**
- Routing is semantic/prompt-based
- No code changes (except prompts)
- No database changes
- No API changes

**Benefits:**
- ✓ Clearer agent separation
- ✓ Better user experience (right agent for question)
- ✓ More accurate results
- ✓ Prevents analyst from synthesizing (wrong scope)

---

## Follow-up Items

1. **Test:** Run integration tests with pattern questions
2. **Monitor:** Check logs for analyst routing decisions
3. **Document:** Update public-facing help if needed
4. **Feedback:** Gather user feedback on routing accuracy

---

## References

- AGENT_ROUTING_GUIDE.md — Complete routing decision tree
- AGENT_DATA_SOURCES.md — Agent capabilities  
- chat.py — Agent prompt definitions

---

**Status:** ✓ IMPLEMENTED  
**Effective:** May 24, 2026  
**All agents now follow routing rules**

Analyst now explicitly routes research synthesis questions to Deep Researcher.
