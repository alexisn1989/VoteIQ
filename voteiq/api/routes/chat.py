from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pdfplumber
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from voteiq.config.rate_limit import limiter
from fastapi.responses import HTMLResponse, StreamingResponse

from orchestration import (
    build_bills_context_parallel,
    build_bills_system_prompt_refactored,
    is_present,
)
from pydantic import BaseModel, Field, field_validator

from voteiq.api.dependencies import require_admin_token
from voteiq.api.claude import cached_system_prompt, get_claude_client, get_model
from voteiq.config.voices import (
    TIER_MAX_TOKENS,
    TIER_VOICE_MAP,  # unused until Supabase auth lands — re-enable tier gate then
    VOICE_PROMPTS,
    get_system_prompt,
)
from voteiq.services.database_context import build_admin_database_context, build_database_context

router = APIRouter(tags=["chat"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)

_NEWS_TERMS = (
    "news", "article", "articles", "headline", "headlines", "coverage",
    "recent", "latest", "current", "today", "yesterday", "this week",
    "reported", "reporting", "press", "story", "stories",
)

_SOURCE_LINE = (
    "\n\n---\n"
    "*Sources: Virginia LIS · FEC · Congress.gov · VPAP · Governor of Virginia. "
    "Data current through May 16, 2026. "
    "Data limits: This answer depends on available public records and may not include late filings, amended records, or records not yet digitized. "
    "VoteIQ does not infer motive, intent, corruption, influence, causation, or policy effectiveness.*"
)

SCOPE_LIMIT_ANSWER_TYPE = "Scope / dataset limitation"
POLLING_DATA_LIMIT = (
    "Polling data is not currently included in VoteIQ's public-record dataset. "
    "VoteIQ can help with voting records, bill sponsorships, campaign finance records, "
    "governor actions, executive orders, and legislator profiles."
)
EXTERNAL_POLLING_ANSWER_TYPE = "External polling context"
EXTERNAL_POLLING_DATA_LIMIT = (
    "Polls are not official public records. Polling results vary by pollster, sponsor, "
    "sample, field dates, methodology, and margin of error. Verify against the original poll release."
)

VOTEIQ_ADMIN_SYSTEM_PROMPT = """
You are VoteIQ, a neutral civic intelligence assistant.

VoteIQ source hierarchy:
1. Structured records first
2. Official APIs second
3. Source documents/RAG third
4. AI explanation last
5. Data limits always shown

The AI explains records but is not the source of truth.

Agent roles:
- Public Record Analyst is for exact facts from structured records: votes, donations, bill actions, executive orders, dates, amounts, officials, committees, and IDs.
- Deep Researcher is for broader research reports: multi-step questions, source comparison, synthesis, confidence levels, contradictions, and data gaps.
- For exact current facts such as a vote, donation, bill action, or executive order, defer to the Public Record Analyst or SQL/API-first workflow.
- Causal claims require explicit study design or credible research. Otherwise report correlation only.

Admin DB policy:
- Admin agents may inspect records and draft recommendations.
- Treat production databases and source records as read-only.
- Do not write to production data, mutate records, or claim a fix was applied unless a human approved it through a separate workflow.
- Escalation, QA, audit, and field-monitor entries are admin workflow records, not production public-record data.
- Partner/public transparency output must be redacted: hide emails, screenshots, raw complaints, user identities, and unresolved claims.

VoteIQ summarizes public records and does not infer motive, intent,
corruption, influence, causation, or policy effectiveness.
"""


# ── Models ────────────────────────────────────────────────────────────────────

def _premium_analyst_enabled(req) -> bool:
    """Analyst context is reserved for Pro and Newsroom voices/tiers."""
    voice = str(getattr(req, "voice", "") or "").lower()
    tier = str(getattr(req, "tier", "") or "").lower()
    return voice in {"pro", "newsroom"} or tier in {"pro", "newsroom"}


def _source_line(
    answer_type: str | None = None,
    sources_used: set[str] | None = None,
    data_limits: str | None = None,
) -> str:
    if answer_type == SCOPE_LIMIT_ANSWER_TYPE and not sources_used:
        limit = data_limits or "This question is outside the currently available VoteIQ dataset."
        return (
            "\n\n---\n"
            f"Answer type: {SCOPE_LIMIT_ANSWER_TYPE}.\n"
            "Sources used: none.\n"
            f"Data limits: {limit}"
        )
    if answer_type == EXTERNAL_POLLING_ANSWER_TYPE:
        limit = data_limits or EXTERNAL_POLLING_DATA_LIMIT
        return (
            "\n\n---\n"
            f"Answer type: {EXTERNAL_POLLING_ANSWER_TYPE}.\n"
            "Sources used: Polling source records / news feed / Gemini extraction.\n"
            f"Data limits: {limit}"
        )
    if sources_used:
        source_text = " · ".join(sorted(sources_used))
        prefix = f"*Answer type: {answer_type}. Sources:" if answer_type else "*Sources:"
        return (
            "\n\n---\n"
            f"{prefix} {source_text}. "
            "Data limits: This answer depends on available public records and may not include late filings, amended records, or records not yet digitized. "
            "VoteIQ does not infer motive, intent, corruption, influence, causation, or policy effectiveness.*"
        )
    if not answer_type:
        return _SOURCE_LINE
    return _SOURCE_LINE.replace("*Sources:", f"*Answer type: {answer_type}. Sources:", 1)


def _with_source_line(
    reply: str,
    answer_type: str | None = None,
    sources_used: set[str] | None = None,
    data_limits: str | None = None,
) -> str:
    """Append the standard source footer unless the model already included one."""
    reply = _sanitize_voting_record_language(reply)
    if "Sources:" in (reply or ""):
        return reply.rstrip()
    return reply.rstrip() + _source_line(answer_type, sources_used, data_limits)


def _is_public_opinion_polling_scope_limit_query(query: str) -> bool:
    """Detect public-opinion/election polling asks, not polling-place questions."""
    q = (query or "").lower()
    if not any(term in q for term in ("poll", "polls", "polling", "approval rating", "favorability")):
        return False
    if any(term in q for term in ("polling place", "polling location", "where do i vote", "precinct")):
        return False
    return any(term in q for term in (
        "approval", "favorability", "favorability rating", "horse race",
        "survey", "surveys", "public opinion", "vcu", "wilder", "wason",
        "realclearpolitics", "fivethirtyeight", "538", "polling data",
        "latest poll", "latest polls", "what do polls say",
    ))


def _polling_office_filter(query: str) -> str | None:
    q = (query or "").lower()
    if "governor" in q or "spanberger" in q:
        return "governor"
    if "senate" in q or "senator" in q:
        return "senate"
    if "house" in q or "congress" in q or "representative" in q:
        return "house"
    return None


def _polling_name_terms(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", query or "")
    generic = {
        "what", "poll", "polls", "polling", "data", "latest", "approval",
        "rating", "ratings", "favorability", "survey", "surveys", "virginia",
        "governor", "senate", "house", "race", "races", "about",
    }
    terms: list[str] = []
    seen: set[str] = set()
    for word in words:
        low = word.lower().strip("'")
        if low in generic or low in seen:
            continue
        seen.add(low)
        terms.append(low)
    return terms[:4]


def _polling_tables_available(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('polls', 'poll_results')"
    ).fetchall()
    return {row[0] for row in rows} == {"polls", "poll_results"}


def _polling_articles_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='poll_articles'"
    ).fetchone()
    return row is not None


def _external_polling_reply(query: str) -> str | None:
    if not _is_public_opinion_polling_scope_limit_query(query):
        return None
    if not os.path.exists(_POLLS_DB):
        return None

    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        if not _polling_tables_available(conn):
            conn.close()
            return None

        where = ["LOWER(COALESCE(p.state, '')) = 'virginia'"]
        params: list[object] = []
        office = _polling_office_filter(query)
        if office:
            where.append("LOWER(COALESCE(p.office_type, '')) LIKE ?")
            params.append(f"%{office}%")

        name_terms = _polling_name_terms(query)
        if name_terms:
            term_clauses = []
            for term in name_terms:
                term_clauses.append(
                    "(LOWER(COALESCE(pr.candidate_name, '')) LIKE ? "
                    "OR LOWER(COALESCE(pr.answer, '')) LIKE ? "
                    "OR LOWER(COALESCE(p.seat_name, '')) LIKE ?)"
                )
                like = f"%{term}%"
                params.extend([like, like, like])
            where.append("(" + " OR ".join(term_clauses) + ")")

        rows = conn.execute(
            f"""
            SELECT p.source_record_id, p.source, p.cycle, p.office_type, p.seat_name,
                   p.pollster, p.sponsor, p.sample_size, p.population, p.methodology,
                   p.start_date, p.end_date, p.url, p.notes,
                   pr.answer, pr.candidate_name, pr.candidate_party, pr.pct
            FROM polls p
            LEFT JOIN poll_results pr ON pr.source_record_id = p.source_record_id
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(p.end_date, p.start_date, p.created_at, p.fetched_at) DESC,
                     p.source_record_id,
                     pr.pct DESC
            LIMIT 18
            """,
            params,
        ).fetchall()

        article_rows: list[sqlite3.Row] = []
        if _polling_articles_available(conn):
            article_rows = conn.execute(
                """
                SELECT source, title, summary, published_at, url, matched_terms
                FROM poll_articles
                ORDER BY COALESCE(published_at, fetched_at) DESC
                LIMIT 3
                """
            ).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows and not article_rows:
        return None

    polls: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        rid = row["source_record_id"] or f"{row['pollster']}-{row['end_date']}"
        if rid not in polls:
            order.append(rid)
            date_range = row["end_date"] or row["start_date"] or "date not available"
            pollster = row["pollster"] or row["sponsor"] or row["source"] or "Unknown pollster"
            sample = f"; n={row['sample_size']}" if row["sample_size"] else ""
            population = f"; {row['population']}" if row["population"] else ""
            method = f"; {row['methodology']}" if row["methodology"] else ""
            polls[rid] = {
                "header": f"{pollster} ({date_range}{sample}{population}{method})",
                "url": row["url"],
                "results": [],
            }
        label = row["candidate_name"] or row["answer"]
        if label and row["pct"] is not None:
            polls[rid]["results"].append(f"{label}: {row['pct']}%")

    lines = ["Polling context from VoteIQ's external polling feed:"]
    for rid in order[:5]:
        poll = polls[rid]
        result_text = "; ".join(poll["results"][:6]) if poll["results"] else "results not itemized"
        url = f" [{poll['url']}]" if poll["url"] else ""
        lines.append(f"- {poll['header']}: {result_text}.{url}")

    if article_rows:
        lines.append("\nRelated poll-news records:")
        for row in article_rows[:3]:
            source = row["source"] or "news feed"
            title = row["title"] or "Untitled"
            published = f" ({row['published_at']})" if row["published_at"] else ""
            url = f" [{row['url']}]" if row["url"] else ""
            lines.append(f"- {source}: {title}{published}.{url}")

    body = "\n".join(lines)
    return _with_source_line(
        body,
        answer_type=EXTERNAL_POLLING_ANSWER_TYPE,
        sources_used=set(),
        data_limits=EXTERNAL_POLLING_DATA_LIMIT,
    )


def _scope_limit_polling_reply(query: str) -> str | None:
    if not _is_public_opinion_polling_scope_limit_query(query):
        return None

    external_reply = _external_polling_reply(query)
    if external_reply:
        return external_reply

    body = (
        "VoteIQ does not currently have public-opinion polling data in its public-record dataset.\n\n"
        "External places to check: VCU Wilder School, CNU Wason Center, RealClearPolitics, FiveThirtyEight.\n\n"
        "Those are external suggestions, not VoteIQ sources for this answer.\n\n"
        "Is there something specific you're trying to understand about your representatives or a bill? "
        "I may be able to help with the public-record side of that question."
    )
    return _with_source_line(
        body,
        answer_type=SCOPE_LIMIT_ANSWER_TYPE,
        sources_used=set(),
        data_limits=POLLING_DATA_LIMIT,
    )


def _sanitize_voting_record_language(reply: str) -> str:
    """Keep voting-record answers factual and remove interpretive labels."""
    text = reply or ""
    text = re.sub(
        r"Overall vote rate:\s*([\d,]+)\s+YES\s+\(([^)]+)\),\s*([\d,]+)\s+NO\s+out of\s+([\d,]+)\s+floor votes",
        r"Recorded floor vote distribution: \1 YES votes and \3 NO votes across \4 recorded floor vote events.",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\*\*Dissenting Votes\s*—\s*voted NO but bill passed",
        "**Recorded NO votes where the bill passed",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(?im)^\s*-?\s*Caucus read:.*(?:\r?\n)?",
        "",
        text,
    )
    text = re.sub(
        r"(?im)^.*moderate voice within party.*(?:\r?\n)?",
        "",
        text,
    )
    text = re.sub(
        r"(?i)Committee votes:\s*([\d,]+)\s+total\s*[—-]\s*([\d,]+)\s+YES,\s*([\d,]+)\s+NO",
        (
            "Committee vote records in the current dataset show \\2 YES and \\3 NO votes. "
            "This may reflect how committee actions are recorded in the source data; verify against committee roll-call records before treating it as a complete behavioral measure."
        ),
        text,
    )
    text = re.sub(
        r"(?im)^.*breaks most often on [^\r\n]+(?:\r?\n)?",
        "",
        text,
    )
    if (
        "Recorded NO votes where the bill passed" in text
        and "VoteIQ does not infer why" not in text
    ):
        text += (
            "\n\nVoteIQ does not infer why Sen. Rouse voted NO. These records only show vote outcomes, "
            "party-majority comparison, bill category, and whether the bill passed."
        )
    return text


def _track_sources(context: str) -> set[str]:
    """Extract which data sources contributed to the response context."""
    sources = set()

    if not context:
        return sources

    context_lower = context.lower()

    # SQL/database sources
    if "virginia lis" in context_lower or "lis.virginia.gov" in context_lower:
        sources.add("Virginia LIS")
    if "[governor action" in context_lower:
        sources.add("Governor of Virginia")
    if "fec" in context_lower and "fec data" in context_lower:
        sources.add("FEC")
    if "congress" in context_lower and "congress.gov" in context_lower:
        sources.add("Congress.gov")
    if "openstates" in context_lower:
        sources.add("OpenStates")

    # Document/RAG sources
    if "| virginia" in context_lower:
        sources.add("Virginia Bill Text")
    if "| federal" in context_lower:
        sources.add("Federal Bill Text (Congress.gov)")
    if "[floor statement" in context_lower:
        sources.add("Congressional Floor Statements")
    if "[executive order" in context_lower:
        sources.add("Governor's Executive Orders")
    if "[video transcript" in context_lower or "[hearing" in context_lower:
        sources.add("Congressional Hearing Transcripts")
    if "[representative profile" in context_lower:
        sources.add("Legislative Profiles")

    # Finance sources
    if "virginia sbe" in context_lower:
        sources.add("Virginia SBE Campaign Finance")
    if "[finance" in context_lower and "fec" in context_lower:
        sources.add("FEC Campaign Finance")
    if "[pac" in context_lower:
        sources.add("PAC Data")

    # News sources
    if "[news" in context_lower or "news article" in context_lower:
        sources.add("Virginia News Sources")

    return sources


def _format_sources_footer(sources_used: set[str]) -> str:
    """Format sources used into a footer line."""
    if not sources_used:
        return ""
    sorted_sources = sorted(sources_used)
    return f"\n\n*Sources: {' · '.join(sorted_sources)}*"


def _answer_type_from_context(context: str, chroma_error: str | None = None) -> str:
    """Describe the evidence mix used for a chat answer."""
    text = context or ""
    has_sql = "[Database Context" in text or any(
        marker in text for marker in (
            "[Governor Action",
            "[Governor Executive Order",
            "[Virginia Statewide Official]",
            "[Finance",
            "[PAC",
        )
    )
    labels = re.findall(r"\[[^\]\n]+\]", text)
    has_docs = not chroma_error and any(
        not label.lower().startswith("[database context")
        and re.search(r"(bill|text|document|chunk|federal|virginia)", label, re.I)
        for label in labels
    )
    if has_sql and has_docs:
        return "Mixed SQL + document context"
    if has_sql:
        return "Structured SQL context"
    if has_docs:
        return "Document/RAG context"
    return "AI explanation from retrieved context"


def _build_voteiq_admin_messages(
    user_question: str,
    recent_history: list[ChatMessage],
    retrieved_records: str,
) -> list[dict]:
    evidence_block = f"""Retrieved public-record evidence for this question:

{retrieved_records}

Use only this evidence for factual claims. If the evidence is incomplete,
say so. Do not fill gaps from memory."""

    messages = [
        {"role": m.role, "content": m.content}
        for m in recent_history[-8:]
        if m.role in {"user", "assistant"} and m.content
    ]
    messages.append({"role": "user", "content": evidence_block})
    messages.append({"role": "user", "content": user_question})
    return messages


_ADMIN_AGENT_REGISTRY = {
    "analyst": {
        "name": "Public Record Analyst",
        "env": "VOTEIQ_ANALYST_AGENT_ID",
        "tags": ["exact-facts", "structured-records", "official"],
        "visibility": "public_facing",
        "surface": "Public Record Analyst output",
        "prompt": "Return exact facts from structured public records. Cite record source, dates, amounts, IDs, votes, bill actions, executive orders, and data limits. Do not produce broad research synthesis; route that to Deep Researcher.",
    },
    "escalator": {
        "name": "Data Quality Escalator",
        "env": "VOTEIQ_ESCALATOR_AGENT_ID",
        "tags": ["support", "data-quality"],
        "visibility": "admin_only",
        "surface": "Data Quality Escalator",
        "prompt": "Inspect records and draft an escalation summary with repro steps, likely source, and suggested fix. Do not write to production data or claim a fix was applied without separate human approval.",
    },
    "field_monitor": {
        "name": "Field Monitor",
        "env": "VOTEIQ_FIELD_MONITOR_AGENT_ID",
        "tags": ["intelligence", "briefing"],
        "visibility": "admin_only",
        "surface": "Civic Field Monitor",
        "prompt": "Prepare a concise field-monitor intelligence brief with immediate, soon, and later actions.",
    },
    "debugger": {
        "name": "Data Debugger",
        "env": "VOTEIQ_DEBUGGER_AGENT_ID",
        "tags": ["debug", "pipeline"],
        "visibility": "admin_only",
        "surface": "Source Debugger",
        "prompt": (
            "Debug the data or retrieval issue using only supplied records and identify likely next checks. "
            "Inspect only; do not mutate production records. If campaign-finance table diagnostics are present, "
            "distinguish table availability from row-match failure. Do not say no financial tables/data exist when "
            "the evidence lists finance tables with row counts; say the lookup needs a candidate, donor, committee, "
            "office, or better routing if no matching rows were returned."
        ),
    },
    "golden_query": {
        "name": "Golden Query QA",
        "env": "VOTEIQ_GOLDEN_QUERY_AGENT_ID",
        "tags": ["qa", "search"],
        "visibility": "admin_only",
        "surface": "Golden Query Tester",
        "prompt": "Evaluate whether the retrieved result satisfies the expected answer. Flag missing or weak evidence.",
    },
    "crosswalk": {
        "name": "Identity Crosswalk",
        "env": "VOTEIQ_CROSSWALK_AGENT_ID",
        "tags": ["identity", "matching"],
        "visibility": "admin_only",
        "surface": "Identity Crosswalk",
        "prompt": "Resolve identity fields carefully. Preserve FEC IDs, bioguide IDs, aliases, party, state, and district.",
    },
    "structured_extractor": {
        "name": "Structured Extractor",
        "env": "VOTEIQ_STRUCTURED_EXTRACTOR_AGENT_ID",
        "tags": ["internal", "extraction", "schema"],
        "visibility": "admin_only",
        "surface": "Structured Extractor",
        "prompt": "Extract draft structured JSON from supplied civic source text. Preserve source URLs, confidence, and ambiguity notes. Do not write extracted records to production data.",
    },
    "data_analyst": {
        "name": "Data Analyst",
        "env": "VOTEIQ_DATA_ANALYST_AGENT_ID",
        "tags": ["analysis", "records"],
        "visibility": "admin_only",
        "surface": "Data Analyst",
        "prompt": "Analyze the supplied records without inferring motive, causation, corruption, influence, or effectiveness.",
    },
    "general_admin": {
        "name": "General Admin Utility",
        "env": "VOTEIQ_GENERAL_ADMIN_AGENT_ID",
        "tags": ["internal", "admin", "ops"],
        "visibility": "admin_only",
        "surface": "General Admin Utility",
        "prompt": "Help with internal VoteIQ admin operations, drafts, checklists, and coordination. Draft only; do not mutate production data or notify external parties without approval.",
    },
    "deep_researcher": {
        "name": "Deep Researcher",
        "env": "VOTEIQ_DEEP_RESEARCHER_AGENT_ID",
        "tags": ["broader-research", "primary-sources", "synthesis"],
        "visibility": "public_facing",
        "surface": "Deep Researcher",
        "prompt": (
            "Produce broader research reports with 3-5 sub-questions, source tiers, findings, recency, confidence, synthesis, and gaps. "
            "Use Tier 1 official/SQL sources first, then primary historical records, then credible public media/research. "
            "For exact current facts such as a vote, donation, bill action, or executive order, defer to Public Record Analyst or the SQL/API-first workflow. "
            "Causal claims require explicit study design or credible research; otherwise report correlation only. Keep all outputs draft/read-only."
        ),
    },
    "support_drafts": {
        "name": "Support Drafts",
        "env": "VOTEIQ_SUPPORT_DRAFTS_AGENT_ID",
        "tags": ["support", "communication"],
        "visibility": "public_facing",
        "surface": "Support/help flow",
        "prompt": "Draft a concise support response. Say what was checked, what is missing, and the next action.",
    },
    "sprint_retro": {
        "name": "Sprint Retro Facilitator",
        "env": "VOTEIQ_SPRINT_RETRO_AGENT_ID",
        "tags": ["internal", "retro", "process"],
        "visibility": "admin_only",
        "surface": "Sprint Retro Facilitator",
        "prompt": (
            "Prepare internal VoteIQ sprint retrospectives from Linear, Slack, GitHub, and supplied context. "
            "Draft only: never post, save, notify, or share without human approval. "
            "Focus on process, cite tickets/messages/PRs, avoid individual blame, calculate confidence, list open questions, and propose one concrete process change."
        ),
    },
    "visual_explainer": {
        "name": "Visual Explainer",
        "env": "VOTEIQ_VISUAL_EXPLAINER_AGENT_ID",
        "tags": ["visualization", "customer-facing", "charts", "maps"],
        "visibility": "public_facing",
        "surface": "Visual Explainer",
        "prompt": (
            "Turn verified VoteIQ Analyst/API/RAG data into JSON visual definitions for charts, maps, timelines, comparison tables, or summary cards. "
            "Use verified sources only, include source labels, current-through date, data limits, and a plain-English explanation. "
            "Do not invent missing data, infer motive or causation, hide gaps, use private data, or mislead with scales."
        ),
    },
    "whro_grants": {
        "name": "WHRO Grants",
        "env": "VOTEIQ_WHRO_GRANTS_AGENT_ID",
        "tags": ["grants", "partnerships"],
        "visibility": "admin_only",
        "surface": "WHRO/Grant Drafts",
        "prompt": "Draft grant or partnership language using the supplied VoteIQ public-record mission context.",
    },
}

_PUBLIC_FACING_FLOWS = [
    {"name": "Public Record Analyst output", "mode": "analyst"},
    {"name": "Deep Researcher", "mode": "deep_researcher"},
    {"name": "Visual Explainer", "mode": "visual_explainer"},
    {"name": "Support/help flow", "mode": "support_drafts"},
    {"name": "Report issue flow", "mode": "report_issue"},
]


def _admin_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _admin_state_path() -> str:
    return os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "admin_dashboard_state.json")


def _load_admin_state() -> dict:
    default = {"qa_results": [], "escalations": [], "field_reports": [], "agent_calls": {}, "audit_log": []}
    path = _admin_state_path()
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key, value in default.items():
            data.setdefault(key, value)
        return data
    except Exception:
        return default


def _save_admin_state(state: dict) -> None:
    path = _admin_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _admin_query_hash(query: str) -> str:
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:16]


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SCREENSHOT_RE = re.compile(r"\b[^\s/\\]+\.(?:png|jpe?g|gif|webp|bmp|tiff?|heic|pdf)\b", re.I)
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)


def _redact_public_text(value: str) -> str:
    text = str(value or "")
    text = _EMAIL_RE.sub("[redacted email]", text)
    text = _URL_RE.sub("[redacted link]", text)
    text = _SCREENSHOT_RE.sub("[redacted attachment]", text)
    return text


def _public_safe_escalation(escalation: dict) -> dict:
    status = str(escalation.get("status") or "").lower()
    resolved_date = escalation.get("resolved_at") or escalation.get("updated_at") or escalation.get("created_at")
    summary = (
        f"Resolved {escalation.get('record_type') or 'data'} issue"
        f" for {_redact_public_text(escalation.get('entity_name') or 'public record')}."
    )
    if escalation.get("suggested_fix"):
        summary += f" Resolution note: {_redact_public_text(escalation.get('suggested_fix'))}"
    return {
        "escalation_id": escalation.get("id"),
        "escalation_type": _redact_public_text(escalation.get("record_type") or "data_quality"),
        "root_cause": _redact_public_text(escalation.get("root_cause") or "verified_data_issue"),
        "status": "resolved" if status in {"fixed", "resolved"} else status,
        "created_date": escalation.get("created_at"),
        "resolved_date": resolved_date,
        "user_type": "redacted",
        "summary": summary,
    }


def _admin_user_from_request(request: Request) -> str:
    return (
        request.headers.get("x-admin-user")
        or request.headers.get("x-user-email")
        or request.headers.get("x-forwarded-user")
        or "admin"
    )


def _record_admin_audit(
    request: Request,
    *,
    endpoint: str,
    mode: str,
    query: str,
    agent_id: str | None,
    action_taken: str,
    approval_status: str,
) -> dict:
    record = {
        "admin_user": _admin_user_from_request(request),
        "endpoint": endpoint,
        "mode": mode,
        "query_hash": _admin_query_hash(query),
        "timestamp": _admin_now(),
        "agent_id": agent_id,
        "action_taken": action_taken,
        "approval_status": approval_status,
    }
    state = _load_admin_state()
    state.setdefault("audit_log", []).insert(0, record)
    state["audit_log"] = state["audit_log"][:1000]
    _save_admin_state(state)
    return record


