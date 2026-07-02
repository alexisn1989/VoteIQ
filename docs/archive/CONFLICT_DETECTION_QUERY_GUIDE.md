# Conflict Detection — Query Logic Implementation Guide

**Purpose:** Guide for implementing the query logic that detects conflicts between FEC and VPAP data  
**Status:** Design document (Phase 2 implementation)  
**Date:** May 25, 2026

---

## Overview

The analyst agent needs to detect conflicts when querying campaign finance data. This guide specifies the query logic that should detect when FEC and VPAP report different values for the same donation.

---

## Query Pattern

### Current Pattern (Single Source)
```python
# Analyst queries FEC only
fec_donation = query_fec(
    donor="Education PAC",
    recipient="Candidate Smith",
    cycle=2026
)
return f"OFFICIAL: FEC, {date} — {fec_donation['amount']}"
```

### New Pattern (Multi-Source with Conflict Detection)
```python
# Analyst queries both sources
fec_donation = query_fec(donor, recipient, cycle)
vpap_donation = query_vpap(donor, recipient, cycle)

# Compare results
if fec_donation and vpap_donation:
    if fec_donation['amount'] != vpap_donation['amount']:
        flag_conflict(fec_donation, vpap_donation)
    else:
        return_single_fact(fec_donation)
elif fec_donation and not vpap_donation:
    check_vpap_lag(fec_donation['date'])
    return_single_fact(fec_donation)
else:
    # Handle other cases
```

---

## Detection Scenarios

### 1. Direct Amount Conflict

**Query:**
```python
fec = query_fec(donor="Education PAC", recipient="Smith", cycle=2026)
vpap = query_vpap(donor="Education PAC", recipient="Smith", cycle=2026)
```

**Data:**
```
fec: {donor: "Education PAC", recipient: "Smith", amount: 5000, date: "2026-05-15", source: "FEC"}
vpap: {donor: "Education PAC", recipient: "Smith", amount: 0, date: "2026-05-24", source: "VPAP"}
```

**Detection:**
```python
if fec['amount'] != vpap['amount']:
    # Both sources present, both authoritative, amounts differ
    flag_conflict(fec, vpap)
```

**Output:**
```
⚠️ SOURCE CONFLICT DETECTED
FEC (2026-05-15): Education PAC donated $5,000
VPAP (2026-05-24): Education PAC donated $0
```

---

### 2. Existence Conflict (One Source Has Record, Other Doesn't)

**Query:**
```python
fec = query_fec(donor="Green Future Fund", recipient="Smith", cycle=2026)
vpap = query_vpap(donor="Green Future Fund", recipient="Smith", cycle=2026)
```

**Data:**
```
fec: {donor: "Green Future Fund", recipient: "Smith", amount: 3000, date: "2026-05-20", source: "FEC"}
vpap: None  # Record not found
```

**Detection Logic:**
```python
if fec and not vpap:
    # Check if VPAP is lagging or if this is a real conflict
    days_since_filing = (today - fec['date']).days
    if days_since_filing > 5:  # Beyond normal lag (2-5 days)
        # After normal indexing time, VPAP should have it
        # This could be a real conflict
        flag_conflict(fec, None)
    else:
        # Within normal lag window
        note_indexing_lag(fec, vpap)
```

**Output (After Normal Lag):**
```
⚠️ SOURCE CONFLICT DETECTED
FEC (2026-05-20): Green Future Fund donated $3,000
VPAP (2026-05-24): Record not found

Possible causes:
- VPAP indexing delayed beyond normal 2-5 day window
- FEC filing error or ID mismatch
- VPAP record suppressed or merged

Contact Support or use Data Quality Escalator.
```

**Output (Within Normal Lag):**
```
OFFICIAL: FEC, 2026-05-24
Green Future Fund donated $3,000 (filed 2026-05-20)

Note: VPAP typically reflects FEC filings within 2-5 business days.
```

---

### 3. Indexing Lag (Not a Conflict)

**Query:**
```python
fec = query_fec(donor="Tech Voices", recipient="Smith", cycle=2026)
vpap = query_vpap(donor="Tech Voices", recipient="Smith", cycle=2026)
```

