# Search Assistant — Discovery & Exploration Mandate

**Agent:** Search Assistant  
**Status:** ✓ IMPLEMENTED  
**Date:** May 25, 2026  
**Purpose:** Enable users to discover and explore VoteIQ data

---

## The Problem

**Before:**
- Users need to know exactly what to search for
- "Find bills about education" requires knowing Virginia LIS query syntax
- "Who funds education?" requires understanding FEC donor sectors
- No exploratory/discovery capability

**After:**
- Search Assistant guides users through discovery
- Vague requests become structured queries
- Users learn what data exists
- Exploration becomes possible

---

## Solution: Search Assistant Agent

### Role
Query builder and discovery guide — help users transform vague requests into structured searches

### Input
Vague/exploratory requests:
- "Show me education bills"
- "Who funds education?"
- "Find representatives from [district]"
- "How did reps vote on education?"

### Output
Structured queries + discovery paths:
```
"To find education bills, use Analyst with:
 - Source: Virginia LIS
 - Keywords: [education, K-12, university]
 - Year: 2026
 - Status: [pending/passed/vetoed]
 
 Click below to run this query with Analyst."
```

---

## Discovery Modes

### Mode 1: Bill Discovery

**User Request:** "Find education bills from 2026"

**Search Assistant Response:**
```
📋 BILL SEARCH

Looking for: Education bills from 2026
Source: Virginia LIS
Filters:
  - Keywords: education, K-12, higher education, university
  - Year: 2026
  - Status options: pending, passed, vetoed
  - Committee: [suggested: Education Committee]
  
Ready to search? Click "Search Bills" below.
Alternative filters: sponsoring representative, bill number, etc.
```

**Analyst Query Built:**
```
Virginia LIS | 2026 | Keywords: education | Committee: Education
→ Returns: Bill list with sponsors, status, committees
```

---

### Mode 2: Donor Discovery

**User Request:** "Who funds education? Show me big donors."

**Search Assistant Response:**
```
💰 DONOR SEARCH

Looking for: Education sector donors
Source: FEC
Filters:
  - Sector: Education
  - Amount range options: 
    • All donors
    • $1K+
    • $5K+
    • $10K+
  - Recipient type options:
    • All recipients
    • Candidates only
    • PACs only
  
Ready to search? Click "Find Donors" below.
Tip: You can also filter by recipient name (e.g., "donations to Smith").
```

**Analyst Query Built:**
```
FEC | Sector: Education | Amount: $5K+ | Recipient: [optional]
→ Returns: Top education donors with amounts, recipients
```

---

### Mode 3: Legislator Discovery

**User Request:** "Find education committee members from my district"

**Search Assistant Response:**
```
👥 LEGISLATOR SEARCH

Looking for: Education Committee members
Source: Virginia LIS
Filters:
  - Committee: Education Committee
  - District options: [dropdown by district number]
  - Chamber: [House/Senate/Both]
  - Party: [Republican/Democrat/Both]
  
Additional discovery:
  - Committee chair/ranking member
  - Voting record on education bills
  - Campaign finance by education sector
  
Ready to search? Click "Find Legislators" below.
```

**Analyst Query Built:**
```
Virginia LIS | Education Committee | District: [X] | Chamber: House
→ Returns: Committee members with voting records, donations, etc.
```

---

### Mode 4: Vote Discovery

**User Request:** "How did representatives vote on education?"

**Search Assistant Response:**
```
🗳️ VOTE SEARCH

Looking for: Education bill votes
Source: Virginia LIS
Filters:
  - Bill criteria:
    • Keywords: education, K-12, university
    • Year: [select]
    • Status: [passed/pending/vetoed]
  - Vote filter options:
    • All votes
    • Party breakdown
    • By representative
    
Ready to search? Click "Find Votes" below.
Tip: You can also search for votes by specific bill ID (e.g., HB 123).
```

**Analyst Query Built:**
```
Virginia LIS | Education bills | 2026 | Voting records
→ Returns: Vote totals, by-party breakdown, individual votes
```

