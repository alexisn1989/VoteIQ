# VoteIQ Structured Output Mandate

**Rule:** All data sources must be labeled with source type, name, and date.  
**Status:** ✓ IMPLEMENTED & ENFORCED  
**Date:** May 24, 2026

---

## The Mandate

Every factual claim or data point must cite its source using one of these prefixes:

```
OFFICIAL: [source name, date]
NEWS: [outlet name, date]
POLLING: [pollster name, date, sample size]
```

---

## Format Reference

### OFFICIAL: Official Public Records

Used for official government records and verified filing data.

**Format:**
```
OFFICIAL: [source name], [date]
```

**Examples:**
```
OFFICIAL: Virginia LIS, 2026-05-24
OFFICIAL: FEC, 2026-05-23
OFFICIAL: Congress.gov, 2026-05-22
OFFICIAL: VPAP, 2026-05-24
OFFICIAL: Virginia SBE, 2026-05-20
OFFICIAL: Governor's Office, 2026-05-24
```

**Sources that use OFFICIAL:**
- Virginia Legislative Information System (Virginia LIS)
- Federal Election Commission (FEC)
- Congress.gov
- Virginia State Board of Elections (SBE)
- Virginia Public Access Project (VPAP)
- Governor's Office (executive actions)

**Used by:**
- analyst (always)
- field_monitor (for official records portion)
- data_analyst (when reporting from official records)
- deep_researcher (for Tier 1 sources)

---

### NEWS: News Coverage

Used for news reporting and public narrative.

**Format:**
```
NEWS: [outlet name], [date]
```

**Examples:**
```
NEWS: Virginia Mercury, 2026-05-24
NEWS: WVTF, 2026-05-24
NEWS: WHRO, 2026-05-23
NEWS: Richmond Times-Dispatch, 2026-05-22
```

**Sources that use NEWS:**
- Virginia Mercury
- WVTF (NPR)
- WHRO
- Local news outlets
- Credible journalism covering Virginia politics

**Used by:**
- news_monitor (always)
- field_monitor (if mentioning news coverage)
- deep_researcher (for Tier 2 context sources)

**Important:**
- NEWS: is for what outlets are REPORTING
- Not for verification or fact-checking (use OFFICIAL)
- Not for opinion pieces (unless citing public narrative)

---

### POLLING: Polling Data

Used for survey data and polling results.

**Format:**
```
POLLING: [pollster name], [date], n=[sample size]
```

**Examples:**
```
POLLING: Roanoke College, 2026-05-24, n=500
POLLING: Monmouth University, 2026-05-23, n=800
POLLING: Emerson College, 2026-05-22, n=1200
```

**Sample Size:**
- **n=** prefix required
- Always include sample size when available
- Use actual sample size, not "n/a" or "unknown"

**Used by:**
- field_monitor (for polling portion)
- data_analyst (when reporting polling correlations)
- deep_researcher (when synthesizing polling trends)

**Important:**
- Always include sample size
- Always cite date of poll
- Note margin of error if available
- "Correlation does not imply causation"

---

## Examples by Use Case

### Single Fact (analyst)
```
Rep. Smith voted YES on HB 456
OFFICIAL: Virginia LIS, 2026-05-24

Education PAC donated $5,000 to candidate Jones
OFFICIAL: FEC, 2026-05-23
```

### Intelligence Brief (field_monitor)
```
IMMEDIATE (48h):
- House voted 43-37 on education bill
  OFFICIAL: Virginia LIS, 2026-05-24

- Public supports education funding
  POLLING: Roanoke College, 2026-05-24, n=500 (58% approve)

SOON (2 weeks):
- Senate education committee hearing scheduled
  OFFICIAL: Congress.gov, 2026-05-24
```

### News Coverage (news_monitor)
```
Today's Headlines:
- Virginia Mercury reports Governor announces education initiative
  NEWS: Virginia Mercury, 2026-05-24

- Rural caucus raises concerns about implementation
  NEWS: WVTF, 2026-05-24
```

