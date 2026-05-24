# VoteIQ 12-Agent System Usage Guide

## Quick Reference

| Agent | Endpoint | Best For | Scope |
|-------|----------|----------|-------|
| Public Record Analyst | /api/analyst-chat | Fact-based answers | Local/State/Federal |
| Support Agent | /api/support-chat | Help & escalations | All |
| Civic Field Monitor | /api/field-monitor | Virginia trends | State/Local only |
| Data Escalator | (internal routing) | Data quality issues | All |
| Source Debugger | (internal routing) | Source conflicts | All |
| Golden Query Tester | (internal/admin) | QA & regression | All |
| Identity Crosswalk | (internal/admin) | Duplicate records | All |
| Data Analyst | (internal/admin) | Trend analysis | All |
| Support Drafts | (internal/admin) | Help articles | All |
| WHRO/Grant Drafts | (internal/admin) | Proposals | All |
| Deep Researcher | (internal routing) | Complex research | All |
| Structured Extractor | /api/structured-extractor | Extract from text | All |
| General Admin Utility | /admin/chat (mode 12) | Code/ops/docs | Internal only |

## 1. Public Record Analyst (`/api/analyst-chat`)

**Use when:** User asks "Who voted?", "What is the bill?", "When did this happen?"

**Returns:** Structured fact with sources, data quality, and limits

**Key features:**
- Scope-aware (Virginia/local by default, federal when needed)
- Sources: Virginia LIS, OpenStates, VPAP, FEC (when federal), Congress.gov (when federal)
- Output: Answer, Record Type, Sources, Data Quality, Limits

**Example:**
```
Q: "How did Rouse vote on SB 658?"
A: Rouse (Sen. John Rouse, R-Arlington) voted YES on SB 658 on May 15, 2026.
Sources: Virginia LIS · OpenStates
Current Through: 2026-05-24
Data Quality: Complete
```

## 2. Support Agent (`/api/support-chat`)

**Use when:** User asks "Why can't I find X?", "Is this data correct?", "How do I use VoteIQ?"

**Returns:** Answer or escalation for human review

**Key features:**
- Searches VoteIQ docs and FAQs first
- Assesses confidence: HIGH (>=90%), MEDIUM (70-89%), LOW (<70%)
- Escalates data issues with root cause analysis
- Drafts Asana tasks, Slack messages, and support replies

**Escalation types:** data-quality, privacy, legal-ethics, system, abuse

## 3. Civic Field Monitor (`/api/field-monitor`)

**Use when:** User asks "What's happening in Virginia this week?"

**Returns:** Trend clusters, high-impact items, action items, grants

**IMPORTANT SCOPE:** Virginia state and Hampton Roads local ONLY. Does NOT include federal Congress or U.S. elections.

**Key features:**
- Tracks Virginia legislative trends
- Identifies high-impact bills and votes
- Monitors statewide patterns
- Suggests grant opportunities
- No federal content (by design)

## 4. Structured Extractor (`/api/structured-extractor`)

**Use when:** "Extract votes from these meeting minutes", "Parse donation data from text"

**Returns:** JSON with validation, confidence scores, source tracking

**Key features:**
- Normalizes: bill IDs, dates, amounts, vote values, office titles
- Confidence scoring: HIGH/MEDIUM/LOW
- Schema validation
- No auto-persistence (draft only)

**Schemas:** vote, donation, bill, official

## 5. General Admin Utility (`/admin/chat`, mode 12)

**Use when:** "Help me debug this", "Draft a deployment script", "Write documentation"

**Not for:** Civic facts (route to Analyst), data issues (route to Escalator)

**Access:**
```bash
python3 admin_cli.py 12 "Help me debug this Python error"
python3 admin_cli.py 12  # interactive mode
curl http://localhost:8000/admin/agents
```

## Scope Policy

All agents respect scope:

**State/Local Queries:** Use Virginia LIS, OpenStates, Virginia SBE, VPAP, Legistar, Municipal Records. **Don't use:** FEC, Congress.gov

**Federal Queries:** Use FEC, Congress.gov, OpenStates, VPAP

**Mixed Queries:** Use all sources, label each scope clearly

**Unknown Queries:** Default to Virginia/local, ask if federal intent unclear

## Routing Guide

| User Asks | Route To |
|-----------|----------|
| "Who voted?" | Public Record Analyst |
| "Why can't I find?" | Support Agent |
| "What's trending?" | Civic Field Monitor |
| "Why do sources differ?" | Source Debugger |
| "Extract this data" | Structured Extractor |
| "Help me code" | General Admin Utility |
| "Research this impact" | Deep Researcher |
| "Fix duplicates" | Identity Crosswalk |

## API Usage

### Public APIs
```bash
# Chat
POST /chat
POST /api/analyst-chat
POST /api/support-chat
POST /api/field-monitor
POST /api/structured-extractor

# Admin (requires auth)
POST /admin/chat
GET /admin/agents
GET /admin/agents/{mode}
```

### CLI
```bash
python3 admin_cli.py 12 "query"     # Admin mode
python3 admin_cli.py 12             # Interactive
```

## Response Format

### Analyst Response
```
Answer: [fact or "not found"]
Record Type: person|bill|vote|donation|executive_order|meeting
Answer Type: SQL-backed|API-backed|RAG-backed|mixed|fallback
Sources: [only used sources]
Current Through: [date]
Data Quality: Complete|Partial|Limited
Data Limits: [gaps, exclusions]
Inference Flag: NONE or "INFERRED: ..."
```

### Field Monitor Response
```
TREND CLUSTERS: [themes]
HIGH-IMPACT ITEMS: [bills, votes]
ACTION ITEMS: [what to do]
GRANT OPPORTUNITIES: [funding]
RESEARCH TO WATCH: [adjacent work]
```

### Structured Extractor Response
```json
{
  "status": "success|validation_failed",
  "records": [...],
  "validation": {valid: bool, errors: [...]},
  "confidence_summary": {high: N, medium: N, low: N},
  "notes": ["warnings"]
}
```

## Agent Limitations

- **Analyst:** Cannot infer motive, causation, or wrongdoing
- **Support:** Cannot guarantee timelines or fixes
- **Field Monitor:** Excludes federal Congress (scope limitation)
- **Extractor:** No auto-persistence, draft only
- **Admin:** Not for civic facts (route to Analyst)

## Testing Agents

### Test Analyst
```
Q: "How did Rouse vote on SB 658?"
Expected: Exact vote, bill number, date, Virginia sources only
```

### Test Support
```
Q: "I can't find this person"
Expected: Scope note or escalation with root cause
```

### Test Field Monitor
```
Q: "What's trending in Virginia?"
Expected: State/local trends, NO federal Congress content
```

### Test Extractor
```
Text: "John voted YES on HB 456 on May 15, 2026"
Expected: JSON {bill_id, official_name, vote, vote_date, _confidence}
```

## Questions?

- Which agent should I use? See **Routing Guide** above
- How do I access admin agents? See **CLI** section
- Can I override scope? Not yet (future feature)
- Is my data current? See agent response "Current Through" date

---

See also: [Scope Policy](./docs/source_scope_policy.md), [Admin Setup](./ADMIN_SETUP.md)
