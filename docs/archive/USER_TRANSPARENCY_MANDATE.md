# User-Facing Transparency Mandate

**Rule:** Every response shows users exactly what data was used, when it was current, and how fresh it is  
**Status:** ✓ IMPLEMENTED  
**Date:** May 25, 2026  
**Principle:** Users must never wonder "What data am I looking at? How current is it?"

---

## The Problem

**Before:**
```
USER: "How much did Education PAC donate to Smith?"
ANALYST: "Education PAC donated $5,000"
[User has no idea: Was this from FEC? VPAP? When was it updated? Is it current?]
```

**After:**
```
USER: "How much did Education PAC donate to Smith?"
ANALYST: "Education PAC donated $5,000 to Smith

📊 Data Transparency
├─ Source: FEC, filed 2026-05-15
├─ Last Updated: 2026-05-24 (indexed same day)
├─ Freshness: Current
└─ Note: VPAP may reflect this within 2-5 business days"
```

---

## Solution: Transparency Manifest

### What Is It?
Every response includes a "Data Transparency" footer showing:
1. **Sources:** Which official records were used
2. **Dates:** When each source was last updated
3. **Freshness:** Real-time? With known lag? Pending?
4. **Gaps:** What's missing or delayed
5. **Version:** Data snapshot version if applicable

### Format
```
📊 Data Transparency
├─ Sources: FEC (2026-05-24), Virginia LIS (2026-05-24)
├─ Freshness: Current through above dates
├─ Last Updated: FEC 2026-05-24 10:00 UTC; Virginia LIS 2026-05-24
├─ Data Gaps: VPAP data lags by 2-5 days (normal indexing)
└─ Version: Snapshot 2026-05-24T10:00Z
```

---

## Data Freshness Standards

### Real-Time / Current (< 1 day lag)

**FEC** (Federal Election Commission)
- Filing date: Filings dated 2026-05-24
- Update frequency: Same day or next business day
- Manifest: "FEC current through 2026-05-24"

**Virginia LIS** (Legislative Information System)
- Session date: Bills/votes dated 2026-05-24
- Update frequency: Real-time during session
- Manifest: "Virginia LIS current through 2026-05-24"

**Congress.gov**
- Filing date: Bills/votes dated 2026-05-24
- Update frequency: Same day or next business day
- Manifest: "Congress.gov current through 2026-05-24"

**Virginia SBE** (State Board of Elections)
- Election date: Results dated 2026-05-24
- Update frequency: Real-time during/after elections
- Manifest: "Virginia SBE current through 2026-05-24"

### Known Lag (2-5 days)

**VPAP** (Virginia Public Access Project)
- Lag: Typically 2-5 business days behind FEC
- Indexing: Processes FEC filings weekly
- Manifest: "VPAP lags FEC by 2-5 business days (typical)"
- Note: If FEC filed 5/24, VPAP shows by 5/28-5/31

### Pending / Delayed (> 5 days)

**Updated Records** (Amended filings, corrections)
- May take longer to process
- Manifest: "Amended records may not be reflected yet"
- Note: Flag if VPAP data missing beyond normal lag

---

## Transparency Manifest Components

### 1. Sources Used

**Shows which official sources provided the data:**

```
Sources: FEC, Virginia LIS, VPAP
```

**Include all sources queried, with priority:**
- Tier 1: Official records (FEC, Virginia LIS, Congress.gov, Virginia SBE)
- Tier 2: Aggregators (VPAP, state databases)
- Tier 3: News/polling (if mentioned)

### 2. Last Updated Dates

**Shows when each source was refreshed:**

```
Last Updated:
  - FEC: 2026-05-24 10:00 UTC
  - Virginia LIS: 2026-05-24 14:30 UTC
  - VPAP: 2026-05-20 09:00 UTC
```

**Format:** ISO 8601 timestamp (YYYY-MM-DDTHH:MM:SS UTC)

### 3. Freshness Status

**Shows whether data is current or has known lag:**

```
Freshness:
  ✓ FEC: Current (same-day)
  ✓ Virginia LIS: Current (real-time)
  ⏳ VPAP: Lagging (typical 2-5 day lag)
  ⚠️ Amended filings: May have 5+ day lag
```

**Status codes:**
- ✓ Current (< 1 day old)
- ⏳ Lagging (known lag, expected)
- ⚠️ Delayed (beyond expected lag)
- ❌ Unavailable (no recent update)

### 4. Data Gaps

**Shows what's missing or delayed:**

```
Data Gaps:
  - VPAP state-level donors not yet indexed (typical 2-5 day lag)
  - Amended filings may not be reflected
  - Pending bills may not show final status
```

### 5. Version Info

**Shows data snapshot version if applicable:**

```
Version: VoteIQ Data Snapshot 2026-05-24T10:00:00Z
Coverage: All filings through 2026-05-24
```

---

## Response Examples

### Example 1: Single Donation (Complete Transparency)

**User Query:** "How much did Education PAC donate to Smith?"

**Analyst Response:**
```
OFFICIAL: FEC, 2026-05-24
Education PAC donated $5,000 to Smith (filed 2026-05-15)

📊 Data Transparency
├─ Sources: FEC (Federal Election Commission)
├─ Last Updated: 2026-05-24 10:00 UTC (same-day indexing)
├─ Freshness: Current through 2026-05-24
├─ Data Gaps: VPAP may reflect this within 2-5 business days
└─ Note: Correlation does not imply causation
```

### Example 2: Bill Search (Multiple Sources)

**User Query:** "What happened to HB 456?"

