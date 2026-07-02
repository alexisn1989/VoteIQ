# Search Assistant — Quick Reference

## Four Discovery Modes

| Mode | User Says | Search Assistant Does |
|------|-----------|----------------------|
| **BILLS** | "Find education bills" | Clarifies keywords, year, status, committee → Structures query for Analyst |
| **DONORS** | "Who funds education?" | Clarifies sector, amount, recipient → Structures query for Analyst |
| **LEGISLATORS** | "Find committee members" | Clarifies committee, district, party → Structures query for Analyst |
| **VOTES** | "How did reps vote?" | Clarifies bill/topic, party breakdown → Structures query for Analyst |

---

## Typical Discovery Flow

```
User: "Find education bills from 2026"
             ↓
Search Assistant: "I can help!
                   - What status? (pending/passed/vetoed)
                   - Specific committee?
                   - Specific sponsor?
                   [SEARCH]"
             ↓
Analyst: Returns matching bills
             ↓
User: "Show me votes on HB 456"
             ↓
Search Assistant: Refines query
             ↓
Analyst: Returns votes on HB 456
```

---

## What Search Assistant Does

✓ Clarifies vague requests  
✓ Suggests relevant filters  
✓ Builds structured queries  
✓ Routes to Analyst  
✓ Offers refinement options  
✗ Does NOT return raw data  
✗ Does NOT query directly  

---

## Example Queries

### Bills Discovery
```
"Show me education bills"
  → Keywords: [education, K-12, university]
     Year: [2026]
     Status: [options shown]
     [SEARCH]

"HB 456 — what's the vote history?"
  → Bill: HB 456
     Votes: [all sessions]
     [SEARCH]
```

### Donors Discovery
```
"Who funds education?"
  → Sector: Education
     Amount: [$1K+]
     [SEARCH]

"Top donors to Smith from tech sector"
  → Sector: Technology
     Recipient: Smith
     Sort: By amount
     [SEARCH]
```

### Legislators Discovery
```
"Education committee members"
  → Committee: Education
     Chamber: [House/Senate/Both]
     [SEARCH]

"Reps from Northern Virginia"
  → District: [1-11]
     [SEARCH]
```

### Votes Discovery
```
"How did reps vote on education?"
  → Keywords: education
     Year: 2026
     Party breakdown: [yes]
     [SEARCH]

"Smith's votes on K-12 bills"
  → Keywords: K-12
     Representative: Smith
     [SEARCH]
```

---

## Filter Examples

### Bill Filters
- Keywords: education, K-12, university
- Year: 2020-2026
- Status: pending, passed, vetoed
- Committee: Education, Finance, etc.
- Sponsor: [rep name]

### Donor Filters
- Sector: Education, Tech, Real Estate, etc.
- Amount: $1K+, $5K+, $10K+
- Recipient: [name]
- Region: [state/district]

### Legislator Filters
- Committee: [list]
- District: [1-11]
- Party: R/D/I
- Chamber: House/Senate

### Vote Filters
- Keywords: [topics]
- Bill ID: [e.g., HB 123]
- Year: [range]
- Vote breakdown: by party/rep

---

## Routing Rule

```
Vague/exploratory request
        ↓
Search Assistant:
  Clarifies → Suggests filters → Routes to Analyst
        ↓
Analyst: Returns structured facts
```

---

## Key Phrases

Search Assistant activates for:
- "Find", "Show me", "Who", "What"
- "Bills about", "Donors to", "Committee members"
- "How did [person] vote"
- "Search for", "Look for", "Explore"

Analyst handles afterwards with specific queries built by Search Assistant.

---

## User Value

| Before | After |
|--------|-------|
| Must know exact syntax | Guided discovery |
| Can't explore data | Can iterate & refine |
| High barrier to entry | Low barrier to entry |
| Single query mindset | Discovery mindset |

---

**Status:** ✓ Ready for use  
**Use when:** User makes vague/exploratory request  
**Routes to:** Public Record Analyst with structured query

