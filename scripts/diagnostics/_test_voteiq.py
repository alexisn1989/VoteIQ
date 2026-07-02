"""Quick smoke-test for the voteiq package. Run from project root."""
import sys, traceback

failures = []

def check(label, fn):
    try:
        fn()
        print(f"  PASS  {label}")
    except Exception as e:
        failures.append(label)
        print(f"  FAIL  {label}: {e}")
        traceback.print_exc()


# ── 1. Imports ────────────────────────────────────────────────────────────────
print("=== 1. Imports ===")

def test_imports():
    from voteiq.config.finance_rules import (
        LevelConfig, ContributionLimits, get_config, get_disclaimer,
        FEDERAL_CONFIG, STATE_CONFIG,
    )
    from voteiq.config.prompts import (
        LEGAL_DISCLAIMER, SYSTEM_PROMPT, CHAT_PROMPT,
        get_prompt, format_sector_lines, format_committee_lines,
        format_defection_lines, format_cycle_lines,
        format_geography_lines, format_network_lines, format_loyalty_lines,
    )
    from voteiq.db import federal, virginia
    from voteiq.queries.sectors import get_federal_sector_totals, get_state_sector_totals
    from voteiq.queries.donor_shift import get_federal_donor_shift, get_state_donor_shift
    from voteiq.queries.geography import get_federal_geography, get_state_geography
    from voteiq.queries.votes import get_federal_loyalty, get_state_loyalty
    from voteiq.queries.committees import get_federal_committees, get_state_committees
    from voteiq.analysis.classify import classify_sectors, _LARGE_DONOR_LABELS
    from voteiq.analysis.summarize import summarize_profile, build_classified
    from voteiq.analysis.guardrails import (
        GuardrailResult, run_guardrails, safe_output, FALLBACK_RESPONSE,
    )
    from voteiq.llm.claude_client import (
        get_client, generate_report, generate_chat_response,
        build_triangle_prompt, build_donor_shift_prompt,
        build_geography_prompt, build_network_prompt,
        build_loyalty_prompt, build_effectiveness_prompt,
    )
    from voteiq.llm.prompt_builder import build_analyst_prompt

check("all package imports", test_imports)


# ── 2. LevelConfig ────────────────────────────────────────────────────────────
print("\n=== 2. LevelConfig / get_config ===")

from voteiq.config.finance_rules import get_config

def test_federal_config():
    c = get_config("federal", cycle=2024)
    assert c.level == "Federal", c.level
    assert c.limits.individual == 3300
    assert c.limits.pac == 5000
    assert c.db_path.endswith("polls.db"), c.db_path

def test_state_config():
    c = get_config("state")
    assert c.level == "Virginia State"
    assert c.limits.individual is None
    assert c.limits.pac is None
    assert c.db_path.endswith("legislative_intelligence.db"), c.db_path

def test_2026_limit():
    c = get_config("federal", cycle=2026)
    assert c.limits.individual == 3500

check("federal config (2024)", test_federal_config)
check("state config", test_state_config)
check("federal 2026 limit = 3500", test_2026_limit)


# ── 3. classify + summarize ───────────────────────────────────────────────────
print("\n=== 3. classify_sectors / build_classified ===")

from voteiq.analysis.classify import classify_sectors
from voteiq.analysis.summarize import build_classified

FAKE_TOTALS = [
    ("Healthcare", 50000.0, 5,  10000.0),
    ("Legal",      10000.0, 20,   500.0),
    ("Individual",  2000.0, 40,    50.0),
]

def test_classify_sectors():
    # Federal config: limit=3300, 0.8*limit=2640
    # Healthcare avg=10000 >= 2640 -> Near-max donor money
    # Legal avg=500 >= 200 but < 1000 -> Mixed donor base
    # Individual avg=50 < 200 -> Small-dollar base
    cfg_dict = {"contribution_limit": 3300, "grassroots_threshold": 200, "major_donor": 50000}
    rows = classify_sectors(FAKE_TOTALS, cfg_dict)
    assert len(rows) == 3
    labels = {r["sector"]: r["concentration"] for r in rows}
    assert labels["Healthcare"] == "Near-max donor money",  labels
    assert labels["Legal"]      == "Mixed donor base",      labels
    assert labels["Individual"] == "Small-dollar base",     labels

def test_build_classified():
    cfg = get_config("state")
    bundle = build_classified(FAKE_TOTALS, cfg)
    p = bundle["profile"]
    assert "corporate_pct"             in p
    assert "grassroots_pct"            in p
    assert "large_donor_pct_by_dollars" in p
    assert p["corporate_pct"] == p["large_donor_pct_by_dollars"]
    assert p["has_limits"] is False
    assert bundle["defections"] == []

check("classify_sectors labels", test_classify_sectors)
check("build_classified bundle + aliases", test_build_classified)


# ── 4. Format helpers ─────────────────────────────────────────────────────────
print("\n=== 4. Format helpers ===")

from voteiq.config.prompts import (
    format_sector_lines, format_committee_lines, format_defection_lines,
    format_cycle_lines, format_geography_lines,
)

