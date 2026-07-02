"""Local stress test: route realistic WHRO-reporter questions through the
same pre-flight chain bills_chat uses, and report which handler answers,
whether it falls through to Claude, and how big that context is.

Run: python -X utf8 _stress_test.py
"""
import sys
import traceback

from voteiq.api.routes import chat as C
from voteiq.services.database_context import build_database_context

PREMIUM = True  # exercise the richest (analyst) path


def route(q: str):
    """Replicate the bills_chat pre-flight chain. Returns (handler, text)."""
    # 1. scope limit
    try:
        r = C._scope_limit_polling_reply(q)
        if r:
            return "scope_limit", r
    except Exception as e:
        return "ERROR:scope_limit", f"{type(e).__name__}: {e}"
    # 2. identity crosswalk
    try:
        r = C._direct_identity_crosswalk_reply(q)
        if r:
            return "identity", r
    except Exception as e:
        return "ERROR:identity", f"{type(e).__name__}: {e}"
    # 3. source hierarchy
    try:
        r = C._direct_voteiq_source_hierarchy_reply(q)
        if r:
            return "source", r
    except Exception as e:
        return "ERROR:source", f"{type(e).__name__}: {e}"
    # 4. governor_reply chain (exact order from endpoint)
    chain = [
        ("donor_alignment", lambda: C._direct_donor_alignment_reply(q)),
        ("va_legislator",   lambda: C._direct_va_legislator_reply(q, premium=PREMIUM)),
        ("va_finance",      lambda: C._direct_va_finance_reply(q, premium=PREMIUM)),
        ("gov_eo",          lambda: C._direct_governor_eo_reply(q)),
        ("gov_veto",        lambda: C._direct_governor_veto_reply(q)),
        ("gov_overview",    lambda: C._direct_spanberger_governor_overview_reply(q)),
        ("gov_correlation", lambda: C._direct_governor_correlation_reply(q) if PREMIUM else None),
    ]
    for name, fn in chain:
        try:
            r = fn()
            if r:
                return name, r
        except Exception as e:
            return f"ERROR:{name}", f"{type(e).__name__}: {e}"
    # 5. fall through to Claude — measure the context it would get
    try:
        ctx = build_database_context(q, pro=PREMIUM)
        return "→CLAUDE", ctx
    except Exception as e:
        return "ERROR:context", f"{type(e).__name__}: {e}\n" + traceback.format_exc()


QUESTIONS = [
    # donor-alignment (the headline feature)
    "does Phillip Scott vote with his donors",
    "does Timothy Griffin vote with his donors",
    "does Aaron Rouse vote with his donors",
    # races / elections
    "who is running in the 2026 senate race in virginia",
    "who is running for governor",
    # legislators
    "tell me about Jen Kiggans",
    "what bills did Don Scott sponsor",
    "tell me about delegate Wren Williams",
    # money
    "who funds Aaron Rouse",
    "which legislators take the most money from Dominion",
    "who are the biggest donors in Virginia politics",
    # governor
    "what executive orders has Spanberger signed",
    "what bills has Spanberger vetoed",
    "who is Abigail Spanberger",
    # bills / committees
    "what is HB7",
    "who chairs the finance committee",
    # federal / hearings
    "what hearings did Mark Warner attend",
    # local angle (Hampton Roads)
    "what conflicts of interest do school board members have",
    # tricky / edge
    "compare senate and house voting records",
    "does Jay Jones vote with his donors",   # AG candidate — may not be in table
]


def main():
    print(f"STRESS TEST — {len(QUESTIONS)} questions, PREMIUM={PREMIUM}\n" + "=" * 78)
    counts = {}
    for q in QUESTIONS:
        handler, text = route(q)
        counts[handler.split(":")[0]] = counts.get(handler.split(":")[0], 0) + 1
        text = text or ""
        n = len(text)
        # one-line verdict
        if handler == "→CLAUDE":
            # show context size + whether the generic dump leaked in
            leaked = "GENERIC-DUMP" if "[Admin SQL Generic Search" in text else "targeted"
            flag = "  ⚠BLOATED" if n > 14000 else ""
            tag = f"→ Claude  ctx={n:>6}c [{leaked}]{flag}"
        elif handler.startswith("ERROR"):
            tag = f"💥 {handler}  {text[:90]}"
        else:
            empty = "  ⚠EMPTY" if n == 0 else ""
            tag = f"✓ {handler:16} {n:>5}c{empty}"
        print(f"\nQ: {q}")
        print(f"   {tag}")
    print("\n" + "=" * 78)
    print("Handler distribution:", dict(sorted(counts.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
