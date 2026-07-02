# Agent Routing — Quick Reference Card

## The Golden Rule

```
Single Fact Question? → analyst
                ↓
Research Synthesis Question? → deep_researcher
                ↓
Data Correlation? → data_analyst
                ↓
News Context? → news_monitor
                ↓
Need Help? → support_drafts
```

---

## Quick Decision Matrix

| Question | Agent | Signal |
|----------|-------|--------|
| "How did Smith vote on HB 456?" | analyst | Single fact |
| "Who donated to campaign X?" | analyst | Single fact |
| "What is bill SB 123?" | analyst | Single fact |
| "Why do donors support certain votes?" | deep_researcher | Why/pattern |
| "What patterns exist in voting?" | deep_researcher | Pattern |
| "Do donations influence votes?" | deep_researcher | Causation |
| "How has spending changed?" | deep_researcher | Trend |
| "Correlation between donations and votes?" | data_analyst | Statistics |
| "What patterns exist (correlation)?" | data_analyst | Statistics |
| "What's the news covering?" | news_monitor | News context |
| "How do I search?" | support_drafts | Help |

---

## Analyst Scope

**✓ DOES:**
- Return single facts from official records
- Cite source, date, amount, ID
- Flag data limits
- Route synthesis questions to Deep Researcher

**✗ DOESN'T:**
- Research synthesis
- Pattern analysis
- "Why" questions
- Causation claims
- Multi-step analysis

---

## Deep Researcher Scope

**✓ DOES:**
- Research synthesis
- Multi-step questions
- "Why" and "what patterns" questions
- Causation investigation (with caveats)
- Include confidence + limitations

**✗ DOESN'T:**
- Return raw single facts
- Make causal claims without evidence
- Report weak correlations as findings

---

## Data Analyst Scope

**✓ DOES:**
- Calculate correlations
- Report r > 0.60 OR p < 0.05 only
- Include n, methodology, confidence intervals
- Flag weak correlations as "not significant"

**✗ DOESN'T:**
- Make causal claims
- Report weak correlations as findings
- Return single facts

---

## Routing Examples

### ✓ Analyst (Single Fact)
```
Q: "How did Smith vote on education bill HB 234?"
A: "YES, March 10, 2024, per Virginia LIS"
```

### → Deep Researcher (Pattern/Synthesis)
```
Q: "Why do education donors support education votes?"
ANALYST: "Use Deep Researcher for pattern analysis"

Q (to Deep Researcher): Same question
A: Research report with 3-5 sub-questions, 
   confidence levels, and limitations
```

### → Data Analyst (Statistics)
```
Q: "Correlation between education donations and education votes?"
DATA_ANALYST:
  r=0.74, p<0.001, n=156
  Strong, statistically significant
  Correlation does not imply causation
```

---

## Implementation Checklist

- [x] Analyst prompt updated
- [x] System prompt updated
- [x] Documentation created
- [x] Examples provided
- [ ] Test with sample questions
- [ ] Deploy to production
- [ ] Monitor routing decisions

---

## Code Detection

**Analyst should decline if:**
```python
keywords = ["why", "pattern", "trend", "cause", "effect", "influence"]
multi_step = "and" in question.lower()
synthesis = any(kw in question.lower() for kw in keywords) or multi_step

if synthesis:
    return "Use Deep Researcher for pattern analysis"
```

---

## Signal Words

| Agent | Keywords |
|-------|----------|
| analyst | How, What, When, Where (single) |
| deep_researcher | Why, pattern, trend, effect, cause |
| data_analyst | Correlation, relationship, r, p-value |
| news_monitor | News, reported, coverage, headlines |
| support_drafts | Help, how do I, explain, feature |

---

## Common Mistakes to Avoid

❌ Analyst answering "Why do donors support votes?"
→ Route to Deep Researcher

❌ Analyst reporting weak correlation (r=0.35)
→ Route to Data Analyst

❌ Deep Researcher claiming causation without evidence
→ Acknowledge confounders, limit to correlation

❌ Data Analyst reporting r<0.60 as finding
→ Label as "weak/not significant"

---

## One-Liner Rule

```
Is it a single fact? → analyst
Is it a pattern/why? → deep_researcher
Is it statistics? → data_analyst
Is it news? → news_monitor
Is it help? → support_drafts
```

---

## Testing

```python
# Analyst declines synthesis
assert "Deep Researcher" in analyst("Why do donors support votes?")

# Analyst answers facts
assert "Virginia LIS" in analyst("How did Smith vote on HB 456?")

# Deep Researcher handles synthesis
assert "Question 1:" in deep_researcher("Why do donors support votes?")

# Data Analyst filters weak correlations
assert "not significant" in data_analyst(r=0.35, p=0.08)
```

---

## Resources

- **Full Guide:** AGENT_ROUTING_GUIDE.md
- **Implementation:** ANALYST_ROUTING_IMPLEMENTATION.md
- **Agent Capabilities:** AGENT_DATA_SOURCES.md
- **Code:** voteiq/api/routes/chat.py (agent registry)

---

**Rule Effective:** May 24, 2026  
All agents now follow routing rules.  
Analyst routes research synthesis to Deep Researcher.
