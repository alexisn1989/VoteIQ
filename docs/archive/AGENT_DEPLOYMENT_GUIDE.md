# VoteIQ Agent Deployment Guide

You have **14 agents** defined in your agent registry. This guide shows how to deploy them all.

## Quick Start

### Option 1: Deploy All at Once (Recommended)

```bash
bash deploy-all-agents.sh
```

This will:
1. Deploy all 14 agents via Anthropic API
2. Display each agent ID as it's created
3. Show a summary with all agent IDs

### Option 2: Deploy Individual Agents

Deploy specific agents one at a time:

```bash
bash deploy-analyst.sh
bash deploy-escalator.sh
bash deploy-field-monitor.sh
bash deploy-debugger.sh
bash deploy-golden-query.sh
bash deploy-crosswalk.sh
bash deploy-structured-extractor.sh
bash deploy-data-analyst.sh
bash deploy-general-admin.sh
bash deploy-deep-researcher.sh
bash deploy-support-drafts.sh
bash deploy-sprint-retro.sh
bash deploy-visual-explainer.sh
bash deploy-whro-grants.sh
```

Each script outputs the agent ID created. Copy these IDs to your `.env` file.

---

## The 14 Agents

### Public-Facing Agents (3)

1. **analyst** — Public Record Analyst
   - Returns exact facts from structured public records
   - Cites sources, dates, amounts, votes, and data limits
   - Does NOT infer motive or causation
   - Available via `/chat` endpoint (public)

2. **support_drafts** — Support/Help Flow
   - Drafts concise support responses
   - Explains what was checked and next actions
   - Used for customer support

3. **visual_explainer** — Visual Explainer
   - Converts verified data into JSON visual definitions
   - Creates charts, maps, timelines, comparison tables
   - Includes source labels and data limits

### Admin-Only Agents (11)

4. **escalator** — Data Quality Escalator
   - Inspects data issues
   - Drafts escalation summaries with repro steps
   - Routes to appropriate specialist agents

5. **field_monitor** — Civic Field Monitor
   - Prepares field intelligence briefs
   - Identifies immediate, soon, and later actions
   - Monitors legislative activity and campaign finance

6. **debugger** — Data Debugger
   - Diagnoses why data retrieval failed
   - Identifies table availability vs. row-match failure
   - Does NOT modify production data

7. **golden_query** — Golden Query QA
   - Quality assurance for retrieval results
   - Evaluates if retrieved data satisfies expected answers
   - Flags missing or weak evidence

8. **crosswalk** — Identity Crosswalk
   - Resolves candidate/donor identity fields
   - Preserves FEC IDs, bioguide IDs, aliases, party, state
   - Handles name ambiguities and party switches

9. **structured_extractor** — Structured Extractor
   - Extracts structured JSON from civic source documents
   - Preserves source URLs, confidence levels, and notes
   - Does NOT write to production database

10. **data_analyst** — Data Analyst
    - Analyzes records WITHOUT inferring motive or causation
    - Identifies patterns, trends, and correlations
    - Quantifies relationships (correlation only, never causation)

11. **general_admin** — General Admin Utility
    - Helps with code, scripts, documentation, deployment
    - Drafts communications and operations plans
    - Does NOT automatically post/send/file

12. **deep_researcher** — Deep Researcher
    - Produces broader research reports with 3-5 sub-questions
    - Uses Tier 1 official sources first
    - Does NOT infer causation without explicit research

13. **sprint_retro** — Sprint Retro Facilitator
    - Prepares internal sprint retrospectives
    - Focuses on process, cites tickets/messages
    - Avoids individual blame

14. **whro_grants** — WHRO Grants
    - Drafts grant and partnership language
    - Uses VoteIQ's public-record mission context
    - Does NOT commit to deliverables without approval

---

## Adding Agent IDs to .env

After deployment, add all 14 agent IDs to your `.env` file:

```bash
# VoteIQ Agent IDs
VOTEIQ_ANALYST_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_ESCALATOR_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_FIELD_MONITOR_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_DEBUGGER_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_GOLDEN_QUERY_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_CROSSWALK_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_STRUCTURED_EXTRACTOR_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_DATA_ANALYST_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_GENERAL_ADMIN_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_DEEP_RESEARCHER_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_SUPPORT_DRAFTS_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_SPRINT_RETRO_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_VISUAL_EXPLAINER_AGENT_ID=agent_xxxxxxxxxx
VOTEIQ_WHRO_GRANTS_AGENT_ID=agent_xxxxxxxxxx
```

---

## Verifying Deployment

Once all agent IDs are in your `.env`, restart your API server and verify:

```bash
# Check agent status
curl http://localhost:8000/admin/agents \
  -H "Authorization: Bearer <admin-token>"
```

Expected response:
```json
{
  "status": "success",
  "summary": {
    "total_agents": 14,
    "deployed": 14,
    "missing": 0
  },
  "agents": [
    {
      "mode": "analyst",
      "deployed": true,
      "agent_id": "agent_xxxxxxxxxx",
      "deployment_status": "Ready"
    },
    ...
  ]
}
```

---

## Testing Individual Agents

Once deployed, test each agent:

```bash
# Test Public Record Analyst
curl -X POST http://localhost:8000/admin/chat \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "analyst",
    "query": "Who voted for HB 456 in 2024?"
  }'

# Test Data Debugger
curl -X POST http://localhost:8000/admin/chat \
  -H "Authorization: Bearer <admin-token>" \
  -d '{
    "mode": "debugger",
    "query": "Why did the search for donations return 0 rows?"
  }'
```

---

## Troubleshooting

### Deployment Script Fails
- Verify `ANTHROPIC_API_KEY` is set: `echo $ANTHROPIC_API_KEY`
- Check network connectivity to api.anthropic.com
- Review the curl response in the error output

### Agent ID Not Extracted
- Check the curl response format
- Verify the API key has proper permissions
- Try deploying again

### Agents Show "Not deployed" After Adding IDs
- Verify `.env` file syntax is correct
- Restart the API server: `pkill -f "python.*main.py"` then restart
- Check that environment variables are actually loaded: `env | grep VOTEIQ_`

### API Calls Fail with "Unknown admin mode"
- Verify the mode name exactly matches: `analyst`, `escalator`, etc. (all lowercase)
- Check that agent ID environment variable is set
- Verify admin token is valid

---

## Deployment Timeline

Expect each deployment to take:
- Deploy script runs: 2-5 seconds
- API response: 1-3 seconds
- Total per agent: ~5 seconds
- All 14 agents: ~1-2 minutes

---

## Backing Up Your .env

The deployment script automatically backs up your `.env` to `.env.backup`:

```bash
# Restore from backup if needed
cp .env.backup .env
```

---

## Next Steps

1. Run the deployment script (or deploy individually)
2. Copy agent IDs to `.env`
3. Restart API server
4. Verify with `/admin/agents` endpoint
5. Test with sample agent calls

Questions? Check the agent system prompts in `voteiq/api/routes/chat.py` (lines 518-650).
