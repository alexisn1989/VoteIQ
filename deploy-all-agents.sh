#!/bin/bash
# Deploy all 14 VoteIQ agents and update .env
# Usage: ./deploy-all-agents.sh

set -e

echo "========================================"
echo "VoteIQ Agent Deployment Suite"
echo "Deploying all 14 agents"
echo "========================================"
echo ""

# Check API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Error: ANTHROPIC_API_KEY not set"
    exit 1
fi

# Backup original .env
if [ -f .env ]; then
    cp .env .env.backup
    echo "✓ Backed up .env to .env.backup"
fi

# Create temporary env updates file
ENV_UPDATES="/tmp/voteiq_agent_ids_$$.txt"
> "$ENV_UPDATES"

# Helper function to deploy agent
deploy_agent() {
    local name=$1
    local script=$2

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Deploying: $name"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ ! -f "$script" ]; then
        echo "❌ Script not found: $script"
        return 1
    fi

    # Run deployment script and capture response
    response=$(bash "$script" 2>&1)

    # Extract agent ID from response
    agent_id=$(echo "$response" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

    if [ -z "$agent_id" ]; then
        echo "❌ Failed to create agent: $name"
        echo "Response: $response"
        return 1
    fi

    echo "✓ Created: $name"
    echo "  Agent ID: $agent_id"
    echo "$agent_id" >> "$ENV_UPDATES"
    return 0
}

# Track results
DEPLOYED=0
FAILED=0

# Deploy all agents
deploy_agent "analyst" "./deploy-analyst.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "escalator" "./deploy-escalator.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "field_monitor" "./deploy-field-monitor.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "debugger" "./deploy-debugger.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "golden_query" "./deploy-golden-query.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "crosswalk" "./deploy-crosswalk.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "structured_extractor" "./deploy-structured-extractor.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "data_analyst" "./deploy-data-analyst.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "general_admin" "./deploy-general-admin.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "deep_researcher" "./deploy-deep-researcher.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "support_drafts" "./deploy-support-drafts.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "sprint_retro" "./deploy-sprint-retro.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "visual_explainer" "./deploy-visual-explainer.sh" && ((DEPLOYED++)) || ((FAILED++))
deploy_agent "whro_grants" "./deploy-whro-grants.sh" && ((DEPLOYED++)) || ((FAILED++))

# Summary
echo ""
echo "========================================"
echo "Deployment Summary"
echo "========================================"
echo "✓ Deployed: $DEPLOYED agents"
echo "❌ Failed: $FAILED agents"
echo ""

if [ $FAILED -gt 0 ]; then
    echo "⚠️  Some agents failed to deploy. Review output above."
    exit 1
fi

echo "All agents deployed successfully!"
echo ""
echo "Next step: Update .env file with agent IDs"
echo ""
echo "Read agent IDs from deploy script output and add to .env:"
echo ""
echo "# VoteIQ Agent IDs"
echo "VOTEIQ_ANALYST_AGENT_ID=agent_..."
echo "VOTEIQ_ESCALATOR_AGENT_ID=agent_..."
echo "VOTEIQ_FIELD_MONITOR_AGENT_ID=agent_..."
echo "VOTEIQ_DEBUGGER_AGENT_ID=agent_..."
echo "VOTEIQ_GOLDEN_QUERY_AGENT_ID=agent_..."
echo "VOTEIQ_CROSSWALK_AGENT_ID=agent_..."
echo "VOTEIQ_STRUCTURED_EXTRACTOR_AGENT_ID=agent_..."
echo "VOTEIQ_DATA_ANALYST_AGENT_ID=agent_..."
echo "VOTEIQ_GENERAL_ADMIN_AGENT_ID=agent_..."
echo "VOTEIQ_DEEP_RESEARCHER_AGENT_ID=agent_..."
echo "VOTEIQ_SUPPORT_DRAFTS_AGENT_ID=agent_..."
echo "VOTEIQ_SPRINT_RETRO_AGENT_ID=agent_..."
echo "VOTEIQ_VISUAL_EXPLAINER_AGENT_ID=agent_..."
echo "VOTEIQ_WHRO_GRANTS_AGENT_ID=agent_..."
echo ""
echo "Then restart the VoteIQ API server."
echo ""
