"""Pre-warm the VoteIQ demo path through /chat.

Sends every scripted demo beat (see demo/DEMO_SCRIPT.md) verbatim with
session_type="research" so the Anthropic prompt cache is written with a
1-hour TTL. Run this 15-30 minutes before showtime; re-run if the demo
slips past the hour.

Usage:
    python demo/demo_warmup.py                          # local default
    BASE_URL=https://your-app.onrender.com python demo/demo_warmup.py

Exit code 0 = every beat returned a healthy reply. Non-zero = fix before
going on stage.
"""
import os
import sys
import time

import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

_PORTSMOUTH = {
    "district": "VA-03",
    "hod_district": 88,
    "sd_district": 18,
    "locality": "PORTSMOUTH CITY",
    "tier": "pro",
    "session_type": "research",
}
_VABEACH = {
    "district": "VA-02",
    "sd_district": 22,
    "locality": "VIRGINIA BEACH CITY",
    "tier": "pro",
    "session_type": "research",
}

# (beat name, base payload, verbatim question, any-of marker groups)
# Every marker group must have at least one case-insensitive hit in the
# reply, otherwise the beat is flagged — the data may have shifted since
# the script was written.
BEATS = [
    (
        "1. Who represents me (Portsmouth)",
        _PORTSMOUTH,
        "Who represents me?",
        [["bobby scott"], ["don"], ["lucas"]],
    ),
    (
        "2. Lucas financial conflicts",
        _PORTSMOUTH,
        "Does Senator Lucas have any conflicts of interest between her "
        "committee role and her personal financial holdings?",
        [["towne", "finance"], ["lucas"]],
    ),
    (
        "3. Lucas lobbyist pressure map",
        _PORTSMOUTH,
        "Which registered lobbyists are active in the policy areas "
        "Senator Lucas oversees?",
        [["banker", "credit union", "aflac", "genworth", "lobby"]],
    ),
    (
        "4. Don Scott voting coalitions",
        _PORTSMOUTH,
        "Who does Delegate Don Scott vote most similarly with, and who "
        "opposes him most?",
        [["scott"], ["%", "agree", "oppos"]],
    ),
    (
        "5. Kiggans defense money + votes",
        _VABEACH,
        "How much defense industry money has Jen Kiggans received, and "
        "how does she vote on defense bills?",
        [["kiggans"], ["defense"], ["65,268", "defense & aerospace", "shipyard", "$"]],
    ),
    (
        "6. Kiggans outside money",
        _VABEACH,
        "What outside groups have spent money for or against Jen Kiggans?",
        [["kiggans"], ["independent expenditure", "super pac", "outside", "spent"]],
    ),
    (
        "7. Deliberate decline — causation question",
        _VABEACH,
        "Why did Kiggans really vote that way — was it because of the PAC money?",
        # Must NOT assert causation; must offer factual data instead.
        [
            ["can't", "cannot", "no record", "not in", "don't have", "doesn't tell",
             "motivation", "voting record", "public record", "why she voted"],
            ["pac", "outside spending", "vote", "timeline", "correlation", "record"],
        ],
    ),
]

_FAILURE_PHRASES = (
    "temporarily unavailable",
    "unknown district",
    "gemini error",
)


def run_beat(name, base, question, marker_groups, post):
    payload = dict(base)
    payload["messages"] = [{"role": "user", "content": question}]
    t0 = time.time()
    try:
        resp = post(f"{BASE_URL}/chat", json=payload, timeout=180)
    except Exception as e:
        return False, f"request failed: {type(e).__name__}: {e}", 0.0
    elapsed = time.time() - t0
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}", elapsed
    reply = (resp.json().get("reply") or "").strip()
    low = reply.lower()
    if not reply:
        return False, "empty reply", elapsed
    for phrase in _FAILURE_PHRASES:
        if phrase in low:
            return False, f"error reply: {reply[:120]}", elapsed
    missing = [g for g in marker_groups if not any(m in low for m in g)]
    if missing:
        return False, f"markers missing {missing} — reply: {reply[:160]}...", elapsed
    return True, reply, elapsed


def main(post=requests.post):
    print(f"Warming demo path at {BASE_URL}\n")
    failures = 0
    for name, base, question, markers in BEATS:
        ok, detail, elapsed = run_beat(name, base, question, markers, post)
        status = "OK  " if ok else "FAIL"
        print(f"[{status}] {elapsed:6.1f}s  {name}")
        if not ok:
            failures += 1
            print(f"        {detail}")
    print()
    if failures:
        print(f"{failures} beat(s) unhealthy — do NOT go on stage until green.")
        return 1
    print("All beats green. Prompt cache warm for ~1 hour (research TTL).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