**Data:**
```
fec: {donor: "Tech Voices", recipient: "Smith", amount: 2500, date: "2026-05-23", source: "FEC", status: "indexed"}
vpap: None  # Not yet indexed (within normal 2-5 day lag)
```

**Detection:**
```python
days_since_filing = (today - fec['date']).days
if days_since_filing < 6:  # Normal indexing window
    # Don't flag as conflict
    return fec_data_with_note("VPAP may reflect this within 2-5 business days")
else:
    # Flag as potential conflict (beyond normal lag)
    flag_conflict(fec, vpap)
```

**Output:**
```
OFFICIAL: FEC, 2026-05-24
Tech Voices donated $2,500 (filed 2026-05-23)

Note: Virginia state records may reflect this within 2-5 business days.
```

---

### 4. Different Totals, Same Donor (Aggregation Conflict)

**Query:**
```python
# Asking: "Total donations to Smith from Education sector?"
fec_education = query_fec_by_sector(sector="Education", recipient="Smith", cycle=2026)
vpap_education = query_vpap_by_sector(sector="Education", recipient="Smith", cycle=2026)
```

**Data:**
```
fec_education: {
    sector: "Education",
    total: 18500,
    donors: ["PAC A", "PAC B", "Individual C"],
    date: "2026-05-24"
}
vpap_education: {
    sector: "Education",
    total: 15000,
    donors: ["PAC A", "PAC B"],  # Missing Individual C
    date: "2026-05-24"
}
```

**Detection:**
```python
if fec_education['total'] != vpap_education['total']:
    # Check if difference is due to missing donors in VPAP
    missing = set(fec_education['donors']) - set(vpap_education['donors'])
    if missing:
        # Likely indexing lag for missing donors
        note_partial_lag(fec_education, vpap_education, missing)
    else:
        # Same donors, different totals = actual conflict
        flag_conflict(fec_education, vpap_education)
```

**Output (Indexing Lag):**
```
OFFICIAL: FEC, 2026-05-24
Education sector contributed $18,500 to Smith
- PAC A: $5,000
- PAC B: $10,000
- Individual C: $3,500

Note: VPAP shows $15,000 (2 donors); Individual C contribution may be pending indexing.
```

---

## Implementation Requirements

### 1. Dual-Source Query Function

```python
def query_donation_conflict_check(
    donor: str,
    recipient: str,
    cycle: int,
    sources: list = ["FEC", "VPAP"]
) -> dict:
    """
    Query multiple sources for the same donation.
    Returns conflict status and values from each source.
    """
    results = {}
    for source in sources:
        if source == "FEC":
            results['fec'] = query_fec(donor, recipient, cycle)
        elif source == "VPAP":
            results['vpap'] = query_vpap(donor, recipient, cycle)
    
    return results
```

### 2. Conflict Detection Logic

```python
def detect_conflict(fec_result, vpap_result, fec_filing_date) -> dict:
    """
    Determine if results represent a conflict or normal lag.
    Returns: {has_conflict: bool, reason: str, values: dict}
    """
    
    # Both sources have data
    if fec_result and vpap_result:
        if fec_result['amount'] != vpap_result['amount']:
            return {
                'has_conflict': True,
                'reason': 'Amount mismatch',
                'values': {'fec': fec_result, 'vpap': vpap_result}
            }
    
    # FEC has data, VPAP doesn't
    elif fec_result and not vpap_result:
        days_since_filing = (today - fec_filing_date).days
        if days_since_filing > 5:  # Beyond normal lag
            return {
                'has_conflict': True,
                'reason': 'Missing from VPAP (beyond lag window)',
                'values': {'fec': fec_result, 'vpap': None}
            }
        else:
            return {
                'has_conflict': False,
                'reason': 'Normal indexing lag',
                'values': {'fec': fec_result, 'vpap': None}
            }
    
    # Neither source has data
    else:
        return {'has_conflict': False, 'reason': 'No data in either source'}
```

### 3. Output Formatting