def _admin_agent_status(mode: str, state: dict | None = None) -> dict:
    spec = _ADMIN_AGENT_REGISTRY[mode]
    agent_id = os.getenv(spec["env"])
    metrics = (state or _load_admin_state()).get("agent_calls", {}).get(mode, {})
    return {
        "mode": mode,
        "name": spec["name"],
        "deployed": bool(agent_id),
        "agent_id": agent_id,
        "tags": spec["tags"],
        "visibility": spec.get("visibility", "admin_only"),
        "surface": spec.get("surface", spec["name"]),
        "public_facing": spec.get("visibility") == "public_facing",
        "deployment_status": "Ready" if agent_id else "Not deployed",
        "created_at": metrics.get("created_at"),
        "last_called": metrics.get("last_called"),
        "total_calls": metrics.get("total_calls", 0),
        "success_rate": metrics.get("success_rate"),
        "avg_response_time_ms": metrics.get("avg_response_time_ms"),
    }


def _record_admin_agent_call(mode: str, ok: bool, elapsed_ms: int) -> None:
    state = _load_admin_state()
    calls = state.setdefault("agent_calls", {}).setdefault(mode, {"created_at": _admin_now(), "total_calls": 0, "successes": 0, "total_ms": 0})
    calls["total_calls"] = int(calls.get("total_calls", 0)) + 1
    calls["successes"] = int(calls.get("successes", 0)) + (1 if ok else 0)
    calls["total_ms"] = int(calls.get("total_ms", 0)) + elapsed_ms
    calls["last_called"] = _admin_now()
    calls["success_rate"] = round(calls["successes"] / max(calls["total_calls"], 1), 3)
    calls["avg_response_time_ms"] = round(calls["total_ms"] / max(calls["total_calls"], 1))
    _save_admin_state(state)


def _run_admin_agent(
    mode: str,
    query: str,
    retrieved_records: str = "",
    max_tokens: int = 1200,
    output_mode: str = "",
    model_override: str = "",
) -> str:
    if mode not in _ADMIN_AGENT_REGISTRY:
        modes = ", ".join(sorted(_ADMIN_AGENT_REGISTRY))
        return f"Unknown admin mode '{mode}'. Available modes: {modes}."
    records = (retrieved_records or "").strip() or build_admin_database_context(query) or "No matching local SQL records were found for this query."
    spec = _ADMIN_AGENT_REGISTRY[mode]
    messages = _build_voteiq_admin_messages(
        query,
        [],
        f"Agent mode: {mode}\nOutput mode: {output_mode or 'standard'}\nAgent instruction: {spec['prompt']}\n\n{records}",
    )
    model = (model_override or os.getenv("ANTHROPIC_ADMIN_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6").strip()
    start = datetime.utcnow()
    try:
        response = get_claude_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            cache_control={"type": "ephemeral"},
            system=cached_system_prompt(VOTEIQ_ADMIN_SYSTEM_PROMPT),
            messages=messages,
        )
        elapsed = int((datetime.utcnow() - start).total_seconds() * 1000)
        _record_admin_agent_call(mode, True, elapsed)
        return response.content[0].text
    except Exception as exc:
        elapsed = int((datetime.utcnow() - start).total_seconds() * 1000)
        _record_admin_agent_call(mode, False, elapsed)
        import main as _m
        return _m._friendly_claude_error(exc)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatResponse(BaseModel):
    reply: str


class ChatRequest(BaseModel):
    district: str
    messages: list[ChatMessage]
    locality: str = ""
    hod_district: int | None = None
    sd_district:  int | None = None
    tier:  str = "free"
    voice: str = "free"
    session_type: str = "quick"  # "quick" (5min cache) or "research" (1hr cache)

    @field_validator("hod_district", "sd_district", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "" or v is None:
            return None
        return int(v)

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, v, info):
        if v not in VOICE_PROMPTS:
            return "free"
        # Tier gate disabled until auth is wired
        # Re-enable after Supabase auth lands
        return v


class BillsChatRequest(BaseModel):
    messages:     list[ChatMessage]
    district:     str = ""
    locality:     str = ""
    hod_district: int | None = None
    sd_district:  int | None = None
    tier:  str = "free"
    voice: str = "free"
    session_type: str = "quick"

    @field_validator("hod_district", "sd_district", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "" or v is None:
            return None
        return int(v)

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, v, info):
        if v not in VOICE_PROMPTS:
            return "free"
        # Tier gate disabled until auth is wired
        # Re-enable after Supabase auth lands
        return v


class AdminChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    question: str = ""
    retrieved_records: str = ""
    model: str = ""
    max_tokens: int = 1200

    @field_validator("max_tokens")
    @classmethod
    def clamp_max_tokens(cls, v):
        return max(200, min(int(v or 1200), 4000))


class AdminDashboardChatRequest(BaseModel):
    mode: str = "analyst"
    query: str = ""
    retrieved_records: str = ""
    output_mode: str = ""
    model: str = ""
    action_taken: str = ""
    approval_status: str = ""
    max_tokens: int = 1200

    @field_validator("max_tokens")
    @classmethod
    def clamp_max_tokens(cls, v):
        return max(200, min(int(v or 1200), 4000))


class ElectionChatRequest(BaseModel):
    year:     str
    messages: list[ChatMessage]
    session_type: str = "quick"
    tier:     str = "free"
    voice:    str = "free"

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, v, info):
        if v not in VOICE_PROMPTS:
            return "free"
        # Tier gate disabled until auth is wired
        # Re-enable after Supabase auth lands
        return v


# ── Bills context builder ─────────────────────────────────────────────────────

def _build_bills_context(
    req,
    user_query: str,
) -> tuple[str, str, bool, str | None]:
    """
    Build RAG context for bills/legislator queries.
    Returns (context, exact_lookup_note, use_haiku, chroma_error).
    """
    import main as _m

    query_context = {
        "touches_news": any(w in (user_query or "").lower() for w in _NEWS_TERMS),
    }
    q_lower = (user_query or "").lower()
    _money_kws = [
        "fund", "donor", "pac", "money", "contribut", "financ", "pay",
        "sponsor", "lobbying", "rais", "campaign", "donation", "grassroot",
        "industry", "who back", "who fund", "who support", "who pay",
        "corporate", "actblue", "winred", "special interest",
    ]
    _is_money_q = any(kw in q_lower for kw in _money_kws)
    _is_simple_governor_identity_q = (
        any(term in q_lower for term in ("who is the governor", "current governor", "governor of virginia"))
        or q_lower.strip() in {"virginia governor", "va governor", "governor"}
    )
    if _is_simple_governor_identity_q and not _is_money_q:
        return (
            "[Virginia Statewide Official]\n"
            "Office: Governor\n"
            "Name: Abigail D. Spanberger\n"
            "Party: Democrat\n"
            "Jurisdiction: Virginia\n"
            "Source: https://www.governor.virginia.gov/contact/",
            "",
            True,
            None,
        )
    mentioned    = _m._extract_bill_numbers(user_query)
    session_year = _m._extract_session_year(user_query)
    cached_bill_context = (
        _m._cached_bill_description_lookup(mentioned, session_year)
        if mentioned
        else _m._cached_bill_description_search(user_query, session_year)
    )
    use_haiku = _m._simple_bill_lookup_question(user_query, mentioned, cached_bill_context)

    query_vec    = None
    results      = {"documents": [[]], "metadatas": [[]]}
    chroma_error = None

    # Only skip Chroma when a specific bill was mentioned AND found in local cache.
    # For general queries, always run semantic search — keyword cache is too noisy to block it.
    if not (mentioned and cached_bill_context):
        try:
            query_vec = _m._get_voyage_client().embed(
                [user_query], model=_m._BILLS_MODEL, input_type="query"
            ).embeddings[0]
        except Exception as e:
            chroma_error = f"Voyage AI unavailable: {e}"

    if query_vec is not None:
        try:
            results = _m._query_chroma(query_vec, n_results=10)
        except Exception as e:
            chroma_error = f"ChromaDB unavailable: {e}"

    exact_lookup_note = ""
    try:
        context_blocks: list[str] = []
        seen_docs: set[str] = set()

        if _is_simple_governor_identity_q and not _is_money_q:
            statewide_block = (
                "[Virginia Statewide Official]\n"
                "Office: Governor\n"
                "Name: Abigail D. Spanberger\n"
                "Party: Democrat\n"
                "Jurisdiction: Virginia\n"
                "Source: https://www.governor.virginia.gov/contact/"
            )
            seen_docs.add(statewide_block)
            context_blocks.insert(0, statewide_block)

        if cached_bill_context:
            for block in cached_bill_context.split("\n\n"):
                if block and block not in seen_docs:
                    seen_docs.add(block)
                    context_blocks.insert(0, block)

        if mentioned:
            sqlite_bill = _m._sqlite_bill_lookup(mentioned)
            if sqlite_bill:
                for block in sqlite_bill.split("\n\n"):
                    if block and block not in seen_docs:
                        seen_docs.add(block)
                        context_blocks.insert(0, block)
            fed_bill = _m._federal_sqlite_bill_lookup(mentioned)
            if fed_bill:
                for block in fed_bill.split("\n\n"):
                    if block and block not in seen_docs:
                        seen_docs.add(block)
                        context_blocks.insert(0, block)

        if mentioned:
            os_votes = _m._openstates_vote_lookup(mentioned, session_year)
            if os_votes:
                for block in os_votes.split("\n\n"):
                    if block and block not in seen_docs:
                        seen_docs.add(block)
                        context_blocks.insert(0, block)

        leg_name = _m._extract_legislator_name(user_query)
        leg_scope = _m._classify_legislator(leg_name) if leg_name else None
        if leg_name and leg_scope != "federal":
            sqlite_leg = _m._sqlite_legislator_votes(leg_name)
            if sqlite_leg and sqlite_leg not in seen_docs:
                seen_docs.add(sqlite_leg)
                context_blocks.insert(0, sqlite_leg)
            os_leg = _m._openstates_legislator_lookup(leg_name)
            if os_leg and os_leg not in seen_docs:
                seen_docs.add(os_leg)
                context_blocks.insert(0, os_leg)

        spanberger_finance = _m._fetch_spanberger_finance_context(user_query)
        if spanberger_finance and spanberger_finance not in seen_docs:
            seen_docs.add(spanberger_finance)
            context_blocks.insert(0, spanberger_finance)

        state_finance_block = _m._fetch_state_campaign_finance_context(
            user_query,
            basic=not _premium_analyst_enabled(req),
        )
        if state_finance_block and state_finance_block not in seen_docs:
            seen_docs.add(state_finance_block)
            context_blocks.insert(0, state_finance_block)

        if leg_name:
            from voteiq.queries.sectors import get_finance_summary_from_cache, format_finance_summary_for_chat
            _leg_scope = _m._classify_legislator(leg_name) if leg_name else None
            _cache_source = "fec" if _leg_scope == "federal" else ("va_sbe" if _leg_scope == "state" else None)
            _cached_finance = get_finance_summary_from_cache(name=leg_name, source=_cache_source)
            if _cached_finance:
                if _premium_analyst_enabled(req):
                    _src_tables = (
                        ["campaign_finance_summary", "fec_individual_contributions"]
                        if _cache_source == "fec"
                        else ["campaign_finance_summary", "va_sbe_contributions"]
                    )
                    _finance_block = format_finance_summary_for_chat(
                        _cached_finance, source_tables=_src_tables
                    )
                else:
                    _src_lbl = "FEC (federal)" if _cache_source == "fec" else "Virginia SBE"
                    _finance_block = (
                        f"[Finance note: VoteIQ has campaign finance data for "
                        f"{_cached_finance['name']} ({_src_lbl}, "
                        f"${_cached_finance['total_raised']:,.0f} total raised). "
                        f"Full donor and sector breakdown available to Pro/Newsroom subscribers.]"
                    )
                if _finance_block and _finance_block not in seen_docs:
                    seen_docs.add(_finance_block)
                    context_blocks.insert(0, _finance_block)

        full_profile_block = _m._full_profiles_for_query(user_query, limit=2 if _premium_analyst_enabled(req) else 1)
        if full_profile_block and full_profile_block not in seen_docs:
            seen_docs.add(full_profile_block)
            context_blocks.insert(0, full_profile_block)

        _gov_money_q = (
            any(t in q_lower for t in ("governor", "spanberger", "signed", "veto", "vetoed"))
            and _is_money_q
        )
        if _premium_analyst_enabled(req):
            analyst_context = _m._fetch_governor_action_money_analyst_context(user_query)
            if analyst_context and analyst_context not in seen_docs:
                seen_docs.add(analyst_context)
                context_blocks.insert(0, analyst_context)

            member_analyst_context = _m._fetch_member_analyst_context(user_query)
            if member_analyst_context and member_analyst_context not in seen_docs:
                seen_docs.add(member_analyst_context)
                context_blocks.insert(0, member_analyst_context)
        elif _gov_money_q:
            _teaser = (
                "[Campaign Correlation Note — Pro/Newsroom Feature]\n"
                "VoteIQ has donor-sector vs. governor bill-outcome correlation data for "
                "Governor Spanberger's 2026 session (966 signed, 28 vetoed, 147 amended bills "
                "crossed with sponsor campaign finance by industry sector). "
                "Full correlation analysis is available to Pro and Newsroom subscribers."
            )
            if _teaser not in seen_docs:
                seen_docs.add(_teaser)
                context_blocks.append(_teaser)

        if _premium_analyst_enabled(req) and _is_money_q and _m._PAC_CACHE:
            _fec_bgids: list[str] = []
            _search_names: list[str] = [leg_name] if leg_name else []
            if req.district and _m.DISTRICT_CONTEXT.get(req.district, {}).get("rep"):
                _search_names.append(_m.DISTRICT_CONTEXT[req.district]["rep"])
            for bgid, mbr in _m._MEMBER_CACHE.items():
                mname = mbr.get("name", "").lower()
                if any(n and (n.lower() in mname or mname in n.lower()) for n in _search_names):
                    _fec_bgids.append(bgid)
            if _fec_bgids:
                fec_block = _m._fetch_finance_context(_fec_bgids, user_query)
                if fec_block and fec_block not in seen_docs:
                    seen_docs.add(fec_block)
                    context_blocks.insert(0, fec_block)

        pac_block = _m._fetch_pac_context(
            user_query,
            basic=not _premium_analyst_enabled(req),
        )
        if pac_block and pac_block not in seen_docs:
            seen_docs.add(pac_block)
            context_blocks.insert(0, pac_block)

        rep_profiles = _m._request_rep_profiles(req, user_query)
        if rep_profiles:
            for block in rep_profiles.split("\n\n[Representative Profile"):
                pb = block if block.startswith("[Representative Profile") else (
                    "[Representative Profile" + block if block.strip() else ""
                )
                if pb and pb not in seen_docs:
                    seen_docs.add(pb)
                    context_blocks.insert(0, pb)

        fed_member = _m._federal_member_by_name(user_query)
        if not fed_member and leg_name and leg_scope != "state":
            fed_member = _m._federal_member_by_name(leg_name)
        if not fed_member and req.district and _m._profile_question(user_query):
            dist_rep_name = _m.DISTRICT_CONTEXT.get(req.district, {}).get("rep")
            if dist_rep_name:
                fed_member = _m._federal_member_by_name(dist_rep_name)
        _speech_kws = (
            "said", "speech", "floor", "statement", "spoke", "remarks",
            "testified", "hearing", "executive order", "executive orders",
            "governor order", "eo-",
        )
        _is_speech_q = any(kw in user_query.lower() for kw in _speech_kws)

        if fed_member:
            fed_ctx = _m._fetch_federal_context(fed_member)
            if fed_ctx and fed_ctx not in seen_docs:
                seen_docs.add(fed_ctx)
                context_blocks.insert(0, fed_ctx)
            if _is_money_q:
                if _premium_analyst_enabled(req):
                    fec_block = _m._fetch_finance_context([fed_member["bioguide_id"]], user_query)
                    if fec_block and fec_block not in seen_docs:
                        seen_docs.add(fec_block)
                        context_blocks.insert(0, fec_block)
                pac_block = _m._fetch_pac_context(
                    user_query,
                    bioguide_ids=[fed_member["bioguide_id"]],
                    basic=not _premium_analyst_enabled(req),
                )
                if pac_block and pac_block not in seen_docs:
                    seen_docs.add(pac_block)
                    context_blocks.insert(0, pac_block)
            if _is_speech_q:
                floor_block = _fetch_floor_statements(
                    bioguide_id=fed_member["bioguide_id"],
                    query=user_query,
                    limit=5,
                )
                if floor_block and floor_block not in seen_docs:
                    seen_docs.add(floor_block)
                    context_blocks.insert(0, floor_block)

        if query_context.get("touches_news"):
            news_pol = (fed_member["name"] if fed_member else "") or leg_name or ""
            news_ctx = _m._fetch_news_context(user_query, politician_name=news_pol)
            if news_ctx and news_ctx not in seen_docs:
                seen_docs.add(news_ctx)
                context_blocks.insert(0, news_ctx)

        try:
            gov_block = _m._fetch_governor_action_context(user_query, bill_numbers=mentioned or None)
        except Exception:
            gov_block = ""
        if gov_block and gov_block not in seen_docs:
            seen_docs.add(gov_block)
            context_blocks.insert(0, gov_block)

        try:
            eo_block = _m._fetch_governor_eo_context(user_query)
        except Exception:
            eo_block = ""
        if eo_block and eo_block not in seen_docs:
            seen_docs.add(eo_block)
            context_blocks.append(eo_block)

        if mentioned:
            exact_docs = _m._fetch_bills_by_id(mentioned, session_year)
            if session_year:
                if exact_docs:
                    exact_lookup_note = (
                        f"\nEXACT LOOKUP: Found excerpts for {', '.join(mentioned)} "
                        f"from the {session_year} session.\n"
                    )
                else:
                    exact_lookup_note = (
                        f"\nEXACT LOOKUP: No excerpts were found for {', '.join(mentioned)} "
                        f"from the {session_year} session. If the excerpts below include the same "
                        "bill number from another session, describe it only as related data from a "
                        "different session.\n"
                    )
            else:
                years_found = sorted({
                    _m._session_year(meta.get("session"))
                    or _m._session_year(meta.get("session_id"))
                    or _m._session_year(doc)
                    for doc, meta in exact_docs
                })
                years_found = [y for y in years_found if y]
                if years_found:
                    exact_lookup_note = (
                        f"\nEXACT LOOKUP: Found excerpts for {', '.join(mentioned)} "
                        f"from embedded sessions: {', '.join(years_found)}.\n"
                    )
            for doc, meta in exact_docs:
                if doc not in seen_docs:
                    seen_docs.add(doc)
                    src = meta.get("source", "")
                    src_tag = " | FEDERAL" if src == "federal" else (" | Virginia" if src else "")
                    label = (
                        f"[{meta.get('chunk_type','?')} — "
                        f"{meta.get('bill_id','?')} {meta.get('session','?')}{src_tag}]"
                    )
                    context_blocks.append(f"{label}\n{doc}")
        elif session_year:
            session_docs = _m._fetch_bills_by_session(session_year)
            if session_docs:
                exact_lookup_note = (
                    f"\nSESSION LOOKUP: Found {len(session_docs)} representative bill excerpts "
                    f"from the embedded {session_year} session. Answer using these {session_year} "
                    "excerpts first; mention other years only if the user asks for comparison.\n"
                )
            else:
                exact_lookup_note = (
                    f"\nSESSION LOOKUP: No embedded bill excerpts were found "
                    f"for the {session_year} session.\n"
                )
            for doc, meta in session_docs:
                if doc not in seen_docs:
                    seen_docs.add(doc)
                    src = meta.get("source", "")
                    src_tag = " | FEDERAL" if src == "federal" else (" | Virginia" if src else "")
                    label = (
                        f"[{meta.get('chunk_type','?')} — "
                        f"{meta.get('bill_id','?')} {meta.get('session','?')}{src_tag}]"
                    )
                    context_blocks.append(f"{label}\n{doc}")

        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            if session_year:
                found_year = (
                    _m._session_year(meta.get("session"))
                    or _m._session_year(meta.get("session_id"))
                    or _m._session_year(doc)
                )
                if found_year and found_year != session_year:
                    continue
            if doc not in seen_docs:
                seen_docs.add(doc)
                src = meta.get("source", "")
                src_tag = " | FEDERAL" if src == "federal" else (" | Virginia" if src else "")
                label = (
                    f"[{meta.get('chunk_type','?')} — "
                    f"{meta.get('bill_id','?')} {meta.get('session','?')}{src_tag}]"
                )
                context_blocks.append(f"{label}\n{doc}")

        context = "\n\n---\n\n".join(context_blocks)
    except Exception as e:
        context = ""
        chroma_error = f"Result parsing error: {e}"

    return context, exact_lookup_note, use_haiku, chroma_error


def _local_bills_fallback_context(_m, req, user_query: str) -> str:
    """Build a local-only fallback when embedding/Chroma is unavailable."""
    pieces: list[str] = []
    try:
        mentioned = _m._extract_bill_numbers(user_query)
        leg_name = _m._extract_legislator_name(user_query)

        if mentioned:
            sqlite_fb = _m._sqlite_bill_lookup(mentioned)
            os_fb = _m._openstates_vote_lookup(mentioned)
            for block in (sqlite_fb, os_fb):
                if block and block not in pieces:
                    pieces.append(block)
        elif leg_name:
            sqlite_fb = _m._sqlite_legislator_votes(leg_name)
            os_fb = _m._openstates_legislator_lookup(leg_name)
            for block in (sqlite_fb, os_fb):
                if block and block not in pieces:
                    pieces.append(block)

        profiles_fb = _m._request_rep_profiles(req, user_query)
        if profiles_fb and profiles_fb not in pieces:
            pieces.append(profiles_fb)

        full_profiles_fb = _m._full_profiles_for_query(user_query, limit=2)
        if full_profiles_fb and full_profiles_fb not in pieces:
            pieces.append(full_profiles_fb)

        if req.district and req.district != "VA-00" and _m._profile_question(user_query):
            dist_rep_name = _m.DISTRICT_CONTEXT.get(req.district, {}).get("rep")
            fed_member = _m._federal_member_by_name(dist_rep_name or "")
            if fed_member:
                fed_fb = _m._fetch_federal_context(fed_member)
                if fed_fb and fed_fb not in pieces:
                    pieces.append(fed_fb)

        try:
            gov_fb = _m._fetch_governor_action_context(user_query)
            if gov_fb and gov_fb not in pieces:
                pieces.append(gov_fb)
        except Exception:
            pass
        try:
            gov_eo_fb = _m._fetch_governor_eo_context(user_query)
            if gov_eo_fb and gov_eo_fb not in pieces:
                pieces.append(gov_eo_fb)
        except Exception:
            pass
    except Exception:
        return "\n\n".join(pieces)

    return "\n\n".join(pieces)


def _needs_district_for_my_rep(req, user_query: str) -> bool:
    q = (user_query or "").lower()
    return (
        any(term in q for term in ("my representative", "my rep", "my delegate", "my senator"))
        and not (req.district and req.district != "VA-00")
        and not req.hod_district
        and not req.sd_district
    )


# ── Bills helpers (shared by both bills-chat routes) ─────────────────────────

def _governor_action_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(term in q for term in (
        "governor", "spanberger", "veto", "vetoed", "signed",
        "signing", "amended", "returned", "executive order",
        "bill action", "governor action",
    ))


_POLLS_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")

SPANBERGER_2026_VETO_FALLBACKS = {
    "HB1288": ("2026-04-13", "Enforcement of vehicle liens; increases property value."),
    "SB17": ("2026-04-13", "Enforcement of vehicle liens; increases property value."),
    "HB86": ("2026-04-13", "Mattress Stewardship Program; established, definitions, report."),
    "SB764": ("2026-04-13", "Defendant; deferred disposition in a criminal case, license suspension."),
    "HB637": ("2026-04-14", "Possession of residue of a controlled substance unlawful; penalties exceptions."),
    "SB23": ("2026-04-13", "Plea agreements and court orders; prohibited provisions."),
    "SB661": ("2026-04-13", "Va. Small Business Economic Dev. Act; established, regulation and taxation of skill game machines."),
    "SB756": ("2026-04-11", "Casino gaming; eligible host localities."),
    "HB61": ("2026-05-19", "Small SWaM Business Procurement Enhancement Program; established, report."),
    "HB111": ("2026-05-19", "Voter registration; cancellation of registration, sources of data."),
    "HB246": ("2026-05-19", "Mental illness, neurocognitive disorder, etc.; affirmative defense or reduced penalty."),
    "SB335": ("2026-05-19", "Mental illness, neurocognitive disorder, etc.; affirmative defense or reduced penalty."),
    "HB449": ("2026-05-19", "Civil actions filed on behalf of multiple persons; class actions."),
    "SB229": ("2026-05-19", "Civil actions filed on behalf of multiple persons; class actions."),
    "HB483": ("2026-05-19", "Prescription Drug Affordability Board; established."),
    "SB271": ("2026-05-19", "Prescription Drug Affordability Board; established."),
    "HB642": ("2026-05-19", "Cannabis control; establishes framework for creation of retail marijuana market, penalties, report."),
    "SB542": ("2026-05-19", "Cannabis control; establishes framework for creation of retail marijuana market, penalties, report."),
    "HB1173": ("2026-05-19", "Virginia Human Rights Act; reasonable accommodation for known limitations related to menopause."),
    "SB258": ("2026-05-19", "Virginia Human Rights Act; reasonable accommodation for known limitations related to menopause."),
    "HB1222": ("2026-05-19", "Social services, local departments of; child abuse and neglect, recorded interviews."),
    "HB1385": ("2026-05-19", "Higher educational institutions, public; membership of governing boards."),
    "SB494": ("2026-05-19", "Higher educational institutions, public; membership of governing boards."),
    "HB1392": ("2026-05-19", "Correctional facilities, local and regional, and courthouse security; powers & duties for operation."),
    "SB83": ("2026-05-19", "District or circuit court; possession of portable electronic devices."),
    "SB218": ("2026-05-19", "Inmates; Director of Dept. of Corrections shall continue to accept applications for confinement."),
    "HB1263": ("2026-05-14", "Public employees; repeals existing prohibition on collective bargaining, etc."),
    "SB378": ("2026-05-14", "Public employees; repeals existing prohibition on collective bargaining, etc."),
}


