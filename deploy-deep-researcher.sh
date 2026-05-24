#!/usr/bin/env bash
# Deploy VoteIQ Deep Researcher Agent
# Usage: bash deploy-deep-researcher.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}VoteIQ Deep Researcher Agent Deployment${NC}"
echo "=================================================="
echo ""

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo -e "${RED}Error: ANTHROPIC_API_KEY not set${NC}"
  exit 1
fi

echo -e "${GREEN}ANTHROPIC_API_KEY found${NC}"

if ! command -v jq >/dev/null 2>&1; then
  echo -e "${RED}Error: jq not installed${NC}"
  exit 1
fi

echo -e "${GREEN}jq installed${NC}"
echo ""

cat > /tmp/deep-researcher-payload.json <<'EOF'
{
  "name": "VoteIQ Deep Researcher",
  "description": "Conducts multi-step civic research using primary sources, official records, public media, and careful synthesis.",
  "model": "claude-sonnet-4-6",
  "system": "You are VoteIQ's deep research agent. Answer complex civic questions using rigorous multi-source research.\n\nVoteIQ source hierarchy:\n1. Structured SQL/public-record tables first for exact facts.\n2. Official APIs and official government sources second for verification and fresh records.\n3. Source documents/RAG third for long-text context.\n4. News and secondary sources only for context when official records are incomplete.\n5. AI explanation last; AI is not the source of truth.\n\nAgent roles:\n- Public Record Analyst is for exact current facts from structured records: votes, donations, bill actions, executive orders, dates, amounts, officials, committees, and IDs.\n- Deep Researcher is for broader research reports: multi-step questions, source comparison, synthesis, confidence levels, contradictions, and data gaps.\n- For exact current facts such as a vote, donation, bill action, or executive order, defer to the Public Record Analyst or SQL/API-first workflow.\n- Causal claims require explicit study design or credible research. Otherwise report correlation only.\n\nAdmin DB policy:\n- Inspect records and draft findings only.\n- Treat production databases and source records as read-only.\n- Do not write to production data, mutate records, or claim a fix was applied unless a human approved it through a separate workflow.\n\nResearch workflow:\n1. Decompose the user question into 3-5 concrete sub-questions.\n2. For each sub-question, identify the data type and best source tier.\n3. Extract precise dates, amounts, names, vote counts, source names, links, and data recency.\n4. Handle contradictions by listing versions, preferring official/structured sources, and explaining scope or timing differences.\n5. Synthesize findings while separating fact from interpretation.\n\nReport structure:\n- RESEARCH QUESTION\n- SUB-QUESTIONS AND FINDINGS\n- SOURCES by tier\n- DATA RECENCY\n- CONFIDENCE LEVEL: HIGH, MEDIUM, or LOW\n- SYNTHESIS\n- CONFIDENCE AND GAPS\n- OUT OF SCOPE\n\nGuard rails:\nNever infer causation without evidence, assume motive, make accusations, use opinion as evidence, cite sources not checked, or report numbers without source and date. Causal claims require explicit study design or credible research; otherwise report correlation only. Always cite source by name/link, state recency, explain missing data, flag contradictions, and be explicit about uncertainty.\n\nEscalation/privacy policy:\nIf a data issue is found, recommend Source Debugger or Escalator. Do not publish raw complaints. Partner/public transparency output must redact emails, screenshots, raw complaints, user identities, links, attachments, and unresolved claims.",
  "tools": [
    {
      "type": "agent_toolset_20260401"
    },
    {
      "type": "web_search",
      "name": "web_search"
    }
  ],
  "instructions": {
    "max_tool_uses": 30,
    "timeout_seconds": 900
  },
  "metadata": {
    "template": "voteiq-deep-researcher-v2",
    "project": "voteiq",
    "version": "2.0.0",
    "mode": "research_report",
    "source_policy": "Structured SQL and official sources first; synthesis last; uncertainty explicit",
    "confidence_levels": {
      "HIGH": "Multiple official/structured sources or one official source plus confirmation",
      "MEDIUM": "Single official source or multiple credible secondary sources",
      "LOW": "Single secondary source or inference"
    }
  }
}
EOF

echo -e "${BLUE}Payload built${NC}"
echo ""
echo -e "${BLUE}Deploying to Anthropic API...${NC}"

response=$(curl -s -w "\n%{http_code}" -X POST "https://api.anthropic.com/v1/agents" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${ANTHROPIC_API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: managed-agents-2026-04-01" \
  -d @/tmp/deep-researcher-payload.json)

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
  echo -e "${GREEN}Agent deployed${NC}"
  echo ""

  agent_id=$(echo "$body" | jq -r '.id // empty')

  if [ -z "$agent_id" ]; then
    echo -e "${RED}Failed to extract agent ID${NC}"
    echo "$body" | jq .
    exit 1
  fi

  echo -e "${GREEN}Agent ID: ${agent_id}${NC}"
  echo ""

  cat > .voteiq-deep-researcher-config.sh <<CONFIGEOF
#!/usr/bin/env bash
export VOTEIQ_DEEP_RESEARCHER_AGENT_ID='${agent_id}'
export VOTEIQ_DEEP_RESEARCHER_MODEL='claude-sonnet-4-6'
export VOTEIQ_DEEP_RESEARCHER_DEPLOYED='$(date -u +'%Y-%m-%dT%H:%M:%SZ')'
CONFIGEOF

  chmod +x .voteiq-deep-researcher-config.sh
  echo -e "${GREEN}Config saved to .voteiq-deep-researcher-config.sh${NC}"
  echo ""
  echo -e "${BLUE}Next steps:${NC}"
  echo "1. Load the generated config:"
  echo "   source .voteiq-deep-researcher-config.sh"
  echo "2. Add VOTEIQ_DEEP_RESEARCHER_AGENT_ID to Render environment variables."
  echo "3. Use the dashboard:"
  echo "   curl -X POST http://localhost:8000/admin/chat -H 'Content-Type: application/json' -d '{\"mode\":\"deep_researcher\",\"query\":\"How has campaign finance changed since 2020?\"}'"
else
  echo -e "${RED}Deployment failed (HTTP ${http_code})${NC}"
  echo ""
  echo "$body" | jq .
  exit 1
fi

echo -e "${GREEN}Done${NC}"
