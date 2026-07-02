# VoteIQ Agent Data Sources Architecture

## Core Principle

VoteIQ distinguishes between **verified facts** and **news context** across its agent tier.

---

## Public-Facing Agents: Data Source Hierarchy

### **Tier 1: Verified Facts**

#### **analyst** — Official Public Records Only
**Sources:**
- Virginia LIS (state bills, votes, sponsors)
- FEC (federal donations, candidates, committees)
- Congress.gov (federal bills, votes, chamber activity)
- Virginia SBE (election results, voting records)
- VPAP (campaign finance across state/federal/local)
- Governor's Office (executive actions)

**Scope:** Individual facts ONLY
```
✓ What: Single facts (one vote, one donation, one bill)
✓ Questions: "How did Rep. Smith vote on HB 456?"
✓ Questions: "Who donated to Candidate X?"
✓ Questions: "What does bill SB 123 do?"
✗ Questions: "Why do donors support certain votes?"
✗ Questions: "What patterns exist in voting?"
✗ Questions: "Do education donors influence education votes?"
```

**CONFLICT DETECTION & ESCALATION:**
If different sources report conflicting values (e.g., FEC vs. VPAP donation amounts):
→ Flag the conflict explicitly
→ List all conflicting sources and their values
→ Suggest Data Quality Escalator for resolution

**ROUTING RULES:**
1. Research synthesis question → Route to **Deep Researcher**
2. Source conflict detected → Flag conflict and suggest **Data Quality Escalator**

**Examples:**
```
USER: "How did Smith vote on HB 456?"
→ ANALYST: "YES, on [date], per Virginia LIS"

USER: "How much did Education PAC donate to Smith?"
→ ANALYST: "⚠️ Source conflict: FEC reports $5,000 (2026-05-24), VPAP reports $0 (2026-05-24).
   Contact Support or use Data Quality Escalator to resolve this discrepancy."

USER: "Why do donors support certain votes?"
→ ANALYST: "I can't answer that. Try Deep Researcher for pattern analysis."
```

**Output:** Exact facts with citations
**Example:** "Rep. Smith voted YES on HB 456 (2024-03-15, Virginia LIS)"

---

### **Tier 2: Official Records + News Context**

#### **field_monitor** — Intelligence Brief (Official + Polling)
**Sources:**
- All Tier 1 sources (official records)
- Polling data extracted from news feeds (updated 6h)
  - Source: Virginia Mercury, Google News, WVTF, WHRO
  - Extracted: Pollster, sample size, dates, candidates, percentages

**Output:** Intelligence brief with structured citations
**Structure:**
```
IMMEDIATE (48h):
  Official: Recent votes/filings (Virginia LIS, FEC, dated)
  Polling: Latest polling (Roanoke College, dated, n=500)

SOON (2 weeks):
  Official: Upcoming deadlines (Congress.gov, dated)
  Polling: Trend direction (multiple pollsters, dated)

LATER (2+ months):
  Patterns: Emerging trends combining both sources
```

**Example Output:**
```
IMMEDIATE:
- Official: House passed education bill 43-37 (Virginia LIS, 2026-05-24)
- Polling: 58% public approval for education bill (IPOR Roanoke College, 2026-05-23, n=500)

Note: Polling reflects sentiment, not outcomes. Correlation does not imply causation.
```

**Data Source Separation:**
- Polling extracted by: `ingest_va_polls.py --source news --use-gemini` (runs every 6 hours)
- Official records queried directly from APIs
- Always cite both source types separately

---

#### **news_monitor** — News Context Only (NEW)
**Sources:**
- Virginia Mercury
- Local news outlets
- News coverage of civic activity
- Public reporting and statements

**Output:** News summary, not verified facts
**Structure:**
```
Today's Headlines: What stories are breaking
Trending Stories: What's getting coverage
This Week's Coverage: What outlets are reporting
What People Are Saying: Politician statements, public reaction
```

**Example Output:**
```
Today's Headlines:
- Virginia Mercury: Governor announces bipartisan commission on education

What People Are Saying:
- "This is a step forward" (education advocacy group)
- House Republican leader: "We're ready to work together"

NOTE: This is news coverage, not verified fact. Use Analyst to verify the commission composition and official details.
```

**Key Distinction:**
- **NOT** a substitute for analyst
- **NOT** for verifying facts
- **IS** for understanding public narrative and what's being reported
- Routes users to analyst when verification needed

---

### **Tier 3: Research + Synthesis**

#### **deep_researcher** — Multi-Angle Research
**Sources:**
- All Tier 1 sources (official records) — primary
- Historical records (past votes, legislation, changes)
- Credible media (secondary context only)

**Output:** 3-5 sub-questions answered, source tiers, confidence levels
**Approach:**
1. Start with official SQL/API sources
2. Add historical context
3. Cite credible media (context only)

**Does NOT:**
- Use polling as primary data
- Infer causation without research

---

#### **data_analyst** — Pattern Analysis
**Sources:**
- Official records only
- Correlation analysis (never causation)
- Pattern identification