---

## Structured Output Format

### Standard Discovery Output

```
[DISCOVERY QUESTION]
"Show me education bills from 2026"

[CLARIFICATION]
What type of search? Bills
What criteria? Topic (education), Year (2026)

[FILTERS AVAILABLE]
- Keywords: education, K-12, university, higher ed
- Year: 2026
- Status: pending / passed / vetoed
- Committee: [optional]
- Sponsor: [optional]

[STRUCTURED QUERY]
Source: Virginia LIS
Query: (education OR K-12 OR university) AND year=2026
Optional filters: [suggested committees/sponsors]

[ACTION]
Ready to search with Analyst? [SEARCH BUTTON]
Want to refine? [MODIFY FILTERS]
Learn more about bills? [HELP]
```

---

## How It Works

### Flow 1: User → Search Assistant → Analyst

```
USER: "Who funds education?"
    ↓
SEARCH ASSISTANT:
  "Looking for donor information about education sector.
   
   I can help you find:
   1. Top education sector donors (by total given)
   2. Donors to specific candidates
   3. Donors in specific regions
   
   Which would you like?"
    ↓
USER: "Show me top donors to education"
    ↓
SEARCH ASSISTANT:
  "Source: FEC
   Sector: Education
   Sort: By total amount (descending)
   
   [SEARCH WITH ANALYST]"
    ↓
ANALYST: Returns structured data
    ↓
USER: Gets filtered results (donors, amounts, recipients)
```

### Flow 2: Refinement

```
SEARCH ASSISTANT shows initial results
    ↓
USER: "Filter to candidates in Northern Virginia"
    ↓
SEARCH ASSISTANT: Refines query filters
    ↓
ANALYST: Returns refined results
```

---

## Discovery Capabilities

### Bills
- ✓ By topic/keywords
- ✓ By sponsor
- ✓ By committee
- ✓ By year
- ✓ By status (pending, passed, vetoed, etc.)
- ✓ By legislative chamber

### Donors
- ✓ By sector
- ✓ By amount range
- ✓ By recipient
- ✓ By region
- ✓ By donor type (individual, PAC, corporate)
- ✓ By time period

### Legislators
- ✓ By committee
- ✓ By district
- ✓ By party
- ✓ By chamber
- ✓ By voting record patterns
- ✓ By funding sources

### Votes
- ✓ By bill
- ✓ By topic
- ✓ By party breakdown
- ✓ By individual legislator
- ✓ By vote outcome
- ✓ By time period

---

## Agent Integration

### Search Assistant Role
```
INPUT: "Find education bills"
  ↓
PROCESS: Clarify intent + suggest filters
  ↓
OUTPUT: Structured query for Analyst
  ↓
ROUTE: To Public Record Analyst
  ↓
ANALYST: Returns structured facts
  ↓
USER: Gets discovery results
```

### Routing Rules
- **Discovery request** → Search Assistant
  - Examples: "Find", "Show me", "Who", "What bills"
  - Action: Build structured query
  
- **Specific query** → Analyst (via Search Assistant or direct)
  - Examples: "How did Smith vote?", "Total donations to X"
  - Action: Return exact facts
  
- **Broad analysis** → Deep Researcher
  - Examples: "Why do donors support votes?", "Patterns in voting"
  - Action: Research synthesis
  
- **Visualization** → Visual Explainer (with Golden Query verification)
  - Examples: "Chart donations over time"
  - Action: Create verified visualization

---

## User Experience

### Case 1: Complete Beginner
```
USER: "I want to learn about education legislation"
    ↓
SEARCH ASSISTANT:
  "Great! I can help you explore education data.
   
   Would you like to:
   1. See what education bills exist (2026)
   2. Find who funds education
   3. See education committee members
   4. View voting patterns on education"
    ↓
USER: Selects option 1
    ↓
SEARCH ASSISTANT: Builds bill discovery query
    ↓
ANALYST: Returns education bills
    ↓
USER: "Now show me who authored the top bills"
    ↓
[Continues iterative discovery]
```