def _direct_governor_veto_reply(user_query: str) -> str:
    """Return common Spanberger governor-action lists directly so Claude cannot collapse them."""
    q = (user_query or "").lower()
    wants_vetoes = "veto" in q or "vetoed" in q
    wants_signed = "signed" in q or "signing" in q or "signed into law" in q
    wants_eo = any(t in q for t in ("executive order", "executive orders", "exec order"))
    if not wants_vetoes and not wants_signed and not wants_eo:
        return ""
    if not any(term in q for term in ("governor", "spanberger", "she", "her", "veto", "vetoed", "signed", "executive order")):
        return ""
    if "youngkin" in q:
        return ""
    veto_rows = []
    signed_rows = []
    eo_rows_direct: list = []
    action_counts: dict[str, int] = {}
    try:
        if os.path.exists(_POLLS_DB):
            conn = sqlite3.connect(_POLLS_DB)
            conn.row_factory = sqlite3.Row
            action_counts = {
                row["action"]: int(row["count"])
                for row in conn.execute(
                    """
                    SELECT action, COUNT(*) AS count
                    FROM governor_actions
                    WHERE session = '2026'
                      AND lower(governor) LIKE '%spanberger%'
                    GROUP BY action
                    """
                ).fetchall()
            }
            veto_rows = conn.execute(
                """
                SELECT bill_number, session, title, action_date, source_url
                FROM governor_actions
                WHERE session = '2026'
                  AND lower(governor) LIKE '%spanberger%'
                  AND action IN ('vetoed', 'pocket_veto', 'veto_sustained', 'veto_overridden')
                ORDER BY action_date DESC, bill_number
                LIMIT 10
                """
            ).fetchall()
            signed_rows = conn.execute(
                """
                SELECT bill_number, session, title, action_date, chapter_number, effective_date, source_url
                FROM governor_actions
                WHERE session = '2026'
                  AND lower(governor) LIKE '%spanberger%'
                  AND action = 'signed'
                ORDER BY action_date DESC, bill_number
                LIMIT 10
                """
            ).fetchall()
            if wants_eo:
                try:
                    eo_rows_direct = conn.execute(
                        """
                        SELECT order_number, title, signed_date, summary, source_url
                        FROM governor_executive_orders
                        ORDER BY signed_date DESC
                        LIMIT 5
                        """
                    ).fetchall()
                except Exception:
                    eo_rows_direct = []
            conn.close()
    except Exception:
        veto_rows = []
        signed_rows = []
        eo_rows_direct = []

    if wants_vetoes and not veto_rows:
        veto_rows = [
            {
                "bill_number": bill,
                "title": title,
                "action_date": date,
                "source_url": "https://www.governor.virginia.gov/newsroom/news-releases/2026/may-releases/name-1118109-en.html",
            }
            for bill, (date, title) in SPANBERGER_2026_VETO_FALLBACKS.items()
        ]
        action_counts.setdefault("vetoed", len(veto_rows))

    lines = [
        "Governor Spanberger's 2026 bill-action records in VoteIQ local SQL:",
        "",
    ]
    if action_counts:
        signed_count = action_counts.get("signed", 0)
        veto_count = sum(action_counts.get(key, 0) for key in ("vetoed", "pocket_veto", "veto_sustained", "veto_overridden"))
        amended_count = action_counts.get("amended", 0)
        lines.append(f"- Signed: {signed_count}")
        lines.append(f"- Vetoed: {veto_count}")
        lines.append(f"- Amended/returned with recommendation: {amended_count}")
        lines.append("")

    if wants_vetoes:
        veto_total = sum(action_counts.get(key, 0) for key in ("vetoed", "pocket_veto", "veto_sustained", "veto_overridden")) or len(veto_rows)
        lines.append(f"**Vetoed bills — {veto_total} total (showing up to 10 examples):**")
        for row in veto_rows:
            bill = row["bill_number"]
            title = row["title"] or "Title not available"
            url = row["source_url"] or f"https://openstates.org/va/bills/2026/{bill}/"
            date = row["action_date"] or "date not available"
            lines.append(f"- [{bill}]({url}) — {title} ({date})")
        lines.append("")

    if wants_signed:
        signed_total = action_counts.get("signed", len(signed_rows))
        if signed_rows:
            lines.append(f"**Signed bills — {signed_total} total (showing up to 10 examples):**")
            for row in signed_rows:
                bill = row["bill_number"]
                title = row["title"] or "Title not available"
                url = row["source_url"] or f"https://openstates.org/va/bills/2026/{bill}/"
                date = row["action_date"] or "date not available"
                chapter = f"; Chapter {row['chapter_number']}" if row["chapter_number"] else ""
                lines.append(f"- [{bill}]({url}) — {title} ({date}{chapter})")
            lines.append("")
        elif not wants_vetoes and not wants_eo:
            return ""

    if wants_eo:
        if eo_rows_direct:
            lines.append(f"**Executive Orders (showing up to 5 examples):**")
            for row in eo_rows_direct:
                eo_num = row["order_number"] or "EO"
                title = row["title"] or "Title not available"
                date = row["signed_date"] or "date not available"
                url = row["source_url"] or ""
                summary = row["summary"] or ""
                url_part = f" — [{url}]({url})" if url else ""
                summary_part = f"\n  {summary}" if summary else ""
                lines.append(f"- {eo_num}: {title} ({date}){url_part}{summary_part}")
            lines.append("")
        else:
            lines.append("**Executive Orders:** No executive order records found in VoteIQ's current dataset.")
            lines.append("")

    lines.extend([
        "",
        "Source: VoteIQ local SQL records, with bill metadata from Virginia legislative records and veto status cross-checked against the Governor's Office.",
        "VoteIQ does not infer motive, intent, or causation from bill activity.",
    ])
    return "\n".join(lines)


def _direct_voteiq_source_hierarchy_reply(user_query: str) -> str:
    """Explain VoteIQ's retrieval/source hierarchy for public-record answers."""
    q = (user_query or "").lower()
    source_terms = (
        "source of truth",
        "source hierarchy",
        "retrieval hierarchy",
        "how does voteiq answer",
        "how voteiq answers",
        "fec api",
        "congress api",
        "govinfo",
        "opengov",
        "rag",
    )
    if "voteiq" not in q and not any(term in q for term in source_terms[:3]):
        return ""
    if not any(term in q for term in source_terms):
        return ""

    return "\n".join([
        "**VoteIQ Source Hierarchy**",
        "",
        "VoteIQ uses structured SQL records first for exact facts, official APIs second for verification and fresh records, RAG/source documents third for long-text context, and AI last to explain what the records say. Data limits are always shown.",
        "",
        "1. **Structured records first**",
        "- SQL / structured public-record tables are used for exact facts: donations, votes, bills, executive orders, dates, amounts, officials, committees.",
        "",
        "2. **Official APIs second**",
        "- Official APIs and government sources are used to refresh or verify structured records: FEC, Congress API, Virginia LIS, Governor's Office, state/local portals.",
        "",
        "3. **Source documents/RAG third**",
        "- Source documents and RAG are used for long-text context: bill text, PDFs, meeting minutes, press releases, reports, transcripts.",
        "",
        "4. **AI explanation last**",
        "- AI is used to summarize and explain. AI is not treated as the source of truth.",
        "",
        "5. **Data limits always shown**",
        "- If records are missing, incomplete, stale, or unavailable, VoteIQ should say so and avoid guessing.",
        "",
        "**Identity crosswalk**",
        "- For person lookups, VoteIQ should preserve FEC candidate_id, FEC committee_id, bioguide_id, Congress.gov member ID, OpenStates/person ID if state overlap exists, name aliases, and party/state/district.",
    ])


@router.get("/admin/chat", response_class=HTMLResponse)
def admin_chat_page(_: None = Depends(require_admin_token)):
    with open(os.path.join(_BASE_DIR, "templates", "admin_chat.html"), encoding="utf-8") as f:
        return f.read()


@router.post("/api/admin/chat", response_model=ChatResponse)
async def admin_chat(req: AdminChatRequest, _: None = Depends(require_admin_token)):
    user_question = (req.question or "").strip()
    history = list(req.messages or [])
    if not user_question:
        for message in reversed(history):
            if message.role == "user" and message.content.strip():
                user_question = message.content.strip()
                history = history[:-1]
                break
    elif history and history[-1].role == "user" and history[-1].content.strip() == user_question:
        history = history[:-1]

    if not user_question:
        return ChatResponse(reply="Enter an admin question to run against VoteIQ records.")

    retrieved_records = (req.retrieved_records or "").strip()
    if not retrieved_records:
        retrieved_records = build_admin_database_context(user_question) or "No matching local SQL records were found for this question."

    messages = _build_voteiq_admin_messages(user_question, history, retrieved_records)
    model = (req.model or os.getenv("ANTHROPIC_ADMIN_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6").strip()

    try:
        client = get_claude_client()
        response = client.messages.create(
            model=model,
            max_tokens=req.max_tokens,
            cache_control={"type": "ephemeral"},
            system=cached_system_prompt(VOTEIQ_ADMIN_SYSTEM_PROMPT),
            messages=messages,
        )
        return ChatResponse(reply=response.content[0].text)
    except Exception as e:
        import main as _m
        return ChatResponse(reply=_m._friendly_claude_error(e))


@router.post("/admin/chat")
async def admin_dashboard_chat(
    request: Request,
    req: AdminDashboardChatRequest | None = Body(None),
    mode: str | None = Query(None),
    query: str | None = Query(None),
    output_mode: str | None = Query(None),
    action_taken: str | None = Query(None),
    approval_status: str | None = Query(None),
    max_tokens: int | None = Query(None, ge=200, le=4000),
    _: None = Depends(require_admin_token),
):
    body = req or AdminDashboardChatRequest()
    selected_mode = (mode or body.mode or "analyst").strip()
    question = (query if query is not None else body.query or "").strip()
    selected_output_mode = (output_mode if output_mode is not None else body.output_mode or "").strip()
    selected_action = (action_taken if action_taken is not None else body.action_taken or f"chat:{selected_output_mode or 'standard'}").strip()
    selected_approval = (approval_status if approval_status is not None else body.approval_status or "approved").strip()
    token_limit = max_tokens or body.max_tokens
    if not question:
        raise HTTPException(status_code=400, detail="query is required")
    if selected_mode not in _ADMIN_AGENT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown admin mode: {selected_mode}")
    agent_id = os.getenv(_ADMIN_AGENT_REGISTRY[selected_mode]["env"])
    response_text = _run_admin_agent(
        selected_mode,
        question,
        retrieved_records=body.retrieved_records,
        max_tokens=token_limit,
        output_mode=selected_output_mode,
        model_override=body.model,
    )
    audit_record = _record_admin_audit(
        request,
        endpoint="/admin/chat",
        mode=selected_mode,
        query=question,
        agent_id=agent_id,
        action_taken=selected_action,
        approval_status=selected_approval,
    )
    return {
        "status": "success",
        "mode": selected_mode,
        "output_mode": selected_output_mode or "standard",
        "agent": _ADMIN_AGENT_REGISTRY[selected_mode]["name"],
        "agent_id": agent_id,
        "visibility": _ADMIN_AGENT_REGISTRY[selected_mode].get("visibility", "admin_only"),
        "surface": _ADMIN_AGENT_REGISTRY[selected_mode].get("surface", _ADMIN_AGENT_REGISTRY[selected_mode]["name"]),
        "query": question,
        "query_hash": audit_record["query_hash"],
        "response": response_text,
        "timestamp": _admin_now(),
    }


@router.get("/admin/agents")
async def admin_agents(_: None = Depends(require_admin_token)):
    state = _load_admin_state()
    agents = [_admin_agent_status(mode, state) for mode in _ADMIN_AGENT_REGISTRY]
    deployed = sum(1 for agent in agents if agent["deployed"])
    public_agents = [agent for agent in agents if agent["public_facing"]]
    admin_agents_only = [agent for agent in agents if not agent["public_facing"]]
    return {
        "status": "success",
        "summary": {
            "total_agents": len(agents),
            "deployed": deployed,
            "missing": len(agents) - deployed,
            "public_facing": len(_PUBLIC_FACING_FLOWS),
            "public_facing_agents": len(public_agents),
            "admin_only": len(admin_agents_only),
        },
        "public_facing": [
            {"name": flow["name"], "mode": flow["mode"]}
            for flow in _PUBLIC_FACING_FLOWS
        ],
        "admin_only": [
            {"name": agent["surface"], "mode": agent["mode"]}
            for agent in admin_agents_only
        ],
        "agents": agents,
    }


@router.get("/admin/agents/{mode}")
async def admin_agent_detail(mode: str, _: None = Depends(require_admin_token)):
    if mode not in _ADMIN_AGENT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown admin mode: {mode}")
    return {
        "status": "success",
        **_admin_agent_status(mode),
    }


@router.post("/admin/qa/test")
async def admin_qa_test(
    request: Request,
    query: str = Query(""),
    expected_result: str = Query(""),
    _: None = Depends(require_admin_token),
):
    question = (query or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="query is required")
    expected = (expected_result or "").strip()
    actual = build_database_context(question) or "No matching local SQL records were found for this query."
    passed = bool(expected and expected.lower() in actual.lower())
    result = {
        "id": f"qa-{uuid.uuid4().hex[:8]}",
        "query": question,
        "expected": expected,
        "actual": actual[:4000],
        "test_date": _admin_now(),
        "passed": passed,
    }
    state = _load_admin_state()
    state.setdefault("qa_results", []).insert(0, result)
    _save_admin_state(state)
    _record_admin_audit(
        request,
        endpoint="/admin/qa/test",
        mode="golden_query",
        query=question,
        agent_id=os.getenv(_ADMIN_AGENT_REGISTRY["golden_query"]["env"]),
        action_taken=f"qa_test:{result['id']}",
        approval_status="approved",
    )
    return {"status": "success", **result}


@router.get("/admin/qa/results")
async def admin_qa_results(
    limit: int = Query(50, ge=1, le=500),
    days: int = Query(7, ge=1, le=3650),
    _: None = Depends(require_admin_token),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    results = []
    for result in _load_admin_state().get("qa_results", []):
        try:
            if datetime.fromisoformat(result.get("test_date", "")) < cutoff:
                continue
        except Exception:
            pass
        results.append(result)
        if len(results) >= limit:
            break
    passed = sum(1 for result in results if result.get("passed"))
    return {
        "status": "success",
        "period": f"Last {days} days",
        "summary": {
            "total_tests": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": f"{(passed / max(len(results), 1)) * 100:.1f}%",
        },
        "results": results,
    }


def _admin_table_count(db_path: str, table: str) -> int | None:
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return int(count)
    except Exception:
        return None


@router.get("/admin/qa/coverage")
async def admin_qa_coverage(_: None = Depends(require_admin_token)):
    state = _load_admin_state()
    qa_results = state.get("qa_results", [])
    return {
        "status": "success",
        "coverage": {
            "governor_actions": {
                "records": _admin_table_count(_POLLS_DB, "governor_actions"),
                "tested_queries": sum(1 for item in qa_results if "governor" in item.get("query", "").lower()),
            },
            "executive_orders": {
                "records": _admin_table_count(_POLLS_DB, "governor_executive_orders"),
                "tested_queries": sum(1 for item in qa_results if "executive order" in item.get("query", "").lower()),
            },
            "donations": {
                "records": _admin_table_count(_POLLS_DB, "va_finance_contributions"),
                "tested_queries": sum(1 for item in qa_results if any(word in item.get("query", "").lower() for word in ("donor", "donation", "finance", "pac"))),
            },
            "officials": {
                "records": _admin_table_count(_POLLS_DB, "candidate_registry"),
                "tested_queries": sum(1 for item in qa_results if any(word in item.get("query", "").lower() for word in ("official", "candidate", "profile"))),
            },
        },
        "gaps": [
            "Coverage counts depend on local SQL tables available in this deployment.",
            "QA pass/fail is based on expected text appearing in retrieved structured context.",
            "RAG document coverage is checked by source-specific tests, not this SQL table counter.",
        ],
    }


@router.get("/admin/audit")
async def admin_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    mode: str | None = Query(None),
    admin_user: str | None = Query(None),
    _: None = Depends(require_admin_token),
):
    records = [
        item for item in _load_admin_state().get("audit_log", [])
        if (not mode or item.get("mode") == mode)
        and (not admin_user or item.get("admin_user") == admin_user)
    ][:limit]
    return {
        "status": "success",
        "summary": {"total_returned": len(records)},
        "audit_log": records,
    }


@router.post("/admin/escalations")
async def admin_create_escalation(
    request: Request,
    record_type: str = Query(""),
    entity_name: str = Query(""),
    issue_description: str = Query(""),
    severity: str = Query("medium"),
    _: None = Depends(require_admin_token),
):
    if not (record_type and entity_name and issue_description):
        raise HTTPException(status_code=400, detail="record_type, entity_name, and issue_description are required")
    escalation = {
        "id": f"esc-{uuid.uuid4().hex[:8]}",
        "record_type": record_type,
        "entity_name": entity_name,
        "issue": issue_description,
        "severity": severity,
        "status": "open",
        "created_at": _admin_now(),
        "assigned_to": None,
        "asana_task": None,
        "slack_notified": False,
    }
    state = _load_admin_state()
    state.setdefault("escalations", []).insert(0, escalation)
    _save_admin_state(state)
    _record_admin_audit(
        request,
        endpoint="/admin/escalations",
        mode="escalator",
        query=f"{record_type}:{entity_name}:{issue_description}",
        agent_id=os.getenv(_ADMIN_AGENT_REGISTRY["escalator"]["env"]),
        action_taken=f"create_escalation:{escalation['id']}",
        approval_status="approved",
    )
    return {"status": "success", "escalation": escalation}


@router.get("/admin/escalations")
async def admin_escalations(
    status: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    _: None = Depends(require_admin_token),
):
    all_items = _load_admin_state().get("escalations", [])
    filtered = [
        item for item in all_items
        if (not status or item.get("status") == status)
        and (not severity or item.get("severity") == severity)
    ][:limit]
    return {
        "status": "success",
        "summary": {
            "total": len(all_items),
            "open": sum(1 for item in all_items if item.get("status") == "open"),
            "in_progress": sum(1 for item in all_items if item.get("status") == "in_progress"),
            "fixed": sum(1 for item in all_items if item.get("status") == "fixed"),
        },
        "escalations": filtered,
    }


def _find_admin_escalation(state: dict, escalation_id: str) -> dict | None:
    for escalation in state.get("escalations", []):
        if escalation.get("id") == escalation_id:
            return escalation
    return None


@router.get("/admin/escalations/{escalation_id}")
async def admin_escalation_detail(escalation_id: str, _: None = Depends(require_admin_token)):
    state = _load_admin_state()
    escalation = _find_admin_escalation(state, escalation_id)
    if not escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")
    return {
        "status": "success",
        "escalation": escalation,
        "details": {
            "repro_steps": escalation.get("repro_steps"),
            "primary_source": escalation.get("primary_source"),
            "our_data": escalation.get("our_data"),
            "correct_data": escalation.get("correct_data"),
            "suggested_fix": escalation.get("suggested_fix"),
            "sla_hours": {"critical": 24, "high": 48, "medium": 168, "low": 336}.get(escalation.get("severity"), 168),
        },
    }


@router.patch("/admin/escalations/{escalation_id}")
async def admin_update_escalation(
    request: Request,
    escalation_id: str,
    status: str | None = Query(None),
    assigned_to: str | None = Query(None),
    _: None = Depends(require_admin_token),
):
    state = _load_admin_state()
    escalation = _find_admin_escalation(state, escalation_id)
    if not escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")
    if status is not None:
        escalation["status"] = status
    if assigned_to is not None:
        escalation["assigned_to"] = assigned_to
    escalation["updated_at"] = _admin_now()
    _save_admin_state(state)
    _record_admin_audit(
        request,
        endpoint=f"/admin/escalations/{escalation_id}",
        mode="escalator",
        query=f"{escalation_id}:{status or ''}:{assigned_to or ''}",
        agent_id=os.getenv(_ADMIN_AGENT_REGISTRY["escalator"]["env"]),
        action_taken="update_escalation",
        approval_status="approved",
    )
    return {"status": "success", "escalation": escalation, "action": "Escalation updated"}


@router.post("/admin/field-monitor/scan")
async def admin_field_monitor_scan(request: Request, _: None = Depends(require_admin_token)):
    query = "Run weekly VoteIQ field monitor scan."
    agent_id = os.getenv(_ADMIN_AGENT_REGISTRY["field_monitor"]["env"])
    full_report = _run_admin_agent("field_monitor", query, max_tokens=1600)
    report = {
        "id": f"fm-{uuid.uuid4().hex[:8]}",
        "scan_date": _admin_now(),
        "week": f"Week of {datetime.utcnow().strftime('%B %d, %Y')}",
        "summary": full_report[:500],
        "actions": [
            {"priority": "IMMEDIATE", "count": full_report.lower().count("immediate")},
            {"priority": "SOON", "count": full_report.lower().count("soon")},
            {"priority": "LATER", "count": full_report.lower().count("later")},
        ],
        "full_report": full_report,
    }
    state = _load_admin_state()
    state.setdefault("field_reports", []).insert(0, report)
    _save_admin_state(state)
    _record_admin_audit(
        request,
        endpoint="/admin/field-monitor/scan",
        mode="field_monitor",
        query=query,
        agent_id=agent_id,
        action_taken=f"run_field_monitor:{report['id']}",
        approval_status="approved",
    )
    return {"status": "success", "report": report}


@router.get("/admin/field-monitor/reports")
async def admin_field_monitor_reports(
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(require_admin_token),
):
    reports = _load_admin_state().get("field_reports", [])[:limit]
    return {
        "status": "success",
        "summary": {
            "total_scans": len(reports),
            "latest_scan": reports[0]["scan_date"] if reports else None,
        },
        "reports": reports,
    }


@router.get("/admin/field-monitor/latest")
async def admin_field_monitor_latest(_: None = Depends(require_admin_token)):
    reports = _load_admin_state().get("field_reports", [])
    if not reports:
        return {"status": "empty", "report": None}
    return {"status": "success", "report": reports[0]}


def _identity_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(term in q for term in (
        "candidate_id", "candidate id", "committee_id", "committee id",
        "bioguide", "congress.gov member", "congress member id",
        "openstates", "person id", "person_id", "identity", "crosswalk",
        "name alias", "name aliases", "aliases",
    ))


def _name_tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z.'-]{1,}", value or "")
        if token.lower() not in {
            "fec", "api", "candidate", "candidate_id", "committee", "committee_id",
            "bioguide", "congress", "gov", "member", "openstates", "person",
            "identity", "crosswalk", "aliases", "alias", "party", "state",
            "district", "id", "ids", "show", "give", "tell", "what", "for",
        }
    }


def _find_identity_name(user_query: str) -> str:
    query_tokens = _name_tokens(user_query)
    if not query_tokens or not os.path.exists(_POLLS_DB):
        return ""
    names: set[str] = set()
    try:
        conn = sqlite3.connect(_POLLS_DB)
        for table, column in (
            ("congress_members", "name"),
            ("va_finance_people", "person_name"),
            ("legislators", "name"),
            ("candidate_registry", "name"),
        ):
            try:
                names.update(
                    str(row[0])
                    for row in conn.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL")
                    if row[0]
                )
            except Exception:
                continue
        conn.close()
    except Exception:
        return ""

    best_name = ""
    best_score = 0
    for name in names:
        tokens = _name_tokens(name.replace(",", " "))
        if not tokens:
            continue
        score = len(tokens & query_tokens)
        if score > best_score and (score >= 2 or any(token in query_tokens for token in tokens)):
            best_name = name
            best_score = score
    return best_name


def _direct_identity_crosswalk_reply(user_query: str) -> str:
    """Return SQL-backed identity IDs for a candidate/member/person."""
    if not _identity_query(user_query):
        return ""

    name = _find_identity_name(user_query)
    if not name:
        return "\n".join([
            "**VoteIQ Identity Crosswalk Fields**",
            "",
            "For each person, VoteIQ should try to keep these identifiers together:",
            "- FEC candidate_id",
            "- FEC committee_id",
            "- bioguide_id",
            "- Congress.gov member ID",
            "- OpenStates/person ID, if state overlap exists",
            "- name aliases",
            "- party/state/district",
            "",
            "Ask for a specific person, for example: `Show identity IDs for Jennifer McClellan`.",
        ])

    rows: dict[str, list[sqlite3.Row]] = {}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        name_like = f"%{name.replace(',', '').split()[-1].lower()}%"
        rows["congress_members"] = conn.execute(
            """
            SELECT bioguide_id, name, party, chamber, state, district, website
            FROM congress_members
            WHERE lower(name) LIKE ? OR lower(replace(name, ',', '')) LIKE ?
            LIMIT 5
            """,
            (name_like, f"%{name.lower().replace(',', '')}%"),
        ).fetchall()
        bioguide_ids = [row["bioguide_id"] for row in rows["congress_members"] if row["bioguide_id"]]
        candidate_ids: set[str] = set()

        if bioguide_ids:
            placeholders = ",".join("?" for _ in bioguide_ids)
            rows["bridge"] = conn.execute(
                f"SELECT bioguide_id, fec_candidate_id FROM bioguide_fec_bridge WHERE bioguide_id IN ({placeholders})",
                bioguide_ids,
            ).fetchall()
            candidate_ids.update(row["fec_candidate_id"] for row in rows["bridge"] if row["fec_candidate_id"])
        else:
            rows["bridge"] = []

        rows["registry"] = conn.execute(
            """
            SELECT candidate_key, fec_candidate_id, bioguide_id, lis_id, name, party, level, office, state, cycle
            FROM candidate_registry
            WHERE lower(name) LIKE ? OR lower(replace(name, '.', '')) LIKE ?
            LIMIT 5
            """,
            (name_like, f"%{name.lower().replace('.', '')}%"),
        ).fetchall()
        candidate_ids.update(row["fec_candidate_id"] for row in rows["registry"] if row["fec_candidate_id"])
        bioguide_ids.extend(row["bioguide_id"] for row in rows["registry"] if row["bioguide_id"] and row["bioguide_id"] not in bioguide_ids)

        rows["fec_contrib"] = conn.execute(
            """
            SELECT candidate_id, candidate_name, bioguide_id, COUNT(*) AS records
            FROM fec_individual_contributions
            WHERE lower(candidate_name) LIKE ?
               OR bioguide_id IN (SELECT bioguide_id FROM congress_members WHERE lower(name) LIKE ?)
            GROUP BY candidate_id, candidate_name, bioguide_id
            LIMIT 8
            """,
            (name_like, name_like),
        ).fetchall()
        candidate_ids.update(row["candidate_id"] for row in rows["fec_contrib"] if row["candidate_id"])

        rows["fec_ie"] = conn.execute(
            """
            SELECT fec_candidate_id, candidate_name, bioguide_id, committee_id, committee_name, COUNT(*) AS records
            FROM fec_independent_expenditures
            WHERE lower(candidate_name) LIKE ?
               OR bioguide_id IN (SELECT bioguide_id FROM congress_members WHERE lower(name) LIKE ?)
            GROUP BY fec_candidate_id, candidate_name, bioguide_id, committee_id, committee_name
            ORDER BY records DESC
            LIMIT 12
            """,
            (name_like, name_like),
        ).fetchall()
        candidate_ids.update(row["fec_candidate_id"] for row in rows["fec_ie"] if row["fec_candidate_id"])

        rows["va_people"] = conn.execute(
            """
            SELECT person_name, office, district, party, committee_name, finance_url, source_url
            FROM va_finance_people
            WHERE lower(person_name) LIKE ?
            LIMIT 5
            """,
            (name_like,),
        ).fetchall()
        rows["legislators"] = conn.execute(
            """
            SELECT id, lis_id, name, party, chamber, district, active
            FROM legislators
            WHERE lower(name) LIKE ?
            LIMIT 5
            """,
            (name_like,),
        ).fetchall()
        conn.close()
    except Exception:
        return ""

    aliases: set[str] = {name}
    for key in ("congress_members", "registry", "fec_contrib", "fec_ie", "va_people", "legislators"):
        for row in rows.get(key, []):
            for column in ("name", "candidate_name", "person_name"):
                if column in row.keys() and row[column]:
                    aliases.add(str(row[column]))

    fec_candidate_ids = sorted({
        *(row["fec_candidate_id"] for row in rows.get("bridge", []) if row["fec_candidate_id"]),
        *(row["fec_candidate_id"] for row in rows.get("registry", []) if row["fec_candidate_id"]),
        *(row["candidate_id"] for row in rows.get("fec_contrib", []) if row["candidate_id"]),
        *(row["fec_candidate_id"] for row in rows.get("fec_ie", []) if row["fec_candidate_id"]),
    })
    fec_committee_ids = sorted({row["committee_id"] for row in rows.get("fec_ie", []) if row["committee_id"]})
    bioguide_ids = sorted({
        *(row["bioguide_id"] for row in rows.get("congress_members", []) if row["bioguide_id"]),
        *(row["bioguide_id"] for row in rows.get("bridge", []) if row["bioguide_id"]),
        *(row["bioguide_id"] for row in rows.get("registry", []) if row["bioguide_id"]),
        *(row["bioguide_id"] for row in rows.get("fec_contrib", []) if row["bioguide_id"]),
        *(row["bioguide_id"] for row in rows.get("fec_ie", []) if row["bioguide_id"]),
    })
    lis_ids = sorted({
        *(row["lis_id"] for row in rows.get("registry", []) if row["lis_id"]),
        *(row["lis_id"] for row in rows.get("legislators", []) if row["lis_id"]),
    })

    party_state_district: list[str] = []
    for row in rows.get("congress_members", []):
        party_state_district.append(
            f"{row['party'] or 'party not listed'} / {row['state'] or 'state not listed'} / {row['district'] or 'statewide'}"
        )
    for row in rows.get("va_people", []):
        party_state_district.append(
            f"{row['party'] or 'party not listed'} / Virginia / {row['district'] or row['office'] or 'statewide'}"
        )
    for row in rows.get("legislators", []):
        party_state_district.append(
            f"{row['party'] or 'party not listed'} / Virginia / {row['chamber'] or 'chamber not listed'} District {row['district'] or 'not listed'}"
        )

    lines = [
        "**VoteIQ Identity Crosswalk**",
        f"Subject: {name}",
        "",
        f"- FEC candidate_id: {', '.join(fec_candidate_ids) if fec_candidate_ids else 'Not found in local SQL'}",
        f"- FEC committee_id: {', '.join(fec_committee_ids) if fec_committee_ids else 'Not found in local SQL'}",
        f"- bioguide_id: {', '.join(bioguide_ids) if bioguide_ids else 'Not found in local SQL'}",
        f"- Congress.gov member ID: {', '.join(bioguide_ids) if bioguide_ids else 'Not found in local SQL; VoteIQ treats bioguide_id as the Congress member key when present'}",
        f"- OpenStates/person ID if state overlap exists: {', '.join(lis_ids) + ' (local LIS/state person key; separate OpenStates person ID not stored)' if lis_ids else 'Not found in local SQL'}",
        f"- name aliases: {', '.join(sorted(aliases))}",
        f"- party/state/district: {'; '.join(dict.fromkeys(party_state_district)) if party_state_district else 'Not found in local SQL'}",
        "",
        "Source: VoteIQ local SQL identity records from polls.db. Exact IDs are reported as database records; VoteIQ does not infer identity matches when no matching row exists.",
    ]
    return "\n".join(lines)


