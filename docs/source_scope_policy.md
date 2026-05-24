# VoteIQ Source Scope Policy

## Overview

VoteIQ's Scope Policy governs source selection, agent routing, and citation behavior based on query scope. It ensures that:

1. **Default: Virginia/Local Focus** — VoteIQ defaults to Virginia state and Hampton Roads local sources
2. **Conditional: Federal Access** — Federal sources (FEC, Congress.gov) are enabled only when queries clearly relate to federal topics
3. **Smart Detection** — Query scope is automatically detected based on keywords and patterns
4. **Transparent Citations** — Source footers show only sources actually used, with current-through dates
5. **Flexible Routing** — Different agents respect scope when making decisions

**Goal:** VoteIQ remains focused on Virginia civic data while providing federal context when needed.

---

## Query Scopes

VoteIQ recognizes five query scopes:

### 1. Local Hampton Roads
Questions about local government in the Hampton Roads area.

**Keywords:** city council, city manager, mayor, municipal, Virginia Beach, Norfolk, Newport News, Chesapeake, Legistar, local government

**Example queries:**
- "How did the Virginia Beach city council vote on the budget?"
- "What's on the Norfolk city council agenda?"
- "Newport News city manager decisions"

**Enabled sources:** Legistar, Municipal Records, VPAP, Virginia SBE
**Disabled sources:** FEC, Congress.gov

---

### 2. Virginia State
Questions about Virginia state government, General Assembly, statewide offices, state elections.

**Keywords:** Virginia General Assembly, House of Delegates, State Senate, Governor, Virginia bill, SB, HB, Virginia law, state legislator, state election

**Example queries:**
- "How did Rouse vote on SB 658?"
- "What bills did the Virginia House of Delegates pass?"
- "Virginia state election results 2026"

**Enabled sources:** Virginia LIS, OpenStates, Virginia SBE, VPAP, Legistar
**Disabled sources:** FEC, Congress.gov

---

### 3. Federal
Questions about U.S. Congress, federal candidates, federal elections, federal legislation.

**Keywords:** U.S. Congress, U.S. House, U.S. Senate, congressman, congresswoman, senator, representative, congressional, federal candidate, federal committee, federal election, Congress.gov, federal bill, federal donation, federal campaign

**Example queries:**
- "How did Elaine Luria vote on the healthcare bill?"
- "Who are Virginia's U.S. senators?"
- "Congressional delegation donations from Virginia"
- "What bills did Congress vote on last week?"

**Enabled sources:** FEC, Congress.gov, OpenStates, VPAP
**Disabled sources:** None (all sources available)

---

### 4. Mixed
Questions comparing or combining state/local and federal data.

**Keywords:** compare, both, state and federal, local and federal, vs, versus

**Example queries:**
- "Compare state and federal donations to this person"
- "Virginia state vs federal voting records"
- "Both state and federal campaign finance for this candidate"

**Enabled sources:** All sources (Virginia LIS, OpenStates, Virginia SBE, VPAP, Legistar, Municipal Records, FEC, Congress.gov)
**Disabled sources:** None

---

### 5. Unknown
Queries where scope cannot be clearly determined.

**Default behavior:** Treats as Virginia state / local query (conservative approach)

**Enabled sources:** Virginia LIS, OpenStates, Virginia SBE, VPAP, Legistar, Municipal Records
**Disabled sources:** FEC, Congress.gov

---

## How Scope Detection Works

VoteIQ detects query scope using pattern matching and keyword detection:

### Detection Rules (in priority order)

1. **Mixed scope comparison** — "compare", "both", "vs", "versus"
   - Confidence: HIGH
   - Example: "Compare state and federal donations"

2. **Virginia bill ID** — Matches format `SB 123`, `HB 456`, `CR 789`
   - Confidence: HIGH
   - Example: "How did Virginia vote on SB 658?"

3. **U.S. Congress** — "U.S. House", "U.S. Senate", "congressman", "congresswoman", "representative ... congress", "senator ... U.S."
   - Confidence: HIGH
   - Example: "How did the U.S. Senate vote?"

4. **Virginia congressional delegation** — Named members like "Elaine Luria", "Morgan Griffith", "Gus Grisham", "Tim Kaine", "Tim Scott"
   - Confidence: HIGH
   - Example: "Elaine Luria's donations"

