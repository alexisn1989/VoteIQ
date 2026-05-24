# Field Monitor Scope Update

## Goal

Update the Civic Field Monitor to exclude federal content by default (Congress, federal elections, federal committees) while allowing federal focus when explicitly requested.

## Current Behavior

Field monitor searches broad civic tech trends, which can include federal Congress, FEC, and federal election data.

## Desired Behavior

1. **Default:** Monitor only Virginia state and Hampton Roads local civic trends
2. **Conditional:** Include federal content only if focus_areas explicitly includes "federal" or "congressional"
3. **Scope:** Always exclude U.S. Congress general monitoring unless federal is explicitly asked

## Implementation

### Change 1: Update FieldMonitorRequest Model

**Location:** `voteiq/api/routes/chat.py` (search for `class FieldMonitorRequest`)

**Current:**
```python
class FieldMonitorRequest(BaseModel):
    focus_areas: list[str] = []
    lookback_days: int = 7
```

**Updated:**
```python
class FieldMonitorRequest(BaseModel):
    focus_areas: list[str] = []
    lookback_days: int = 7
    include_federal: bool = False  # NEW: explicitly opt-in to federal content
```

### Change 2: Update _build_field_monitor_queries

**Location:** Around line 4146

**Add scope filtering to query builder:**

```python
def _build_field_monitor_queries(focus_areas: list[str], include_federal: bool = False) -> dict[str, list[str]]:
    """
    Build search queries for field monitoring based on focus areas.
    
    By default, excludes federal Congress and federal elections.
    Include federal content only if include_federal=True or if focus_areas explicitly mentions "federal"
    """
    
    # Check if user explicitly asked for federal
    requested_federal = include_federal or any(
        area.lower() in ["federal", "congressional", "congress", "u.s. congress"] 
        for area in focus_areas
    )
    
    queries = {
        # Virginia state civic tech (always included)
        "virginia_legislation": [
            "Virginia General Assembly bills 2026",
            "Virginia House of Delegates votes",
            "Virginia State Senate legislation",
            "Virginia legislative trends",
        ],
        "virginia_elections": [
            "Virginia state elections 2026",
            "Virginia election law changes",
            "Virginia voting systems",
        ],
        # Hampton Roads local government (always included)
        "hampton_roads_local": [
            "Virginia Beach city council 2026",
            "Norfolk city government",
            "Newport News municipal government",
            "Hampton Roads local politics",
        ],
        # Campaign finance (always for Virginia)
        "virginia_campaign_finance": [
            "Virginia VPAP campaign finance trends",
            "Virginia statewide fundraising",
            "Virginia state legislative campaigns",
        ],
    }
    
    # ONLY add federal queries if explicitly requested
    if requested_federal:
        queries.update({
            "federal_congress": [
                "U.S. Congress bills 2026",
                "Congressional voting records Virginia delegation",
                "House of Representatives 2026",
                "U.S. Senate 2026",
            ],
            "federal_elections": [
                "2026 federal elections Virginia",
                "Congressional elections Virginia",
            ],
            "federal_campaign_finance": [
                "FEC federal campaign finance",
                "Virginia congressional campaign donations",
                "Federal PAC contributions Virginia",
            ],
        })
    else:
        # Add note to report that federal content was excluded
        queries["_scope_note"] = [
            "Federal content excluded (default scope: Virginia state and local only)"
        ]
    
    return queries
```

### Change 3: Update field_monitor Endpoint

**Location:** Around line 4100

**Update function signature:**