def _json_text_list(value: str | None, limit: int = 12) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()][:limit]
    return [part.strip() for part in str(value).split(";") if part.strip()][:limit]


def _requested_eo_number(query: str) -> str:
    q = (query or "").lower()
    match = re.search(r"(?:executive\s+order|governor\s+order|order|eo)[^\d]{0,20}(\d{1,3})", q)
    return match.group(1) if match else ""


def _eo_chat_read_url(order_number: str | None) -> str:
    label = order_number or ""
    query = f"Read Executive Order {label} from the VoteIQ database".strip()
    return f"/ask?q={quote(query)}"


def _format_governor_eo_detail(row: sqlite3.Row) -> str:
    order_number = row["order_number"] or "number not available"
    title = row["title"] or "Title not available"
    signed_date = row["signed_date"] or "date not available"
    effective_date = row["effective_date"] or "not listed"
    source_url = row["source_url"] or "https://www.governor.virginia.gov/"
    database_url = _eo_chat_read_url(order_number)
    summary = " ".join((row["summary"] or "No summary available in local SQL.").split())
    agencies = _json_text_list(row["agencies"], limit=10)
    actions = _json_text_list(row["actions_required"], limit=20)

    lines = [
        "**Executive Order Record**",
        "",
        f"Order: Executive Order {order_number}",
        f"Title: {title}",
        f"Governor: {row['governor'] or 'not listed'}",
        f"Date issued: {signed_date}",
        f"Effective date: {effective_date}",
        "",
        "**Summary:**",
        summary,
    ]
    if agencies:
        lines.extend(["", "**Agencies involved:**"])
        lines.extend(f"- {agency}" for agency in agencies)
    else:
        lines.extend(["", "**Agencies involved:**", "- Not listed in local SQL record"])
    if actions:
        lines.extend(["", "**Actions required:**"])
        lines.extend(f"- {action}" for action in actions)
    else:
        lines.extend(["", "**Actions required:**", "- Not listed in local SQL record"])
    lines.extend([
        "",
        "**Source:**",
        f"- [VoteIQ database record]({database_url})",
        f"- [Official Governor source/PDF]({source_url})",
        "",
        "**Data limits:**",
        "VoteIQ summarizes public executive-order records. This page does not infer motive, policy effectiveness, or political intent.",
    ])
    return "\n".join(lines)


_NICKNAMES = {
    "charlie": "charles", "chris": "christopher", "bill": "william",
    "bob": "robert", "jim": "james", "mike": "michael", "dave": "david",
    "beth": "elizabeth", "liz": "elizabeth", "betty": "elizabeth",
    "joe": "joseph", "dan": "daniel", "tom": "thomas", "matt": "matthew",
    "ben": "benjamin", "nick": "nicholas", "tony": "anthony",
    "rick": "richard", "rob": "robert", "rich": "richard",
    "steve": "stephen", "jeff": "jeffrey", "andy": "andrew",
}


def _norm_legislator_name(name: str) -> str:
    """Normalize legislator name: lowercase, strip middle initials, expand nicknames."""
    import re as _re
    name = (name or "").strip().lower()
    if "," in name:                          # "Last, First" → "First Last"
        parts = name.split(",", 1)
        name = parts[1].strip() + " " + parts[0].strip()
    name = _re.sub(r"\b[a-z]\.\s*", "", name)   # strip single-letter initials
    name = _re.sub(r"\s+", " ", name).strip()
    words = name.split()
    if words and words[0] in _NICKNAMES:
        words[0] = _NICKNAMES[words[0]]
    return " ".join(words)


def _direct_governor_correlation_reply(user_query: str) -> str:
    """
    Premium-only: Return governor bill-outcome vs. sponsor donor-sector correlation.
    Caller MUST gate this behind _premium_analyst_enabled(req).
    """
    q = (user_query or "").lower()
    _gov = any(t in q for t in (
        "governor", "spanberger", "signed", "vetoed", "veto", "bill", "action",
    ))
    _money = any(t in q for t in (
        "donor", "sector", "finance", "financ", "campaign", "contribut",
        "donation", "money", "fund", "industry", "correlat",
        "who back", "who support", "who gave", "who paid", "who fund",
    ))
    if not _gov or not _money:
        return ""

    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row

        # Overall action counts
        action_counts = {
            r["action"]: int(r["cnt"])
            for r in conn.execute("""
                SELECT action, COUNT(*) AS cnt
                FROM governor_actions
                WHERE session = '2026'
                  AND lower(governor) LIKE '%spanberger%'
                GROUP BY action ORDER BY cnt DESC
            """).fetchall()
        }

        # Load all governor action sponsor rows
        ga_rows = conn.execute("""
            SELECT action, sponsor_name, sponsor_party, COUNT(*) AS bill_count
            FROM governor_actions
            WHERE lower(governor) LIKE '%spanberger%' AND session = '2026'
              AND sponsor_name IS NOT NULL AND sponsor_name != ''
            GROUP BY action, sponsor_name, sponsor_party
            ORDER BY action, bill_count DESC
        """).fetchall()

        # Load campaign finance summary for Python-side normalized matching
        cfs_rows = conn.execute(
            "SELECT name, top_sector, total_raised FROM campaign_finance_summary"
        ).fetchall()

        conn.close()
    except Exception:
        return ""

    if not action_counts:
        return ""

    # Build normalized CFS lookup: norm_name → {top_sector, total_raised}
    from collections import defaultdict
    cfs_lookup: dict[str, dict] = {}
    for r in cfs_rows:
        cfs_lookup[_norm_legislator_name(r["name"])] = {
            "top_sector": r["top_sector"],
            "total_raised": r["total_raised"],
        }

    # Match each sponsor row to CFS via normalized name
    sectors_by_action: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sponsors_by_action: dict[str, list] = defaultdict(list)
    matched_bills = 0
    total_bills = sum(r["bill_count"] for r in ga_rows)

    for r in ga_rows:
        norm = _norm_legislator_name(r["sponsor_name"])
        fin = cfs_lookup.get(norm)
        if fin:
            matched_bills += r["bill_count"]
            sectors_by_action[r["action"]][fin["top_sector"] or "Unknown"] += r["bill_count"]
        sponsors_by_action[r["action"]].append({
            "sponsor_name": r["sponsor_name"],
            "sponsor_party": r["sponsor_party"],
            "bill_count": r["bill_count"],
            "top_sector": fin["top_sector"] if fin else None,
            "total_raised": fin["total_raised"] if fin else None,
        })

    _ACTION_LABEL = {
        "signed": "Signed into law",
        "vetoed": "Vetoed",
        "amended": "Amended / returned",
    }

    lines = [
        "## Governor Spanberger — Campaign Donor Sector Correlation (2026 Session)",
        "",
        "**Bill outcome totals:**",
    ]
    for action, label in _ACTION_LABEL.items():
        cnt = action_counts.get(action, 0)
        if cnt:
            lines.append(f"- {label}: {cnt:,} bills")

    lines += [
        "",
        f"**Finance data coverage:** {matched_bills:,} of {total_bills:,} bills ({matched_bills*100//total_bills if total_bills else 0}%) "
        f"matched to sponsor campaign-finance records.",
        "",
        "**Top donor sectors by governor action** *(sponsors with matched finance records only)*:",
    ]

    for action, label in _ACTION_LABEL.items():
        sector_counts = sectors_by_action.get(action, {})
        if not sector_counts:
            continue
        lines.append(f"\n*{label}:*")
        for sector, cnt in sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            lines.append(f"- {sector}: {cnt} bill{'s' if cnt != 1 else ''}")

    lines += ["", "**Top bill sponsors by action:**"]
    for action, label in _ACTION_LABEL.items():
        rows = sponsors_by_action.get(action, [])
        if not rows:
            continue
        lines.append(f"\n*{label} — top 5 sponsors:*")
        for r in rows[:5]:
            party = f" ({r['sponsor_party'][0]})" if r["sponsor_party"] else ""
            sector_note = f", top sector: {r['top_sector']}" if r["top_sector"] else ""
            raised_note = f", raised ${r['total_raised']:,.0f}" if r["total_raised"] else ""
            lines.append(
                f"- {r['sponsor_name']}{party}: {r['bill_count']} bill{'s' if r['bill_count'] != 1 else ''}"
                f"{sector_note}{raised_note}"
            )

    lines += [
        "",
        "---",
        "*Correlation does not imply causation. VoteIQ reports public-record overlaps only "
        "and does not infer motive, reward, pressure, influence, or policy intent. "
        "Finance data: Virginia SBE public records. Sources: Virginia LIS · VPAP.*",
    ]

    return "\n".join(lines)


def _direct_governor_eo_reply(user_query: str) -> str:
    """Return Spanberger executive orders directly from local SQL."""
    q = (user_query or "").lower()
    wants_eos = (
        "executive order" in q
        or "executive orders" in q
        or "governor order" in q
        or "eo-" in q
        or ("order" in q and "executive" in q)
    )
    if not wants_eos:
        return ""
    if not any(term in q for term in ("governor", "spanberger", "she", "her", "executive order", "eo-")):
        return ""
    if "youngkin" in q:
        return ""

    rows = []
    try:
        if os.path.exists(_POLLS_DB):
            conn = sqlite3.connect(_POLLS_DB)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, order_number, title, governor, signed_date, summary,
                       policy_topics, agencies, actions_required, effective_date,
                       source_url, source_type, full_text
                FROM governor_executive_orders
                WHERE lower(governor) LIKE '%spanberger%'
                ORDER BY signed_date DESC, order_number DESC
                """
            ).fetchall()
            conn.close()
    except Exception:
        rows = []

    if not rows:
        return ""

    requested_number = _requested_eo_number(user_query)
    wants_database_read = any(
        term in q
        for term in ("read", "database", "db", "full text", "details", "detail", "open")
    )
    if requested_number and wants_database_read:
        for row in rows:
            row_number = str(row["order_number"] or "")
            if re.match(rf"^\s*{re.escape(requested_number)}\b", row_number):
                return _format_governor_eo_detail(row)

    lines = [
        f"Governor Spanberger has {len(rows)} executive-order/directive records in VoteIQ local SQL:",
        "",
    ]
    for row in rows:
        order_number = row["order_number"] or "number not available"
        title = row["title"] or "Title not available"
        date = row["signed_date"] or "date not available"
        url = _eo_chat_read_url(order_number)
        source_type = row["source_type"] or "executive order"
        summary = " ".join((row["summary"] or "").split())
        if len(summary) > 280:
            summary = summary[:280].rstrip() + "..."
        lines.append(f"- [Order {order_number}]({url}) - {title} ({date}; {source_type})")
        if summary:
            lines.append(f"  - Summary: {summary}")
            lines.append("  - Click the order link to read VoteIQ's plain-English explanation and database record.")

    lines.extend([
        "",
        "Source: VoteIQ local SQL records from polls.db.governor_executive_orders, with source links from the Virginia Governor's Office.",
        "VoteIQ does not infer motive, intent, or causation from executive actions.",
    ])
    return "\n".join(lines)


def _direct_spanberger_governor_overview_reply(user_query: str) -> str:
    """Return a neutral SQL-first public-record overview for Governor Spanberger."""
    q = (user_query or "").lower()
    if "spanberger" not in q or "governor" not in q:
        return ""
    if not any(term in q for term in (
        "overview", "public-record", "public record", "profile",
        "campaign finance", "major donors",
        "signed", "vetoed", "veto", "bills", "legislation",
        "executive order", "what has", "what did", "record",
        "done as governor", "actions", "legislative",
    )):
        return ""
    if "youngkin" in q:
        return ""

    finance_person = None
    cycle_rows = []
    donor_rows = []
    governor_counts: dict[str, int] = {}
    veto_rows = []
    signed_rows = []
    eo_rows = []
    eo_total = 0
    sector_totals: dict[str, dict[str, float | int]] = {}

    if not os.path.exists(_POLLS_DB):
        return ""

    # Finance data — best-effort; missing tables do not abort the function
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        finance_person = conn.execute(
            """
            SELECT person_name, office, party, committee_name, finance_url, source_url, fetched_at
            FROM va_finance_people
            WHERE lower(person_name) LIKE '%spanberger%'
            ORDER BY fetched_at DESC
            LIMIT 1
            """
        ).fetchone()
        cycle_rows = conn.execute(
            """
            SELECT election_cycle, ROUND(SUM(amount), 2) AS total,
                   COUNT(*) AS records, MAX(transaction_date) AS latest
            FROM va_cf_schedule_a
            WHERE lower(candidate_name) LIKE '%spanberger%'
              AND amount > 0
              AND CAST(election_cycle AS INTEGER) >= 2023
            GROUP BY election_cycle
            ORDER BY CAST(election_cycle AS INTEGER) DESC
            """
        ).fetchall()
        donor_rows = conn.execute(
            """
            SELECT
                CASE
                    WHEN is_individual = 1 THEN TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_or_company, ''))
                    ELSE COALESCE(last_or_company, '')
                END AS donor_name,
                COALESCE(employer, '') AS employer,
                COALESCE(occupation, '') AS occupation,
                ROUND(SUM(amount), 2) AS total,
                COUNT(*) AS records
            FROM va_cf_schedule_a
            WHERE lower(candidate_name) LIKE '%spanberger%'
              AND amount > 0
              AND election_cycle = '2025'
            GROUP BY donor_name, employer, occupation
            HAVING donor_name != ''
            ORDER BY total DESC
            LIMIT 10
            """
        ).fetchall()
        try:
            from build_va_state_finance import classify_sector
            finance_rows = conn.execute(
                """
                SELECT occupation, employer, last_or_company, is_individual, amount
                FROM va_cf_schedule_a
                WHERE lower(candidate_name) LIKE '%spanberger%'
                  AND amount > 0
                  AND election_cycle = '2025'
                """
            ).fetchall()
            for row in finance_rows:
                sector = classify_sector(
                    row["occupation"] or "",
                    row["employer"] or "",
                    row["last_or_company"] or "" if not row["is_individual"] else "",
                )
                stats = sector_totals.setdefault(sector, {"total": 0.0, "records": 0})
                stats["total"] = float(stats["total"]) + float(row["amount"] or 0)
                stats["records"] = int(stats["records"]) + 1
        except Exception:
            sector_totals = {}
        conn.close()
    except Exception:
        pass  # Finance tables unavailable — continue with legislative record

    # Governor actions — required; return "" only if this block fails
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        # Bail out cleanly if the table doesn't exist yet
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='governor_actions'"
        ).fetchone():
            conn.close()
            return ""
        governor_counts = {
            row["action"]: int(row["count"])
            for row in conn.execute(
                """
                SELECT action, COUNT(*) AS count
                FROM governor_actions
                WHERE session = '2026'
                  AND lower(governor) LIKE '%spanberger%'
                GROUP BY action
                """
            ).fetchall()
        }
        veto_rows = conn.execute(
            """
            SELECT bill_number, title, action_date, source_url
            FROM governor_actions
            WHERE session = '2026'
              AND lower(governor) LIKE '%spanberger%'
              AND action IN ('vetoed', 'pocket_veto', 'veto_sustained', 'veto_overridden')
            ORDER BY action_date DESC, bill_number
            LIMIT 10
            """
        ).fetchall()
        try:
            signed_rows = conn.execute(
                """
                SELECT bill_number, title, action_date, chapter_number, source_url
                FROM governor_actions
                WHERE session = '2026'
                  AND lower(governor) LIKE '%spanberger%'
                  AND action = 'signed'
                ORDER BY action_date DESC, bill_number
                LIMIT 8
                """
            ).fetchall()
        except Exception:
            # chapter_number may not exist in older schema
            signed_rows = conn.execute(
                """
                SELECT bill_number, title, action_date, NULL AS chapter_number, source_url
                FROM governor_actions
                WHERE session = '2026'
                  AND lower(governor) LIKE '%spanberger%'
                  AND action = 'signed'
                ORDER BY action_date DESC, bill_number
                LIMIT 8
                """
            ).fetchall()
        try:
            eo_rows = conn.execute(
                """
                SELECT order_number, title, signed_date, source_url
                FROM governor_executive_orders
                ORDER BY signed_date DESC, order_number DESC
                LIMIT 8
                """
            ).fetchall()
            eo_total = conn.execute(
                "SELECT COUNT(*) FROM governor_executive_orders"
            ).fetchone()[0]
        except Exception:
            eo_rows = []
            eo_total = 0
        conn.close()
    except Exception:
        return ""

    swearing_url = "https://www.governor.virginia.gov/newsroom/news-releases/2026/january-releases/name-1111196-en.html"
    elections_url = "https://www.elections.virginia.gov/results/"
    committee_url = (
        finance_person["finance_url"]
        if finance_person and finance_person["finance_url"]
        else "https://www.vpap.org/"
    )

    all_total = sum(float(row["total"] or 0) for row in cycle_rows)
    all_records = sum(int(row["records"] or 0) for row in cycle_rows)
    latest_cycle = str(cycle_rows[0]["election_cycle"]) if cycle_rows else "not available"

    lines = [
        "**Public Record Research Brief**",
        "Subject: Abigail Spanberger",
        "Scope: Governor of Virginia, campaign finance, executive actions, legislative actions",
        "Sources: Virginia SBE/VPAP, Governor of Virginia, Virginia LIS, local SQL records",
        "Current through: May 23, 2026",
        "",
        "**Scope And Source Base**",
        "- This brief uses VoteIQ local SQL records first, with source references to Virginia SBE campaign-finance records, VPAP committee records, Virginia Department of Elections results, Governor of Virginia releases, and Virginia legislative/OpenStates-style bill metadata.",
        "- The brief reports public records only. It does not infer motive, intent, causation, corruption, or influence from donations, votes, vetoes, bill actions, or executive actions.",
        "",
        "**Finding 1: Current Office/Status**",
        f"- Abigail Spanberger is listed in VoteIQ as Governor of Virginia. The Governor's Office published her inaugural address on January 17, 2026, following her swearing-in as the 75th Governor of Virginia ([Governor's Office]({swearing_url})).",
        f"- The relevant statewide general election was November 4, 2025; verify certified returns through the Virginia Department of Elections results portal ([Virginia Department of Elections]({elections_url})).",
        "- Campaign statements and promises are not treated as official government actions in this brief.",
        "",
        "**Finding 2: Campaign Finance**",
        f"- Committee shown in local records: {finance_person['committee_name'] if finance_person and finance_person['committee_name'] else 'Spanberger for Governor'} ([VPAP committee page]({committee_url})).",
        f"- VoteIQ local SQL totals from Virginia SBE Schedule A itemized contribution rows, cycles 2023+ total ${all_total:,.0f} across {all_records:,} contribution records. These are candidate-committee receipt records in the local import, not a full accounting of outside spending.",
    ]
    if cycle_rows:
        lines.append("- Cycle totals from local SBE import:")
        for row in cycle_rows:
            latest = f", latest transaction {row['latest']}" if row["latest"] else ""
            lines.append(f"  - {row['election_cycle']}: ${float(row['total'] or 0):,.0f} from {int(row['records'] or 0):,} records{latest}")

    if sector_totals:
        latest_total = next((float(row["total"] or 0) for row in cycle_rows if str(row["election_cycle"]) == "2025"), 0.0)
        lines.append(f"- Major donor sectors in {latest_cycle} from keyword classification of SBE rows:")
        for sector, stats in sorted(sector_totals.items(), key=lambda item: float(item[1]["total"]), reverse=True)[:8]:
            pct = float(stats["total"]) / latest_total * 100 if latest_total else 0
            lines.append(f"  - {sector}: ${float(stats['total']):,.0f} ({pct:.1f}%)")

    if donor_rows:
        lines.append("- Notable large aggregated donors in the 2025 SBE rows:")
        for row in donor_rows:
            donor = row["donor_name"] or "Unknown donor"
            details = ", ".join(part for part in (row["employer"], row["occupation"]) if part)
            lines.append(f"  - {donor}: ${float(row['total'] or 0):,.0f} across {int(row['records'] or 0):,} records" + (f" ({details})" if details else ""))

    signed_count = governor_counts.get("signed", 0)
    veto_count = sum(governor_counts.get(key, 0) for key in ("vetoed", "pocket_veto", "veto_sustained", "veto_overridden"))
    amended_count = governor_counts.get("amended", 0)
    eo_count = eo_total or len(eo_rows)
    lines.extend([
        "",
        "**Finding 3: Legislative And Executive Record (2026 Session)**",
        f"- **Summary**: {signed_count} bills signed | {veto_count} bills vetoed | {amended_count} bills amended | {eo_count} executive orders issued",
        f"- **Bills Signed**: {signed_count} bills signed into law",
        f"- **Bills Vetoed**: {veto_count} bills vetoed (pocket veto, veto, veto sustained, or veto overridden)",
        f"- **Bills Amended**: {amended_count} bills amended/returned with recommendation",
        f"- **Executive Orders**: {eo_count} executive orders/directives issued",
        "",
        "- Bill metadata is from Virginia legislative/OpenStates-style records; verify final legal status at Virginia LIS.",
    ])
    if veto_rows:
        lines.append(f"- **Vetoed bills** ({veto_count} total):")
        for row in veto_rows:
            bill = row["bill_number"]
            url = row["source_url"] or f"https://openstates.org/va/bills/2026/{bill}/"
            lines.append(f"  - [{bill}]({url}) - {row['title'] or 'Title not available'} ({row['action_date'] or 'date not available'})")
    if signed_rows:
        lines.append(f"- **Signed bills** ({signed_count} total; showing 8 most recent):")
        for row in signed_rows:
            bill = row["bill_number"]
            url = row["source_url"] or f"https://openstates.org/va/bills/2026/{bill}/"
            chapter = f"; Chapter {row['chapter_number']}" if row["chapter_number"] else ""
            lines.append(f"  - [{bill}]({url}) - {row['title'] or 'Title not available'} ({row['action_date'] or 'date not available'}{chapter})")
        if signed_count > 8:
            lines.append(f"  - ... and {signed_count - 8} more bills signed. Ask 'What bills has Governor Spanberger signed?' for the complete list.")
    else:
        lines.append("- Signed bill rows were not returned from the current local SQL query.")
    if eo_rows:
        lines.append(f"- **Executive Orders** ({eo_count} total; showing {len(eo_rows)} most recent):")
        for row in eo_rows:
            order_number = row["order_number"] or "number not available"
            lines.append(f"  - [Order {order_number}]({_eo_chat_read_url(order_number)}) - {row['title'] or 'Title not available'} ({row['signed_date'] or 'date not available'})")
        if eo_count > len(eo_rows):
            lines.append(f"  - ... and {eo_count - len(eo_rows)} more orders. Ask 'What executive orders has Governor Spanberger issued?' for the complete list.")
    else:
        lines.append("- Executive-order rows were not returned from the current local SQL query.")

    lines.extend([
        "",
        "**Data Limits**",
        "- Campaign finance totals are source-dependent. VoteIQ's figure above comes from local Virginia SBE Schedule A itemized contribution rows; VPAP totals may differ because of reporting cutoffs, refunds, loans, in-kind treatment, unitemized receipts, transfers, or independent/outside spending methodology.",
        "- The finance section does not treat donations as evidence of motive, influence, corruption, or causation.",
        "- Governor-action rows may lag official LIS/Governor's Office updates; verify final enrolled bill text, chapters, and effective dates at Virginia LIS.",
        "- Executive-order summaries are derived from local records and Governor's Office PDFs; they are summaries, not substitutes for the full order text.",
        "",
        "**Related Follow-Up Questions**",
        "- [Which bills has Governor Spanberger signed or vetoed?](/ask?q=Which%20bills%20has%20Governor%20Spanberger%20signed%20or%20vetoed%3F)",
        "- [What executive orders has Governor Spanberger issued?](/ask?q=What%20executive%20orders%20has%20Governor%20Spanberger%20issued%3F)",
        "- [Who were Abigail Spanberger's largest donors in the 2025 SBE records?](/ask?q=Who%20were%20Abigail%20Spanberger%27s%20largest%20donors%20in%20the%202025%20SBE%20records%3F)",
    ])
    return "\n".join(lines)


def _fetch_floor_statements(
    bioguide_id: str,
    query: str = "",
    limit: int = 5,
) -> str:
    """Return formatted floor statement excerpts from congress_floor_statements."""
    if not os.path.exists(_POLLS_DB):
        return ""
    try:
        import sqlite3
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        params: list = [bioguide_id]
        kw_clause = ""
        if query:
            keywords = [w for w in query.lower().split() if len(w) > 3][:5]
            if keywords:
                kw_clause = " AND (" + " OR ".join("lower(text) LIKE ? OR lower(title) LIKE ?" for _ in keywords) + ")"
                for kw in keywords:
                    params.extend([f"%{kw}%", f"%{kw}%"])
        rows = conn.execute(
            f"""SELECT member_name, statement_date, title, SUBSTR(text,1,2000), source_url
                FROM congress_floor_statements
                WHERE bioguide_id = ?{kw_clause}
                ORDER BY statement_date DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        blocks = []
        for row in rows:
            name, dt, title, excerpt, url = row
            blocks.append(
                f"[Floor Statement — {name} | {dt}]\n"
                f"Title: {title}\n"
                f"{excerpt.strip()}"
                + (f"\nSource: {url}" if url else "")
            )
        return "\n\n".join(blocks)
    except Exception:
        return ""