5. **Local government** — "city council", "city manager", "mayor", "municipal", city names, "Legistar"
   - Confidence: HIGH
   - Example: "Virginia Beach city council vote"

6. **Virginia state government** — "Virginia House", "Virginia Senate", "General Assembly", "Virginia bill", "Virginia law", "Virginia legislature", "Virginia election"
   - Confidence: HIGH
   - Example: "Virginia House bills"

7. **FEC mention** — "FEC", "Federal Election Commission"
   - Confidence: HIGH
   - Example: "What does the FEC say?"

### Example Detections

| Query | Detected Scope | Confidence |
|-------|---|---|
| "How did Rouse vote on SB 658?" | virginia_state | HIGH (bill ID) |
| "How did Virginia House vote on HB 456?" | virginia_state | HIGH (Virginia + bill ID) |
| "Virginia Beach city council vote" | local_hampton_roads | HIGH (city council keyword) |
| "How did Elaine Luria vote?" | federal | HIGH (VA delegation member) |
| "Who are Virginia's U.S. senators?" | federal | HIGH (U.S. senators keyword) |
| "Compare state and federal donations" | mixed | HIGH (compare keyword) |

---

## Source Availability by Scope

### Virginia State Queries

| Source | Available | Used When | Current Through |
|--------|---|---|---|
| Virginia LIS | Yes | State bills, votes, legislators | 2026-05-24 |
| OpenStates | Yes | Open records, standardized data | 2026-05-24 |
| Virginia SBE | Yes | State elections, voting records | 2026-05-24 |
| VPAP | Yes | State campaign finance | 2026-05-23 |
| Legistar | Yes | Local government records | varies |
| FEC | **No** | — | — |
| Congress.gov | **No** | — | — |

---

### Local Government Queries

| Source | Available | Used When | Current Through |
|--------|---|---|---|
| Legistar | Yes | City council votes, meetings | varies |
| Municipal Records | Yes | Ordinances, resolutions | varies |
| VPAP | Yes | Local campaign finance | 2026-05-23 |
| Virginia SBE | Yes | Local elections | 2026-05-24 |
| Virginia LIS | No | — | — |
| FEC | **No** | — | — |
| Congress.gov | **No** | — | — |

---

### Federal Queries

| Source | Available | Used When | Current Through |
|--------|---|---|---|
| FEC | Yes | Federal campaign finance, candidates | 2026-05-20 |
| Congress.gov | Yes | Federal bills, votes, members | 2026-05-24 |
| OpenStates | Yes | Congressional records | 2026-05-24 |
| VPAP | Yes | Virginia federal candidates' state-level giving | 2026-05-23 |
| Legistar | No | — | — |
| Municipal Records | No | — | — |
| Virginia LIS | No | — | — |
| Virginia SBE | No | — | — |

---

### Mixed Queries

All sources are available. Footers will show sources actually used.

| Source | Available | Current Through |
|--------|---|---|
| Virginia LIS | Yes | 2026-05-24 |
| OpenStates | Yes | 2026-05-24 |
| Virginia SBE | Yes | 2026-05-24 |
| VPAP | Yes | 2026-05-23 |
| Legistar | Yes | varies |
| Municipal Records | Yes | varies |
| FEC | Yes | 2026-05-20 |
| Congress.gov | Yes | 2026-05-24 |

---

## Source Footers

VoteIQ generates dynamic source footers that show only sources actually used in the response.

### Footer Format

```
Sources used: [Source 1] (current through [Date]) · [Source 2] (current through [Date]) | [Scope Explanation]
```

### Examples

**Virginia state bill answer:**
```
Sources used: Virginia LIS (current through 2026-05-24) · OpenStates (current through 2026-05-24) | Virginia state sources
```

**Federal donation answer:**
```
Sources used: FEC (current through 2026-05-20) | Federal sources per congressional delegation query
```

**Local council answer:**
```
Sources used: Legistar · Municipal Records (current through May 2026) | Local government sources
```

**Mixed query answer:**
```
Sources used: VPAP (current through 2026-05-23) · FEC (current through 2026-05-20) | Combined state/federal sources per query scope
```

### Key Features

1. **Only actual sources shown** — Generic source lists are replaced with sources actually used
2. **Current-through dates** — Shows freshness for each source
3. **Scope explanation** — Brief note on why those sources were chosen
4. **No bloat** — Keeps footers concise and relevant