```python
def format_conflict_output(conflict_data: dict) -> str:
    """Format conflict flag for user output."""
    
    fec = conflict_data['values'].get('fec')
    vpap = conflict_data['values'].get('vpap')
    
    output = "⚠️ SOURCE CONFLICT DETECTED\n\n"
    
    if fec:
        output += f"FEC ({fec['date']}): {fec.get('donor', '?')} donated ${fec['amount']:,}\n"
    else:
        output += "FEC: No record found\n"
    
    if vpap:
        output += f"VPAP ({vpap['date']}): {vpap.get('donor', '?')} donated ${vpap['amount']:,}\n"
    else:
        output += "VPAP: No record found\n"
    
    output += "\nContact Support or use Data Quality Escalator for resolution."
    
    return output
```

---

## Integration Points

### Analyst Agent (voteiq/api/routes/chat.py)

**Location:** Where analyst queries for donations (need to find existing query logic)

**Modification:**
```python
# Instead of:
result = query_fec(donor, recipient, cycle)

# Do:
results = query_donation_conflict_check(donor, recipient, cycle)
conflict = detect_conflict(results['fec'], results['vpap'], results['fec']['date'])

if conflict['has_conflict']:
    return format_conflict_output(conflict)
else:
    return format_single_source_output(results['fec'])
```

---

## Test Cases for Implementation

### Test 1: Amount Mismatch
```
Input: donor="Education PAC", recipient="Smith", cycle=2026
FEC: $5,000 (2026-05-15)
VPAP: $0 (2026-05-24)
Expected: Flag conflict, list both amounts
```

### Test 2: Indexing Lag
```
Input: donor="Education PAC", recipient="Smith", cycle=2026
FEC: $5,000 (2026-05-23)
VPAP: Not found (filed only 1 day ago)
Expected: Return FEC, note lag, don't flag conflict
```

### Test 3: Missing Record (After Lag)
```
Input: donor="Tech Fund", recipient="Smith", cycle=2026
FEC: $3,000 (2026-05-10)
VPAP: Not found (filed 15 days ago, past lag window)
Expected: Flag conflict, suggest escalation
```

### Test 4: Both Have Same Data
```
Input: donor="Green Future", recipient="Smith", cycle=2026
FEC: $2,500 (2026-05-24)
VPAP: $2,500 (2026-05-24)
Expected: Return single fact, no conflict flag
```

### Test 5: Existence Conflict
```
Input: donor="New PAC", recipient="Smith", cycle=2026
FEC: Yes, $1,000 (2026-05-20)
VPAP: No record found (6+ days after filing)
Expected: Flag conflict
```

---

## Performance Considerations

1. **Parallel Queries:** Query FEC and VPAP in parallel (not sequential) to minimize latency
2. **Caching:** Consider caching query results if analyst gets same question multiple times
3. **Timeouts:** Set query timeouts (suggest 5-10 seconds per source to avoid hanging)
4. **Error Handling:** If one source fails, return available data and note the gap

---

## Data Source Notes

### FEC Data
- Source: Federal Election Commission
- Update frequency: Filings within 1-2 days
- Coverage: Federal candidates, PACs, individuals (employer/occupation level)
- Authoritative for: Federal contributions

### VPAP Data
- Source: Virginia Public Access Project
- Update frequency: Filings indexed 2-5 days after FEC receipt
- Coverage: Virginia state/local candidates, donors
- Aggregates: FEC data + Virginia-specific records

### Lag Assumptions
- FEC → VPAP indexing: 2-5 business days typical
- Edge case (weekends/holidays): May be up to 10 days
- For testing: Use 6 days as cutoff for "beyond normal lag"

---

## References

- **SOURCE_CONFLICT_RESOLUTION.md** — Full specification
- **CONFLICT_QUICK_REFERENCE.md** — Decision table
- **voteiq/api/routes/chat.py** — Analyst agent implementation
- **Data source docs:** FEC API, VPAP query endpoints

---

## Next Steps

1. Review this guide with the engineer who will implement query logic
2. Identify existing donation query functions in codebase
3. Implement `query_donation_conflict_check()` function
4. Implement `detect_conflict()` logic
5. Integrate into analyst agent flow
6. Test with test cases above
7. Deploy to production

---

**Document Status:** Design / Ready for implementation  
**Target Phase:** Phase 2 (Query Logic Implementation)  
**Priority:** B (Secondary)