**Analyst Response:**
```
OFFICIAL: Virginia LIS, 2026-05-24
HB 456 (Education Funding Reform)
- Introduced: 2026-03-15 by Smith (D-4)
- Committee: Education Committee
- Status: Passed House 43-37 on 2026-05-20
- Current: In Senate Finance Committee

📊 Data Transparency
├─ Sources: Virginia LIS, Congress.gov
├─ Last Updated: Virginia LIS 2026-05-24 14:30 UTC; Congress.gov 2026-05-24 10:00 UTC
├─ Freshness: Current (real-time during session)
├─ Data Gaps: Senate committee vote date not yet scheduled
└─ Version: Legislative session snapshot 2026-05-24

Correlation does not imply causation
```

### Example 3: Donor Analysis (Complex Freshness)

**User Query:** "Total donations to Smith from education sector?"

**Analyst Response:**
```
OFFICIAL: FEC, 2026-05-24 + VPAP, 2026-05-20
Education sector donations to Smith: $47,000
- FEC donors: 12 individuals/PACs
- State-level donors (VPAP): [pending indexing]

📊 Data Transparency
├─ Sources: FEC (federal), VPAP (state aggregator)
├─ Last Updated: FEC 2026-05-24 10:00 UTC; VPAP 2026-05-20 09:00 UTC
├─ Freshness: FEC current; VPAP lagging (typical 2-5 day lag)
├─ Data Gaps: 
│   - VPAP state-level donors not yet indexed for recent FEC filings
│   - Expected VPAP update: 2026-05-22 through 2026-05-28
├─ Completeness: FEC data is complete; VPAP awaiting indexing
└─ Version: Data snapshot 2026-05-24T10:00Z; VPAP snapshot 2026-05-20

Correlation does not imply causation
```

### Example 4: Source Conflict (Full Transparency)

**User Query:** "How much did Education PAC donate to Smith?"

**Analyst Response (Conflict Detected):**
```
⚠️ SOURCE CONFLICT DETECTED

FEC (2026-05-15, indexed 2026-05-24): Education PAC donated $5,000
VPAP (2026-05-20): Education PAC donated $0

📊 Data Transparency
├─ Sources: FEC, VPAP
├─ Last Updated: FEC 2026-05-24 10:00 UTC; VPAP 2026-05-20 09:00 UTC
├─ Freshness: FEC current; VPAP may have indexing lag
├─ Data Conflict: Sources report different amounts
│   - Possible causes:
│     1. VPAP indexing lag (expected to clear within 5 days)
│     2. FEC filing error or VPAP entry error
│     3. Record ID mismatch
├─ Next Steps: Contact Support or use Data Quality Escalator
└─ Note: Escalator will investigate and recommend authoritative source

Contact Support or use Data Quality Escalator for resolution.
Correlation does not imply causation
```

---

## Transparency Manifest in System

### Analyst Agent (chat.py)

**Required in every response:**
1. Cite source with date: `OFFICIAL: [source], [date]`
2. Append transparency manifest with:
   - Sources used
   - Last updated dates
   - Freshness status
   - Data gaps
   - Version info

**Example output structure:**
```
[Facts from official records]

📊 Data Transparency
├─ Sources: [list]
├─ Last Updated: [dates with times]
├─ Freshness: [status]
├─ Data Gaps: [what's missing]
└─ Version: [snapshot info if applicable]

[Disclaimer: Correlation does not imply causation]
```

---

## Data Currency Matrix

### Source Update Frequencies

| Source | Update Freq | Lag | Current Through | Example |
|--------|-------------|-----|-----------------|---------|
| **FEC** | Same day | <1 day | Filing date | FEC current through 2026-05-24 |
| **Virginia LIS** | Real-time | <1 day | Session date | Virginia LIS current through 2026-05-24 |
| **Congress.gov** | Next day | <1 day | Vote/filing date | Congress.gov current through 2026-05-24 |
| **Virginia SBE** | Real-time | <1 day | Election date | Virginia SBE current through 2026-05-24 |
| **VPAP** | Weekly | 2-5 days | Indexing lag | VPAP lags by 2-5 business days |

---

## User Benefits

### Transparency
- Users see exactly what data sources are included
- No hidden limitations or unknowns

### Trust
- Users know how current the data is
- Understand when to expect updates

### Informed Decisions
- Know if data is real-time or lagging
- Understand data gaps before making decisions

### Accountability
- Analyst is transparent about source origins
- Users can verify through original sources

---

## Implementation Checklist

- [x] Transparency Manifest agent created
- [x] Analyst prompt updated to require manifest
- [x] System prompt documents freshness standards
- [x] Data freshness standards documented
- [x] Response examples created
- [ ] Analyst responses include manifests
- [ ] User testing and feedback
- [ ] Refinement based on feedback

---

## Testing Strategy

### Manifest Accuracy
- [ ] All sources listed in manifest
- [ ] Dates are correct for each source
- [ ] Freshness status accurate
- [ ] Data gaps correctly identified

### User Understanding
- [ ] Users understand data currency
- [ ] Know when to expect updates
- [ ] Can identify data gaps
- [ ] Trust in data transparency

### Integration
- [ ] Manifest appends correctly to responses
- [ ] No format issues
- [ ] Displays properly for all query types
- [ ] Works with conflict flags

---

## References

- **Agent:** chat.py, transparency_manifest (lines 703-728)
- **Analyst update:** chat.py, analyst prompt
- **System prompt:** chat.py, DATA FRESHNESS section
- **Data standards:** This document

---

## Success Criteria

- [x] Transparency Manifest agent created
- [x] Analyst includes manifest in responses
- [x] Users see data currency information
- [x] Data freshness standards established
- [ ] User adoption and satisfaction
- [ ] Reduces questions about data currency
- [ ] Increases user trust

---

**Effective:** May 25, 2026  
**Status:** ✓ Implemented  
**Next:** Testing with user responses

