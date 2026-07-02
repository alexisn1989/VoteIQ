# VoteIQ Agent Routing Guide

**Rule:** Route research synthesis questions to Deep Researcher, not Analyst.

---

## Quick Decision Tree

```
User Question
     ↓
Is it a SINGLE FACT?
(one vote, one donation, one bill, one executive order)
     ↓
   YES → Public Record Analyst
     ↓
   NO ↓
     ↓
Is it a PATTERN or TREND question?
(why, how many, correlations, trends)
     ↓
   YES → Deep Researcher
     ↓
   NO ↓
     ↓
Is it asking for DATA ANALYSIS?
(calculate correlations, find patterns, statistics)
     ↓
   YES → Data Analyst
     ↓
   NO ↓
     ↓
Need SUPPORT or HELP?
     ↓
   YES → Support Drafts
     ↓
   NO → Try Deep Researcher or ask for clarification
```

---

## Agent Scope Matrix

| Agent | Scope | ✓ Do This | ✗ Don't Do This |
|-------|-------|----------|---|
| **analyst** | **Exact facts from records** | Single vote, donation, bill, executive order, date, amount, ID | Research synthesis, pattern analysis, "why" questions |
| **deep_researcher** | **Research synthesis & patterns** | Multi-step questions, trend analysis, pattern investigation, cross-source research | Return raw single facts (use analyst) |
| **data_analyst** | **Statistical pattern analysis** | Correlations r>0.60 or p<0.05, pattern metrics | Causation claims, weak correlations |
| **field_monitor** | **Intelligence briefing** | Official records + polling context, weekly digests | Verification (use analyst for facts) |
| **news_monitor** | **News narrative** | News coverage summary, what's being reported | Fact verification, official details |
| **support_drafts** | **Help & support** | Answer questions about how to use VoteIQ, explain features | Make analysis claims |

---

## Examples: Which Agent?

### ✓ Public Record Analyst

**USER:** "How did Virginia Rep. Smith vote on HB 456?"
```
ANALYST: "YES, on March 15, 2024, per Virginia LIS"
→ Correct: Single fact from official record
```

**USER:** "Who donated to the education committee?"
```
ANALYST: Lists donors with amounts and dates from FEC/VPAP
→ Correct: Direct record lookup
```

**USER:** "What is bill SB 123?"
```
ANALYST: "SB 123 authorizes education funding [details from Virginia LIS]"
→ Correct: Direct record lookup
```

---

### ✗ Don't Use Analyst For (Route to Deep Researcher)

**USER:** "Why do education donors support education votes?"
```
ANALYST: ✗ WRONG: "Donors may prefer candidates..."
ANALYST: ✓ CORRECT: "I can't synthesize patterns. Try Deep Researcher for analysis."
→ Route to Deep Researcher for multi-step investigation
```

**USER:** "Do large donations influence voting patterns?"
```
ANALYST: ✗ WRONG: "I found correlation of 0.73..."
ANALYST: ✓ CORRECT: "That requires pattern analysis. Use Deep Researcher."
→ Route to Deep Researcher or Data Analyst
```

**USER:** "How has education funding changed over time?"
```
ANALYST: ✗ WRONG: "Funding increased from X to Y..."
ANALYST: ✓ CORRECT: "For trend analysis, use Deep Researcher."
→ Route to Deep Researcher for trend analysis
```

---

### ✓ Deep Researcher

**USER:** "Why do education donors support education votes?"
```
DEEP_RESEARCHER: 
  Question 1: How much do education donors give vs. other donors?
  Question 2: Do education committee members receive more education donations?
  Question 3: Do education votes correlate with education donations?
  Question 4: What other explanations exist? (candidate recruitment, sector focus, etc.)
  Conclusion: Multiple factors contribute; causation unclear...
→ Correct: Multi-step research synthesis
```

**USER:** "What patterns exist in healthcare voting?"
```
DEEP_RESEARCHER:
  Research: Analyzed 200+ healthcare votes across 50 legislators
  Finding 1: Healthcare committee members vote YES on healthcare bills 85% of time
  Finding 2: Party affiliation explains some variation (r=0.58)
  Finding 3: Gaps in data for certain categories
  Confidence: Moderate; recommend additional analysis on [specific area]
→ Correct: Research synthesis with confidence levels
```

