"""
Tests for _add_pac_vote_correlation_context() causation-language safety.

Rules under test:
- Context block must not contain causation-implying language itself.
- INFERENCE RULE instruction must be present in the block.
- _check_banned_causation_phrases() must catch adversarial LLM output.
- Neutral juxtaposition language must pass the filter.
- Adversarial prompts fishing for quid-pro-quo assertions must be caught.
"""
import pytest

from voteiq.services.database_context import (
    _add_pac_vote_correlation_context,
    _check_banned_causation_phrases,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _pac_blocks(query: str, terms: list[str]) -> list[str]:
    blocks: list[str] = []
    _add_pac_vote_correlation_context(blocks, query, terms)
    return [b for b in blocks if "PAC vs. Vote Correlation" in b]


# ── Test 1: context block contains the INFERENCE RULE instruction ─────────────

def test_context_block_contains_inference_rule():
    """The raw context block must carry the INFERENCE RULE no-causation directive."""
    blocks = _pac_blocks("kiggans pac vote", ["kiggans", "vote", "pac"])
    if not blocks:
        pytest.skip("No PAC correlation data available in local DB")
    block = blocks[0]
    assert "INFERENCE RULE" in block, (
        "INFERENCE RULE missing — LLM has no explicit in-context constraint"
    )
    assert "Do NOT assert" in block or "do not assert" in block.lower()


# ── Test 2: context block itself is free of causation language ────────────────

def test_context_block_has_no_causation_language():
    """The context block the LLM sees must not model causation phrasing itself."""
    blocks = _pac_blocks("kiggans pac vote defense money", ["kiggans", "defense", "money"])
    for block in blocks:
        hit = _check_banned_causation_phrases(block)
        assert hit is None, f"Causation phrase '{hit}' found in context block itself"


# ── Test 3: neutral LLM output passes the filter ─────────────────────────────

@pytest.mark.parametrize("text", [
    "Kiggans received PAC support from defense PACs and voted YES on H.R. 1234.",
    "Congressional Leadership Fund spent $1.1M for Kiggans; she voted Yea on S. 4465.",
    "The record shows PAC spending and Kiggans's vote side by side. "
    "VoteIQ does not infer causation.",
    "Data: FOR Kiggans $1.1M (CLF) | Vote: Yea on H.R. 7726 (On Passage).",
    "No causal claim is made. These are public records presented in parallel.",
])
def test_neutral_text_passes_filter(text):
    """Neutral juxtaposition language must not trigger the banned-phrase filter."""
    assert _check_banned_causation_phrases(text) is None, (
        f"False positive: neutral text '{text[:80]}' incorrectly flagged"
    )


# ── Test 4: banned-phrase filter catches known causation patterns ─────────────

@pytest.mark.parametrize("text", [
    "She voted because of PAC funding from CLF.",
    "The vote was given in exchange for outside spending.",
    "Defense PACs bought her vote on the NDAA.",
    "Her position was influenced by money from conservative PACs.",
    "As a result of PAC donations, she voted YES.",
    "This looks like a quid pro quo between CLF and Kiggans.",
    "She voted in return for the PAC support she received.",
    "The PAC paid off her YES vote on H.R. 7726.",
])
def test_banned_phrases_caught(text):
    """Every known causation-pattern phrase must be detected."""
    hit = _check_banned_causation_phrases(text)
    assert hit is not None, (
        f"Filter missed causation phrase in: '{text[:80]}'"
    )


# ── Test 5: adversarial prompt — full quid-pro-quo assertion ─────────────────

def test_adversarial_quid_pro_quo_assertion():
    """Simulate an adversarial LLM response explicitly asserting quid pro quo.

    This models the worst-case: an LLM that ignores the INFERENCE RULE and
    writes a causation claim anyway. The filter must catch every variant.
    """
    adversarial_responses = [
        (
            "Based on the correlation data, it is clear that Kiggans voted YES "
            "on the defense bill because of PAC funding from CLF."
        ),
        (
            "The pattern strongly suggests a quid pro quo arrangement between "
            "Kiggans and defense PACs — money in, favorable votes out."
        ),
        (
            "PAC money clearly influenced her vote on H.R. 7726. "
            "The timeline shows donations preceding the floor vote."
        ),
        (
            "In exchange for PAC support totalling $1.1M, she voted YES "
            "on legislation aligned with donor interests."
        ),
        (
            "Conservative PACs essentially bought her Yea vote on defense "
            "authorization by spending heavily in her district."
        ),
    ]
    for response in adversarial_responses:
        hit = _check_banned_causation_phrases(response)
        assert hit is not None, (
            f"Adversarial quid-pro-quo assertion escaped the filter:\n  '{response[:100]}'"
        )
