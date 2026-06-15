#!/usr/bin/env python3
"""
VoteIQ Query Test Harness — 15 representative queries.

For each query, runs build_database_context() and evaluates whether the
context layer either:
  (a) "answerable" — includes a source-URL provenance field, OR
  (b) "decline"    — signals unavailability without fabricating data.

Shows the full relevant context excerpt so failures of the form
"declines rudely" or "answers without a source" are visible.

Run:
    python tests/run_query_harness.py
"""
import re
import sys
import os
import io
import textwrap

# Force UTF-8 on Windows where the default console codec is cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voteiq.services.database_context import build_database_context, _check_banned_causation_phrases

# ── Provenance / decline detectors ────────────────────────────────────────────

_PROVENANCE = [
    (r"source_url=https?://[^\s;]+",           "source_url"),
    (r"openstates_url=https?://[^\s;]+",       "openstates_url"),
    (r"source=(?:OpenStates|Virginia SBE)[^\n]*", "source= label"),
    (r"clerk\.house\.gov/Votes/\d{4}/\d+",    "House Clerk URL"),
    (r"fec\.gov/data/committee/[A-Z0-9]+",    "FEC committee URL"),
    (r"openstates\.org/va/bills/",             "OpenStates bill URL"),
]

_DECLINE = [
    (r"lookup_status=zero_records",             "zero_records"),
    (r"lookup_status=lookup_error",             "lookup_error"),
    (r"lookup_status=out_of_scope",             "out_of_scope"),
    (r"database_unavailable",                   "db_unavailable"),
    (r"table_missing",                          "table_missing"),
    (r"not in the dataset",                     "scope note"),
    (r"no rows matched",                        "no rows matched"),
    (r"no local federal vote rows",             "no federal vote rows"),
    (r"SCOPE LIMIT",                            "SCOPE_LIMIT block"),
    (r"polling.*not.*included|not.*included.*polling", "polling excluded"),
    (r"no (?:SBE|contribution) records found", "no SBE records"),
    (r"lookup_status=needs_entity",             "needs_entity"),
    (r"not found in local dataset",             "not in local dataset"),
    (r"zero-records|zero_records",              "zero_records"),
    (r"\bno\b.*vote records found",             "no vote records"),
]

_MOTIVE_FABRICATION = re.compile(
    r"\b(bribed?|corruption|real reason|true reason|ulterior|quid pro quo"
    r"|under the table|secret deal|pay.?to.?play)\b",
    re.I,
)


def _find_first(patterns, text):
    for pat, label in patterns:
        m = re.search(pat, text, re.I | re.S)
        if m:
            start = max(0, m.start() - 60)
            end   = min(len(text), m.end() + 80)
            excerpt = text[start:end].replace("\n", " ").strip()
            return label, f"…{excerpt}…"
    return None, ""


def _context_excerpt(ctx: str, chars: int = 1800) -> str:
    """Return the first `chars` chars of context, line-wrapped for readability."""
    chunk = ctx[:chars]
    if len(ctx) > chars:
        chunk += f"\n[…{len(ctx) - chars} more chars omitted…]"
    return chunk


# ── Query catalogue ────────────────────────────────────────────────────────────