**USER:** "Do small donors have less influence than large donors?"
```
DEEP_RESEARCHER:
  Question 1: How many votes do candidates with large donors win?
  Question 2: How many votes do candidates with small donors win?
  Question 3: What confounders exist? (party, district competitiveness, experience)
  Conclusion: Cannot isolate donor influence from confounders; requires more sophisticated analysis
→ Correct: Research synthesis acknowledging limitations
```

---

### ✓ Data Analyst

**USER:** "What's the correlation between education donations and education votes?"
```
DATA_ANALYST:
  Correlation: r=0.74, p<0.001, n=156 legislators
  Sample: All 156 state legislators, 2023-2025
  Methodology: Pearson correlation of [metric] vs [metric]
  Limitation: Small sample in some districts
  Result: Strong, statistically significant correlation
  Note: Correlation does not imply causation
→ Correct: Statistical analysis with thresholds
```

**USER:** "What patterns exist in PAC donations by sector?"
```
DATA_ANALYST:
  Sector breakdown: [correlation table with r, p, n for each]
  Only highlights: r>0.60 OR p<0.05 correlations
  Notes: "Weak correlation in tech sector (r=0.38, not significant)"
→ Correct: Pattern metrics with strength filtering
```

---

### ✓ Support Drafts

**USER:** "How do I use the search feature?"
```
SUPPORT_DRAFTS: "The search bar allows you to..."
→ Correct: Help & support
```

**USER:** "What data sources does VoteIQ use?"
```
SUPPORT_DRAFTS: "VoteIQ uses official public records from FEC, Virginia LIS, Congress.gov..."
→ Correct: Explain features
```

---

## Conversation Flow Examples

### Example 1: Single Fact Question

```
USER: "How did Smith vote on education bill HB 234?"

ANALYST: "YES, March 10, 2024, Virginia LIS reference [link]"

USER: "Is that typical for him on education bills?"

→ This is now a pattern question, route to Deep Researcher
ANALYST: "For pattern analysis, use Deep Researcher."
DEEP_RESEARCHER: "Yes, Smith votes YES on ~85% of education bills, which is above the chamber average..."
```

### Example 2: Pattern Question

```
USER: "Why do some politicians vote differently on education than their party?"

ANALYST: ✗ Can't help (research synthesis)

DEEP_RESEARCHER:
  Question 1: How many intra-party deviations on education bills? (n=23)
  Question 2: Which sectors do deviating members represent? (urban/rural split)
  Question 3: Do they receive different donations? (correlation analysis)
  Conclusion: Committee assignment and district demographics correlate with deviations...

DATA_ANALYST (if needed): Calculates correlation metrics
  - Party alignment vs education donations: r=0.38 (weak)
  - Committee assignment vs votes: r=0.71 (strong)
```

### Example 3: Broad Question

```
USER: "Is campaign money influencing Virginia politics?"

ANALYST: ✗ Can't help (broad research question)

DEEP_RESEARCHER:
  Question 1: Do legislators receiving more money vote differently?
  Question 2: In which policy areas? (education, healthcare, transportation)
  Question 3: What's the magnitude of effect?
  Question 4: Could other factors explain the pattern? (party, district, committee)
  Conclusion: Modest correlations observed, but confounders make causation unclear.
  Recommendation: Conduct targeted analysis on specific policy area...
```

---

## Routing Rules

### Rule 1: Analyst Declines Research Synthesis

```python
if request.is_research_synthesis:
    return analyst.decline("Use Deep Researcher for pattern analysis")
else:
    return analyst.answer(request)
```

**Signs of research synthesis:**
- Multi-step questions
- "Why" questions
- Pattern questions ("Do donors influence votes?")
- Trend questions ("Is X increasing?")
- Comparative questions ("Do politicians in Y behave differently?")

### Rule 2: Deep Researcher Always Includes Limitations

```
DEEP_RESEARCHER MUST:
- Acknowledge confounders
- Cite confidence levels (high/moderate/low)
- Note data gaps
- Avoid causal claims without evidence
- Suggest follow-up analysis
```

### Rule 3: Data Analyst Filters Weak Correlations