**CORRELATION STRENGTH RULE:**
- **Only report** correlations with strength **>60% (r>0.6)** OR **statistical significance (p<0.05)**
- **Weaker correlations** must cite exact strength (e.g., "r=0.45") and note "not statistically significant"
- Always include: sample size (n=), methodology, confidence intervals, limitations
- Every correlation must end with: "Correlation does not imply causation."

**Output:** Neutral descriptions of correlations with strength thresholds
**Example:**
```
✓ "80% of votes on education bills align with education-sector donations (r=0.81, p<0.01, n=234)"
✓ "Weak alignment observed (r=0.35, not statistically significant) — insufficient evidence"
✗ "Donations caused education votes"
```

---

#### **visual_explainer** — Visualization
**Sources:**
- Data from analyst, field_monitor, or data_analyst
- Must be pre-verified by golden_query (admin)

**Output:** JSON definitions for charts, maps, timelines
**Constraint:** Only visualizes verified data

---

### **Tier 4: Support**

#### **support_drafts** — Customer Support
**Sources:**
- Whatever the user is asking about
- Retrieves via analyst or explains limitations

---

## Admin-Only Agents: Data QA

### **escalator** — Data Issue Detection
Flags conflicts: "FEC says $5K, VPAP says $0"

### **debugger** — Retrieval Failure Diagnosis
Explains why data retrieval failed

### **golden_query** — QA Gate
Verifies data before visualization

### **crosswalk** — Identity Matching
Resolves same person across sources (FEC ID ↔ Virginia LIS ID)

---

## Data Source Update Frequencies

| Source | Update Frequency | Coverage |
|--------|------------------|----------|
| Virginia LIS | Real-time | State bills, votes, sponsors |
| FEC | Weekly | Federal donations, committees |
| Congress.gov | Daily | Federal bills, votes |
| Virginia SBE | Election-day + weekly | Election results, voting |
| VPAP | Daily | Campaign finance (state/local/federal) |
| Polling (Gemini) | Every 6 hours | News-extracted polling data |
| News feeds | Real-time | Virginia Mercury, local outlets |

---

## Critical Boundaries

### ❌ What Agents CANNOT Do

- **analyst** cannot cite news opinion
- **field_monitor** cannot cite news opinion (polling + official only)
- **news_monitor** cannot verify facts
- **deep_researcher** cannot infer causation without research
- **data_analyst** cannot claim causation
- No agent can cite unverified sources

### ✅ What Agents SHOULD Do

- **analyst**: "per Virginia LIS, dated [date]"
- **field_monitor**: "Official: [source, date] | Polling: [pollster, date, n]"
- **news_monitor**: "Virginia Mercury reports..." with disclaimer
- **deep_researcher**: "Research suggests..." with confidence level
- **data_analyst**: "Correlation of 0.75 between X and Y (does not imply causation)"

---

## User Workflows

### "What happened with HB 456?"
→ **analyst** (official records)

### "What's the news saying about HB 456?"
→ **news_monitor** (public narrative)

### "How do voters feel about HB 456?"
→ **field_monitor** (polling + official context)

### "What's behind the voting pattern on education bills?"
→ **data_analyst** (patterns, no causation)

### "Why do education bills pass/fail in Virginia?"
→ **deep_researcher** (research synthesis)

### "Show me education bill voting trends"
→ **visual_explainer** (verified via golden_query)

---

## Architecture Principles

1. **Official records are ground truth** (tier 1)
2. **Polling is news context** (extracted from news feeds, not primary)
3. **News is narrative context** (not verification)
4. **Research is synthesis** (multiple sources, with confidence)
5. **Visualization requires QA** (admin gate before publishing)

---

## Deployment

**15 agents total:**
- 7 public-facing (analyst, news_monitor, field_monitor, deep_researcher, data_analyst, visual_explainer, support_drafts)
- 8 admin-only (escalator, debugger, golden_query, crosswalk, structured_extractor, general_admin, sprint_retro, whro_grants)

**Agent ID:** `VOTEIQ_NEWS_MONITOR_AGENT_ID=agent_01NgxqHbSeo8Ds82py31KzPU`

---

## Testing Data Sources

```bash
# Verify analyst (official records only)
curl -X POST http://localhost:8000/admin/chat \
  -d '{"mode":"analyst", "query":"Who voted for HB 456?"}' 

# Verify field_monitor (official + polling)
curl -X POST http://localhost:8000/admin/chat \
  -d '{"mode":"field_monitor", "query":"What is the political landscape this week?"}'

# Verify news_monitor (news narrative only)
curl -X POST http://localhost:8000/admin/chat \
  -d '{"mode":"news_monitor", "query":"What are news outlets covering today?"}'
```

---

## Key Takeaway

**field_monitor** is not general news — it's **official records + polling** (structured, cited, timestamped).

**news_monitor** is for public narrative and what's being reported (context only).

This separation keeps VoteIQ true to its public-records mission while adding useful context layers.