### Case 2: Specific Question
```
USER: "Show me bills from Smith in 2026"
    ↓
SEARCH ASSISTANT:
  "Looking for: Bills sponsored by Smith, 2026
   
   Source: Virginia LIS
   Sponsor: Smith
   Year: 2026
   
   [SEARCH]"
    ↓
ANALYST: Returns Smith's bills
    ↓
USER: Gets results
```

### Case 3: Exploration & Refinement
```
USER: "Who funds education in Northern Virginia?"
    ↓
SEARCH ASSISTANT: Clarifies region + builds query
    ↓
ANALYST: Returns donors
    ↓
USER: "Filter to top 10 by amount"
    ↓
SEARCH ASSISTANT: Refines filters
    ↓
ANALYST: Returns top 10
    ↓
USER: "Show me their voting records"
    ↓
[Continues discovery chain]
```

---

## Implementation Details

### Agent Prompt (chat.py, lines 667-695)

**Key Instructions:**
1. Do NOT query directly; guide users instead
2. Clarify intent: bill? donor? legislator? vote?
3. Suggest filters and refinements
4. Build structured queries for Analyst
5. Route to Analyst with clear parameters
6. Don't return raw data — guide to Analyst

**Discovery Modes:**
- Bills (topic, sponsor, committee, year, status)
- Donors (sector, amount, recipient, region)
- Legislators (committee, district, party, chamber)
- Votes (bill, topic, party, outcome)

### System Prompt (chat.py, lines 87-101)

**Updated with:**
- Search Assistant role definition
- INPUT/OUTPUT specification
- Routing rule for discovery requests
- Integration with Analyst

---

## Filter & Refinement Options

### Bill Search Filters
```
Keywords: [text search]
Year: [dropdown: 2020-2026]
Status: [checkboxes: pending, passed, vetoed, etc.]
Committee: [dropdown list]
Sponsor: [search by name]
Chamber: [House/Senate/Both]
```

### Donor Search Filters
```
Sector: [dropdown: Education, Technology, Real Estate, etc.]
Amount range: [All / $1K+ / $5K+ / $10K+]
Recipient type: [All / Candidates / PACs]
Recipient name: [search]
Region: [state/district optional]
Year: [dropdown]
```

### Legislator Search Filters
```
Committee: [dropdown list]
District: [dropdown]
Chamber: [House/Senate/Both]
Party: [Republican/Democrat/Independent/All]
Voting record: [keywords optional]
```

### Vote Search Filters
```
Bill criteria:
  - Keywords: [text]
  - Year: [dropdown]
  - Bill ID: [e.g., HB 123]
  - Status: [pending/passed/vetoed]
Vote breakdown:
  - By party
  - By representative
  - By vote outcome
```

---

## Testing Checklist

- [ ] Search Assistant clarifies vague requests
- [ ] Suggests appropriate filters
- [ ] Builds correct structured queries
- [ ] Routes to Analyst with clear parameters
- [ ] Offers refinement options
- [ ] All discovery modes working
- [ ] User can iterate and refine
- [ ] Error handling for invalid inputs

---

## Performance Considerations

- **Latency:** Discovery guidance should be instant (no data queries)
- **Complexity:** Filters should be simple (checkboxes/dropdowns)
- **Scalability:** Scales with Analyst queries (not directly constrained)

---

## References

- **Agent registry:** chat.py, lines 667-695 (search_assistant)
- **System prompt:** chat.py, lines 87-101 (routing)
- **Analyst agent:** chat.py (returns structured facts)
- **Deep Researcher:** chat.py (broader research)

---

## Success Criteria

- [x] Search Assistant agent implemented
- [x] System prompt documents discovery flows
- [x] 4 discovery modes defined (Bills, Donors, Legislators, Votes)
- [x] Routing to Analyst documented
- [x] Filter/refinement options specified
- [ ] Testing with sample queries
- [ ] User acceptance testing

---

**Effective:** May 25, 2026  
**Status:** ✓ Implemented and ready for testing  
**Next:** Testing and refinement

