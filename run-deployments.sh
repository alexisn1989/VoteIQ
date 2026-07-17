#!/bin/bash
set -e

cd "C:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election"
source .env

echo "========================================"
echo "Deploying all 14 VoteIQ agents"
echo "========================================"
echo ""

# Create associative array for agent IDs
declare -A AGENT_IDS
declare -a AGENTS=("analyst" "escalator" "field-monitor" "debugger" "golden-query" "crosswalk" "structured-extractor" "data-analyst" "general-admin" "deep-researcher" "support-drafts" "sprint-retro" "visual-explainer" "whro-grants")
declare -a ENV_VARS=("ANALYST" "ESCALATOR" "FIELD_MONITOR" "DEBUGGER" "GOLDEN_QUERY" "CROSSWALK" "STRUCTURED_EXTRACTOR" "DATA_ANALYST" "GENERAL_ADMIN" "DEEP_RESEARCHER" "SUPPORT_DRAFTS" "SPRINT_RETRO" "VISUAL_EXPLAINER" "WHRO_GRANTS")

# Deploy each agent
for i in "${!AGENTS[@]}"; do
    agent="${AGENTS[$i]}"
    script="deploy-${agent}.sh"
    env_var="${ENV_VARS[$i]}"
    
    echo "Deploying: $agent"
    
    # Run deployment script and extract agent ID
    response=$(bash "$script" 2>&1)
    agent_id=$(echo "$response" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    
    if [ -z "$agent_id" ]; then
        echo "❌ Failed: $agent"
        echo "$response"
        exit 1
    fi
    
    AGENT_IDS["$env_var"]="$agent_id"
    echo "✓ $agent: $agent_id"
    echo ""
done

# Update .env file
echo ""
echo "========================================"
echo "Updating .env file"
echo "========================================"

# Backup
cp .env .env.backup.$(date +%s)
echo "✓ Backed up .env"

# Add agent IDs to .env
{
    echo ""
    echo "# VoteIQ Agent IDs (deployed $(date))"
    echo "VOTEIQ_ANALYST_AGENT_ID=${AGENT_IDS[ANALYST]}"
    echo "VOTEIQ_ESCALATOR_AGENT_ID=${AGENT_IDS[ESCALATOR]}"
    echo "VOTEIQ_FIELD_MONITOR_AGENT_ID=${AGENT_IDS[FIELD_MONITOR]}"
    echo "VOTEIQ_DEBUGGER_AGENT_ID=${AGENT_IDS[DEBUGGER]}"
    echo "VOTEIQ_GOLDEN_QUERY_AGENT_ID=${AGENT_IDS[GOLDEN_QUERY]}"
    echo "VOTEIQ_CROSSWALK_AGENT_ID=${AGENT_IDS[CROSSWALK]}"
    echo "VOTEIQ_STRUCTURED_EXTRACTOR_AGENT_ID=${AGENT_IDS[STRUCTURED_EXTRACTOR]}"
    echo "VOTEIQ_DATA_ANALYST_AGENT_ID=${AGENT_IDS[DATA_ANALYST]}"
    echo "VOTEIQ_GENERAL_ADMIN_AGENT_ID=${AGENT_IDS[GENERAL_ADMIN]}"
    echo "VOTEIQ_DEEP_RESEARCHER_AGENT_ID=${AGENT_IDS[DEEP_RESEARCHER]}"
    echo "VOTEIQ_SUPPORT_DRAFTS_AGENT_ID=${AGENT_IDS[SUPPORT_DRAFTS]}"
    echo "VOTEIQ_SPRINT_RETRO_AGENT_ID=${AGENT_IDS[SPRINT_RETRO]}"
    echo "VOTEIQ_VISUAL_EXPLAINER_AGENT_ID=${AGENT_IDS[VISUAL_EXPLAINER]}"
    echo "VOTEIQ_WHRO_GRANTS_AGENT_ID=${AGENT_IDS[WHRO_GRANTS]}"
} >> .env

echo "✓ Added 14 agent IDs to .env"
echo ""
echo "========================================"
echo "Deployment Complete!"
echo "========================================"
echo ""
echo "All 14 agents deployed successfully."
echo ""
echo "Next: Restart your API server"
echo ""