def test_defection_format():
    d = [{"bill_id": "HB100", "bill_title": "Budget", "vote": "NO",
          "party_majority": "YES", "classification": "Donor overlap"}]
    out = format_defection_lines(d)
    assert "HB100" in out and "Budget" in out and "Donor overlap" in out

def test_geography_format():
    geo = [("Richmond", "VA", 10, 5000.0, "In State"),
           ("New York",  "NY",  2, 1000.0, "Out of State")]
    out = format_geography_lines(geo)
    assert "Richmond" in out and "New York" in out

def test_empty_fallbacks():
    assert "No sector"     in format_sector_lines([])
    assert "None provided" in format_committee_lines([])
    assert "No notable"    in format_defection_lines([])
    assert "No multi-cycle" in format_cycle_lines([])

check("format_defection_lines (new dict shape)", test_defection_format)
check("format_geography_lines",                  test_geography_format)
check("format_* empty fallbacks",                test_empty_fallbacks)


# ── 5. Prompt templates ───────────────────────────────────────────────────────
print("\n=== 5. build_*_prompt ===")

from voteiq.llm.claude_client import (
    build_triangle_prompt, build_geography_prompt,
    build_network_prompt, build_loyalty_prompt,
)

def test_triangle_prompt():
    cfg = get_config("state")
    bundle = build_classified(FAKE_TOTALS, cfg)
    p = build_triangle_prompt("Jane Smith", cfg, bundle, ["Finance"], "2023")
    assert "Jane Smith" in p
    assert "TRIANGLE"   in p
    assert "Virginia"   in p

def test_geography_prompt():
    cfg = get_config("state")
    geo = [("Richmond", "VA", 10, 5000.0, "In State"),
           ("DC",       "DC",  2,  500.0, "Out of State")]
    p = build_geography_prompt("Jane Smith", cfg, geo, "2023")
    assert "In-state donors"     in p
    assert "Out-of-state donors" in p

def test_network_prompt():
    cfg = get_config("federal", cycle=2024)
    net = [("John Doe", "R", "VA", "House", 50000.0, 12, 4166.0)]
    p = build_network_prompt("Healthcare", cfg, net, 2024)
    assert "Healthcare" in p and "John Doe" in p

def test_loyalty_prompt():
    cfg = get_config("federal", cycle=2024)
    loy = [(118, 210, 230, 91.3)]
    p = build_loyalty_prompt("Jane Smith", cfg, loy, minority_party=False)
    assert "91.3" in p and "Jane Smith" in p

check("build_triangle_prompt",               test_triangle_prompt)
check("build_geography_prompt (in_state_pct)", test_geography_prompt)
check("build_network_prompt",                test_network_prompt)
check("build_loyalty_prompt",                test_loyalty_prompt)


# ── 6. Guardrails ─────────────────────────────────────────────────────────────
print("\n=== 6. Guardrails ===")

from voteiq.analysis.guardrails import run_guardrails, safe_output

def test_report_mode_violation():
    cfg = get_config("federal", cycle=2024)
    r = run_guardrails("This senator was bought by donors.", cfg, {}, mode="report")
    assert r.violations, "Expected violations"
    assert "flagged" in safe_output(r).lower()

def test_chat_mode_warning_not_violation():
    cfg = get_config("state")
    r = run_guardrails("This senator was bought by donors.", cfg, {}, mode="chat")
    assert r.violations == [], "Chat mode should not produce violations"
    assert r.warnings,        "Chat mode should produce warnings"

def test_clean_text():
    cfg = get_config("federal", cycle=2024)
    r = run_guardrails(
        "There is an alignment between healthcare donations and committee work.",
        cfg, {"sectors": [1], "votes": [1]}, mode="report",
    )
    assert r.violations == []
    assert safe_output(r) == r.text

def test_missing_data_warning():
    cfg = get_config("federal", cycle=2024)
    r = run_guardrails("This member voted yes on the bill.", cfg, {}, mode="report")
    assert any("vote" in w.lower() for w in r.warnings), r.warnings

check("report mode: banned term = violation",  test_report_mode_violation)
check("chat mode: banned term = warning only", test_chat_mode_warning_not_violation)
check("clean text: no violations",             test_clean_text)
check("missing vote data = warning",           test_missing_data_warning)


# ── 7. DB queries (live, read-only) ──────────────────────────────────────────
print("\n=== 7. DB queries (live) ===")

from voteiq.queries.sectors import get_federal_sector_totals, get_state_sector_totals
from voteiq.queries.votes import get_federal_loyalty, get_state_loyalty

def test_state_sector():
    rows = get_state_sector_totals("LIS001", "2023")
    assert isinstance(rows, list)

def test_state_loyalty():
    rows = get_state_loyalty("LIS001")
    assert isinstance(rows, list)

def test_federal_sector():
    rows = get_federal_sector_totals("H0VA00001", 2024)
    assert isinstance(rows, list)

check("state sector totals (returns list)", test_state_sector)
check("state loyalty (returns list)",       test_state_loyalty)
check("federal sector totals (returns list)", test_federal_sector)


# ── Summary ───────────────────────────────────────────────────────────────────
print()
if failures:
    print(f"FAILED {len(failures)} check(s): {failures}")
    sys.exit(1)
else:
    print(f"ALL {28 - len(failures)} CHECKS PASSED")
