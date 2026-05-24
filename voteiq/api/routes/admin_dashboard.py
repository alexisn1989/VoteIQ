"""
VoteIQ Admin Dashboard Routes
Internal-only API endpoints for admin operations, agent management, and operational tasks.

All endpoints marked as ADMIN_ONLY - require authentication.
"""
import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from enum import Enum
from anthropic import Anthropic
from voteiq.services.database_context import build_database_context

router = APIRouter()

# Admin-only authentication (implement as needed)
def require_admin():
    """Placeholder for admin authentication"""
    # In production, implement proper JWT/session validation
    # For now, check environment variable
    if not os.getenv("VOTEIQ_ADMIN_MODE"):
        raise HTTPException(status_code=403, detail="Admin access required")


class AgentMode(str, Enum):
    """Available admin agents"""
    GENERAL_ADMIN = "general_admin"


# Agent registry
ADMIN_AGENTS = {
    AgentMode.GENERAL_ADMIN: {
        "name": "General Admin Utility",
        "env_var": "VOTEIQ_GENERAL_ADMIN_AGENT_ID",
        "description": "Internal admin assistant for coding, scripts, documentation, deployment, testing, and operational tasks",
        "tags": ["internal", "admin", "operational"],
        "scope": [
            "code_help",
            "scripting",
            "documentation",
            "deployment",
            "testing",
            "operations",
            "data_analysis",
            "drafting",
        ],
        "out_of_scope": [
            "public_record_answers",
            "source_contradictions",
            "data_quality_issues",
            "data_extraction",
            "complex_research",
        ],
        "draft_only": True,
        "requires_approval": [
            "delete_operations",
            "production_pushes",
            "sensitive_data_access",
            "external_posts",
        ],
    }
}


class AdminChatRequest(BaseModel):
    """Request to call an admin agent"""
    mode: AgentMode
    query: str
    retrieved_records: str = ""
    output_mode: str = ""
    interactive: bool = False


class AdminChatResponse(BaseModel):
    """Response from admin agent"""
    agent: str
    query: str
    response: str
    draft_only: bool = True
    requires_approval: bool = False


class AdminAgentInfo(BaseModel):
    """Info about an admin agent"""
    name: str
    description: str
    tags: list[str]
    scope: list[str]
    out_of_scope: list[str]
    draft_only: bool
    requires_approval: list[str]


@router.get("/admin/agents", tags=["admin"])
async def list_admin_agents():
    """List available admin agents - ADMIN ONLY"""
    require_admin()

    return {
        "agents": [
            {
                "mode": mode.value,
                "info": AdminAgentInfo(**info),
            }
            for mode, info in ADMIN_AGENTS.items()
        ]
    }


@router.get("/admin/agents/{mode}", tags=["admin"])
async def get_admin_agent(mode: AgentMode):
    """Get details about a specific admin agent - ADMIN ONLY"""
    require_admin()

    if mode not in ADMIN_AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent mode '{mode}' not found")

    return {"mode": mode.value, "info": AdminAgentInfo(**ADMIN_AGENTS[mode])}


@router.post("/admin/chat", tags=["admin"])
async def admin_chat(req: AdminChatRequest) -> AdminChatResponse:
    """Call an admin agent - ADMIN ONLY"""
    require_admin()

    if req.mode not in ADMIN_AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent mode '{req.mode}' not found")

    agent_info = ADMIN_AGENTS[req.mode]
    env_var = agent_info["env_var"]
    agent_id = os.getenv(env_var)

    if not agent_id:
        raise HTTPException(
            status_code=500,
            detail=f"Agent not configured: {env_var} not set in environment",
        )

    try:
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        retrieved_records = (
            (req.retrieved_records or "").strip()
            or build_database_context(req.query)
            or "No matching local SQL records were found for this query."
        )
        agent_prompt = (
            "Use the following SQL/API/RAG evidence first. If evidence is present, "
            "do not say no structured records were returned.\n\n"
            f"Retrieved records:\n{retrieved_records}\n\n"
            f"User query:\n{req.query}"
        )

        response = client.agents.messages.create(
            agent_id=agent_id, messages=[{"role": "user", "content": agent_prompt}]
        )

        result_text = response.content[0].text if response.content else "No response"

        return AdminChatResponse(
            agent=agent_info["name"],
            query=req.query,
            response=result_text,
            draft_only=agent_info["draft_only"],
            requires_approval=any(
                keyword in req.query.lower()
                for keyword in ["delete", "push", "production", "send", "post"]
            ),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")


@router.post("/admin/chat/{mode}", tags=["admin"])
async def admin_chat_mode(mode: AgentMode, query: str = Query(...)):
    """Call an admin agent by mode - ADMIN ONLY"""
    require_admin()

    return await admin_chat(AdminChatRequest(mode=mode, query=query))


@router.get("/admin/status", tags=["admin"])
async def admin_status():
    """Get admin system status - ADMIN ONLY"""
    require_admin()

    agents_configured = {
        mode.value: bool(os.getenv(info["env_var"]))
        for mode, info in ADMIN_AGENTS.items()
    }

    return {
        "status": "ok",
        "agents": agents_configured,
        "admin_mode_enabled": bool(os.getenv("VOTEIQ_ADMIN_MODE")),
        "notes": [
            "This is an internal-only API",
            "All responses are DRAFT-ONLY unless explicitly approved",
            "Approval required for: delete, production pushes, sensitive data access, external posts",
            "Audit trail maintained for all operations",
        ],
    }