```python
if correlation.strength > 0.60 or correlation.p_value < 0.05:
    return data_analyst.report(correlation)
else:
    return data_analyst.note("Weak/not significant, n=" + correlation.n)
```

---

## Common Mistakes

### ❌ Mistake 1: Analyst Doing Synthesis

```
USER: "Why do some legislators defect from party votes?"

ANALYST (WRONG):
  "Party members defect for several reasons:
   1. Committee assignments align with votes
   2. Donors may prefer certain votes
   3. ..."

ANALYST (RIGHT):
  "I can only provide individual facts. For pattern analysis, 
   use Deep Researcher to investigate why defections occur."
```

### ❌ Mistake 2: Deep Researcher Making Causal Claims

```
USER: "Does campaign money influence voting?"

DEEP_RESEARCHER (WRONG):
  "Yes, donations cause votes. Donors give to candidates who vote their way."

DEEP_RESEARCHER (RIGHT):
  "Donations and votes correlate (r=0.71, p<0.01).
   Possible explanations:
   1. Donors support candidates with aligned views (not causation)
   2. Candidates with certain views attract certain donors
   3. Confounders: party, district, committee assignments
   We cannot determine causation from this data alone."
```

### ❌ Mistake 3: Data Analyst Reporting Weak Correlations

```
USER: "Correlate tech donations with tech votes"

DATA_ANALYST (WRONG):
  "I found a correlation of r=0.35 between tech donations and tech votes"

DATA_ANALYST (RIGHT):
  "Weak correlation observed (r=0.35, p=0.12, n=89).
   Not statistically significant. Insufficient evidence."
```

---

## For Developers: Implementing Routing

### Pseudo-code Example

```python
def route_question(user_question):
    if is_single_fact(user_question):
        return route_to("analyst")
    elif is_research_synthesis(user_question):
        return route_to("deep_researcher")
    elif is_correlation_analysis(user_question):
        return route_to("data_analyst")
    elif is_news_context(user_question):
        return route_to("news_monitor")
    else:
        return route_to("support_drafts")

def is_research_synthesis(q):
    keywords = ["why", "pattern", "trend", "influence", "cause", "effect"]
    multi_step = q.count("and") > 1
    return any(k in q.lower() for k in keywords) or multi_step
```

### Python Detection Example

```python
import re

def needs_deep_researcher(question):
    """Check if question requires Deep Researcher"""
    synthesis_patterns = [
        r"why.*vote",
        r"pattern.*vot",
        r"trend.*don",
        r"influence.*vote",
        r"cause.*vote",
        r"does.*affect",
        r"compared?.*to",
        r"how.*chang",
    ]
    
    is_synthesis = any(
        re.search(pattern, question.lower())
        for pattern in synthesis_patterns
    )
    
    return is_synthesis
```

---

## Testing Routing

### Unit Tests

```python
def test_analyst_single_fact():
    q = "How did Smith vote on HB 456?"
    assert route_to(q) == "analyst"

def test_deep_researcher_pattern():
    q = "Why do education donors support education votes?"
    assert route_to(q) == "deep_researcher"

def test_data_analyst_correlation():
    q = "What's the correlation between donations and votes?"
    assert route_to(q) == "data_analyst"

def test_analyst_declines_synthesis():
    q = "Do donations influence voting?"
    result = route_to(q)
    assert "Deep Researcher" in result.suggestion
```

---

## Summary

| Question Type | Agent | Signal Words |
|---|---|---|
| Single fact | **analyst** | How, What, When, Where, Who (singular) |
| Research synthesis | **deep_researcher** | Why, pattern, trend, influence, compared |
| Correlations | **data_analyst** | Correlation, relationship, strength, p-value |
| News context | **news_monitor** | What's being reported, news, coverage |
| Help & support | **support_drafts** | How do I, how does, explain, feature |

**Golden Rule:** If it requires multiple steps to answer, route to Deep Researcher.

---

## References

- AGENT_DATA_SOURCES.md — Agent capabilities and data boundaries
- DATA_ANALYSIS_GUIDELINES.md — Correlation reporting standards
- chat.py (Agent Registry) — Agent prompts and configuration
- VOTEIQ_ADMIN_SYSTEM_PROMPT — System-wide agent roles

---

**Effective:** May 24, 2026  
**All agents now follow routing rules**