### Research Synthesis (deep_researcher)
```
Question 1: Do education donors support education votes?
- 87% of education committee members receive education sector donations
  OFFICIAL: FEC + Virginia LIS, 2026-05-24
- Education votes pass 78% of the time
  OFFICIAL: Congress.gov, 2026-05-24
- Public opinion favors education funding (62% approval)
  POLLING: Roanoke College, 2026-05-24, n=500

Confidence: Moderate (correlational, confounders present)
```

### Data Analysis (data_analyst)
```
Education donations correlate with education votes:
- r=0.74, p<0.001, n=156
- Sources: OFFICIAL: FEC + Virginia LIS, 2026-05-24
- Confidence: Strong, statistically significant
- Note: Correlation does not imply causation
```

---

## When to Use Each Format

### ✓ OFFICIAL
- Voting records
- Campaign finance filings
- Bill actions and sponsors
- Executive orders
- Election results
- Any official government record

### ✓ NEWS
- News outlet reporting
- Press coverage
- Public statements reported in news
- What journalists are writing about civic issues

### ✓ POLLING
- Voter opinion surveys
- Political polling
- Any survey data with sample size

### ✗ Wrong (Don't Use Without Prefix)
```
❌ "Rep. Smith voted YES on HB 456"
✓ "OFFICIAL: Virginia LIS, 2026-05-24 — Rep. Smith voted YES on HB 456"

❌ "Virginia Mercury reports education concerns"
✓ "NEWS: Virginia Mercury, 2026-05-24 — Education concerns reported"

❌ "Voters favor education funding (58%)"
✓ "POLLING: Roanoke College, 2026-05-24, n=500 — 58% favor education funding"
```

---

## Implementation by Agent

### Public Record Analyst
**Always use:**
```
OFFICIAL: [source], [date]
```

**Example output:**
```
How did Smith vote on HB 456?

OFFICIAL: Virginia LIS, 2026-05-24
Rep. Smith voted YES on March 15, 2026. Vote was 43-37.
```

### Field Monitor
**Use both:**
```
OFFICIAL: [source], [date]
POLLING: [pollster], [date], n=[size]
```

**Example output:**
```
IMMEDIATE (48h):

OFFICIAL: Virginia LIS, 2026-05-24
- House passed education bill 43-37

POLLING: Roanoke College, 2026-05-24, n=500
- 58% public approval for education bill (margin of error: ±4%)
```

### News Monitor
**Always use:**
```
NEWS: [outlet], [date]
```

**Example output:**
```
Today's Headlines:

NEWS: Virginia Mercury, 2026-05-24
- Governor announces education initiative

NEWS: WVTF, 2026-05-24
- Rural caucus raises concerns about implementation cost
```

### Data Analyst
**Use relevant format:**
```
OFFICIAL: [source], [date]
POLLING: [pollster], [date], n=[size]
```

**Example output:**
```
Education donation correlation analysis:
- r=0.74, p<0.001, n=156
- Sources: OFFICIAL: FEC + Virginia LIS, 2026-05-24
- Confidence: Strong, statistically significant
```

### Deep Researcher
**Use all three as relevant:**
```
OFFICIAL: [source], [date]
NEWS: [outlet], [date]
POLLING: [pollster], [date], n=[size]
```

**Example output:**
```
Question 1: How much do education donors give?
OFFICIAL: FEC, 2026-05-24 — $47M total in 2026

Question 2: What's the news saying?
NEWS: Virginia Mercury, 2026-05-24 — "Education funding debated"

Question 3: What do voters think?
POLLING: Roanoke College, 2026-05-24, n=500 — 62% support education funding
```

---

## Testing Checklist

Before publishing any response, verify:

- [ ] All facts have source citation (OFFICIAL/NEWS/POLLING)
- [ ] Date is included with source
- [ ] Polling data includes sample size (n=)
- [ ] Source names are correct and complete
- [ ] Dates are in YYYY-MM-DD format
- [ ] No facts stated without source
- [ ] Distinction between OFFICIAL (facts), NEWS (reporting), and POLLING (opinion/trends)

---

## Common Mistakes to Avoid