```python
@router.post("/api/field-monitor")
async def field_monitor(req: FieldMonitorRequest):
    """
    VoteIQ Civic Field Monitor
    
    DEFAULT SCOPE: Virginia state and Hampton Roads local only
    FEDERAL CONTENT: Excluded by default
    
    To include federal Congress, elections, or federal-level trends:
    - Set include_federal=true in request, OR
    - Include "federal" or "congressional" in focus_areas
    
    Example:
    POST /api/field-monitor
    {
        "focus_areas": ["elections", "federal"],  # federal added
        "lookback_days": 7
    }
    
    Returns:
    - Structured report with clusters, impact assessment
    - Draft Slack notification
    - Draft Notion database entry
    - Draft GitHub issues (for product action items)
    
    All outputs are DRAFT only — no automatic posting.
    """
    client = get_claude_client()
    
    # Build search queries (now includes scope filtering)
    search_queries = _build_field_monitor_queries(
        req.focus_areas, 
        include_federal=req.include_federal  # Pass include_federal flag
    )
    
    # Check if federal was requested vs default
    requested_federal = req.include_federal or any(
        area.lower() in ["federal", "congressional", "congress"] 
        for area in req.focus_areas
    )
    
    # Gather intelligence via web search
    findings = await _gather_field_intelligence(search_queries, req.lookback_days)
    
    # Analyze and structure findings using Claude
    report = await _analyze_field_findings(findings, req.focus_areas, client)
    
    # Add scope note to report
    if not requested_federal:
        report["scope_note"] = "Federal content excluded per scope policy (Virginia state and local only)"
        report["federal_available"] = "To include federal Congress trends, set include_federal=true or add 'federal' to focus_areas"
    
    # Generate drafts
    slack_draft = _draft_slack_field_monitor(report)
    notion_draft = _draft_notion_field_monitor(report)
    github_draft = _draft_github_field_monitor(report)
    
    return FieldMonitorDraft(
        summary=report.get("summary", ""),
        clusters=report.get("clusters", []),
        competitive_landscape=report.get("competitive_landscape", ""),
        grant_opportunities=report.get("grant_opportunities", []),
        research_to_watch=report.get("research_to_watch", []),
        questions_for_leadership=report.get("questions_for_leadership", []),
        scope_note=report.get("scope_note", ""),  # NEW
        federal_available=report.get("federal_available", ""),  # NEW
        slack_draft=slack_draft,
        notion_draft=notion_draft,
        github_issues_draft=github_draft
    )
```

### Change 4: Update FieldMonitorDraft Response Model

**Location:** Search for `class FieldMonitorDraft`

**Add new fields:**

```python
class FieldMonitorDraft(BaseModel):
    summary: str
    clusters: list[dict]
    competitive_landscape: str
    grant_opportunities: list[dict]
    research_to_watch: list[str]
    questions_for_leadership: list[str]
    scope_note: str = ""  # NEW
    federal_available: str = ""  # NEW
    slack_draft: dict
    notion_draft: dict
    github_issues_draft: list[dict]
```

## Usage Examples

### Default (Virginia/Local only)
```bash
POST /api/field-monitor
{
  "focus_areas": ["legislation", "elections"],
  "lookback_days": 7
}
```
Response: Virginia state and Hampton Roads local content only. No federal Congress.

### With Federal Content
```bash
POST /api/field-monitor
{
  "focus_areas": ["legislation", "elections", "federal"],
  "lookback_days": 7,
  "include_federal": true
}
```
Response: Includes Virginia state, local, AND federal Congress trends.

### Check Scope Note
```bash
POST /api/field-monitor
{
  "focus_areas": ["elections"],
  "lookback_days": 7
}
```
Response includes:
```
"scope_note": "Federal content excluded per scope policy (Virginia state and local only)",
"federal_available": "To include federal Congress trends, set include_federal=true or add 'federal' to focus_areas"
```

## Testing

### Test 1: Default scope (Virginia/local only)
```bash
Query: Virginia legislation trends
Expected: Virginia House, Virginia Senate, local city councils
Not expected: Congress, U.S. House, U.S. Senate
```

### Test 2: Federal opt-in
```bash
Query: Federal elections + include_federal=true
Expected: Congressional elections, federal campaigns, Congress trends
Also includes: Virginia state elections, local elections
```

### Test 3: Explicit federal in focus_areas
```bash
Query: focus_areas=["federal", "elections"]
Expected: Federal content included without include_federal flag
Behavior: "federal" in focus_areas automatically enables federal content
```

## Backwards Compatibility

- Old requests without `include_federal` field: Treated as `include_federal=false` (federal excluded)
- Old requests that included federal terms in focus_areas: Still work (auto-enables federal)
- Existing field monitor reports: Will show scope_note about federal exclusion

## Benefits

✓ **Clearer scope:** Field monitor explicitly focused on Virginia/local
✓ **Reduced noise:** Less federal Congress chatter unless explicitly requested
✓ **Flexibility:** Users can opt-in to federal if they want it
✓ **Transparent:** Response includes scope note explaining what was included/excluded
✓ **Consistent:** Respects scope policy across all agents

## Related Work

- Scope Policy: [docs/source_scope_policy.md](./docs/source_scope_policy.md)
- Agent Usage: [AGENT_USAGE_GUIDE.md](./AGENT_USAGE_GUIDE.md)
- Field Monitor Docs: See inline docstring in chat.py line 4100
