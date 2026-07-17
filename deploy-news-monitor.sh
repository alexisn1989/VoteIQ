#!/bin/bash
# Deploy VoteIQ News Monitor Agent
# Usage: ./deploy-news-monitor.sh

set -e

echo "========================================"
echo "Deploying VoteIQ News Monitor"
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
  "name": "VoteIQ News Monitor",
  "description": "Monitors and summarizes news reporting on Virginia civic activity. Provides context and public narrative, not verified facts.",
  "model": "claude-sonnet-4-6"
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
echo "   VOTEIQ_NEWS_MONITOR_AGENT_ID=$AGENT_ID"
echo ""
echo "2. Test with admin endpoint:"
echo "   curl http://localhost:8000/admin/chat?mode=news_monitor&query=What%20is%20the%20news%20covering%20today"
echo ""
