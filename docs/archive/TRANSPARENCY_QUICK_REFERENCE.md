# User Transparency — Quick Reference

## The Rule

**Every response shows:**
```
1. What data was used
2. When it was last updated
3. How fresh/current it is
4. What's missing or delayed
```

---

## Manifest Template

```
📊 Data Transparency
├─ Sources: [FEC, Virginia LIS, VPAP, etc.]
├─ Last Updated: [Dates with times]
├─ Freshness: [Current/Lagging/Delayed]
├─ Data Gaps: [What's missing]
└─ Version: [Snapshot date if applicable]
```

---

## Freshness Status Codes

| Status | Meaning | Example |
|--------|---------|---------|
| ✓ Current | < 1 day old | FEC indexed same day |
| ⏳ Lagging | Known lag expected | VPAP 2-5 day lag (normal) |
| ⚠️ Delayed | Beyond expected lag | Data not yet indexed (unexpected) |
| ❌ Unavailable | No recent update | Source offline/down |

---

## Data Source Currency

| Source | Update Freq | Lag | Freshness |
|--------|-------------|-----|-----------|
| **FEC** | Same day | <1 day | ✓ Current |
| **Virginia LIS** | Real-time | <1 day | ✓ Current |
| **Congress.gov** | Next day | <1 day | ✓ Current |
| **Virginia SBE** | Real-time | <1 day | ✓ Current |
| **VPAP** | Weekly | 2-5 days | ⏳ Lagging (normal) |

---

## Example Manifests

### Complete Data (Current)
```
📊 Data Transparency
├─ Sources: FEC, Virginia LIS
├─ Last Updated: FEC 2026-05-24 10:00 UTC; VLI 2026-05-24 14:30 UTC
├─ Freshness: Current (same-day indexing)
├─ Data Gaps: None
└─ Note: All official records current
```

### Partial Data (With Lag)
```
📊 Data Transparency
├─ Sources: FEC (current), VPAP (lagging)
├─ Last Updated: FEC 2026-05-24; VPAP 2026-05-20
├─ Freshness: FEC current; VPAP lagging (normal 2-5 day lag)
├─ Data Gaps: VPAP state-level donors pending indexing
└─ Expected VPAP update: 2026-05-28
```

### Conflicting Data
```
📊 Data Transparency
├─ Sources: FEC vs VPAP (CONFLICT)
├─ Last Updated: FEC 2026-05-24; VPAP 2026-05-20
├─ Freshness: FEC current; VPAP lag may explain conflict
├─ Data Gaps: VPAP indexing may be incomplete
└─ Action: Use Data Quality Escalator to resolve
```

---

## When to Show

**Always append manifest to:**
- ✓ Single fact responses (donations, votes, bills)
- ✓ Donor/legislator profiles
- ✓ Bill information
- ✓ Any official record response
- ✓ Conflict-flagged responses

**Show detailed version when:**
- Multiple sources used
- Known lag exists
- Data conflicts detected
- Data gaps identified

---

## What Users Learn

1. **Data Currency:** "Is this current?"
2. **Data Source:** "Where did this come from?"
3. **Update Frequency:** "When will it update?"
4. **Data Gaps:** "What's missing?"
5. **Trust Level:** "How confident should I be?"

---

## User Questions Answered

```
"How current is this data?" 
→ Freshness status shows current/lagging/delayed

"Where did you get this?"
→ Sources list shows FEC, Virginia LIS, VPAP, etc.

"When was it updated?"
→ Last Updated shows exact date/time

"Is anything missing?"
→ Data Gaps explains known limitations

"Can I trust this?"
→ Combined manifest shows transparency level
```

---

## Implementation Points

- Analyst appends manifest to every response
- Manifest shows ALL sources used
- Includes dates with times (ISO 8601)
- Uses status codes (✓/⏳/⚠️)
- Explains data gaps plainly
- No jargon—user-friendly language

---

**Status:** ✓ Ready for use  
**Requirement:** Mandatory for all analyst responses  
**Benefit:** Users always know what data they're looking at

