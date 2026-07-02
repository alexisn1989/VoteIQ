# Source Conflict Detection — Quick Reference

## The Rule

**If analyst queries multiple sources and gets different values:**

```
FEC says $5K  +  VPAP says $0  =  FLAG CONFLICT
    ↓
Analyst lists both values
    ↓
Suggest Data Quality Escalator
```

---

## When to Flag

| Scenario | Flag? | Action |
|----------|-------|--------|
| FEC=$5K, VPAP=$0 (same date) | ✓ Yes | List both, suggest escalation |
| FEC=$5K, VPAP=not yet indexed | ✗ No | Note indexing lag |
| FEC=[A,B,C], VPAP=[A] (lag) | ✗ No | Note VPAP pending |
| FEC=$5K filed 5/23, VPAP indexed 5/24 | ✗ No | Normal lag, no conflict |
| FEC=$5K, VPAP=$3K (same record) | ✓ Yes | List both, suggest escalation |

---

## Analyst Output Format

### No Conflict (Single Source)
```
OFFICIAL: FEC, 2026-05-24
Education PAC donated $5,000 to Candidate Smith
```

### No Conflict (Indexing Lag)
```
OFFICIAL: FEC, 2026-05-24
Education PAC donated $5,000 to Candidate Smith
Note: VPAP shows this within 2-5 business days
```

### Conflict Detected
```
⚠️ SOURCE CONFLICT DETECTED

FEC (2026-05-24): Education PAC donated $5,000
VPAP (2026-05-24): Education PAC donated $0

Data conflict between sources.
Contact Support or use Data Quality Escalator for resolution.
```

---

## Decision Tree

```
Query analyst for fact → Analyst queries sources

        ↓
    Same value? ──YES→ Return single fact with source
        │
        NO
        │
        ↓
   Both sources current? ──NO→ Note lag, return available data
        │
        YES
        │
        ↓
   Values differ? ──YES→ FLAG CONFLICT
        │                List all sources + values
        │                Suggest Escalator
        │
        NO
        ↓
   Return dominant/authoritative source
```

---

## Escalator Response

**Input:** Flagged conflict  
**Output:** Escalation summary (admin-facing)

```
CONFLICT: FEC=$5K vs VPAP=$0

Likely source: [FEC filing error / VPAP entry lag / ID mismatch]
Recommended fix: [Use FEC as authoritative / Investigate VPAP / Check IDs match]
Repro steps: Query both sources for Education PAC → Smith donation

Status: Draft (requires human approval before any data change)
```

---

## Testing Checklist

- [ ] Test: Different amounts from FEC/VPAP → Flags conflict
- [ ] Test: Timing lag (FEC current, VPAP pending) → Does NOT flag
- [ ] Test: One source missing record (due to lag) → Does NOT flag
- [ ] Test: Both sources disagree on existence → Flags conflict
- [ ] Test: Escalator receives flagged conflict → Processes without error
- [ ] Test: No production data written without human approval

---

## User Escalation Options

1. **Contact Support:** support@voteiq.io (Support triages to escalator)
2. **Data Quality Escalator:** Direct link (for advanced users)
3. **Report Issue:** Public issue reporting flow

---

**Implementation Date:** May 25, 2026  
**Priority Level:** B (Secondary)  
**User Impact:** Medium (Transparency + resolution path)