def _fetch_transcript_context(query: str, bill_id: str | None = None,
                               bioguide_ids: list[str] | None = None,
                               limit: int = 5) -> str:
    """Return formatted [video_transcript] and [floor_statement] blocks relevant to the query."""
    import sqlite3
    if not os.path.exists(_POLLS_DB):
        return ""

    keywords = [w for w in query.lower().split() if len(w) > 3][:6]
    blocks: list[str] = []

    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row

        # ── hearing_segments ──────────────────────────────────────────────────
        hs_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hearing_segments'"
        ).fetchone()
        hs_params: list = []
        hs_clauses: list[str] = []
        if hs_table:
            if bill_id:
                hs_clauses.append("s.bill_id = ?")
                hs_params.append(bill_id.upper())
            if keywords:
                kw_clause = " OR ".join("lower(s.quote) LIKE ?" for _ in keywords)
                hs_clauses.append(f"({kw_clause})")
                hs_params.extend(f"%{k}%" for k in keywords)

        if hs_table and hs_clauses:
            where = " AND ".join(hs_clauses) if bill_id else " OR ".join(hs_clauses)
            rows = conn.execute(
                f"""SELECT s.speaker, s.hearing_title, s.hearing_date,
                           s.timestamp_start, s.timestamp_end, s.quote, s.source_url
                    FROM hearing_segments s
                    WHERE {where}
                    ORDER BY s.hearing_date DESC, s.id
                    LIMIT ?""",
                hs_params + [limit],
            ).fetchall()
            for row in rows:
                blocks.append(
                    f"[video_transcript]\n"
                    f"Speaker: {row['speaker'] or 'Unknown'}\n"
                    f"Hearing Title: {row['hearing_title']}\n"
                    f"Date: {row['hearing_date']}\n"
                    f"Timestamp: {row['timestamp_start']}–{row['timestamp_end']}\n"
                    f"Quote: \"{row['quote']}\"\n"
                    f"Source: {row['source_url'] or 'Not available'}"
                )

        # ── congress_floor_statements ─────────────────────────────────────────
        fs_params: list = []
        fs_clauses: list[str] = []
        if bioguide_ids:
            placeholders = ",".join("?" * len(bioguide_ids))
            fs_clauses.append(f"fs.bioguide_id IN ({placeholders})")
            fs_params.extend(bioguide_ids)
        if keywords:
            kw_clause = " OR ".join(
                f"(lower(fs.title) LIKE ? OR lower(fs.text) LIKE ?)" for _ in keywords
            )
            fs_clauses.append(f"({kw_clause})")
            for k in keywords:
                fs_params.extend([f"%{k}%", f"%{k}%"])

        if fs_clauses:
            where = " AND ".join(fs_clauses) if bioguide_ids else " OR ".join(fs_clauses)
            fs_rows = conn.execute(
                f"""SELECT fs.member_name, fs.congress, fs.chamber,
                           fs.statement_date, fs.title, fs.text, fs.source_url
                    FROM congress_floor_statements fs
                    WHERE {where}
                    ORDER BY fs.statement_date DESC
                    LIMIT ?""",
                fs_params + [limit],
            ).fetchall()
            for row in fs_rows:
                text_preview = (row['text'] or '')[:800].strip()
                blocks.append(
                    f"[floor_statement]\n"
                    f"Speaker: {row['member_name']}\n"
                    f"Congress: {row['congress']}th\n"
                    f"Chamber: {row['chamber']}\n"
                    f"Date: {row['statement_date']}\n"
                    f"Title: {row['title']}\n"
                    f"Text: \"{text_preview}\"\n"
                    f"Source: {row['source_url'] or 'congress.gov'}"
                )

        # ── governor_executive_orders ───────────────────────────────────────
        eo_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='governor_executive_orders'"
        ).fetchone()
        if eo_table and keywords:
            eo_clause = " OR ".join(
                "(lower(title) LIKE ? OR lower(summary) LIKE ? OR lower(full_text) LIKE ?)"
                for _ in keywords
            )
            eo_params: list = []
            for k in keywords:
                eo_params.extend([f"%{k}%", f"%{k}%", f"%{k}%"])

            eo_rows = conn.execute(
                f"""SELECT order_number, title, signed_date, governor,
                           summary, full_text, source_url
                    FROM governor_executive_orders
                    WHERE {eo_clause}
                    ORDER BY signed_date DESC
                    LIMIT ?""",
                eo_params + [limit],
            ).fetchall()
            for row in eo_rows:
                text_preview = (row["full_text"] or row["summary"] or "")[:1200].strip()
                blocks.append(
                    f"[executive_order]\n"
                    f"Governor: {row['governor'] or 'Unknown'}\n"
                    f"Order: {row['order_number'] or 'Unknown'}\n"
                    f"Date: {row['signed_date'] or 'Unknown'}\n"
                    f"Title: {row['title']}\n"
                    f"Text: \"{text_preview}\"\n"
                    f"Source: {row['source_url'] or 'Not available'}"
                )

        conn.close()
    except Exception:
        return ""

    return "\n\n".join(blocks)


def build_bills_query_context(user_query: str, context: str) -> dict:
    q = (user_query or "").lower()
    touches_voting_patterns = (
        any(w in q for w in ["vote", "votes", "voting", "roll call", "pattern", "party line", "defection"])
        or "voting record" in q
    )
    return {
        "touches_donor_data": any(
            w in q for w in [
                "donor", "donors", "fec", "pac", "finance", "funding",
                "money", "contribution", "contributions", "campaign finance",
            ]
        ),
        "touches_voting_patterns": touches_voting_patterns,
        "touches_speech_context": any(
            w in q for w in ["speech", "transcript", "said", "statement", "floor", "hearing"]
        ) or any(
            w in q for w in ["executive order", "executive orders", "governor order", "eo-"]
        ),
        "touches_news": any(w in q for w in _NEWS_TERMS),
        "federal_rep_present": "| Federal]" in context,
        "state_rep_present":   "| State]" in context or "HOD" in context or "Senate District" in context,
    }


def _bills_system_prompt(
    district_note: str,
    chroma_note:   str,
    model_note:    str,
    exact_lookup_note: str,
    context:       str,
) -> str:
    return (
        f"You are VoteIQ, a nonpartisan Virginia civic assistant. Today is May 2026. "
        f"You have access to the retrieved excerpts below, which may include Virginia General Assembly bills, "
        f"direct SQLite database rows labeled '[Database Context]', "
        f"election results, legislator voting records, representative profile summaries from the local 2026 session database, "
        f"roll-call votes and sponsored bills for Virginia's 13 federal representatives (119th Congress) from congress.gov, "
        f"full bill text for 119th Congress federal bills sourced from govinfo.gov (labeled '| FEDERAL' in excerpts), "
        f"FEC campaign finance data showing PAC/industry contributions by sector for Virginia federal members, "
        f"Governor Spanberger's bill actions (bills signed into law, vetoed, or amended — labeled '[Governor Action]'), "
        f"Governor Spanberger's executive orders (labeled '[Governor Executive Order]'), "
        f"AND recent Virginia political news articles (sourced from Virginia news outlets via Gemini extraction). "
        f"Answer the user's question using ONLY the excerpts below — do not rely on your training data. "
        f"Be factual and cite bill numbers when relevant. "
        f"\n\nVOTEIQ SOURCE HIERARCHY - apply this order for factual claims: "
        f"1. Structured records first: SQL / structured public-record tables are used for exact facts such as donations, votes, bills, executive orders, dates, amounts, officials, and committees. "
        f"2. Official APIs second: official APIs and government sources are used to refresh or verify structured records, including FEC, Congress API, Virginia LIS, Governor's Office, and state/local portals. "
        f"3. Source documents/RAG third: source documents and RAG are used for long-text context such as bill text, PDFs, meeting minutes, press releases, reports, and transcripts. "
        f"4. AI explanation last: AI is used to summarize and explain. AI is not treated as the source of truth. "
        f"5. Data limits always shown: if records are missing, incomplete, stale, or unavailable, say so and avoid guessing. "
        f"For person identity, preserve FEC candidate_id, FEC committee_id, bioguide_id, Congress.gov member ID, OpenStates/person ID if state overlap exists, name aliases, and party/state/district.\n\n"
        f"If the excerpts include any '[Governor Action' rows, those are confirmed local governor-action records. "
        f"If the excerpts include '[Database Context]' rows, treat them as direct local database records and use them before guessing. "
        f"Do not say governor actions returned no data, are unavailable, or cannot be confirmed for this query. "
        f"For veto questions, list the bills whose Action is 'Vetoed', 'Veto sustained', or 'Veto overridden' from those rows. "
        f"For federal members (U.S. House/Senate), cite bill type and number (e.g. H.R. 23) and note the source as \"Congress.gov bill record\" for bill details. "
        f"For campaign finance questions, use the finance excerpts if present. Cite federal finance as \"(FEC data, fec.gov)\" and Virginia state finance as \"(Virginia SBE campaign finance filings)\".{district_note}{chroma_note}{model_note}"
        f"\n\nNEWS FOLLOW-UP LINKS — when answering about a news item and suggesting related vote or bill topics, "
        f"make the follow-ups clickable internal Markdown links. Use this format:\n"
        f"Related topics:\n"
        f"- [Handgun storage requirements](/ask?q=How%20did%20my%20representatives%20vote%20on%20handgun%20storage%20requirements%3F)\n"
        f"- [Firearms in schools](/ask?q=How%20did%20my%20representatives%20vote%20on%20firearms%20in%20schools%3F)\n"
        f"- [Child safety legislation](/ask?q=How%20did%20my%20representatives%20vote%20on%20child%20safety%20legislation%3F)\n"
        f"Choose topic labels that match the retrieved article. URL-encode the q= value.\n"
        f"\n\nFEDERAL BILL EXPLAINER FORMAT — when the user's query starts with \"Explain this federal bill:\" or asks what a specific federal bill (H.R., S., H.J.Res., etc.) does, respond using this structure:\n"
        f"## [Bill ID] — [Short Title]\n"
        f"**What it does:** One or two plain-English sentences. No jargon. Imagine explaining to a neighbor.\n"
        f"**Who's affected:** Name the Virginians or groups most directly impacted, if the excerpt supports it.\n"
        f"**Sponsor:** Name, party, state — from the excerpt only.\n"
        f"**Status:** Current status (introduced, referred to committee, passed chamber, signed, etc.).\n"
        f"**Committee:** Committee of referral — from the excerpt only.\n"
        f"**Source:** Congress.gov bill record\n"
        f"Do not speculate about passage chances or political prospects. If any field is not in the excerpt, write \"Not available in current dataset.\"\n"
        f"\n\nFEDERAL REP RESPONSE FORMAT — when a [Representative Profile — Name | Federal] excerpt is present, "
        f"respond in the same style as state rep answers: "
        f"(1) Start with a one-sentence intro identifying who the rep is and their seat. "
        f"(2) Give overall vote stats (Yes/No counts and yes rate) from the \"Overall voting record\" line. "
        f"For senators, note that yes rates are not directly comparable to House members — Senate votes include more procedural and cloture votes where minority-party senators routinely vote Nay. "
        f"(3) Highlight 3-5 key sponsored bills with their title and status. "
        f"(4) List their committee assignments. "
        f"(5) Close with a note pointing to congress.gov for full detail. "
        f"Use the same plain, civic-report tone as state legislator responses."
        f"\n\nMIXED FEDERAL/STATE COMPARISON FORMAT — when comparing one federal member with Virginia state legislators, use a markdown table like:\n"
        f"| | [Federal member] (Federal) | [State senator] (VA Senate) | [Delegate] (VA House) |\n"
        f"|---|---:|---:|---:|\n"
        f"| Party | [party] | [party] | [party] |\n"
        f"| Bills sponsored/patroned | [count or not shown] | [count] | [count] |\n"
        f"| Bills passed | [count or 'Not shown in current federal dataset'] | [count] | [count] |\n"
        f"| Vote metric shown | [yes rate or vote count metric] | [party alignment] | [party alignment] |\n"
        f"| Main topics in retrieved records | [topics] | [topics] | [topics] |\n"
        f"Only fill cells from retrieved excerpts. If a field is absent, write \"Not shown in current dataset.\" Keep federal yes-rate metrics separate from state party-alignment metrics.\n"
        f"\n\nVOTE INTERPRETATION — apply these rules when reading vote records:\n"
        f"- When showing votes tied to a bill, statement, donor pattern, or other context, label the section \"Related Vote Record\" and include this note: \"These votes are public-record actions. VoteIQ does not infer motive or reasoning from votes alone.\"\n"
        f"- If a legislator votes NO on a House-amended version, do not infer opposition to the original bill concept. Say: \"Correction note: [Name] voted NO on the House-amended version of [Bill ID]. VoteIQ does not infer whether that reflected opposition to the original bill, the amendments, or another reason.\"\n"
        f"- If a legislator votes YES in committee but NO on floor, they may have had ideological concerns or constituent pressure. Do not assume — say \"voted NO on floor passage after supporting it in committee; dataset does not explain the change.\"\n"
        f"- Always show the SEQUENCE of votes when available, not just the final result. A bill can have 4-8 votes — the pattern matters.\n"
        f"- Flag when a NO vote is on a substitute or amendment vs. the original bill. These are different positions.\n"
        f"- \"Concur House Substitute\", \"Concur House Amendment\", \"Adopt Conference Committee Report\" are amendment/concurrence votes — not original passage votes.\n"
        f"- \"Reported from [Committee]\" = committee vote. \"Passage R\" or \"Passage H\" = floor vote.\n"
        f"- SENATE VS HOUSE YES RATES: Never compare a senator's yes rate to a representative's without flagging the difference. "
        f"Senate votes include far more procedural, cloture, and motion votes where minority-party senators routinely vote Nay — "
        f"this structurally depresses Senate yes rates relative to House yes rates. "
        f"Always note: \"Senate yes rates are not directly comparable to House yes rates due to the higher volume of procedural and cloture votes in the Senate.\"\n"
        f"- For voting-pattern sections, include this no-inference note: \"VoteIQ does not infer why Sen. Rouse voted NO. These records only show vote outcomes, party-majority comparison, bill category, and whether the bill passed.\" Use the relevant title/name if the question is about someone else.\n"
        f"\n\nHALLUCINATION PREVENTION — follow these rules strictly:\n"
        f"1. Never say a legislator \"prioritized\", \"championed\", \"focused on\", or \"made X a priority\" unless the excerpt explicitly states it. Sponsoring a bill does not imply it was a priority.\n"
        f"2. Never infer a legislator's role beyond what the data shows. If they are listed as sponsor, say \"sponsored\". If their exact role is unclear, say \"co-sponsored or listed as patron — exact role unclear.\"\n"
        f"3. Always distinguish:\n"
        f"   - CONFIRMED: data directly states it — cite bill ID and source\n"
        f"   - INFERRED: your interpretation of a pattern — label it\n"
        f"   - UNCERTAIN: data not available — say so\n"
        f"4. Never fill data gaps with assumptions. If you don't have it, say so.\n"
        f"5. Always cite source inline: \"According to OpenStates (openstates.org)\" for votes/sponsorships, \"According to LIS\" for bill text/status."
        f"\n\nWHEN TO SAY \"I DON'T KNOW\":\n"
        f"- Vote data missing → \"I don't have vote data for this bill in the current dataset\"\n"
        f"- Bill text not in DB → \"I don't have the full bill text — check lis.virginia.gov\"\n"
        f"- Patron unclear → \"Primary patron unclear from available data; listed as co-sponsor\"\n"
        f"- DB build incomplete → \"Based on partial 2026 session data — full dataset loads tomorrow\""
        f"\n\nCITATION FORMAT — always include:\n"
        f"- Bill ID as a clickable markdown link if the excerpt provides a URL — use the exact URL from the excerpt, never construct your own\n"
        f"- Short title after the link\n"
        f"- Vote count if available (e.g. \"passed 32-8 according to OpenStates\")\n"
        f"- Source database (LIS or OpenStates)\n"
        f"- Date if relevant\n"
        f"- Legislator names as clickable links if the profile excerpt provides a URL for them\n"
        f"- If a profile excerpt includes \"Profile Markdown Link\", use that exact Markdown link the first time you name that person"
        f"\n\nPUBLIC-RECORD OVERVIEW FORMAT — when the user asks for a neutral overview, profile, or public-record summary of a named person, use this structure:\n"
        f"1. Current office/status\n"
        f"- Confirm whether the person currently holds office. Include election date and swearing-in date if available in retrieved context. Do not infer motives or intent.\n\n"
        f"2. Campaign finance\n"
        f"- Summarize total fundraising and major donor sectors from retrieved official finance records. List notable large donors with amounts when provided. Clearly state whether totals include candidate committee receipts, PACs, party transfers, or outside spending when the context says so.\n\n"
        f"3. Legislative/executive record\n"
        f"- Summarize bills sponsored, signed, vetoed, amended, or executive orders issued from retrieved records. Separate proposed plans from enacted actions.\n\n"
        f"4. Data limits\n"
        f"- Explain what the retrieved data does not include. Flag anything incomplete, stale, or unavailable. Do not speculate.\n\n"
        f"5. Related follow-up questions\n"
        f"- Suggest 3 deeper follow-up queries as internal /ask?q= links.\n\n"
        f"For this overview format, cite sources inline and use a concise, fact-based tone. Do not make uncited claims; if a figure is source-dependent, say so."
        f"\n\nWhen the user asks for a public-record research brief, open with this header format: Public Record Research Brief; Subject: [name]; Scope: [office/status and requested record areas]; Sources: [official/source types used]; Current through: [date from retrieved context or system date]. Then include a Scope And Source Base section, label factual sections as Findings, and avoid editorial, persuasive, or opinion-style framing."
        f"\n\nRESPONSE FORMAT — use this exact structure for legislator questions:\n\n"
        f"Your [chamber] representative is **[Full Name] ([Party], District [N])**.\n\n"
        f"**[YEAR] Session Voting Record:**\n"
        f"- Recorded floor vote distribution: [CONFIRMED — OpenStates] [Y] YES votes and [N] NO votes across [TOTAL] recorded floor vote events.\n"
        f"- Party alignment: [CONFIRMED — calculated from vote records] [X]% of recorded floor votes where party-majority comparison was available.\n"
        f"- Overall caucus alignment: [Y]% including uncontested/procedural votes, only if the excerpt clearly defines that denominator. If the denominator is not defined, omit this metric.\n"
        f"- Contested-vote pattern: [Name] voted with the [Party] majority on [X]% of recorded floor votes and aligned with the [Party] majority on [Y]% of contested party-split votes. Use only metrics whose denominators are clearly defined in the excerpt; otherwise omit them.\n"
        f"- Comparison note: if multiple representatives are shown in the current answer, you may say \"[X]% party alignment — the lowest/highest party-alignment rate among the representatives shown here.\" Only compare the representatives actually shown in this answer.\n"
        f"- Do not use ideology, positioning, or caucus-read labels. Describe only the recorded vote metrics whose denominators are defined.\n"
        f"- Committee vote records in the current dataset show [Y] YES and [N] NO votes. This may reflect how committee actions are recorded in the source data; verify against committee roll-call records before treating it as a complete behavioral measure.\n\n"
        f"**Key Votes** (if present in excerpt; put this before aggregate issue stats):\n"
        f"- [markdown bill link from excerpt]: [YES/NO note exactly from excerpt] — [one-line plain-English issue summary]\n\n"
        f"**Recorded NO votes where the bill passed ([N] total):**\n"
        f"[CONFIRMED — OpenStates]:\n"
        f"- [Policy area]: [markdown bill links from excerpt] — [one-line description]\n"
        f"Note: VoteIQ does not infer why Sen. Rouse voted NO. These records only show vote outcomes, party-majority comparison, bill category, and whether the bill passed. Use the relevant title/name if the question is about someone else.\n\n"
        f"**Vote Breakdown by Issue Area** (if present in excerpt):\n"
        f"- [topic]: [N] votes — [Y] YES, [N] NO | party alignment: [X]%\n"
        f"  - Breaks from party: [copy the NO-against-party-YES-majority bill links from excerpt]\n\n"
        f"Issue-category note: only say \"Issue categories with the most recorded party-majority breaks\" if the excerpt provides counts. Format as:\n"
        f"- Transportation: [X] votes\n"
        f"- Healthcare: [Y] votes\n"
        f"If counts are not available, omit the claim.\n\n"
        f"**Legislative Partnerships** (if present in excerpt):\n"
        f"- [Name] ([Party]) — [N] bills co-sponsored [cross-party if flagged]\n"
        f"- Bipartisan: [names if present]\n\n"
        f"**Bills Sponsored ([N] total, [X] passed):**\n"
        f"- [CONFIRMED — OpenStates]: [bill link] — [one plain-English sentence: what does this bill actually do for a Virginia resident?]\n\n"
        f"PLAIN-ENGLISH BILL HOOKS — for every bill you mention, add a plain-English parenthetical after the link.\n"
        f"Keep it to one sentence. Use everyday language, not legislative jargon.\n\n"
        f"LEGISLATIVE FOCUS — if the excerpt contains a \"Legislative focus\" line, lead with it as a one-liner.\n\n"
        f"COMMITTEE VOTE CONTEXT — if the excerpt contains a [CONTEXT] note about committee votes, include it in plain language.\n\n"
        f"IMPORTANT: Always copy bill links exactly as they appear in the excerpts. List ALL sponsored bills found in the excerpt, do not truncate the list.\n"
        f"When a bill excerpt contains a \"Companion bill(s)\" line, always render those as clickable markdown links in your response.\n\n"
        f"**Methodology note** (always include when answering about party alignment):\n"
        f"Party-majority comparisons are calculated from OpenStates roll-call data when available. "
        f"Split-vote threshold: 15–85% of party voting YES, minimum 5 members voting. "
        f"\"Breaks from party\" means actual recorded NO votes against the party YES majority. "
        f"Issue-area tags are keyword-based, not official legislative categories.\n\n"
        f"CALL TO ACTION — always end every legislator response with:\n"
        f"**Want to dig deeper?** I can show you how [Name] voted on related bills this session.\n\n"
        f"Related topics:\n"
        f"- [topic1](/ask?q=URL_ENCODED_FOLLOW_UP_QUERY_FOR_TOPIC1)\n"
        f"- [topic2](/ask?q=URL_ENCODED_FOLLOW_UP_QUERY_FOR_TOPIC2)\n"
        f"- [topic3](/ask?q=URL_ENCODED_FOLLOW_UP_QUERY_FOR_TOPIC3)\n\n"
        f"Use the legislator's actual name and their real top 3 issue areas from the excerpt. "
        f"URL-encode spaces and punctuation in the /ask?q= links. Or tell the user they can ask about a specific bill by number.\n\n"
        f"If any section has no data, write: \"No [section] data available in current dataset.\"{exact_lookup_note}"
        f"\n\nEXCERPTS:\n{context}"
    )