### ❌ Missing Source
```
WRONG: "Rep. Smith voted YES on HB 456"
RIGHT: "OFFICIAL: Virginia LIS, 2026-05-24 — Rep. Smith voted YES on HB 456"
```

### ❌ Missing Date
```
WRONG: "OFFICIAL: Virginia LIS — Rep. Smith voted YES"
RIGHT: "OFFICIAL: Virginia LIS, 2026-05-24 — Rep. Smith voted YES"
```

### ❌ Missing Sample Size in Polling
```
WRONG: "POLLING: Roanoke College, 2026-05-24 — 58% approve"
RIGHT: "POLLING: Roanoke College, 2026-05-24, n=500 — 58% approve"
```

### ❌ Wrong Source Type
```
WRONG: "NEWS: Virginia Mercury, 2026-05-24 — Vote count was 43-37"
RIGHT: "OFFICIAL: Virginia LIS, 2026-05-24 — Vote count was 43-37"
```

### ❌ Confusing News with Official
```
WRONG: "Virginia Mercury reported House voted 43-37 on education bill"
RIGHT: "OFFICIAL: Virginia LIS, 2026-05-24 — House voted 43-37"
       "NEWS: Virginia Mercury, 2026-05-24 — Reporting on education vote"
```

---

## Configuration

### Embedded in Agent Prompts

**analyst prompt:**
```
MANDATORY STRUCTURED OUTPUT FORMAT: 
OFFICIAL: [source name, date]
```

**field_monitor prompt:**
```
MANDATORY STRUCTURED OUTPUT FORMAT:
  OFFICIAL: [source name, date]
  POLLING: [pollster name, date, sample size]
```

**news_monitor prompt:**
```
MANDATORY STRUCTURED OUTPUT FORMAT:
  NEWS: [outlet name, date]
```

**CHAT_PROMPT (voteiq/config/prompts.py):**
```
MANDATORY STRUCTURED OUTPUT FORMAT:
- OFFICIAL: [source name, date]
- NEWS: [outlet name, date]
- POLLING: [pollster name, date, sample size]
```

---

## Why This Matters

1. **Transparency:** Users see exactly where each fact comes from
2. **Accountability:** Clear attribution enables verification
3. **Distinction:** OFFICIAL ≠ NEWS ≠ POLLING
4. **Trust:** Structured format increases credibility
5. **Auditability:** Easy to trace claims back to sources

---

## Examples: Before vs After

### Before (Unclear Sources)
```
Rep. Smith has voted on education issues. The media has covered this.
Data shows public support for education. Smith's voting record aligns with
donations from education sector groups.
```

### After (Structured)
```
Rep. Smith voting record (OFFICIAL: Virginia LIS, 2026-05-24):
- YES on education bills: 78% (23 of 29 votes)

Media coverage (NEWS: Virginia Mercury, 2026-05-24):
- "Education focus shapes Smith's legislative agenda"

Public opinion (POLLING: Roanoke College, 2026-05-24, n=500):
- 62% public support for education funding

Donations analysis (OFFICIAL: FEC, 2026-05-24):
- Education sector donations: $47,000
- Correlation with education votes: r=0.74, p<0.001
```

---

## Enforcement

All agents enforce this mandate:
- ✓ analyst — OFFICIAL only
- ✓ field_monitor — OFFICIAL + POLLING
- ✓ news_monitor — NEWS only
- ✓ data_analyst — OFFICIAL + POLLING
- ✓ deep_researcher — OFFICIAL + NEWS + POLLING

**Non-compliance:** If an agent fails to cite sources with prefixes, flag it as missing attribution.

---

## References

- AGENT_DATA_SOURCES.md — Agent capabilities and boundaries
- AGENT_ROUTING_GUIDE.md — Which agent to use
- DATA_ANALYSIS_GUIDELINES.md — Correlation reporting standards
- voteiq/api/routes/chat.py — Agent prompt definitions
- voteiq/config/prompts.py — CHAT_PROMPT definition

---

**Effective:** May 24, 2026  
**All agents now enforce structured output**  
**Sources labeled: OFFICIAL | NEWS | POLLING**
