# VoteIQ — Agent Instructions (v2)

## Architecture (STRICT — do not modify without explicit approval)

- FastAPI backend + SQLite production DB at: `/var/data/polls.db` (Render production path only)
- SQL-first retrieval is the primary source of truth for: votes, bills, donations, legislators
- RAG fallback:
  - primary use: long-form text (bill text, speeches, documents, news/commentary)
  - may supplement structured queries with context/commentary, but must **never** override or contradict SQL-sourced facts for votes, counts, or financials
- Embeddings: Voyage AI (voyage-law-2)
- Vector DB: ChromaDB (supplemental only, never authoritative for structured facts)
- Model routing:
  - Sonnet 4.6 → daily development + bug fixes
  - Opus 4.8 → complex reasoning / debugging / production incidents
  - Fable 5 → architecture decisions / grant writing / system design only

---

## CORE DEMO PROTECTION ZONE (HIGHEST PRIORITY)

The following is the WHRO demo-critical asset:

- `_add_pac_vote_correlation_context()`
- vote retrieval waterfall (4-path system)

**RULE: Do NOT modify, refactor, rename, or reorder this system under any circumstances unless:**
- explicit user confirmation AND stated intent to modify demo logic.

If touched accidentally:
1. stop
2. explain risk
3. request confirmation

---

## DATA INTEGRITY RULES

- Never hallucinate: APIs, database fields, table schemas, external endpoints
- If unsure: inspect codebase first, or explicitly say "not found in repository"
- **"I don't know" is a VALID AND REQUIRED output when data is missing**

---

## ROUTING RULES (IMPORTANT)

### Individual vote lookup rule
**Single-legislator + single-bill queries must use a name-filtered SQL query — never the full roll-call blob.**

When the user asks "how did [legislator] vote on [bill]" or any variant:
- Use `WHERE lower(voter_name) LIKE lower('%name%') AND lower(bill_number) LIKE lower('%HB84%')`
- This is implemented in `_add_targeted_vote_lookup()` in `database_context.py`
- Full roll-call retrieval (all voters for a bill) is only appropriate when the question is about the full vote distribution (e.g. "how did the House vote on HB84?")
- Rationale: roll-calls are alphabetical; a 666-row blob ordered A→Z truncates before 'H' names, causing false "vote not found" answers

### Code change routing
Before modifying anything in:
- `virginia.query`
- vote retrieval pipeline
- donor-vote correlation logic

You must:
1. trace DB query path
2. confirm source table
3. check recent git history / commit messages for this path for prior fixes

If unclear → **STOP and ask.**

---

## WORKFLOW (MANDATORY)

### Step 1 — Explore
- Read relevant files first
- Identify data flow (DB → service → API → UI)

### Step 2 — Plan (REQUIRED BEFORE CODE)
Provide:
- files to be changed
- reason for each change
- risk level (low / medium / high)
- whether demo path is affected (YES/NO)

### Step 3 — Implement
- minimal diffs only
- no architecture changes unless explicitly requested

### Step 4 — Verify
- check edge cases
- ensure no DB path breakage
- ensure no regression in vote accuracy

---

## HARD CONSTRAINTS

Never modify without approval:
- `_add_pac_vote_correlation_context()`
- vote retrieval waterfall logic
- Render DB path `/var/data/polls.db`

Never assume:
- schema fields
- API endpoints
- ingestion formats

Always:
- inspect before editing
- minimize diffs
- preserve "I don't know" / trust behavior

---

## OUTPUT FORMAT (REQUIRED)

**Understanding**
(what the system currently does)

**Plan**
(files + steps + risk level)

**Changes**
(code or diff only)

**Risk Check**
- demo impacted? (yes/no)
- DB impacted? (yes/no)
- regression risk (low/med/high)
