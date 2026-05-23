from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import pdfplumber
from fastapi import APIRouter, File, Form, Request, UploadFile
from voteiq.config.rate_limit import limiter
from fastapi.responses import StreamingResponse

from orchestration import (
    build_bills_context_parallel,
    build_bills_system_prompt_refactored,
    is_present,
)
from pydantic import BaseModel, field_validator

from voteiq.api.claude import get_claude_client, get_model
from voteiq.config.voices import (
    TIER_MAX_TOKENS,
    TIER_VOICE_MAP,  # unused until Supabase auth lands — re-enable tier gate then
    VOICE_PROMPTS,
    get_system_prompt,
)
from voteiq.services.database_context import build_database_context

router = APIRouter(tags=["chat"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)

_NEWS_TERMS = (
    "news", "article", "articles", "headline", "headlines", "coverage",
    "recent", "latest", "current", "today", "yesterday", "this week",
    "reported", "reporting", "press", "story", "stories",
)

_SOURCE_LINE = (
    "\n\n---\n"
    "*Sources: Congress.gov · Congressional Record/GovInfo where available · "
    "OpenStates · Virginia LIS · FEC where available. "
    "Data current through May 16, 2026. "
    "VoteIQ does not infer motive, intent, or causation from votes, donations, or bill activity.*"
)


# ── Models ────────────────────────────────────────────────────────────────────

def _premium_analyst_enabled(req) -> bool:
    """Analyst context is reserved for Pro and Newsroom voices/tiers."""
    voice = str(getattr(req, "voice", "") or "").lower()
    tier = str(getattr(req, "tier", "") or "").lower()
    return voice in {"pro", "newsroom"} or tier in {"pro", "newsroom"}


def _with_source_line(reply: str) -> str:
    """Append the standard source footer unless the model already included one."""
    if "Sources:" in (reply or ""):
        return reply.rstrip()
    return reply.rstrip() + _SOURCE_LINE


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


class ElectionChatRequest(BaseModel):
    year:     str
    messages: list[ChatMessage]
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

        full_profile_block = _m._full_profiles_for_query(user_query, limit=2 if _premium_analyst_enabled(req) else 1)
        if full_profile_block and full_profile_block not in seen_docs:
            seen_docs.add(full_profile_block)
            context_blocks.insert(0, full_profile_block)

        if _premium_analyst_enabled(req):
            analyst_context = _m._fetch_governor_action_money_analyst_context(user_query)
            if analyst_context and analyst_context not in seen_docs:
                seen_docs.add(analyst_context)
                context_blocks.insert(0, analyst_context)

            member_analyst_context = _m._fetch_member_analyst_context(user_query)
            if member_analyst_context and member_analyst_context not in seen_docs:
                seen_docs.add(member_analyst_context)
                context_blocks.insert(0, member_analyst_context)

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
    if not wants_vetoes and not wants_signed:
        return ""
    if not any(term in q for term in ("governor", "spanberger", "she", "her", "veto", "vetoed", "signed")):
        return ""
    if "youngkin" in q:
        return ""
    veto_rows = []
    signed_rows = []
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
                LIMIT 30
                """
            ).fetchall()
            conn.close()
    except Exception:
        veto_rows = []
        signed_rows = []

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
        lines.append(f"**Vetoed bills ({len(veto_rows)}):**")
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
            lines.append(f"**Signed bills:** VoteIQ has {signed_total} signed records. Showing the 30 most recent:")
            for row in signed_rows:
                bill = row["bill_number"]
                title = row["title"] or "Title not available"
                url = row["source_url"] or f"https://openstates.org/va/bills/2026/{bill}/"
                date = row["action_date"] or "date not available"
                chapter = f"; Chapter {row['chapter_number']}" if row["chapter_number"] else ""
                lines.append(f"- [{bill}]({url}) — {title} ({date}{chapter})")
        elif not wants_vetoes:
            return ""

    lines.extend([
        "",
        "Source: VoteIQ local SQL records, with bill metadata from Virginia legislative records and veto status cross-checked against the Governor's Office.",
        "VoteIQ does not infer motive, intent, or causation from bill activity.",
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
        f"Always note: \"Senate yes rates are not directly comparable to House yes rates due to the higher volume of procedural and cloture votes in the Senate.\""
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
        f"\n\nRESPONSE FORMAT — use this exact structure for legislator questions:\n\n"
        f"Your [chamber] representative is **[Full Name] ([Party], District [N])**.\n\n"
        f"**[YEAR] Session Voting Record:**\n"
        f"- Overall vote rate: [CONFIRMED — OpenStates] [Y] YES ([X]%), [N] NO out of [N] floor votes\n"
        f"- Party alignment: [CONFIRMED — calculated from vote records] voted with [Party] party majority on [X]% of floor votes\n"
        f"- Comparison note: if multiple representatives are shown in the current answer, you may say \"[X]% party alignment — the lowest/highest party-alignment rate among the representatives shown here.\" Only compare the representatives actually shown in this answer.\n"
        f"- Caucus read: [copy exactly from excerpt if present; use the plain-language wording, not the old technical faction label]\n"
        f"- Committee votes: [CONFIRMED — OpenStates] [N] total — [Y] YES, [N] NO\n\n"
        f"**Key Votes** (if present in excerpt; put this before aggregate issue stats):\n"
        f"- [markdown bill link from excerpt]: [YES/NO note exactly from excerpt] — [one-line plain-English issue summary]\n\n"
        f"**Dissenting Votes — voted NO but bill passed ([N] total):**\n"
        f"[CONFIRMED — OpenStates, INFERRED significance]:\n"
        f"- [Policy area]: [markdown bill links from excerpt] — [one-line description]\n"
        f"Pattern: [INFERRED] [one sentence. Always end with: \"Dataset does not include stated reasons for these votes.\"]\n\n"
        f"**Vote Breakdown by Issue Area** (if present in excerpt):\n"
        f"- [topic]: [N] votes — [Y] YES, [N] NO | party alignment: [X]%\n"
        f"  - Breaks from party: [copy the NO-against-party-YES-majority bill links from excerpt]\n\n"
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
        f"**Methodology note** (always include when answering about caucus labels or party alignment):\n"
        f"Caucus read is plain-language shorthand derived from OpenStates roll-call data (confirmed votes only). "
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
    direct_governor_reply = _direct_governor_veto_reply(last_question)
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

    base_prompt = get_system_prompt(voice=req.voice, query_context=query_context)
    max_tokens  = TIER_MAX_TOKENS.get(req.tier, TIER_MAX_TOKENS["free"])

    transcript_context = ""
    if query_context.get("touches_speech_context"):
        transcript_context = _fetch_transcript_context(
            last_question, bioguide_ids=bioguide_ids or None
        )

    system_prompt = f"""{base_prompt}

---

{district_block}

Available data for this representative:
- Committee assignments and legislative role
- Recent votes (with bill name and Yea/Nay)
- Campaign donors and industry totals (FEC data, fec.gov)
- Voter registration and civic participation info
- Governor Spanberger's bill actions (signed, vetoed, amended) and executive orders

Governor action rule:
If the context below includes any "[Governor Action" rows, treat those as confirmed local governor-action records. Do not say governor actions returned no data, are unavailable, or cannot be confirmed for this query. For veto questions, list bills whose Action is "Vetoed", "Veto sustained", or "Veto overridden" from those rows.

Database access rule:
If the context below includes "[Database Context" rows, treat them as direct local SQLite records from VoteIQ's databases. Use those rows before making any general statement that data is unavailable.

Profile linking rule:
If the context below includes "Profile Markdown Link", use that exact Markdown link the first time you name that person.

{database_context if database_context else ""}

{vote_context if vote_context else ""}

{finance_context if finance_context else ""}

{spanberger_finance_context if spanberger_finance_context else ""}

{state_finance_context if state_finance_context else ""}

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

    try:
        return ChatResponse(reply=_m._claude_reply(system_prompt, req.messages, max_tokens=max_tokens))
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/election-chat ────────────────────────────────────────────────────────

@router.post("/api/gemini-chat", response_model=ChatResponse)
async def gemini_chat(request: Request, req: ChatRequest):
    """Chat with VoteIQ using Gemini, grounded in FEC data and district context."""
    import main as _m

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    direct_governor_reply = _direct_governor_veto_reply(user_query)
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

    try:
        return ChatResponse(
            reply=_m._claude_reply(
                system_prompt,
                req.messages,
                max_tokens=TIER_MAX_TOKENS.get(req.tier, 400),
            )
        )
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/bills-chat ───────────────────────────────────────────────────────────

@router.post("/api/bills-chat", response_model=ChatResponse)
async def bills_chat(req: BillsChatRequest):
    import main as _m

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    direct_governor_reply = _direct_governor_veto_reply(user_query)
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
            cached = _with_source_line(cached)
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
    voice_prompt  = get_system_prompt(req.voice, query_context)
    system_prompt = voice_prompt + "\n\n" + _bills_system_prompt(
        district_note, chroma_note, model_note, exact_lookup_note, context
    )
    model, _      = get_model(req.tier, use_haiku)
    max_tokens    = 700 if use_haiku else TIER_MAX_TOKENS.get(req.tier, 1800)

    try:
        reply = _m._claude_reply(system_prompt, req.messages, max_tokens=max_tokens, model=model)
        reply = _with_source_line(reply)
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

    # ── parallel context assembly ─────────────────────────────────────────────
    direct_governor_reply = _direct_governor_veto_reply(user_query)
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
            cached = _with_source_line(cached)
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

    async def _stream_gen():
        full_reply = ""
        try:
            claude_client = get_claude_client()
            with claude_client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=msgs,
            ) as stream:
                for text in stream.text_stream:
                    full_reply += text
                    yield f"data: {json.dumps({'token': text})}\n\n"
            if "Sources:" not in full_reply:
                full_reply = full_reply.rstrip() + _SOURCE_LINE
                yield f"data: {json.dumps({'token': _SOURCE_LINE})}\n\n"
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
