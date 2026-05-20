from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pdfplumber
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from voteiq.api.claude import get_claude_client, get_model
from voteiq.config.voices import TIER_MAX_TOKENS, TIER_VOICE_MAP, VOICE_PROMPTS, get_system_prompt

router = APIRouter(tags=["chat"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)

_SOURCE_LINE = (
    "\n\n---\n"
    "*Sources: [OpenStates](https://openstates.org/va/) · "
    "[LIS](https://lis.virginia.gov) · "
    "Data current through May 16, 2026. "
    "Vote reasons/statements are not available in public datasets.*"
)


# ── Models ────────────────────────────────────────────────────────────────────

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
        tier    = (info.data or {}).get("tier", "free")
        allowed = TIER_VOICE_MAP.get(tier, ["free"])
        if v not in VOICE_PROMPTS:
            return "free"
        if v not in allowed:
            return "free"
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
        tier    = (info.data or {}).get("tier", "free")
        allowed = TIER_VOICE_MAP.get(tier, ["free"])
        if v not in VOICE_PROMPTS:
            return "free"
        if v not in allowed:
            return "free"
        return v


class ElectionChatRequest(BaseModel):
    year:     str
    messages: list[ChatMessage]
    tier:     str = "free"
    voice:    str = "free"

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, v, info):
        tier    = (info.data or {}).get("tier", "free")
        allowed = TIER_VOICE_MAP.get(tier, ["free"])
        if v not in VOICE_PROMPTS:
            return "free"
        if v not in allowed:
            return "free"
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

    if not cached_bill_context:
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
        if leg_name:
            sqlite_leg = _m._sqlite_legislator_votes(leg_name)
            if sqlite_leg and sqlite_leg not in seen_docs:
                seen_docs.add(sqlite_leg)
                context_blocks.insert(0, sqlite_leg)
            os_leg = _m._openstates_legislator_lookup(leg_name)
            if os_leg and os_leg not in seen_docs:
                seen_docs.add(os_leg)
                context_blocks.insert(0, os_leg)

        _money_kws = [
            "fund", "donor", "pac", "money", "contribut", "financ", "pay",
            "sponsor", "lobbying", "rais", "campaign", "donation", "grassroot",
            "industry", "who back", "who fund", "who support", "who pay",
            "corporate", "actblue", "winred", "special interest",
        ]
        _is_money_q = any(kw in user_query.lower() for kw in _money_kws)
        if _is_money_q and _m._PAC_CACHE:
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
        if not fed_member and leg_name:
            fed_member = _m._federal_member_by_name(leg_name)
        if not fed_member and req.district and _m._profile_question(user_query):
            dist_rep_name = _m.DISTRICT_CONTEXT.get(req.district, {}).get("rep")
            if dist_rep_name:
                fed_member = _m._federal_member_by_name(dist_rep_name)
        if fed_member:
            fed_ctx = _m._fetch_federal_context(fed_member)
            if fed_ctx and fed_ctx not in seen_docs:
                seen_docs.add(fed_ctx)
                context_blocks.insert(0, fed_ctx)
            if _is_money_q:
                fec_block = _m._fetch_finance_context([fed_member["bioguide_id"]], user_query)
                if fec_block and fec_block not in seen_docs:
                    seen_docs.add(fec_block)
                    context_blocks.insert(0, fec_block)

        news_pol   = (fed_member["name"] if fed_member else "") or leg_name or ""
        news_block = _m._fetch_news_context(user_query, politician_name=news_pol)
        if news_block and news_block not in seen_docs:
            seen_docs.add(news_block)
            context_blocks.append(news_block)

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
                    label = (
                        f"[{meta.get('chunk_type','?')} — "
                        f"{meta.get('bill_id','?')} {meta.get('session','?')}]"
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
                    label = (
                        f"[{meta.get('chunk_type','?')} — "
                        f"{meta.get('bill_id','?')} {meta.get('session','?')}]"
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
                label = (
                    f"[{meta.get('chunk_type','?')} — "
                    f"{meta.get('bill_id','?')} {meta.get('session','?')}]"
                )
                context_blocks.append(f"{label}\n{doc}")

        context = "\n\n---\n\n".join(context_blocks)
    except Exception as e:
        context = ""
        chroma_error = f"Result parsing error: {e}"

    return context, exact_lookup_note, use_haiku, chroma_error


# ── Bills helpers (shared by both bills-chat routes) ─────────────────────────

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
        ),
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
        f"election results, legislator voting records, representative profile summaries from the local 2026 session database, "
        f"roll-call votes and sponsored bills for Virginia's 13 federal representatives (119th Congress) from congress.gov, "
        f"FEC campaign finance data showing PAC/industry contributions by sector for Virginia federal members, "
        f"AND recent Virginia political news articles (sourced from Virginia news outlets via Gemini extraction). "
        f"Answer the user's question using ONLY the excerpts below — do not rely on your training data. "
        f"Be factual and cite bill numbers when relevant. "
        f"For federal members (U.S. House/Senate), cite bill type and number (e.g. H.R. 23) and note the source as congress.gov. "
        f"For campaign finance questions, use the CAMPAIGN FINANCE & INDUSTRY CORRELATION excerpt if present and cite \"(FEC data, fec.gov)\".{district_note}{chroma_note}{model_note}"
        f"\n\nFEDERAL REP RESPONSE FORMAT — when a [Representative Profile — Name | Federal] excerpt is present, "
        f"respond in the same style as state rep answers: "
        f"(1) Start with a one-sentence intro identifying who the rep is and their seat. "
        f"(2) Give overall vote stats (Yes/No counts and yes rate) from the \"Overall voting record\" line. "
        f"For senators, note that yes rates are not directly comparable to House members — Senate votes include more procedural and cloture votes where minority-party senators routinely vote Nay. "
        f"(3) Highlight 3-5 key sponsored bills with their title and status. "
        f"(4) List their committee assignments. "
        f"(5) Close with a note pointing to congress.gov for full detail. "
        f"Use the same plain, civic-report tone as state legislator responses."
        f"\n\nVOTE INTERPRETATION — apply these rules when reading vote records:\n"
        f"- If a legislator votes YES on passage but NO on concurrence/conference substitute, they likely objected to the amended version, not the bill itself. Say: \"voted against the House-amended version; accepted final compromise.\"\n"
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
        f"- Legislator names as clickable links if the profile excerpt provides a URL for them"
        f"\n\nRESPONSE FORMAT — use this exact structure for legislator questions:\n\n"
        f"Your [chamber] representative is **[Full Name] ([Party], District [N])**.\n\n"
        f"**[YEAR] Session Voting Record:**\n"
        f"- Overall vote rate: [CONFIRMED — OpenStates] [Y] YES ([X]%), [N] NO out of [N] floor votes\n"
        f"- Party alignment: [CONFIRMED — calculated from vote records] voted with [Party] party majority on [X]% of floor votes\n"
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
        f"**Want to dig deeper?** Ask me how [Name] voted on [topic1], [topic2], or [topic3]. Or ask about a specific bill by number.\n"
        f"Use the legislator's actual name and their real top 3 issue areas from the excerpt.\n\n"
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
    finance_context = _m._fetch_finance_context(bioguide_ids, last_question)

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
            ]
        ),
        "federal_rep_present": bool(bioguide_ids),
        "state_rep_present":   bool(hod_info or sd_info),
    }

    base_prompt = get_system_prompt(voice=req.voice, query_context=query_context)
    max_tokens  = TIER_MAX_TOKENS.get(req.tier, TIER_MAX_TOKENS["free"])

    system_prompt = f"""{base_prompt}

---

{district_block}

Available data for this representative:
- Committee assignments and legislative role
- Recent votes (with bill name and Yea/Nay)
- Campaign donors and industry totals (FEC data, fec.gov)
- Voter registration and civic participation info

{vote_context if vote_context else ""}

{finance_context if finance_context else ""}

{f'''
{news_context}

When answering questions about recent news or current events, cite the article source and author.
Format citations as: [Outlet — Author](URL) at the end of the relevant sentence.
If no relevant news is listed above, answer from your training knowledge.
''' if news_context else ''}

When citing a vote, include the bill name and Yea/Nay. When citing donors or industry contributions, state the dollar amount and add "(FEC data, fec.gov)". For official contact info direct users to house.gov, senate.gov, or virginiageneralassembly.gov. Never express opinions on representatives or tell people how to vote."""

    try:
        return ChatResponse(reply=_m._claude_reply(system_prompt, req.messages, max_tokens=max_tokens))
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/election-chat ────────────────────────────────────────────────────────

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
    context, exact_lookup_note, use_haiku, chroma_error = _build_bills_context(req, user_query)

    if not context and chroma_error:
        sqlite_fallback = ""
        try:
            mentioned = _m._extract_bill_numbers(user_query)
            leg_name  = _m._extract_legislator_name(user_query)
            if mentioned:
                sqlite_fallback = _m._sqlite_bill_lookup(mentioned)
                os_fb = _m._openstates_vote_lookup(mentioned)
                if os_fb:
                    sqlite_fallback = (sqlite_fallback + "\n\n" + os_fb).strip()
            elif leg_name:
                sqlite_fallback = _m._sqlite_legislator_votes(leg_name)
                os_fb = _m._openstates_legislator_lookup(leg_name)
                if os_fb:
                    sqlite_fallback = (sqlite_fallback + "\n\n" + os_fb).strip()
            profiles_fb = _m._request_rep_profiles(req, user_query)
            if profiles_fb:
                sqlite_fallback = (sqlite_fallback + "\n\n" + profiles_fb).strip()
        except Exception:
            pass
        if sqlite_fallback:
            context = sqlite_fallback
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
    if _ck:
        cached = _m._get_cached_reply(_ck, _ck_fb)
        if cached:
            if not cached.rstrip().endswith("public datasets.*"):
                cached = cached.rstrip() + _SOURCE_LINE
            return ChatResponse(reply=cached)

    chroma_note = (
        f"\nNOTE: AI knowledge base unavailable ({chroma_error}). Answering from local database only.\n"
        if chroma_error else ""
    )
    model_note = "\nMODEL ROUTING: Simple exact bill lookup using cached local bill context; answer briefly.\n" if use_haiku else ""

    query_context = build_bills_query_context(user_query, context)
    voice_prompt  = get_system_prompt(req.voice, query_context)
    system_prompt = voice_prompt + "\n\n" + _bills_system_prompt(
        district_note, chroma_note, model_note, exact_lookup_note, context
    )
    model, _      = get_model(req.tier, use_haiku)
    max_tokens    = 700 if use_haiku else TIER_MAX_TOKENS.get(req.tier, 1800)

    try:
        reply = _m._claude_reply(system_prompt, req.messages, max_tokens=max_tokens, model=model)
        if not reply.rstrip().endswith("public datasets.*"):
            reply = reply.rstrip() + _SOURCE_LINE
        if _ck:
            _m._set_cached_reply(_ck, reply)
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_m._friendly_claude_error(e))