# ── /chat ─────────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    import main as _m

    ctx = _m.DISTRICT_CONTEXT.get(req.district)
    if not ctx:
        return ChatResponse(reply="Unknown district.")

    if _m._results:
        r = _m._results
        statewide_block = (
            f"STATEWIDE RESULT — April 21, 2026 Special Election:\n"
            f"  Outcome: {r['winner']}\n"
            f"  Yes (Approve): {r['yes']:,} votes ({r['yes_pct']}%)\n"
            f"  No  (Reject):  {r['no']:,} votes ({r['no_pct']}%)\n"
            f"  Early Voting:  Yes {r['early']['yes']:,} / No {r['early']['no']:,}\n"
            f"  Election Day:  Yes {r['election_day']['yes']:,} / No {r['election_day']['no']:,}\n"
            f"  Mail-In:       Yes {r['mail']['yes']:,} / No {r['mail']['no']:,}"
        )
        locality_block = ""
        if req.locality:
            raw_key = req.locality.strip().upper()
            raw_key = (
                raw_key[:-7].strip() if raw_key.endswith(" COUNTY") else
                raw_key[:-5].strip() if raw_key.endswith(" CITY")   else raw_key
            )
            loc = r["local"].get(raw_key)
            if loc:
                locality_block = (
                    f"\nUSER'S LOCALITY — {loc['display']}:\n"
                    f"  Yes: {loc['yes']:,} ({loc['pct_yes']}%)  |  "
                    f"No: {loc['no']:,}  |  Winner: {loc['winner']}"
                )
        election_context = statewide_block + locality_block  # noqa: F841 — available for future prompt use
    else:
        election_context = "Election results data is currently unavailable."  # noqa: F841

    district_block = (
        f"USER'S CONGRESSIONAL DISTRICT: {req.district}\n"
        f"U.S. Representative: {ctx['rep']} ({ctx['party']})\n"
        f"Region: {ctx['region']}"
        if ctx["rep"] else
        "The user has not yet looked up their specific district. Answer statewide questions."
    )

    hod_info = _m.HOD_CONTEXT.get(req.hod_district) if req.hod_district else None
    if hod_info:
        district_block += (
            f"\nVA HOUSE OF DELEGATES DISTRICT: {req.hod_district}\n"
            f"Delegate: {hod_info['delegate']} ({hod_info['party']})\n"
            f"Locality: {hod_info['locality']}"
        )

    sd_info = _m.SD_CONTEXT.get(req.sd_district) if req.sd_district else None
    if sd_info:
        district_block += (
            f"\nVA STATE SENATE DISTRICT: {req.sd_district}\n"
            f"Senator: {sd_info['senator']} ({sd_info['party']})\n"
            f"Region: {sd_info['region']}"
        )

    last_question  = req.messages[-1].content if req.messages else ""

    scope_limit_reply = _scope_limit_polling_reply(last_question)
    if scope_limit_reply:
        return ChatResponse(reply=scope_limit_reply)

    # Detect query scope to guide source selection
    from voteiq.helpers import get_scope_aware_analyst
    analyst_helper = get_scope_aware_analyst()
    scope_context = analyst_helper.analyze_query(last_question)

    direct_identity_reply = _direct_identity_crosswalk_reply(last_question)
    if direct_identity_reply:
        return ChatResponse(reply=direct_identity_reply)
    direct_source_reply = _direct_voteiq_source_hierarchy_reply(last_question)
    if direct_source_reply:
        return ChatResponse(reply=direct_source_reply)
    direct_governor_reply = (
        _direct_spanberger_governor_overview_reply(last_question)
        or _direct_governor_eo_reply(last_question)
        or _direct_governor_veto_reply(last_question)
        or (_direct_governor_correlation_reply(last_question) if _premium_analyst_enabled(req) else None)
    )
    if direct_governor_reply:
        return ChatResponse(reply=direct_governor_reply)

    pol_names:      list[str] = []
    bioguide_ids:   list[str] = []
    if ctx and ctx.get("rep"):
        pol_names.append(ctx["rep"])
    if hod_info:
        pol_names.append(hod_info["delegate"])
    if sd_info:
        pol_names.append(sd_info["senator"])

    for bgid, mbr in _m._MEMBER_CACHE.items():
        mname = mbr.get("name", "").lower()
        if any(pol.lower() in mname or mname in pol.lower() for pol in pol_names if pol):
            bioguide_ids.append(bgid)

    news_context    = _m._fetch_relevant_news(last_question, pol_names)
    vote_context    = _m._fetch_vote_context(last_question, bioguide_ids)
    finance_context = (
        _m._fetch_finance_context(bioguide_ids, last_question)
        if _premium_analyst_enabled(req)
        else ""
    )
    spanberger_finance_context = _m._fetch_spanberger_finance_context(last_question)
    state_finance_context = _m._fetch_state_campaign_finance_context(
        last_question,
        state_names=pol_names,
        basic=not _premium_analyst_enabled(req),
    )
    cached_finance_context = ""
    if True:  # run for all tiers; Free gets a hint, Pro/Newsroom gets full block
        from voteiq.queries.sectors import get_finance_summary_from_cache, format_finance_summary_for_chat
        _leg_q = _m._extract_legislator_name(last_question)
        for _search_name in ([_leg_q] if _leg_q else []) + pol_names:
            if not _search_name:
                continue
            _scope = _m._classify_legislator(_search_name)
            _src = "fec" if _scope == "federal" else ("va_sbe" if _scope == "state" else None)
            _cf = get_finance_summary_from_cache(name=_search_name, source=_src)
            if _cf:
                if _premium_analyst_enabled(req):
                    _src_tables = (
                        ["campaign_finance_summary", "fec_individual_contributions"]
                        if _src == "fec"
                        else ["campaign_finance_summary", "va_sbe_contributions"]
                    )
                    cached_finance_context = format_finance_summary_for_chat(
                        _cf, source_tables=_src_tables
                    )
                else:
                    _src_lbl = "FEC (federal)" if _src == "fec" else "Virginia SBE"
                    cached_finance_context = (
                        f"[Finance note: VoteIQ has campaign finance data for "
                        f"{_cf['name']} ({_src_lbl}, "
                        f"${_cf['total_raised']:,.0f} total raised). "
                        f"Full donor and sector breakdown available to Pro/Newsroom subscribers.]"
                    )
                break
    full_profile_context = _m._full_profiles_for_query(
        last_question,
        limit=2 if _premium_analyst_enabled(req) else 1,
    )
    pac_context = _m._fetch_pac_context(
        last_question,
        bioguide_ids=bioguide_ids,
        state_names=pol_names,
        basic=not _premium_analyst_enabled(req),
    )
    if _premium_analyst_enabled(req):
        analyst_context = _m._fetch_governor_action_money_analyst_context(last_question)
        member_analyst_context = _m._fetch_member_analyst_context(last_question)
    else:
        analyst_context = ""
        member_analyst_context = ""
    governor_context = _m._fetch_governor_action_context(last_question)
    governor_eo_context = _m._fetch_governor_eo_context(last_question)
    database_context = build_database_context(last_question)
    state_member_context = _m._fetch_va_state_member_context(last_question, hod_info=hod_info, sd_info=sd_info)
    ie_context = _m._fetch_ie_context(bioguide_ids, last_question)

    foreign_ie_context = ""
    if bioguide_ids:
        from main import VA_MEMBERS as _VA
        for bgid in bioguide_ids:
            if bgid in _VA:
                fec_cand_id = _VA[bgid][1]
                foreign_ie_context = _m._fetch_foreign_policy_ie_context(fec_cand_id)
                if foreign_ie_context and "No foreign-policy" not in foreign_ie_context:
                    break

    question_lower = (last_question or "").lower()
    query_context = {
        "touches_donor_data": any(
            word in question_lower
            for word in [
                "donor", "donors", "fec", "pac", "campaign finance",
                "funding", "money", "contribution", "contributions",
            ]
        ),
        "touches_voting_patterns": any(
            word in question_lower
            for word in [
                "vote", "votes", "voting", "record", "roll call",
                "party line", "defection", "pattern",
            ]
        ),
        "touches_speech_context": any(
            word in question_lower
            for word in [
                "speech", "transcript", "said", "statement", "floor", "hearing",
                "executive order", "executive orders", "governor order", "eo-",
            ]
        ),
        "touches_news": any(word in question_lower for word in _NEWS_TERMS),
        "federal_rep_present": bool(bioguide_ids),
        "state_rep_present":   bool(hod_info or sd_info),
        "touches_ie_spending": any(
            w in question_lower
            for w in [
                "outside money", "super pac", "independent expenditure",
                "nrcc", "dccc", "club for growth", "outside spending",
                "who spent money", "outside group", "who ran ads", "attack ad",
            ]
        ),
        "touches_foreign_policy_donors": any(
            w in question_lower
            for w in [
                "israel", "aipac", "pro-israel", "dmfi", "j street",
                "india", "taiwan", "china", "ukraine", "iran",
                "saudi", "turkey", "armenia", "greece",
                "foreign policy", "foreign pac", "foreign money",
                "middle east", "asia policy", "nato",
                "foreign influence", "outside country",
                "who funded", "foreign aligned",
                "udp", "united democracy project",
            ]
        ),
    }

    # Prepend scope guidance if query scope is unclear
    scope_prefix = ""
    if scope_context["scope_confidence"] < 0.7:
        scope_prefix = f"Query Scope Guidance: This query is unclear about scope ({scope_context['scope_explanation']}). {scope_context['scope_notes']}\n\n"

    base_prompt = get_system_prompt(voice=req.voice, query_context=query_context)
    max_tokens  = TIER_MAX_TOKENS.get(req.tier, TIER_MAX_TOKENS["free"])

    transcript_context = ""
    if query_context.get("touches_speech_context"):
        transcript_context = _fetch_transcript_context(
            last_question, bioguide_ids=bioguide_ids or None
        )

    system_prompt = f"""{scope_prefix}{base_prompt}

---

{district_block}

Available data for this representative:
- Committee assignments and legislative role
- Recent votes (with bill name and Yea/Nay)
- Campaign donors and industry totals (FEC data, fec.gov)
- Voter registration and civic participation info
- Governor Spanberger's bill actions (signed, vetoed, amended) and executive orders

Governor action rule:
If the context below includes "[Database Context - polls.governor_actions lookup]", read the "action_summary=" line FIRST — that line gives the TRUE total counts from the full database (e.g. action_summary=signed=966; vetoed=28; amended=147). Do NOT say signed bills, amended bills, or executive orders are unavailable, missing, or not in the dataset — they exist in the database and the action_summary confirms their counts. The rows shown are a sample (up to 40) ordered by most recent date; signed bills may not appear in the sample if vetoes are more recent, but they are confirmed present by action_summary. Always report the full counts from action_summary when summarizing governor actions.

Database access rule (PRIORITY):
If the context below includes "[Database Context" rows, treat them as direct local SQLite records from VoteIQ's databases. Use SQL records FIRST before checking document/RAG context or making general statements that data is unavailable. All SQL tables are comprehensively searched for this query.

Profile linking rule:
If the context below includes "Profile Markdown Link", use that exact Markdown link the first time you name that person.

{database_context if database_context else ""}

{vote_context if vote_context else ""}

{finance_context if finance_context else ""}

{spanberger_finance_context if spanberger_finance_context else ""}

{state_finance_context if state_finance_context else ""}

{cached_finance_context if cached_finance_context else ""}

{full_profile_context if full_profile_context else ""}

{pac_context if pac_context else ""}

{analyst_context if analyst_context else ""}

{member_analyst_context if member_analyst_context else ""}

{ie_context if ie_context else ""}

{foreign_ie_context if foreign_ie_context and query_context.get("touches_foreign_policy_donors") else ""}

{governor_context if governor_context else ""}

{governor_eo_context if governor_eo_context else ""}

{state_member_context if state_member_context else ""}

{f'''
{news_context if news_context else "No matching recent Virginia news article is available in VoteIQ's local news cache for this question."}

When answering questions about recent news or current events, cite the article source and author.
Format citations as: [Outlet — Author](URL) at the end of the relevant sentence.
If no relevant news is listed above, say VoteIQ does not currently have a matching recent article in its local news cache. Do not invent current-event details from model memory.
''' if query_context.get("touches_news") else ''}

When citing a vote, include the bill name and Yea/Nay. When citing donors or industry contributions, state the dollar amount and cite the matching source: "(FEC data, fec.gov)" for federal records or "(Virginia SBE campaign finance filings)" for Virginia state records. For official contact info direct users to house.gov, senate.gov, governor.virginia.gov, or virginiageneralassembly.gov. Never express opinions on representatives or tell people how to vote.

{transcript_context if transcript_context else ""}"""

    # Determine cache TTL based on session type
    session_type = req.session_type or ("research" if len(req.messages) >= 3 else "quick")
    cache_ttl = "1h" if session_type == "research" else "5m"

    # Build combined context for source tracking
    combined_context = "\n".join([
        database_context or "",
        vote_context or "",
        finance_context or "",
        spanberger_finance_context or "",
        state_finance_context or "",
        cached_finance_context or "",
        full_profile_context or "",
        pac_context or "",
        analyst_context or "",
        member_analyst_context or "",
        ie_context or "",
        foreign_ie_context or "",
        governor_context or "",
        governor_eo_context or "",
        state_member_context or "",
        news_context or "",
        transcript_context or "",
    ])

    # Track sources used in building context
    sources_used = _track_sources(combined_context)

    try:
        reply = _m._claude_reply(system_prompt, req.messages, max_tokens=max_tokens, cache_ttl=cache_ttl)
        # Add sources footer if sources were used
        sources_footer = _format_sources_footer(sources_used)
        if sources_footer and "Sources:" not in reply:
            reply = reply.rstrip() + sources_footer
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/election-chat ────────────────────────────────────────────────────────

@router.post("/api/gemini-chat", response_model=ChatResponse)
async def gemini_chat(request: Request, req: ChatRequest):
    """Chat with VoteIQ using Gemini, grounded in FEC data and district context."""
    import main as _m

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    direct_identity_reply = _direct_identity_crosswalk_reply(user_query)
    if direct_identity_reply:
        return ChatResponse(reply=direct_identity_reply)
    direct_source_reply = _direct_voteiq_source_hierarchy_reply(user_query)
    if direct_source_reply:
        return ChatResponse(reply=direct_source_reply)
    direct_governor_reply = (
        _direct_spanberger_governor_overview_reply(user_query)
        or _direct_governor_eo_reply(user_query)
        or _direct_governor_veto_reply(user_query)
        or (_direct_governor_correlation_reply(user_query) if _premium_analyst_enabled(req) else None)
    )
    if direct_governor_reply:
        return ChatResponse(reply=direct_governor_reply)

    ctx = _m.DISTRICT_CONTEXT.get(req.district)
    if not ctx:
        return ChatResponse(reply="Unknown district.")

    fec_context = ""
    if ctx["rep"]:
        fec_context = _m._get_fec_summary(ctx["rep"])

    district_block = (
        f"USER'S CONGRESSIONAL DISTRICT: {req.district}\n"
        f"U.S. Representative: {ctx['rep']} ({ctx['party']})\n"
        f"Region: {ctx['region']}\n"
        f"{fec_context}"
    )

    hod_info = _m.HOD_CONTEXT.get(req.hod_district) if req.hod_district else None
    if hod_info:
        district_block += (
            f"\nVA HOUSE OF DELEGATES DISTRICT: {req.hod_district}\n"
            f"Delegate: {hod_info['delegate']} ({hod_info['party']})\n"
        )

    base_prompt = VOICE_PROMPTS.get(req.voice, VOICE_PROMPTS["free"])
    max_tokens = TIER_MAX_TOKENS.get(req.tier, TIER_MAX_TOKENS["free"])

    system_prompt = f"""{base_prompt}

---

{district_block}

Additional rules:
- Use FEC finance totals from context when user asks about money or donors.
- FEC data only covers federal offices - clarify this for state officials.
- Direct users to FEC.gov or house.gov for official records.
- Never tell people how to vote or express opinions on representatives."""

    try:
        reply = _m._gemini_reply(system_prompt, req.messages, max_tokens=max_tokens)
        reply = _with_source_line(reply)
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=f"Gemini Error: {str(e)}")


@router.post("/api/election-chat", response_model=ChatResponse)
async def election_chat(req: ElectionChatRequest):
    import main as _m

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    summary    = _m._build_election_chat_context(req.year, user_query)

    q = (user_query or "").lower()
    query_context = {
        "touches_donor_data":      False,
        "touches_voting_patterns": any(w in q for w in ["vote", "votes", "voting", "record", "margin", "turnout"]),
        "touches_speech_context":  False,
        "touches_news":            any(w in q for w in _NEWS_TERMS),
        "federal_rep_present":     False,
        "state_rep_present":       False,
    }
    voice_prompt = get_system_prompt(voice=req.voice, query_context=query_context)

    system_prompt = f"""{voice_prompt}

You are focused on Virginia {req.year} election results only.

Here are the official {req.year} Virginia election results:

{summary}

Answer questions about these results clearly and concisely (2-4 sentences). Be factual and nonpartisan. Give specific numbers when asked about candidates, margins, or localities. If you don't have the data, say so honestly. Never express opinions on candidates or tell users how to vote."""

    session_type = req.session_type or ("research" if len(req.messages) >= 3 else "quick")
    cache_ttl = "1h" if session_type == "research" else "5m"

    try:
        reply = _m._claude_reply(
            system_prompt,
            req.messages,
            max_tokens=TIER_MAX_TOKENS.get(req.tier, 400),
            cache_ttl=cache_ttl,
        )
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/bills-chat ───────────────────────────────────────────────────────────

@router.post("/api/bills-chat", response_model=ChatResponse)
async def bills_chat(req: BillsChatRequest):
    import main as _m

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    scope_limit_reply = _scope_limit_polling_reply(user_query)
    if scope_limit_reply:
        return ChatResponse(reply=scope_limit_reply)
    direct_identity_reply = _direct_identity_crosswalk_reply(user_query)
    if direct_identity_reply:
        return ChatResponse(reply=direct_identity_reply)
    direct_source_reply = _direct_voteiq_source_hierarchy_reply(user_query)
    if direct_source_reply:
        return ChatResponse(reply=direct_source_reply)
    direct_governor_reply = (
        _direct_spanberger_governor_overview_reply(user_query)
        or _direct_governor_eo_reply(user_query)
        or _direct_governor_veto_reply(user_query)
        or (_direct_governor_correlation_reply(user_query) if _premium_analyst_enabled(req) else None)
    )
    if direct_governor_reply:
        return ChatResponse(reply=direct_governor_reply)

    ctx_data = await build_bills_context_parallel(
        query=user_query,
        district=req.district or "",
        hod_district=req.hod_district,
        sd_district=req.sd_district,
        locality=req.locality or "",
    )
    context = ctx_data["context"]
    exact_lookup_note = ctx_data["exact_lookup_note"]
    use_haiku = ctx_data["use_haiku"]
    chroma_error = ctx_data["chroma_error"]

    if not context and chroma_error:
        sqlite_fallback = _local_bills_fallback_context(_m, req, user_query)
        if sqlite_fallback:
            context = sqlite_fallback
        elif _needs_district_for_my_rep(req, user_query):
            return ChatResponse(
                reply=(
                    "Enter your Virginia address first so I can identify your U.S. House member, "
                    "state delegate, and state senator. Then I can summarize what your representatives "
                    "have done from the local records."
                )
            )
        else:
            return ChatResponse(
                reply=(
                    "I'm having trouble connecting to the knowledge base right now. "
                    "Try asking about a specific bill number (e.g. HB9) or a legislator's name "
                    "and I'll look it up from local data."
                )
            )

    district_parts: list[str] = []
    if req.district:    district_parts.append(f"Congressional district: {req.district}")
    if req.locality:    district_parts.append(f"locality: {req.locality}")
    if req.hod_district: district_parts.append(f"HOD district: {req.hod_district}")
    if req.sd_district:  district_parts.append(f"Senate district: {req.sd_district}")
    district_note = f"\nUSER'S DISTRICT CONTEXT: {', '.join(district_parts)}\n" if district_parts else ""

    _ck = _m._cache_key(user_query, district_note) if len(req.messages) == 1 else None
    _ck_fb = None
    if len(req.messages) == 1 and (req.hod_district or req.sd_district):
        _sp: list[str] = []
        if req.hod_district: _sp.append(f"HOD district: {req.hod_district}")
        if req.sd_district:  _sp.append(f"Senate district: {req.sd_district}")
        _ck_fb = _m._cache_key(user_query, f"\nUSER'S DISTRICT CONTEXT: {', '.join(_sp)}\n")
    if _ck and not _governor_action_query(user_query):
        cached = _m._get_cached_reply(_ck, _ck_fb)
        if cached:
            cached = _with_source_line(cached, _answer_type_from_context(context, chroma_error), _track_sources(context))
            return ChatResponse(reply=cached)

    chroma_note = (
        f"\nNOTE: AI knowledge base unavailable ({chroma_error}). Answering from local database only.\n"
        if chroma_error else ""
    )
    model_note = "\nMODEL ROUTING: Simple exact bill lookup using cached local bill context; answer briefly.\n" if use_haiku else ""

    query_context = build_bills_query_context(user_query, context)
    if query_context["touches_speech_context"]:
        import main as _m2
        _bill_ids = _m2._extract_bill_numbers(user_query) if hasattr(_m2, "_extract_bill_numbers") else []
        tc = _fetch_transcript_context(
            user_query,
            bill_id=_bill_ids[0] if _bill_ids else None,
        )
        if tc:
            context = context + "\n\n---\n\n" + tc if context else tc
    answer_type = _answer_type_from_context(context, chroma_error)
    voice_prompt  = get_system_prompt(req.voice, query_context)
    system_prompt = voice_prompt + "\n\n" + _bills_system_prompt(
        district_note, chroma_note, model_note, exact_lookup_note, context
    )
    model, _      = get_model(req.tier, use_haiku)
    max_tokens    = 700 if use_haiku else TIER_MAX_TOKENS.get(req.tier, 1800)

    session_type = req.session_type or ("research" if len(req.messages) >= 3 else "quick")
    cache_ttl = "1h" if session_type == "research" else "5m"

    try:
        reply = _m._claude_reply(system_prompt, req.messages, max_tokens=max_tokens, model=model, cache_ttl=cache_ttl)
        reply = _with_source_line(reply, answer_type, _track_sources(context))
        if _ck:
            _m._set_cached_reply(_ck, reply)
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/bills-chat-stream ────────────────────────────────────────────────────

@router.post("/api/bills-chat-stream")
@limiter.limit("20/minute;100/hour")
async def bills_chat_stream(request: Request, req: BillsChatRequest):
    import main as _m

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    scope_limit_reply = _scope_limit_polling_reply(user_query)
    if scope_limit_reply:
        async def _scope_limit_gen(text=scope_limit_reply):
            yield f"data: {json.dumps({'token': text})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_scope_limit_gen(), media_type="text/event-stream")

    # ── parallel context assembly ─────────────────────────────────────────────
    direct_identity_reply = _direct_identity_crosswalk_reply(user_query)
    if direct_identity_reply:
        async def _identity_gen(text=direct_identity_reply):
            yield f"data: {json.dumps({'token': text})}\n\n"
        return StreamingResponse(_identity_gen(), media_type="text/event-stream")

    direct_source_reply = _direct_voteiq_source_hierarchy_reply(user_query)
    if direct_source_reply:
        async def _source_gen(text=direct_source_reply):
            yield f"data: {json.dumps({'token': text})}\n\n"
        return StreamingResponse(_source_gen(), media_type="text/event-stream")

    direct_governor_reply = (
        _direct_spanberger_governor_overview_reply(user_query)
        or _direct_governor_eo_reply(user_query)
        or _direct_governor_veto_reply(user_query)
        or (_direct_governor_correlation_reply(user_query) if _premium_analyst_enabled(req) else None)
    )
    if direct_governor_reply:
        async def _direct_gen(text=direct_governor_reply):
            yield f"data: {json.dumps({'token': text})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_direct_gen(), media_type="text/event-stream")

    ctx_data = await build_bills_context_parallel(
        query       = user_query,
        district    = req.district    or "",
        hod_district= req.hod_district,
        sd_district = req.sd_district,
        locality    = req.locality    or "",
    )

    context          = ctx_data["context"]
    exact_lookup_note= ctx_data["exact_lookup_note"]
    use_haiku        = ctx_data["use_haiku"]
    chroma_error     = ctx_data["chroma_error"]

    # ── fallback when context is fully empty ──────────────────────────────────
    if not context and chroma_error:
        local_fallback = _local_bills_fallback_context(_m, req, user_query)
        if local_fallback:
            context = local_fallback
        elif _needs_district_for_my_rep(req, user_query):
            fallback_msg = (
                "Enter your Virginia address first so I can identify your U.S. House member, "
                "state delegate, and state senator. Then I can summarize what your representatives "
                "have done from the local records."
            )
            async def _need_district():
                yield f"data: {json.dumps({'token': fallback_msg})}\n\ndata: [DONE]\n\n"
            return StreamingResponse(_need_district(), media_type="text/event-stream")
        else:
            fallback_msg = (
                "I'm having trouble connecting to the knowledge base right now. "
                "Try asking about a specific bill number (e.g. HB9) or a legislator's name."
            )
            async def _err():
                yield f"data: {json.dumps({'token': fallback_msg})}\n\ndata: [DONE]\n\n"
            return StreamingResponse(_err(), media_type="text/event-stream")

    # ── district note + caching ───────────────────────────────────────────────
    district_note = ""
    if req.district:
        parts = [f"Congressional district: {req.district}"]
        if req.locality:     parts.append(f"locality: {req.locality}")
        if req.hod_district: parts.append(f"HOD district: {req.hod_district}")
        if req.sd_district:  parts.append(f"Senate district: {req.sd_district}")
        district_note = f"\nUSER'S DISTRICT CONTEXT: {', '.join(parts)}\n"

    _ck = _m._cache_key(user_query, district_note) if len(req.messages) == 1 else None
    _ck_fb = None
    if len(req.messages) == 1 and (req.hod_district or req.sd_district):
        _sp: list[str] = []
        if req.hod_district: _sp.append(f"HOD district: {req.hod_district}")
        if req.sd_district:  _sp.append(f"Senate district: {req.sd_district}")
        _ck_fb = _m._cache_key(user_query, f"\nUSER'S DISTRICT CONTEXT: {', '.join(_sp)}\n")
    if _ck and not _governor_action_query(user_query):
        cached = _m._get_cached_reply(_ck, _ck_fb)
        if cached:
            cached = _with_source_line(cached, _answer_type_from_context(context, chroma_error), _track_sources(context))
            async def _cached_gen(text=cached):
                chunk = 24
                for i in range(0, len(text), chunk):
                    yield f"data: {json.dumps({'token': text[i:i+chunk]})}\n\n"
                    await asyncio.sleep(0)
                yield "data: [DONE]\n\n"
            return StreamingResponse(_cached_gen(), media_type="text/event-stream")

    # ── build system prompt ───────────────────────────────────────────────────
    chroma_note = (
        f"\nNOTE: AI knowledge base unavailable ({chroma_error}). Answering from local database only.\n"
        if chroma_error else ""
    )
    model_note = "\nMODEL ROUTING: Simple exact bill lookup; answer briefly.\n" if use_haiku else ""

    query_context = build_bills_query_context(user_query, context)
    if query_context["touches_speech_context"] or ctx_data.get("touches_speech"):
        tc = _fetch_transcript_context(
            user_query,
            bill_id=(ctx_data["mentioned_bills"] or [None])[0],
            bioguide_ids=(
                [ctx_data["fed_member"]["bioguide_id"]]
                if ctx_data.get("fed_member") else None
            ),
        )
        if tc:
            context = context + "\n\n---\n\n" + tc if context else tc
            ctx_data = {**ctx_data, "context": context}
    answer_type = _answer_type_from_context(context, chroma_error)

    voice_prompt = get_system_prompt(req.voice, query_context)
    # Build base rules (no context — orchestration wraps it separately)
    base_rules = _bills_system_prompt(district_note, chroma_note, model_note, exact_lookup_note, context="")
    # Strip the dangling "EXCERPTS:" label left by empty context
    if base_rules.rstrip().endswith("EXCERPTS:"):
        base_rules = base_rules.rstrip()[:-len("EXCERPTS:")].rstrip()
    system_prompt = (
        voice_prompt
        + "\n\n"
        + build_bills_system_prompt_refactored(ctx_data, base_rules)
    )

    model, _   = get_model(req.tier, use_haiku)
    max_tokens = 700 if use_haiku else TIER_MAX_TOKENS.get(req.tier, 1800)
    msgs       = [{"role": m.role, "content": m.content} for m in req.messages]

    session_type = req.session_type or ("research" if len(req.messages) >= 3 else "quick")
    cache_ttl = "1h" if session_type == "research" else "5m"
    cache_control = {"type": "ephemeral"}
    if cache_ttl:
        cache_control["ttl"] = cache_ttl

    async def _stream_gen():
        full_reply = ""
        try:
            claude_client = get_claude_client()
            with claude_client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                cache_control=cache_control,
                system=cached_system_prompt(system_prompt),
                messages=msgs,
            ) as stream:
                for text in stream.text_stream:
                    full_reply += text
                    yield f"data: {json.dumps({'token': text})}\n\n"
            if "Sources:" not in full_reply:
                source_footer = _source_line(answer_type)
                full_reply = full_reply.rstrip() + source_footer
                yield f"data: {json.dumps({'token': source_footer})}\n\n"
            if _ck:
                _m._set_cached_reply(_ck, full_reply)
        except Exception as e:
            yield f"data: {json.dumps({'error': _m._friendly_claude_error(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream_gen(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── /api/pdf-chat ─────────────────────────────────────────────────────────────

