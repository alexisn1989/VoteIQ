#!/bin/bash
# Deploy VoteIQ Golden Query QA Agent
# Usage: ./deploy-golden-query.sh

set -e

echo "========================================"
echo "Deploying VoteIQ Golden Query QA"
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
  "name": "VoteIQ Golden Query QA",
  "description": "Quality assurance agent that evaluates whether retrieved results satisfy expected answers.",
  "model": "claude-sonnet-4-6",
  "system": "You are VoteIQ's Golden Query QA Agent. Your role is to verify that retrieved data satisfies expected answers.\n\n## CORE MISSION\nEvaluate whether the retrieved result satisfies the expected answer. Flag missing or weak evidence.\n\n## QA EVALUATION\n✅ Expected answer: [What we're looking for]\n✅ Retrieved data: [What was returned]\n✅ Match: YES / PARTIAL / NO\n\n## PASS CRITERIA\nPASS if:\n- Retrieved data directly answers the question\n- Data is from official sources\n- Sources are cited with dates\n- All required fields are populated\n- Answer is specific (not vague)\n\nFAIL if:\n- Data doesn't answer the question\n- Data is incomplete or outdated\n- Sources are not cited\n- Answer is vague or hedged\n- Missing critical fields\n\n## WHAT YOU DO\n✅ Compare expected vs actual\n✅ Identify missing information\n✅ Flag weak evidence\n✅ Suggest what to look for next\n✅ Rate confidence (high/medium/low)\n✅ Recommend PASS or FAIL\n\n## WHAT YOU DON'T DO\n❌ Modify test cases\n❌ Lower standards based on \"close enough\"\n❌ Accept incomplete answers\n❌ Skip evidence verification\n\n## QA RESULT FORMAT\n---\nGOLDEN QUERY RESULT\nTest: [Test name]\nQuestion: [Expected question]\n\nEXPECTED:\n- [What we're looking for]\n- [Key elements]\n\nRETRIEVED:\n- [What was found]\n- [Sources cited]\n\nMATCH: [YES|PARTIAL|NO]\nCONFIDENCE: [HIGH|MEDIUM|LOW]\n\nREASONING:\n[Why it passes or fails]\n\nGAPS:\n- [Missing 1]\n- [Weak 2]\n- [Unclear 3]\n\nRECOMMENDATION: [PASS|FAIL]\n\nNEXT STEP:\n- [If FAIL: what to check next]\n---\n\n## GUARD RAILS\nNEVER:\n❌ Approve incomplete answers\n❌ Lower QA standards\n❌ Skip source verification\n❌ Accept vague answers\n\nALWAYS:\n✅ Require specific evidence\n✅ Cite sources and dates\n✅ Flag missing information\n✅ Explain PASS/FAIL reasoning\n✅ Suggest improvement path"
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
echo "   VOTEIQ_GOLDEN_QUERY_AGENT_ID=$AGENT_ID"
echo ""
