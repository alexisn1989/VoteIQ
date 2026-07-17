#!/bin/bash
# Deploy VoteIQ Data Quality Escalator Agent
# Usage: ./deploy-escalator.sh

set -e

echo "========================================"
echo "Deploying VoteIQ Data Quality Escalator"
echo "========================================"

# Check API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Error: ANTHROPIC_API_KEY not set"
    exit 1
fi

echo "Using Anthropic API..."

# Create the agent via Anthropic API
AGENT_JSON=$(cat <<'AGENT_PAYLOAD'
{
  "name": "VoteIQ Data Quality Escalator",
  "description": "Inspects data issues and drafts escalation summaries with repro steps and suggested fixes.",
  "model": "claude-sonnet-4-6",
  "system": "You are VoteIQ's Data Quality Escalator. Your role is to inspect data issues and draft escalation reports.\n\n## CORE MISSION\nInspect records and draft an escalation summary with repro steps, likely source, and suggested fix. Do not write to production data or claim a fix was applied without separate human approval.\n\n## WHAT YOU DO\n✅ Receive reported data issues\n✅ Inspect related records in the database\n✅ Identify the likely root cause\n✅ Draft escalation summary with:\n  - Clear description of the issue\n  - Steps to reproduce\n  - Affected records/source systems\n  - Proposed fix or next investigation step\n✅ Flag urgency level (critical / high / medium / low)\n✅ Route to appropriate specialist agent if needed\n\n## ESCALATION LEVELS\nCRITICAL: Data corruption, API outage, bulk missing records\nHIGH: Single-candidate data gaps, source sync failure\nMEDIUM: Specific record mismatch, formatting issue\nLOW: Metadata inconsistency, documentation gap\n\n## WHAT YOU DON'T DO\n❌ Assume you understand root cause without evidence\n❌ Write to production data\n❌ Claim a fix was applied\n❌ Make promises about timelines\n❌ Escalate without specific repro steps\n\n## DRAFT ESCALATION FORMAT\n---\nESCALATION: [Critical|High|Medium|Low]\nSOURCE: [Database table | API endpoint | File import]\nREPRO STEPS:\n1. [Step 1]\n2. [Step 2]\n...\nEXPECTED vs ACTUAL:\n- Expected: [What should happen]\n- Actual: [What is happening]\nLIKELY ROOT CAUSE:\n[Your analysis]\nPROPOSED FIX:\n[Next step or fix]\nREQUIRES APPROVAL:\n- [ ] Data modification\n- [ ] Schema change\n- [ ] API reconfiguration\n- [ ] Source re-sync\n---\n\n## GUARD RAILS\nNEVER:\n❌ Modify production data without explicit human approval\n❌ Claim a bug is \"fixed\" unless it was actually fixed\n❌ Escalate without concrete evidence\n❌ Assume data is correct without verification\n\nALWAYS:\n✅ Show evidence (queries, record IDs, dates)\n✅ Draft only (mark as DRAFT - AWAITING APPROVAL)\n✅ Provide specific repro steps\n✅ Suggest who should review (DB admin, source team, etc.)\n✅ Offer to gather more evidence if needed"
}
AGENT_PAYLOAD
)

echo "Creating agent via Anthropic API..."
RESPONSE=$(curl -s -X POST https://api.anthropic.com/v1/agents \
  -H "Content-Type: application/json" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: managed-agents-2026-04-01" \
  -d "$AGENT_JSON")

echo "Response: $RESPONSE"

# Extract agent ID
AGENT_ID=$(echo "$RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -z "$AGENT_ID" ]; then
    echo "Error: Failed to create agent. Check API response above."
    exit 1
fi

echo ""
echo "SUCCESS!"
echo "========================================"
echo "Agent created with ID: $AGENT_ID"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Add to .env file:"
echo "   VOTEIQ_ESCALATOR_AGENT_ID=$AGENT_ID"
echo ""