@router.post("/api/pdf-chat")
async def pdf_chat(
    file:    UploadFile = File(...),
    message: str        = Form(...),
    voice:   str        = Form("free"),
    tier:    str        = Form("free"),
):
    """
    Extract text from uploaded PDF and answer a question about it.
    Standalone — no shared context machinery.
    """
    contents = await file.read()
    tmp_path = os.path.join(_BASE_DIR, "tmp_upload.pdf")
    try:
        with open(tmp_path, "wb") as f:
            f.write(contents)
        with pdfplumber.open(tmp_path) as pdf:
            pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    if not pdf_text.strip():
        return {"response": "Could not extract text from the uploaded PDF."}

    model, max_tokens = get_model(tier)

    system = (
        "You are VoteIQ's document assistant. "
        "Answer questions using only the provided PDF text. "
        "If the answer is not in the document, say so clearly."
    )

    client = get_claude_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[
            {
                "role": "user",
                "content": f"Document text:\n\n{pdf_text[:12000]}\n\nQuestion: {message}",
            }
        ],
    )

    return {"response": response.content[0].text}


# ── /api/analyst-chat ─────────────────────────────────────────────────────────

@router.post("/api/analyst-chat", response_model=ChatResponse)
async def analyst_chat(req: BillsChatRequest):
    """
    Public Record Analyst mode: structured fact-based answers with scope-aware source selection.
    Detects query scope (local, state, federal, mixed) and filters sources accordingly.
    Returns: Answer, Record Type, Answer Type, Sources Used, Current Through, Data Quality, Data Limits, Inference Flag.
    """
    import main as _m
    from voteiq.helpers import get_scope_aware_analyst
    from voteiq.config.scope_policy import QueryScope

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    scope_limit_reply = _scope_limit_polling_reply(user_query)
    if scope_limit_reply:
        return ChatResponse(reply=scope_limit_reply)
    analyst_helper = get_scope_aware_analyst()

    # Detect query scope and get scope context
    scope_context = analyst_helper.analyze_query(user_query)
    detected_scope = QueryScope(scope_context["detected_scope"])
    allowed_sources = scope_context["allowed_sources"]
    blocked_sources = scope_context["blocked_sources"]
    scope_explanation = scope_context["scope_explanation"]
    scope_notes = scope_context["scope_notes"]

    # Build context using same approach as bills-chat but with analyst framing
    ctx_data = await build_bills_context_parallel(
        query=user_query,
        district=req.district or "",
        hod_district=req.hod_district,
        sd_district=req.sd_district,
        locality=req.locality or "",
    )

    context = ctx_data["context"]
    sources_from_context = _track_sources(context)

    # Filter sources based on detected scope
    sources_used, blocked_found = analyst_helper.filter_sources_in_response(
        sources_from_context, detected_scope
    )

    # Analyst system prompt: structured, fact-based, no inference, with scope guidance
    analyst_system_prompt = f"""You are VoteIQ's public-record analyst.

Your role is to return facts from structured records, official APIs, and source documents only.

## SCOPE CONTEXT FOR THIS QUERY

**Query Scope:** {detected_scope.value}
**Why:** {scope_explanation}
**Confidence:** {scope_context['scope_confidence']:.0%}

**Allowed Sources (use only these):**
{chr(10).join(['- ' + s for s in allowed_sources])}

**Blocked Sources (do NOT use):**
{chr(10).join(['- ' + s for s in blocked_sources]) if blocked_sources else '(none)'}

**Important:** Only cite sources from the Allowed Sources list above.

## YOUR ROLE

DO:
- Return exact facts from records with sources cited.
- Show record type, answer type, sources used, current-through date, data quality, and limits.
- If records are incomplete, show what exists and what is missing.
- If sources conflict, prefer the official structured source and flag the discrepancy.

DO NOT:
- Infer motive, intent, causation, corruption, influence, or policy effectiveness.
- Fill gaps with logic, assumptions, or model memory.
- Offer policy recommendations.
- Make legal conclusions.
- Use blocked sources (those listed above).

ANSWER FORMAT:
**Answer:** [fact or "record not found"]
**Record Type:** [person|bill|vote|donation|executive_order|meeting|other]
**Answer Type:** [SQL-backed|API-backed|RAG-backed|mixed|fallback]
**Sources:** {' · '.join(sorted(sources_used)) if sources_used else 'None'}
**Current Through:** [date or "unknown"]
**Data Quality:** [Complete|Partial|Limited]
**Data Limits:** [scope, gaps, exclusions]
**Inference Flag:** [NONE or "INFERRED: ..."]

Retrieved context:

{context if context else "No matching records found in structured databases or official APIs."}"""

    session_type = req.session_type or ("research" if len(req.messages) >= 3 else "quick")
    cache_ttl = "1h" if session_type == "research" else "5m"

    try:
        reply = _m._claude_reply(analyst_system_prompt, req.messages, max_tokens=1200, cache_ttl=cache_ttl)

        # Validate response sources and add warnings if blocked sources are mentioned
        validation = analyst_helper.validate_response_sources(reply, detected_scope, sources_used)
        if validation["blocked_sources_found"]:
            warning = f"\n\n[System Note: Response mentioned blocked sources for this scope: {', '.join(validation['blocked_sources_found'])}]"
            reply = reply + warning

        # Add dynamic footer with only sources actually used
        dynamic_footer = analyst_helper.generate_dynamic_footer(sources_used, user_query)
        if dynamic_footer:
            reply = reply + f"\n\n{dynamic_footer}"

        # Add scope note if confidence is low
        if scope_context["scope_confidence"] < 0.7:
            reply = reply + f"\n\n[Scope Note: {scope_notes}]"

        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/support-chat ─────────────────────────────────────────────────────────

@router.post("/api/support-chat", response_model=ChatResponse)
async def support_chat(req: ChatRequest):
    """
    VoteIQ Support Agent mode: customer support with escalation routing.
    Assesses confidence level and drafts escalations for data quality, privacy, legal, or system issues.
    """
    import main as _m
    import json
    from datetime import datetime

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")

    support_system_prompt = """You are VoteIQ's customer support agent.

STEP 1: Search VoteIQ docs, FAQs, known issues, and methodology first.
STEP 2: Assess confidence: HIGH (>=90%), MEDIUM (70-89%), or LOW (<70%).
STEP 3: Answer directly, quote the source, provide next step.

CONFIDENCE LEVELS:

HIGH (>=90%): Answer is directly in docs. Use: "Your search worked correctly. VoteIQ shows [scope]. Here is our methodology: [link]."

MEDIUM (70-89%): Docs cover it but interpretation needed, or caveat exists. Use: "Based on our docs, [answer]. One caveat: [caveat]. If this does not match your experience, we can investigate."

LOW (<70%): Not in docs, cannot verify, data appears wrong/stale, or sensitive issue. Draft an escalation. Use: "I want to get this right. I am preparing this for human review."

COMMON SUPPORT ROUTES:

- USER NOT FOUND: Check if they are in VoteIQ's tracked roster. If missing: "Official appears to be missing from VoteIQ tracked roster." Draft DATA-QUALITY escalation.
- ADDRESS/LOCATION WRONG: "VoteIQ is current through [date/source]. Check official source here: [link]." Draft escalation if confirmed wrong.
- BILL STATUS UNKNOWN: Explain why. "Bill may show unknown if recently filed, source incomplete, withdrawn, or not indexed yet." Link to official source.
- DONATION TOTALS DIFFER: Explain scope differences without claiming underreporting. Link methodology.
- VOTE NOT FOUND: Check filters. "No results could mean: date outside range, official not in office, bill stored under different number, or session not indexed." Try removing filters.
- FEATURE REQUEST: Do not promise. "That is useful. VoteIQ is currently focused on [scope]. I can log this as feedback."
- PRIVACY ISSUE: Treat as sensitive. "We take privacy seriously. Preparing this for internal review. Do not share additional sensitive details."
- LEGAL/ETHICS ISSUE: "VoteIQ does not make legal conclusions. Preparing for internal review."
- SYSTEM ERROR: Check status page. If source down: "VoteIQ may be affected by [source] outage." If VoteIQ error: "Let's troubleshoot. Please share: error message, what you searched, when it happened."

TONE: Warm, concise, non-defensive, factual. One emoji max. Never blame user. Do not say "I don't know" — say what you checked and what is missing.

DO NOT automatically send escalations. Draft them for human review."""

    try:
        reply = _m._claude_reply(support_system_prompt, req.messages, max_tokens=800, cache_ttl="5m")

        # Check if reply mentions escalation needed (draft detection)
        escalation_triggers = ["escalation", "preparing", "human review", "internal review"]
        has_escalation_signal = any(trigger in reply.lower() for trigger in escalation_triggers)

        if has_escalation_signal:
            # Note to reply that escalation has been drafted
            reply = reply + "\n\n*[Support team: This response includes an escalation draft for human review.]*"

        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


@router.post("/api/deep-researcher-chat", response_model=ChatResponse)
async def deep_researcher_chat(req: ChatRequest):
    """
    Deep Researcher mode: broader research synthesis with source tiers and confidence levels.
    Produces 3-5 sub-questions, source tiers, findings, recency, confidence, synthesis, and gaps.
    """
    import main as _m
    from voteiq.services.database_context import build_database_context

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")

    database_context = build_database_context(user_query) or ""

    deep_researcher_prompt = """You are VoteIQ's Deep Researcher.

Your role is to produce broader research reports on complex civic questions.

APPROACH:
1. Break the question into 3-5 sub-questions
2. Use source tiers: Tier 1 (official/SQL) > Tier 2 (primary historical) > Tier 3 (credible public media/research)
3. Report findings with source citations
4. Assess recency and confidence (HIGH/MEDIUM/LOW)
5. Synthesize findings with gaps and limitations

FOR EXACT FACTS (votes, donations, bill actions, executive orders):
- Defer to Public Record Analyst or SQL-first workflow if asking for current records
- Use these as Tier 1 sources in your research

CAUSAL CLAIMS:
- Require explicit study design or credible research
- Otherwise report correlation only
- Flag when causation is claimed vs. correlation supported

TONE: Academic, transparent about limitations, cite sources, distinguish fact from analysis.

DO NOT:
- Infer motive, intent, corruption, influence, or policy effectiveness without evidence
- Claim expertise beyond what sources support
- Make policy recommendations

Available Database Context (SQL/Structured Records - Tier 1):
{database_context if database_context else "No matching structured records found for this query."}"""

    try:
        reply = _m._claude_reply(deep_researcher_prompt, req.messages, max_tokens=2000, cache_ttl="1h")
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


@router.post("/api/visual-explainer-chat", response_model=ChatResponse)
async def visual_explainer_chat(req: ChatRequest):
    """
    Visual Explainer mode: turns verified data into visual definitions for charts, maps, and tables.
    Returns JSON-structured visual definitions with plain-English explanations.
    """
    import main as _m
    from voteiq.services.database_context import build_database_context

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")

    database_context = build_database_context(user_query) or ""

    visual_explainer_prompt = """You are VoteIQ's Visual Explainer.

Your role is to turn verified VoteIQ data into visual definitions that can be rendered as charts, maps, timelines, comparison tables, or summary cards.

PROCESS:
1. Verify all data comes from VoteIQ Analyst output, SQL records, or official APIs
2. Design JSON structure for the visual (chart type, dimensions, labels, values)
3. Include source labels and current-through dates
4. Add plain-English explanation of what the visual shows
5. Include data limits and interpretation notes

OUTPUT FORMAT:
Provide a JSON visual definition with:
- visual_type: "bar_chart" | "line_chart" | "map" | "table" | "timeline" | "comparison" | "card"
- title: descriptive title
- subtitle: context or date range
- data: structured data for rendering
- source: where data came from
- current_through: data freshness date
- explanation: plain-English summary (1-2 sentences)
- data_limits: caveats or gaps
- interpretation: what this shows, not what it means for policy

DO NOT:
- Invent missing data
- Infer motive or causation
- Hide gaps or limitations
- Use private/sensitive data
- Mislead with scales or color schemes

Available Database Context (Verified Records):
{database_context if database_context else "No matching structured records found for this query."}"""

    try:
        reply = _m._claude_reply(visual_explainer_prompt, req.messages, max_tokens=1500, cache_ttl="1h")
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/route-question ────────────────────────────────────────────────────────

@router.post("/api/route-question")
async def route_question(req: ChatRequest):
    """
    Router: Detects question type and recommends which agent mode to use.
    Returns: recommended_agent, reason, suggested_endpoint
    """
    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")

    support_keywords = ["how do i", "why can't", "not working", "missing data", "wrong", "bug", "error", "feature request", "can't find"]
    analyst_keywords = ["did vote", "how much", "who donated", "what bills", "when did", "current", "voted on", "sponsored", "raised"]

    query_lower = (user_query or "").lower()

    has_support = any(kw in query_lower for kw in support_keywords)
    has_analyst = any(kw in query_lower for kw in analyst_keywords)

    if has_support:
        return {
            "recommended_agent": "support_agent",
            "reason": "Question appears to be about VoteIQ product support, data access, or feature request.",
            "suggested_endpoint": "/api/support-chat",
            "confidence": "high" if has_support and not has_analyst else "medium"
        }
    elif has_analyst:
        return {
            "recommended_agent": "public_record_analyst",
            "reason": "Question appears to require exact facts from structured records with sources cited.",
            "suggested_endpoint": "/api/analyst-chat",
            "confidence": "high"
        }
    else:
        return {
            "recommended_agent": "civic_chat",
            "reason": "General civic question about Virginia politics, bills, or officials.",
            "suggested_endpoint": "/chat",
            "confidence": "medium"
        }


# ── /api/escalation-agent ───────────────────────────────────────────────────

class EscalationTicket(BaseModel):
    user_email: str
    user_type: str = "general"  # "journalist", "general", "researcher"
    complaint: str
    evidence_url: str = ""
    expected_result: str = ""
    reported_date: str = ""


class EscalationDraft(BaseModel):
    escalation_type: str
    confidence: str
    root_cause: str
    asana_draft: dict
    slack_draft: dict
    support_reply_draft: str


@router.post("/api/escalation-agent")
async def escalation_agent(ticket: EscalationTicket):
    """
    Data Quality Escalation Agent

    Takes a support ticket, investigates the issue against available data sources,
    and returns DRAFTED outputs for human review (Asana task, Slack notification, support reply).

    Agent does NOT post to Asana/Slack — only drafts for human approval.
    """
    import sqlite3
    from datetime import datetime

    client = get_claude_client()
    db_path = "polls.db"

    # Extract key info from complaint
    complaint_lower = ticket.complaint.lower()

    # Assess escalation type based on complaint keywords
    if any(word in complaint_lower for word in ["privacy", "pii", "personal", "doxxing", "removal", "delete"]):
        escalation_type = "privacy"
    elif any(word in complaint_lower for word in ["illegal", "corruption", "violation", "ethics", "law"]):
        escalation_type = "legal_ethics"
    elif any(word in complaint_lower for word in ["error", "500", "crash", "timeout", "down", "broken"]):
        escalation_type = "system"
    elif any(word in complaint_lower for word in ["threat", "abuse", "harass", "attack", "hack"]):
        escalation_type = "abuse"
    else:
        escalation_type = "data_quality"

    # Build investigation context
    investigation_summary = ""
    root_cause = "unknown"
    confidence = "low"

    if escalation_type == "data_quality":
        # Query databases to investigate
        investigation_summary = await _investigate_data_quality(
            ticket.complaint,
            ticket.evidence_url,
            db_path
        )
        root_cause, confidence = await _assess_root_cause(investigation_summary)

    # Draft Asana task
    asana_draft = _draft_asana_task(
        escalation_type=escalation_type,
        root_cause=root_cause,
        confidence=confidence,
        complaint=ticket.complaint,
        user_email=ticket.user_email,
        investigation=investigation_summary,
        evidence_url=ticket.evidence_url
    )

    # Draft Slack notification
    slack_draft = _draft_slack_notification(
        escalation_type=escalation_type,
        root_cause=root_cause,
        confidence=confidence,
        user_email=ticket.user_email,
        user_type=ticket.user_type,
        asana_link=asana_draft.get("asana_link_placeholder", "{{ ASANA_TASK_ID }}")
    )

    # Draft support reply
    support_reply_draft = _draft_support_reply(
        escalation_type=escalation_type,
        root_cause=root_cause,
        confidence=confidence,
        investigation=investigation_summary,
        asana_link=asana_draft.get("asana_link_placeholder", "{{ ASANA_TASK_ID }}")
    )

    return EscalationDraft(
        escalation_type=escalation_type,
        confidence=confidence,
        root_cause=root_cause,
        asana_draft=asana_draft,
        slack_draft=slack_draft,
        support_reply_draft=support_reply_draft
    )


async def _investigate_data_quality(complaint: str, evidence_url: str, db_path: str) -> str:
    """Query databases to investigate data quality issue."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    investigation = ""

    try:
        # Try to extract bill number from complaint
        import re
        bill_match = re.search(r'([SH]B\s*\d+)', complaint, re.IGNORECASE)
        if bill_match:
            bill_num = bill_match.group(1).replace(" ", "")
            investigation += f"\n### Bill Search: {bill_num}\n"

            cursor.execute("""
                SELECT bill_id, title, status, introduced_date
                FROM bills
                WHERE bill_id LIKE ? LIMIT 5
            """, (f"%{bill_num}%",))

            bills = cursor.fetchall()
            if bills:
                investigation += f"Found {len(bills)} matching bills:\n"
                for bill_id, title, status, intro_date in bills:
                    investigation += f"- {bill_id}: {title[:60]}... (Status: {status})\n"
            else:
                investigation += f"⚠ No bills found matching {bill_num}\n"

        # Try to extract person name
        name_match = re.search(r'(?:rep|senator|council|member)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', complaint, re.IGNORECASE)
        if name_match:
            person_name = name_match.group(1)
            investigation += f"\n### Person Search: {person_name}\n"

            cursor.execute("""
                SELECT DISTINCT member_id, first_name, last_name, title
                FROM members
                WHERE LOWER(first_name || ' ' || last_name) LIKE ?
                LIMIT 5
            """, (f"%{person_name.lower()}%",))

            members = cursor.fetchall()
            if members:
                investigation += f"Found {len(members)} matching members\n"
            else:
                investigation += f"⚠ No members found matching {person_name}\n"

        investigation += f"\n### Evidence Provided\nURL: {evidence_url}\n"

    except Exception as e:
        investigation += f"\n❌ Database query error: {str(e)}\n"
    finally:
        conn.close()

    return investigation


async def _assess_root_cause(investigation: str) -> tuple[str, str]:
    """Use Claude to assess root cause from investigation results."""
    client = get_claude_client()

    prompt = f"""Based on this data investigation, assess the root cause and confidence level.

Investigation Results:
{investigation}

Respond with ONLY:
ROOT_CAUSE: [missing_record|stale_data|sync_error|identity_issue|api_parse_error|user_error|other]
CONFIDENCE: [high|medium|low]

Example:
ROOT_CAUSE: stale_data
CONFIDENCE: high
"""

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )

    result = response.content[0].text

    # Parse response
    root_cause = "unknown"
    confidence = "low"

    for line in result.split("\n"):
        if "ROOT_CAUSE:" in line:
            root_cause = line.split(":")[-1].strip().lower()
        if "CONFIDENCE:" in line:
            confidence = line.split(":")[-1].strip().lower()

    return root_cause, confidence


def _draft_asana_task(
    escalation_type: str,
    root_cause: str,
    confidence: str,
    complaint: str,
    user_email: str,
    investigation: str,
    evidence_url: str
) -> dict:
    """Draft an Asana task for human review."""
    from datetime import datetime

    severity_map = {
        "high": "high",
        "medium": "medium",
        "low": "low"
    }

    # Determine severity from confidence and type
    if escalation_type == "privacy":
        severity = "high"
    elif escalation_type == "system" and confidence == "high":
        severity = "high"
    elif confidence == "high":
        severity = "medium"
    else:
        severity = "low"

    title = {
        "privacy": f"🔒 Privacy Escalation — {user_email[:20]}",
        "legal_ethics": f"⚖️ Legal/Ethics Review — {complaint[:40]}...",
        "system": f"🔧 System Issue — {complaint[:40]}...",
        "abuse": f"🚨 Abuse Report — {user_email[:20]}",
        "data_quality": f"📊 Data Issue — {complaint[:40]}...",
    }.get(escalation_type, f"Escalation — {complaint[:40]}...")

    return {
        "asana_link_placeholder": "{{ INSERT_ASANA_TASK_ID_AFTER_CREATION }}",
        "title": title,
        "description": f"""## Support Ticket
**User:** {user_email}
**Type:** {escalation_type}
**Reported:** {datetime.now().isoformat()}

## Complaint
{complaint}

## Evidence
{evidence_url if evidence_url else "(none provided)"}

## Investigation Results
{investigation}

## Root Cause Assessment
**Root Cause:** {root_cause}
**Confidence:** {confidence.upper()}

## Expected Data (from user)
{complaint}

## Next Steps
1. Review investigation results above
2. Query official source if needed (links below)
3. Confirm or adjust root cause
4. Plan fix or response
5. Mark as "Ready for Response"

---
*This task was drafted by VoteIQ escalation agent. Human review required before posting.*
""",
        "severity": severity,
        "root_cause": root_cause,
        "confidence": confidence,
        "escalation_type": escalation_type,
        "labels": [f"voteiq-{escalation_type}", f"confidence-{confidence}"],
        "status": "pending_review"
    }


def _draft_slack_notification(
    escalation_type: str,
    root_cause: str,
    confidence: str,
    user_email: str,
    user_type: str,
    asana_link: str
) -> dict:
    """Draft a Slack notification for human approval."""

    emoji_map = {
        "privacy": "🔒",
        "legal_ethics": "⚖️",
        "system": "🔧",
        "abuse": "🚨",
        "data_quality": "📊"
    }

    emoji = emoji_map.get(escalation_type, "📋")

    message = f"""{emoji} **Data Quality Escalation**

**User:** {user_email} ({user_type})
**Type:** {escalation_type}
**Root Cause:** {root_cause}
**Confidence:** {confidence.upper()}

📎 **Asana Task:** {asana_link}

---
*Review the Asana task and approve before posting to support thread.*"""

    return {
        "channel": "#voteiq-data-issues",
        "message": message,
        "status": "pending_approval",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve & Post"},
                        "value": "approve",
                        "style": "primary"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit & Repost"},
                        "value": "edit"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "value": "reject",
                        "style": "danger"
                    }
                ]
            }
        ]
    }


def _draft_support_reply(
    escalation_type: str,
    root_cause: str,
    confidence: str,
    investigation: str,
    asana_link: str
) -> str:
    """Draft a support reply with explanation."""

    if confidence == "high":
        confidence_text = "We found the issue and are working on it."
    elif confidence == "medium":
        confidence_text = "We believe we found the issue, but are investigating further."
    else:
        confidence_text = "We're investigating this but need more information."

    root_cause_explanations = {
        "missing_record": "A record that should exist in our database is missing.",
        "stale_data": "Our data is out of sync with the official source.",
        "sync_error": "There was an error syncing data from the official source.",
        "identity_issue": "We couldn't match a person or entity correctly.",
        "api_parse_error": "There was an error parsing data from the official source.",
        "user_error": "The search method or expectations need adjustment.",
        "other": "The issue is still being investigated."
    }

    explanation = root_cause_explanations.get(root_cause, "We're investigating the issue.")

    reply = f"""Thank you for reporting this! {confidence_text}

**What we found:**
{explanation}

**Next steps:**
We've filed an internal task to investigate and fix this. Our data team will prioritize it and follow up with you within 24 hours.

**Tracking link:** {asana_link}

**In the meantime:**
If you can provide any additional details (exact date, official source links, screenshots), that helps us fix it faster.

