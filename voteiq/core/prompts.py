from __future__ import annotations

from voteiq.config.voices import VOICE_PROMPTS, VOICE_CONFIG

# ── Base sections (always included) ──────────────────────────────────────────

_SECTION_USE_ONLY_EXCERPTS = """
Use only the excerpts and data provided in this prompt.
Do not add facts, names, votes, or figures not present in the provided context.
If information is missing, say so clearly and suggest where to find it.
"""

_SECTION_HALLUCINATION_PREVENTION = """
HALLUCINATION PREVENTION:
- Do not invent bill numbers, vote counts, donor amounts, or dates.
- Do not invent quotes or statements from legislators.
- Do not infer a legislator's position from party affiliation alone.
- If a figure appears only once in the data, present it as a single data point, not a pattern.
- If you are uncertain, say so explicitly.
"""

_SECTION_WHEN_TO_SAY_IDK = """
WHEN TO SAY "I DON'T KNOW":
- If the provided data does not contain an answer, say so directly.
- Do not guess or extrapolate.
- Suggest a source where the user can find the missing information.
- Acceptable: "VoteIQ does not have that data in the provided context.
  Check the original source, such as Congress.gov, Virginia LIS,
  FEC, VPAP, or the relevant official record."
"""

# ── Conditional sections (injected based on query_context) ───────────────────

_SECTION_CAMPAIGN_FINANCE_CITATION = """
CAMPAIGN FINANCE CITATION RULES:
- Federal campaign finance figures: cite as "FEC Schedule A, fec.gov, cycle [year]."
- Virginia state campaign finance figures: cite as "Virginia SBE campaign finance filings, cycle [year]."
- Do not present PAC, party committee, business, employee, or individual donations as proof of influence or motive.
- Do not say donations caused votes.
- Use safe language: "donor-network overlap", "funding concentration",
  "public-record pattern", "timing overlap", and "correlation does not imply causation."
"""

_SECTION_VOTE_INTERPRETATION = """
VOTE INTERPRETATION RULES:
- Yea = voted in favor of the bill or measure.
- Nay = voted against.
- Not Voting / Abstain = did not cast a vote; do not infer reason.
- Present = acknowledged but did not vote yes or no.
- Concurrence votes follow the same rules.
- Do not interpret a vote as ideological alignment without additional evidence.
- Present vote patterns as data, not conclusions.
"""

_SECTION_SPEECH_CONTEXT = """
SPEECH AND TRANSCRIPT CONTEXT:
[Stub — transcript ingestion not yet built.
Do not reference speech or floor statement data until this section is populated.]
"""

_SECTION_FEDERAL_REP_FORMAT = """
FEDERAL REP RESPONSE FORMAT:
When responding about a U.S. House or Senate member:
1. Open with: full name, district or state, party, current term.
2. Vote summary: total roll calls shown, yes/no/not voting counts, yes rate.
3. Key bills: sponsored or co-sponsored, with bill number and one-sentence summary.
4. Committee assignments: name each committee and subcommittee.
5. Close with: congress.gov profile link.
Cite bill type and number (e.g. H.R. 23). Note source as congress.gov.
"""

_SECTION_STATE_REP_FORMAT = """
STATE LEGISLATOR RESPONSE FORMAT:
When responding about a Virginia state legislator:
1. Open with: full name, chamber (House of Delegates or Senate),
   district number, party, current term.
2. Vote summary: total votes shown, yes/no/abstain counts, yes rate.
3. Key bills: sponsored or patron, with bill number and one-sentence summary.
   Note: Virginia uses "patron" not "sponsor."
4. Committee assignments: list all committees and any chair roles.
5. Campaign finance: top donor sectors, total raised this cycle,
   notable PAC contributions if present.
6. Close with: Virginia LIS profile link and VPAP link if available.
Cite bill numbers in Virginia format (e.g. HB 1234, SB 567).
Note source as Virginia LIS (lis.virginia.gov).
"""

# ── Section registries ────────────────────────────────────────────────────────

BASE_SECTIONS: list[str] = [
    _SECTION_USE_ONLY_EXCERPTS,
    _SECTION_HALLUCINATION_PREVENTION,
    _SECTION_WHEN_TO_SAY_IDK,
]

CONDITIONAL_SECTIONS: dict[str, dict] = {
    "touches_donor_data": {
        "section":          _SECTION_CAMPAIGN_FINANCE_CITATION,
        "requires_feature": "allow_donor_triangle",
    },
    "touches_voting_patterns": {
        "section":          _SECTION_VOTE_INTERPRETATION,
        "requires_feature": None,
    },
    "touches_speech_context": {
        "section":          _SECTION_SPEECH_CONTEXT,
        "requires_feature": "allow_speech_context",
    },
    "federal_rep_present": {
        "section":          _SECTION_FEDERAL_REP_FORMAT,
        "requires_feature": None,
    },
    "state_rep_present": {
        "section":          _SECTION_STATE_REP_FORMAT,
        "requires_feature": None,
    },
}

# ── Prompt assembly ───────────────────────────────────────────────────────────

def get_system_prompt(
    voice: str = "free",
    query_context: dict | None = None,
) -> str:
    """
    Assemble a complete system prompt from:
    1. Voice identity block (tone, rules, output format)
    2. Base sections (always included)
    3. Conditional sections (gated by query_context + tier feature flags)
    """
    voice = (voice or "free").lower().strip()
    cfg   = VOICE_CONFIG.get(voice, VOICE_CONFIG["free"])

    parts = [VOICE_PROMPTS.get(voice, VOICE_PROMPTS["free"])]
    parts.extend(BASE_SECTIONS)

    if query_context:
        for flag, entry in CONDITIONAL_SECTIONS.items():
            if not query_context.get(flag):
                continue
            feature = entry["requires_feature"]
            if feature and not cfg.get(feature):
                continue
            parts.append(entry["section"])

    return "\n\n".join(parts)