# ── /api/bills-chat-stream ────────────────────────────────────────────────────

@router.post("/api/bills-chat-stream")
async def bills_chat_stream(req: BillsChatRequest):
    import main as _m

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    context, exact_lookup_note, use_haiku, chroma_error = _build_bills_context(req, user_query)

    if not context and chroma_error:
        fallback_msg = (
            "I'm having trouble connecting to the knowledge base right now. "
            "Try asking about a specific bill number (e.g. HB9) or a legislator's name."
        )
        async def _err():
            yield f"data: {json.dumps({'token': fallback_msg})}\n\ndata: [DONE]\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

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
    if _ck:
        cached = _m._get_cached_reply(_ck, _ck_fb)
        if cached:
            if not cached.rstrip().endswith("public datasets.*"):
                cached = cached.rstrip() + _SOURCE_LINE
            async def _cached_gen(text=cached):
                chunk = 24
                for i in range(0, len(text), chunk):
                    yield f"data: {json.dumps({'token': text[i:i+chunk]})}\n\n"
                    await asyncio.sleep(0)
                yield "data: [DONE]\n\n"
            return StreamingResponse(_cached_gen(), media_type="text/event-stream")

    chroma_note = (
        f"\nNOTE: AI knowledge base unavailable ({chroma_error}). Answering from local database only.\n"
        if chroma_error else ""
    )
    model_note = "\nMODEL ROUTING: Simple exact bill lookup using cached local bill context; answer briefly.\n" if use_haiku else ""

    query_context = build_bills_query_context(user_query, context)
    voice_prompt  = get_system_prompt(req.voice, query_context)
    system_prompt = voice_prompt + "\n\n" + _bills_system_prompt(
        district_note, chroma_note, model_note, exact_lookup_note, context
    )
    model, _      = get_model(req.tier, use_haiku)
    max_tokens    = 700 if use_haiku else TIER_MAX_TOKENS.get(req.tier, 1800)
    msgs          = [{"role": m.role, "content": m.content} for m in req.messages]

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
            if not full_reply.rstrip().endswith("public datasets.*"):
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