---

## Integration Points

### 1. Chat & Analyst Endpoints

Before generating response:
1. Detect query scope using `detect_query_scope(query)`
2. Get enabled sources using `get_enabled_sources_for_query(query)`
3. Filter results to use only enabled sources
4. At end, generate footer using `build_footer_for_response(sources_used, query)`

### 2. Source Routing

In `public_record_analyst` and other agents:
1. Determine query scope
2. Check if agent can handle that scope
3. If federal query and agent prefers state/local, offer to route to federal specialist
4. Include scope explanation in response

### 3. Structured Extractor

When extracting from documents:
1. Detect scope of extraction task
2. Include source scope in extraction metadata
3. Filter extracted sources to match scope

### 4. Field Monitor

Virginia state/local civic field monitor:
1. Focus on virginia_state and local_hampton_roads scopes
2. Do not monitor federal field
3. Alert when federal sources are needed for completeness

---

## API Usage

### Python

```python
from voteiq.config.scope_policy import (
    detect_query_scope,
    get_enabled_sources_for_query,
    build_footer_for_response,
)

# Detect scope
scope, explanation, confidence = detect_query_scope("How did the Virginia House vote on HB 456?")
# Returns: (QueryScope.VIRGINIA_STATE, "Query mentions Virginia state government", 0.9)

# Get enabled sources
sources, scope, explanation = get_enabled_sources_for_query("How did Elaine Luria vote?")
# Returns: (["FEC", "Congress.gov", "OpenStates", "VPAP"], QueryScope.FEDERAL, "...")

# Build footer
footer = build_footer_for_response(
    ["Virginia LIS", "OpenStates"],
    "How did Virginia House vote on HB 456?"
)
# Returns: "Sources used: Virginia LIS (current through 2026-05-24) · OpenStates (current through 2026-05-24) | Virginia state sources"
```

---

## Configuration

Scope policy is configured in `voteiq/config/scope_policy.json`:

- **scopes** — Definition of each scope (keywords, enabled sources, disabled sources)
- **source_definitions** — Details of each source (description, freshness, URL, data types)
- **scope_detection_rules** — Regex patterns for scope detection
- **routing_rules** — Which agents can handle which scopes
- **source_footer_rules** — Footer generation rules
- **defaults** — Default behavior (default scope, fallback scope, etc.)

---

## Testing

Run the test suite:

```bash
python test_scope_policy.py
```

Tests verify:
- Query scope detection (12 test cases)
- Source filtering (no FEC for state/local, FEC for federal)
- Source footers (only used sources shown)
- Scope summaries (complete scope metadata)
- Routing guidance (agent-scope compatibility)
- Source freshness dates

---

## Scope-Aware Prompting

When calling agents, include scope context:

**For state questions:**
```
User is asking about Virginia state government.
Default to Virginia LIS, OpenStates, Virginia SBE, and VPAP.
Do not use FEC or Congress.gov.
```

**For local questions:**
```
User is asking about local government in Hampton Roads.
Default to Legistar, Municipal Records, and VPAP.
Do not use FEC or Congress.gov.
```

**For federal questions:**
```
User is asking about federal government or U.S. Congress.
You may use FEC, Congress.gov, OpenStates, and VPAP.
```

**For mixed questions:**
```
User is comparing state/local and federal data.
All sources are available. Show which sources are used for each part.
```

---

## Monitoring & Auditing

VoteIQ logs scope decisions for monitoring:

- Query scope detected
- Sources enabled vs. disabled for that scope
- Sources actually used in response
- Footer generated

This helps identify:
- Scope detection errors
- Unwanted source usage
- Source freshness gaps
- User expectations vs. VoteIQ behavior

---

## Future Extensions

Possible enhancements:
1. **User preferences** — "I prefer federal sources" (override default)
2. **Temporal scope** — "2024 and before" (filter by date range)
3. **Confidence-based filtering** — "Only high-confidence sources" (filter by data quality)
4. **Custom scopes** — Define user/org-specific scopes
5. **Scope negotiation** — Agent suggests different scope if query is ambiguous

---

## Questions?

See:
- `voteiq/config/scope_policy.json` — Configuration file
- `voteiq/config/scope_policy.py` — Policy logic and API
- `test_scope_policy.py` — Test cases and examples