QUERIES = [
    # ── Answerable from records (WHRO demo + similar) ─────────────────────────
    dict(id="Q01", expect="provenance",
         label="WHRO demo — Aaron Rouse SB249 vote",
         query="How did Aaron Rouse vote on SB249?",
         note="Expect openstates_url for VA state vote"),

    dict(id="Q02", expect="provenance",
         label="WHRO demo — Kiggans defense votes",
         query="How has Kiggans voted on defense bills in Congress?",
         note="Expect clerk.house.gov URL on each roll-call row"),

    dict(id="Q03", expect="provenance",
         label="WHRO demo — Spanberger campaign donors",
         query="Who are Spanberger's top campaign donors?",
         note="Expect source=Virginia SBE Schedule A + public_url=unavailable"),

    dict(id="Q04", expect="provenance",
         label="WHRO demo — Outside spending against Kiggans",
         query="Which PACs spent money against Kiggans?",
         note="Expect fec.gov/data/committee URLs"),

    dict(id="Q05", expect="provenance",
         label="WHRO demo — PAC vs vote correlation Kiggans defense",
         query="Show PAC spending vs Kiggans vote record on defense",
         note="Expect INFERENCE RULE + clerk URLs"),

    # ── Looks answerable but no backing record ─────────────────────────────────
    dict(id="Q06", expect="decline",
         label="Nonexistent bill — HB9999 2026",
         query="How did Aaron Rouse vote on HB9999 in 2026?",
         note="Bill does not exist — must not fabricate a vote"),

    dict(id="Q07", expect="decline",
         label="Candidate as donor (not in schema)",
         query="How much did Jennifer McClellan donate to the DCCC?",
         note="Outgoing donations by candidates are not tracked"),

    dict(id="Q08", expect="decline",
         label="Corporate donor not in SBE — Exxon",
         query="How much did Exxon donate to Virginia legislators in 2025?",
         note="Exxon not in va_cf_schedule_a; must not fabricate a dollar figure"),

    # ── Motive / speculation ───────────────────────────────────────────────────
    dict(id="Q09", expect="no_fabricated_motive",
         label="Motive query — why Kiggans voted YES",
         query="Why did Kiggans really vote YES on the defense bill?",
         note="No motive field exists; context must not invent one"),

    dict(id="Q10", expect="no_fabricated_motive",
         label="Bribery allegation — Aaron Rouse SB249",
         query="Was Aaron Rouse bribed to vote against SB249?",
         note="No corruption field in schema; context must carry only records, no causation/bribery language"),

    dict(id="Q11", expect="no_fabricated_motive",
         label="Causation allegation — Dominion bought Spanberger veto",
         query="Did Dominion Energy buy Spanberger's veto of the energy bill?",
         note="Causation data does not exist; context must show only records"),

    # ── Data we don't hold ─────────────────────────────────────────────────────
    dict(id="Q12", expect="decline",
         label="Committee assignments (not in schema)",
         query="What committee assignments does Kiggans have in Congress?",
         note="No committee_assignments table; must signal unavailability"),

    dict(id="Q13", expect="decline",
         label="Pre-2000 — Virginia governor race 1995",
         query="Who ran for Virginia governor in 1995?",
         note="SBE data starts ~2000; no 1995 records"),

    dict(id="Q14", expect="decline",
         label="Pre-2000 — Virginia legislative votes 1998",
         query="What did Virginia legislators vote on in 1998?",
         note="OpenStates VA data starts mid-2000s at earliest"),

    dict(id="Q15", expect="decline",
         label="Polling data (explicitly excluded)",
         query="Show me polling for the 2025 Virginia governor race",
         note="Polling excluded per SCOPE_LIMIT block"),
]

# ── Runner ─────────────────────────────────────────────────────────────────────

SEP = "=" * 72

def run():
    results = []
    for q in QUERIES:
        ctx = build_database_context(q["query"])

        prov_label, prov_excerpt   = _find_first(_PROVENANCE, ctx)
        decl_label, decl_excerpt   = _find_first(_DECLINE,    ctx)
        has_motive_fab = bool(_MOTIVE_FABRICATION.search(ctx))
        has_causation  = _check_banned_causation_phrases(ctx) is not None

        expect = q["expect"]

        if expect == "provenance":
            ok    = prov_label is not None
            verdict = "PASS" if ok else "FAIL — no provenance field found"
            detail  = prov_excerpt if ok else "(none)"

        elif expect == "decline":
            ok    = decl_label is not None
            verdict = "PASS" if ok else "FAIL — no decline signal; may fabricate"
            detail  = decl_excerpt if ok else "(none)"

        elif expect == "no_fabricated_motive":
            ok    = (not has_motive_fab) and (not has_causation)
            verdict = "PASS" if ok else "FAIL — motive/causation text present in context"
            detail  = ("motive fabrication detected" if has_motive_fab
                       else "causation phrase in context" if has_causation
                       else "(context is clean)")

        else:
            ok, verdict, detail = False, "UNKNOWN expect type", ""

        results.append(dict(q=q, ctx=ctx, ok=ok, verdict=verdict,
                            detail=detail, prov_label=prov_label,
                            decl_label=decl_label))

    # ── Print report ──────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r["ok"])
    total  = len(results)

    for r in results:
        q = r["q"]
        print(SEP)
        status = "✓" if r["ok"] else "✗"
        print(f"{status}  {q['id']}  [{q['expect'].upper()}]  {q['label']}")
        print(f"   Query : {q['query']}")
        print(f"   Note  : {q['note']}")
        print(f"   Verdict: {r['verdict']}")
        if r["detail"] and r["detail"] != "(context is clean)":
            print(f"   Match : {r['detail']}")
        print()
        print("   ── Context excerpt ──")
        for line in _context_excerpt(r["ctx"]).split("\n"):
            print("   " + line)
        print()

    print(SEP)
    print(f"SUMMARY: {passed}/{total} passed")
    print(SEP)

    failed = [r for r in results if not r["ok"]]
    if failed:
        print("\nFAILED QUERIES:")
        for r in failed:
            print(f"  {r['q']['id']} — {r['q']['label']}")
        sys.exit(1)


if __name__ == "__main__":
    run()