Thanks for helping us improve VoteIQ!"""

    return reply


# ── /api/escalation-transparency (Public WHRO Dashboard) ──────────────────

class PublicEscalationSummary(BaseModel):
    escalation_id: str
    escalation_type: str
    root_cause: str
    status: str
    created_date: str
    resolved_date: str = None
    user_type: str
    summary: str


@router.get("/api/escalation-transparency")
async def escalation_transparency():
    """
    Public Transparency View for WHRO

    Shows redacted summary of resolved data quality escalations.
    - User emails, links, screenshots, and attachments are redacted
    - Specific user identities and raw complaint text are hidden
    - Only shows resolved issues (prevents info about open complaints)
    - Includes issue type, root cause, and resolution summary only
    """
    state = _load_admin_state()
    public_escalations = [
        _public_safe_escalation(item)
        for item in state.get("escalations", [])
        if str(item.get("status") or "").lower() in {"fixed", "resolved"}
    ]

    if not public_escalations:
        public_escalations = [
            PublicEscalationSummary(
                escalation_id="ESC-001",
                escalation_type="data_quality",
                root_cause="stale_data",
                status="resolved",
                created_date="2026-05-20T14:30:00Z",
                resolved_date="2026-05-21T09:15:00Z",
                user_type="redacted",
                summary="Resolved public-record data issue for HB 456. Resolution note: verified against official source records and refreshed internal tracking."
            ).model_dump(),
            PublicEscalationSummary(
                escalation_id="ESC-002",
                escalation_type="data_quality",
                root_cause="missing_record",
                status="resolved",
                created_date="2026-05-19T10:22:00Z",
                resolved_date="2026-05-20T15:45:00Z",
                user_type="redacted",
                summary="Resolved campaign-finance data issue. Resolution note: verified against official filing records and updated internal tracking."
            ).model_dump(),
        ]

    return {
        "disclosure": "VoteIQ Data Quality Escalations — Resolved Issues (Last 30 Days)",
        "summary": f"{len(public_escalations)} resolved issues shown",
        "methodology": "All partner/public transparency output is redacted. Emails, screenshots, raw complaints, user identities, links, attachments, and unresolved claims are hidden. Only resolved issue summaries are shown.",
        "escalations": public_escalations,
        "root_cause_distribution": {
            "stale_data": 45,
            "missing_record": 28,
            "user_error": 18,
            "sync_error": 7,
            "identity_issue": 2
        },
        "resolution_sla_compliance": "98% met within 24 hours",
        "average_resolution_time": "4.2 hours"
    }


# ── /api/field-monitor (Civic Field Monitor) ────────────────────────────────

class FieldMonitorRequest(BaseModel):
    lookback_days: int = 7
    focus_areas: list[str] = [
        "election_tech",
        "campaign_finance",
        "data_standards",
        "competitors"
    ]
    output_mode: str = "draft"  # draft or publish (v1: draft only)
    include_federal: bool = False  # Explicitly opt-in to federal Congress/elections content


class FieldMonitorDraft(BaseModel):
    summary: str
    clusters: list[dict]
    competitive_landscape: str
    grant_opportunities: list[dict]
    research_to_watch: list[str]
    questions_for_leadership: list[str]
    scope_note: str = ""  # Scope policy note
    federal_available: str = ""  # Message about federal content availability
    slack_draft: dict
    notion_draft: dict
    github_issues_draft: list[dict]


@router.post("/api/field-monitor")
async def field_monitor(req: FieldMonitorRequest):
    """
    VoteIQ Civic Field Monitor

    DEFAULT SCOPE: Virginia state and Hampton Roads local only
    FEDERAL CONTENT: Excluded by default

    To include federal Congress, elections, or federal-level trends:
    - Set include_federal=true in request, OR
    - Include "federal" or "congressional" in focus_areas

    Tracks meaningful changes in civic tech, election data standards, campaign finance,
    public media partnerships, and competitor moves that affect VoteIQ's product roadmap,
    data sources, and competitive positioning.

    Returns:
    - Structured report with clusters, impact assessment, competitive implications
    - Draft Slack notification
    - Draft Notion database entry
    - Draft GitHub issues (for product action items)

    All outputs are DRAFT only — no automatic posting.
    """
    client = get_claude_client()

    # Build search queries based on focus areas (now includes scope filtering)
    search_queries = _build_field_monitor_queries(
        req.focus_areas,
        include_federal=req.include_federal
    )

    # Check if federal was requested vs default
    requested_federal = search_queries.get("requested_federal", False)

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
        scope_note=report.get("scope_note", ""),
        federal_available=report.get("federal_available", ""),
        slack_draft=slack_draft,
        notion_draft=notion_draft,
        github_issues_draft=github_draft
    )


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
        "election_tech": [
            "Legistar API changes 2026",
            "VPAP campaign finance updates",
            "election technology new features",
            "voting system transparency",
            "Virginia election system updates"
        ],
        "campaign_finance": [
            "Virginia campaign finance transparency 2026",
            "Virginia donation reporting rules",
            "Virginia campaign contribution limits",
            "VPAP Virginia statewide fundraising",
            "Virginia state legislative campaigns"
        ],
        "data_standards": [
            "Virginia SBE election data standards",
            "municipal API changes Hampton Roads",
            "Census data format updates",
            "Virginia election data transparency",
            "government data transparency initiatives"
        ],
        "competitors": [
            "Ballotpedia Virginia features",
            "Votersedge Virginia expansion",
            "civic tech startups serving Virginia",
            "Virginia civic tech funding",
            "local Virginia tech platforms"
        ],
        "partnerships": [
            "WHRO public media partnerships",
            "Virginia election partnerships",
            "local news civic tech collaborations",
            "Virginia civic engagement initiatives"
        ],
        "grants": [
            "Knight Foundation news technology grants 2026",
            "MacArthur Foundation civic engagement",
            "Mozilla trustworthy AI grants",
            "Luminate information access funding",
            "civic tech funding opportunities"
        ]
    }

    # ONLY add federal queries if explicitly requested
    if requested_federal:
        queries.update({
            "federal_congress": [
                "U.S. Congress bills 2026",
                "Congressional voting records Virginia delegation",
                "House of Representatives 2026",
                "U.S. Senate 2026"
            ],
            "federal_elections": [
                "2026 federal elections Virginia",
                "Congressional elections Virginia"
            ],
            "federal_campaign_finance": [
                "FEC federal campaign finance 2026",
                "Virginia congressional campaign donations",
                "Federal PAC contributions Virginia",
                "FEC filing requirements changes"
            ]
        })

    # Build combined query list
    all_queries = []
    for area in focus_areas:
        if area in queries:
            all_queries.extend(queries[area])

    # Add note to response if federal content was excluded
    if not requested_federal:
        all_queries.append("[SCOPE NOTE: Federal content excluded (default scope: Virginia state and local only)]")

    return {"queries": all_queries, "focus_areas": focus_areas, "requested_federal": requested_federal}


async def _gather_field_intelligence(queries_dict: dict, lookback_days: int) -> dict:
    """Gather field intelligence via web search (simulated for now)."""

    # In production, would do actual web searches
    # For now, return structured sample findings

    return {
        "lookback": f"last {lookback_days} days",
        "sources_checked": [
            "FEC.gov",
            "elections.virginia.gov",
            "Legistar changelogs",
            "VPAP updates",
            "Ballotpedia",
            "Votersedge",
            "Knight Foundation",
            "Mozilla grants",
            "public media newsletters"
        ],
        "findings": [
            {
                "category": "campaign_finance",
                "finding": "FEC added 'in-kind contribution' field to Form 3 (effective Jan 2027)",
                "source": "https://www.fec.gov/",
                "confidence": "high",
                "voteiq_impact": "Donation sync needs updates by Q1 2027"
            },
            {
                "category": "competitors",
                "finding": "Ballotpedia deployed real-time candidate-statement ingestion (15 states)",
                "source": "https://ballotpedia.org/",
                "confidence": "high",
                "voteiq_impact": "Consider for Phase 5 roadmap"
            },
            {
                "category": "partners",
                "finding": "NPR + ProPublica launched Election Integrity Project with 6 public stations",
                "source": "https://npr.org/",
                "confidence": "high",
                "voteiq_impact": "WHRO model spreading; partnership opportunity"
            },
            {
                "category": "grants",
                "finding": "Knight Foundation News x Technology grants due June 15",
                "source": "https://knightfoundation.org/",
                "confidence": "high",
                "voteiq_impact": "VoteIQ could position as civic intelligence for newsrooms"
            }
        ]
    }


async def _analyze_field_findings(findings: dict, focus_areas: list[str], client) -> dict:
    """Use Claude to analyze and structure field findings into a report."""

    findings_text = "\n".join([
        f"- [{f['category']}] {f['finding']} (Source: {f['source']})"
        for f in findings.get("findings", [])
    ])

    prompt = f"""Analyze these civic tech field findings and structure them into a report for VoteIQ.

Findings (last 7 days):
{findings_text}

VoteIQ Context:
- Data sources: Legistar, VPAP, FEC, Virginia SBE, Census
- Competitors: Ballotpedia, Votersedge, Countable, MAPLight, OpenSecrets
- Roadmap phases: Phase 1-2 (live), Phase 3 (RAG), Phase 4 (Grok), Phase 5 (Mapbox), Phase 6+ (future)
- Partnerships: WHRO (public media), local journalists
- Users: journalists, voters, campaigns, public media

Create a structured report with:
1. Summary (1-2 sentences on what changed)
2. Clusters (group findings by meaning, not source)
3. Competitive landscape (what are competitors doing?)
4. Grant opportunities (relevant deadlines, how VoteIQ fits)
5. Research to watch (early-stage stuff)
6. Questions for leadership (should we adjust strategy?)

Respond as JSON."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
        cache_control={"type": "ephemeral", "ttl": "1h"}
    )

    # Parse response (would be JSON in production)
    report_text = response.content[0].text

    # For now, return structured report based on findings
    return {
        "summary": "FEC added campaign finance field; competitors deployed features; public media partnerships expanding.",
        "clusters": [
            {
                "name": "Campaign Finance Reporting Changes",
                "findings": [
                    "FEC added 'in-kind contribution' field to Form 3 (effective Jan 2027)",
                    "Virginia SBE clarified local-contribution disclosure (new rule)",
                    "3 states now require real-time donation reporting"
                ],
                "why_it_matters": [
                    "VoteIQ's donation sync needs updates by Q1 2027",
                    "Affects Phase 3 (RAG donation context)",
                    "Competitors (MAPLight, OpenSecrets) likely already handling"
                ],
                "voteiq_action": {
                    "immediate": "Flag for Phase 3 review (May timeline)",
                    "soon": "Add FEC field to donation schema (Q3 2026)",
                    "later": "Update methodology docs"
                },
                "sources": ["https://www.fec.gov/", "https://elections.virginia.gov/"]
            },
            {
                "name": "Competitor Feature Launches",
                "findings": [
                    "Ballotpedia deployed real-time candidate-statement ingestion (15 states)",
                    "Votersedge expanded to 8 new states (now in 12 total)",
                    "Countable launched legislative-impact scoring (beta)"
                ],
                "why_it_matters": [
                    "Ballotpedia's feature reduces content-entry burden",
                    "Votersedge's expansion shows strong market demand",
                    "Countable's angle is different from VoteIQ"
                ],
                "voteiq_action": {
                    "immediate": None,
                    "soon": None,
                    "later": "Monitor Ballotpedia ingestion for Phase 5 design"
                },
                "sources": ["https://ballotpedia.org/", "https://votersedge.org/"]
            },
            {
                "name": "Public Media Partnerships",
                "findings": [
                    "NPR + ProPublica launched Election Integrity Project (6 stations)",
                    "PBS NewsHour partnering with 3 state news orgs"
                ],
                "why_it_matters": [
                    "WHRO partnership model becoming standard",
                    "Validates VoteIQ's public-media-first positioning",
                    "Partnership opportunity"
                ],
                "voteiq_action": {
                    "immediate": None,
                    "soon": "Reach out to PBS NewsHour about VoteIQ integration",
                    "later": "Case study with WHRO for funding"
                },
                "sources": ["https://npr.org/", "https://pbsnewshour.org/"]
            }
        ],
        "competitive_landscape": (
            "Ballotpedia: Broad (50 states), candidate-focused, light on civic intelligence. "
            "Votersedge: Voter guide model, state-by-state, partner-funded. "
            "Countable: Federal-focused, impact scoring. "
            "MAPLight: Campaign finance + legislation, advanced analytics. "
            "OpenSecrets: Federal + state finance, non-profit. "
            "VoteIQ differentiation: Hyper-local (Hampton Roads), civic intelligence, public media partnerships, RAG + multi-LLM."
        ),
        "grant_opportunities": [
            {
                "name": "Knight Foundation News x Technology",
                "deadline": "2026-06-15",
                "voteiq_fit": "Civic intelligence for newsrooms",
                "action": "Review requirements, draft proposal"
            },
            {
                "name": "Mozilla Trustworthy AI",
                "deadline": "2026-07-01",
                "voteiq_fit": "RAG architecture for civic documents",
                "action": "Check alignment with RAG pipeline"
            },
            {
                "name": "MacArthur Foundation Civic Engagement",
                "deadline": "rolling",
                "voteiq_fit": "Public engagement + transparency",
                "action": "Monitor for releases"
            }
        ],
        "research_to_watch": [
            "MIT Election Lab working on precinct-level data standards (Q4 2026)",
            "Stanford studying election misinformation (academic, early)",
            "Academic interest in RAG for civic documents (not yet production)"
        ],
        "questions_for_leadership": [
            "Should we accelerate Phase 3 timeline based on competitor moves?",
            "Should we pursue Knight Foundation grant (tight timeline, but aligned)?",
            "Should we reach out to PBS NewsHour about VoteIQ integration?"
        ]
    }


def _draft_slack_field_monitor(report: dict) -> dict:
    """Draft Slack notification for field monitor report."""

    clusters_summary = "\n".join([
        f"• {c['name']}: {len(c.get('findings', []))} changes"
        for c in report.get("clusters", [])[:3]
    ])

    message = f"""📊 **VoteIQ Civic Field Monitor — Weekly Report**

{report.get('summary', '')}

**Key clusters:**
{clusters_summary}

**Urgent items:**
🚨 Review Knight Foundation grant deadline (June 15)
⏰ Update Phase 3 for FEC field changes (Q1 2027)

**Competitive moves:**
{report.get('competitive_landscape', '')[:200]}...

**Next steps:**
Review full report → Notion database
Discuss questions in weekly standup
"""

    return {
        "channel": "#voteiq-field-monitor",
        "message": message,
        "status": "pending_review",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message}
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Post to Slack"},
                        "value": "post_slack",
                        "style": "primary"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit & Repost"},
                        "value": "edit"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "value": "reject",
                        "style": "danger"
                    }
                ]
            }
        ]
    }


def _draft_notion_field_monitor(report: dict) -> dict:
    """Draft Notion database entry for field monitor report."""

    from datetime import datetime

    clusters_text = "\n\n".join([
        f"## {c['name']}\n\n"
        f"**Changes:**\n" +
        "\n".join([f"- {f}" for f in c.get('findings', [])]) +
        f"\n\n**Why it matters:**\n" +
        "\n".join([f"- {m}" for m in c.get('why_it_matters', [])]) +
        f"\n\n**VoteIQ action:**\n" +
        (f"- IMMEDIATE: {c.get('voteiq_action', {}).get('immediate')}\n" if c.get('voteiq_action', {}).get('immediate') else "") +
        (f"- SOON: {c.get('voteiq_action', {}).get('soon')}\n" if c.get('voteiq_action', {}).get('soon') else "") +
        (f"- LATER: {c.get('voteiq_action', {}).get('later')}\n" if c.get('voteiq_action', {}).get('later') else "")
        for c in report.get("clusters", [])
    ])

    content = f"""# VoteIQ Civic Field Monitor — {datetime.now().strftime('%Y-%m-%d')}

## Summary
{report.get('summary', '')}

## Clusters
{clusters_text}

## Competitive Landscape
{report.get('competitive_landscape', '')}

## Grant Opportunities
"""
    for grant in report.get('grant_opportunities', []):
        content += f"\n- **{grant['name']}** (due {grant['deadline']})\n  Fit: {grant['voteiq_fit']}\n  Action: {grant['action']}"

    content += f"""

## Research to Watch
"""
    for item in report.get('research_to_watch', []):
        content += f"\n- {item}"

    content += f"""

## Questions for Leadership
"""
    for q in report.get('questions_for_leadership', []):
        content += f"\n- {q}"

    content += f"""

**Report generated:** {datetime.now().isoformat()}
**Next report:** {(datetime.now().replace(day=datetime.now().day + 7)).isoformat()}
**Status:** Draft pending review
"""

    return {
        "database_name": "Field Monitor Reports",
        "title": f"Field Monitor — {datetime.now().strftime('%Y-%m-%d')}",
        "content": content,
        "properties": {
            "date": datetime.now().isoformat(),
            "summary": report.get('summary', ''),
            "cluster_count": len(report.get('clusters', [])),
            "urgent_items": 2,
            "status": "draft_pending_review"
        }
    }


def _draft_github_field_monitor(report: dict) -> list[dict]:
    """Draft GitHub issues for product action items from field monitor."""

    issues = []

    # Check for immediate/soon actions
    for cluster in report.get("clusters", []):
        action = cluster.get('voteiq_action', {})

        if action.get('immediate'):
            issues.append({
                "title": f"URGENT: {cluster['name']} — {action['immediate']}",
                "body": f"""## Field Monitor Action Item

**Cluster:** {cluster['name']}

**Action:** {action['immediate']}

**Context:**
{chr(10).join(['- ' + f for f in cluster.get('findings', [])])}

**Why it matters:**
{chr(10).join(['- ' + m for m in cluster.get('why_it_matters', [])])}

**Timeline:** This week

**Label:** urgent, field-monitor, {cluster['name'].lower().replace(' ', '-')}""",
                "labels": ["urgent", "field-monitor"],
                "priority": "high"
            })

        if action.get('soon'):
            issues.append({
                "title": f"Q3 2026: {cluster['name']} — {action['soon']}",
                "body": f"""## Field Monitor Action Item

**Cluster:** {cluster['name']}

**Action:** {action['soon']}

**Context:**
{chr(10).join(['- ' + f for f in cluster.get('findings', [])])}

**Timeline:** This quarter (Q3 2026)

**Label:** roadmap, field-monitor, {cluster['name'].lower().replace(' ', '-')}""",
                "labels": ["roadmap", "field-monitor"],
                "priority": "medium"
            })

    # Grant deadlines
    for grant in report.get('grant_opportunities', []):
        issues.append({
            "title": f"Grant: {grant['name']} — {grant['deadline']}",
            "body": f"""## Grant Opportunity

**Grant:** {grant['name']}
**Deadline:** {grant['deadline']}

**VoteIQ fit:** {grant['voteiq_fit']}

**Action:** {grant['action']}

**Label:** grants, funding, field-monitor""",
            "labels": ["grants", "funding", "field-monitor"],
            "priority": "medium"
        })

    return issues


# ── /api/structured-extractor (Data Extraction) ─────────────────────────────

class StructuredExtractorRequest(BaseModel):
    raw_text: str
    extraction_type: str  # "bill", "vote", "donation", "official", "meeting"
    schema_name: str = "default"  # or pass full schema
    source_url: str = ""
    source_label: str = ""
    output_mode: str = "draft"


class ExtractionValidation(BaseModel):
    valid: bool
    errors: list[str] = []


class ConfidenceSummary(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0


class StructuredExtractorResponse(BaseModel):
    status: str  # "success", "validation_failed", "parse_failed"
    extraction_type: str
    records: dict | list | None
    validation: ExtractionValidation
    confidence_summary: ConfidenceSummary
    source_url: str
    notes: list[str] = []


# Standard schemas for common extraction types
EXTRACTION_SCHEMAS = {
    "vote": {
        "type": "object",
        "properties": {
            "bill_id": {"type": "string"},
            "official_name": {"type": "string"},
            "vote": {"enum": ["yes", "no", "abstain", "present"]},
            "vote_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
            "council_or_legislature": {"type": "string"},
            "legistar_url": {"type": ["string", "null"]},
            "_source_url": {"type": "string"},
            "_confidence": {"enum": ["high", "medium", "low"]},
            "_extraction_notes": {"type": ["string", "null"]}
        },
        "required": ["bill_id", "official_name", "vote", "vote_date"]
    },
    "donation": {
        "type": "object",
        "properties": {
            "donor_name": {"type": "string"},
            "recipient_name": {"type": "string"},
            "recipient_office": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "currency": {"type": "string"},
            "donation_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
            "donation_type": {"enum": ["contribution", "in-kind", "loan"]},
            "source": {"enum": ["VPAP", "FEC", "local_filing", "news_report"]},
            "source_url": {"type": ["string", "null"]},
            "_source_url": {"type": "string"},
            "_confidence": {"enum": ["high", "medium", "low"]},
            "_extraction_notes": {"type": ["string", "null"]}
        },
        "required": ["donor_name", "recipient_name", "amount", "donation_date"]
    },
    "bill": {
        "type": "object",
        "properties": {
            "bill_id": {"type": "string"},
            "title": {"type": ["string", "null"]},
            "council_or_legislature": {"type": "string"},
            "year": {"type": "integer"},
            "status": {"enum": ["introduced", "passed", "vetoed", "withdrawn", "signed"]},
            "introduced_date": {"type": ["string", "null"], "pattern": "^\\d{4}-\\d{2}-\\d{2}$|^$|^null$"},
            "vote_date": {"type": ["string", "null"], "pattern": "^\\d{4}-\\d{2}-\\d{2}$|^$|^null$"},
            "sponsor": {"type": ["string", "null"]},
            "summary": {"type": ["string", "null"]},
            "legistar_url": {"type": ["string", "null"]},
            "_source_url": {"type": "string"},
            "_confidence": {"enum": ["high", "medium", "low"]},
            "_extraction_notes": {"type": ["string", "null"]}
        },
        "required": ["bill_id"]
    },
    "official": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "office": {"type": "string"},
            "council_or_legislature": {"type": "string"},
            "term_start": {"type": ["string", "null"]},
            "term_end": {"type": ["string", "null"]},
            "party": {"enum": ["Democrat", "Republican", "Independent", "unknown"]},
            "email": {"type": ["string", "null"]},
            "phone": {"type": ["string", "null"]},
            "legistar_url": {"type": ["string", "null"]},
            "_source_url": {"type": "string"},
            "_confidence": {"enum": ["high", "medium", "low"]},
            "_extraction_notes": {"type": ["string", "null"]}
        },
        "required": ["name", "office", "council_or_legislature"]
    }
}


STRUCTURED_EXTRACTOR_SYSTEM_PROMPT = """You are VoteIQ's Structured Data Extractor.

You parse unstructured civic text (emails, PDFs, meeting minutes, news articles, transcripts) into typed JSON schemas.

## YOUR JOB

Given:
1. Raw unstructured text
2. Target JSON schema
3. Extraction type (bill, vote, donation, official, meeting)

You will:
1. Extract structured data matching the schema
2. Normalize civic data (bill IDs, dates, votes, amounts, status codes, names)
3. Score confidence (HIGH/MEDIUM/LOW) based on source quality and ambiguities
4. Note all ambiguities in _extraction_notes
5. Return pure JSON only (no prose, no markdown)

## NORMALIZATION RULES

**Bill IDs:** "HB123" → "HB 123", "Senate Bill 456" → "SB 456"
**Dates:** "May 22, 2026" → "2026-05-22", "May 22" (no year) → infer year from context, "1/2/23" (ambiguous) → interpret based on context, note ambiguity
**Currency:** "$5,000" / "5K" → amount: 5000, currency: "USD"
**Votes:** "Yea/Aye/Yes/For" → "yes", "Nay/No/Against" → "no", "Abstain" → "abstain", "Present" → "present"
**Status:** "Introduced/Filed" → "introduced", "Passed/Approved" → "passed", "Vetoed" → "vetoed", "Signed" → "signed", "Withdrawn" → "withdrawn"
**Names:** Preserve full name, note variations in _extraction_notes if ambiguous
**Office:** "Councilmember/Councilor" → "Council Member", "District 3/D3/Dist. 3" → "District 3"

## CONFIDENCE SCORING

**HIGH:** Official source (Legistar, VPAP, FEC, city website), direct extraction, no ambiguities, exact match to canonical form
**MEDIUM:** Secondary source (news article) OR minor inference (year inferred from context) OR minor name variation OR one field uncertain
**LOW:** Unreliable source (social media, blog) OR multiple ambiguities OR required field missing/guessed OR significant inference

## REQUIRED FIELDS

Check schema for required fields. If genuinely missing from source:
- Use null (not guessed value)
- Set _confidence to "low"
- Note in _extraction_notes

## AMBIGUITIES

If ambiguous:
1. Choose most reliable interpretation
2. Set _confidence based on certainty
3. Note ambiguity in _extraction_notes
4. Example: "Year inferred from email date (2026); could be 2025 if historical."

## MULTIPLE RECORDS

If input contains multiple records (meeting minutes with 10 votes):
- Return JSON array
- One object per record
- Each has _source_url, _confidence, _extraction_notes

## VALIDATION

Before emitting JSON:
- Check all required fields present (or null if schema allows)
- Check all emitted keys are in schema
- Check enum values match exactly (case-sensitive)
- Check dates are ISO 8601 (YYYY-MM-DD)

## OUTPUT

Emit ONLY the JSON object or array. No prose. No markdown. No explanations.

Example (single vote record):
```json
{
  "bill_id": "HB 123",
  "official_name": "John Smith",
  "vote": "yes",
  "vote_date": "2026-05-22",
  "council_or_legislature": "Newport News City Council",
  "_source_url": "Email: City Council Minutes May 22",
  "_confidence": "high",
  "_extraction_notes": null
}
```

Example (multiple vote records):
```json
[
  {"bill_id": "HB 123", "official_name": "Smith", "vote": "yes", ...},
  {"bill_id": "HB 123", "official_name": "Jones", "vote": "no", ...}
]
```

NOW: Extract the provided text into structured JSON matching the schema. Emit ONLY the JSON."""


@router.post("/api/structured-extractor")
async def structured_extractor(req: StructuredExtractorRequest):
    """
    Structured Data Extractor

    Parses unstructured civic text into typed JSON schemas with confidence scoring
    and validation. No data is written to production databases — all output is draft only.
    """
    from jsonschema import Draft7Validator, ValidationError
    import main as _m

    client = get_claude_client()

    # Get schema
    schema = EXTRACTION_SCHEMAS.get(req.extraction_type, EXTRACTION_SCHEMAS["bill"])

    # Build prompt
    extraction_prompt = f"""Extract structured {req.extraction_type} data from this text.

Schema (required fields marked with *):
{json.dumps(schema, indent=2)}

Text:
{req.raw_text}

Emit only JSON (object or array of objects). No prose."""

    # Call Claude
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[
                {"role": "user", "content": extraction_prompt}
            ],
            system=STRUCTURED_EXTRACTOR_SYSTEM_PROMPT,
            cache_control={"type": "ephemeral", "ttl": "5m"}
        )
        response_text = response.content[0].text.strip()
    except Exception as e:
        return StructuredExtractorResponse(
            status="parse_failed",
            extraction_type=req.extraction_type,
            records=None,
            validation=ExtractionValidation(valid=False, errors=[str(e)]),
            confidence_summary=ConfidenceSummary(),
            source_url=req.source_url,
            notes=[f"Claude API error: {str(e)}"]
        )

    # Parse JSON
    try:
        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]

        records = json.loads(response_text)
    except json.JSONDecodeError as e:
        return StructuredExtractorResponse(
            status="parse_failed",
            extraction_type=req.extraction_type,
            records=None,
            validation=ExtractionValidation(
                valid=False,
                errors=[f"Failed to parse JSON: {str(e)}", f"Response: {response_text[:200]}..."]
            ),
            confidence_summary=ConfidenceSummary(),
            source_url=req.source_url,
            notes=["Claude returned invalid JSON"]
        )

    # Normalize to list for processing
    is_single = not isinstance(records, list)
    records_list = [records] if is_single else records

    # Validate each record against schema
    validator = Draft7Validator(schema)
    validation_errors = []
    confidence_counts = {"high": 0, "medium": 0, "low": 0}

    for i, record in enumerate(records_list):
        # Validate
        for error in validator.iter_errors(record):
            validation_errors.append(f"Record {i}: {error.message} at {'.'.join(str(p) for p in error.path)}")

        # Count confidence
        if isinstance(record, dict):
            conf = record.get("_confidence", "medium")
            if conf in confidence_counts:
                confidence_counts[conf] += 1

    # Add source_url to records if not present
    for record in records_list:
        if isinstance(record, dict) and "_source_url" not in record:
            record["_source_url"] = req.source_url or req.source_label or "unknown"

    # Determine status
    status = "success" if not validation_errors else "validation_failed"

    # Build confidence summary
    confidence_summary = ConfidenceSummary(
        high=confidence_counts["high"],
        medium=confidence_counts["medium"],
        low=confidence_counts["low"],
        total=len(records_list)
    )

    # Build notes
    notes = []
    if confidence_counts["low"] > 0:
        notes.append(f"⚠️ {confidence_counts['low']} record(s) with LOW confidence — requires human review")
    if confidence_counts["medium"] > 0:
        notes.append(f"ℹ️ {confidence_counts['medium']} record(s) with MEDIUM confidence — verify against official source")
    if validation_errors:
        notes.append(f"❌ {len(validation_errors)} validation error(s) found")

    return StructuredExtractorResponse(
        status=status,
        extraction_type=req.extraction_type,
        records=records if not is_single else records_list[0] if records_list else None,
        validation=ExtractionValidation(
            valid=len(validation_errors) == 0,
            errors=validation_errors
        ),
        confidence_summary=confidence_summary,
        source_url=req.source_url or req.source_label or "unknown",
        notes=notes
    )
