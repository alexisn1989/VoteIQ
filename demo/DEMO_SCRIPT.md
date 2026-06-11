# VoteIQ Media Demo — Run of Show

**Surface:** `/chat` only. **Account:** pro tier. **Operator:** one person, one browser tab.
This path was audited end-to-end; it avoids the concurrency ceiling (single user),
output truncation (pro = 1,500 tokens), and the election-results gap in `/chat`
(no special-election questions in this script — that data lives on `/api/election-chat`).

## Latency expectations (measured 2026-06-11, cold cache)

| Beat | Cold | Notes |
|---|---|---|
| 1. Who represents me | ~49s | Sonnet (simple-question downshift) |
| 2. Lucas conflicts | ~91s | Longest answer — narrate over it |
| 3. Lucas lobbyists | ~37s | |
| 4. Don Scott coalitions | ~32s | |
| 5. Kiggans defense | ~78s | |
| 6. Kiggans outside money | ~59s | |

The pre-warm cuts input processing on repeat asks, but Opus still *generates*
the answer live — expect 20–60s per beat even warm. Plan each beat as
"ask → talk through what the platform is doing → answer lands." Never
stand silent watching the spinner.

## Hard rules during the demo

1. **Ask the scripted queries verbatim.** The pre-warm caches each exact prompt
   prefix; rephrasing pays full latency. Improvised follow-ups are fine *after*
   the scripted beats land — expect those to be slower, narrate over it.
2. **One question in flight at a time.** Don't let a second person drive a
   parallel session on the same instance.
3. **No special-election results questions in `/chat`** ("how did Norfolk vote
   in April"). If asked, switch to the election dashboard — different surface.
4. **Run `demo_warmup.py` 15–30 minutes before showtime** (it sets
   `session_type=research` → 1-hour prompt-cache TTL). If the demo slips past
   the hour, run it again.

## Pre-demo checklist (T-30 min)

- [ ] `python demo/demo_warmup.py` against the production URL — all beats green
- [ ] Confirm replies match the "expect" notes below (data hasn't shifted under you)
- [ ] Open legislator profile pages for Lucas, McPike, and Don Scott in spare tabs
      (visual backup if the chat ever stalls)
- [ ] Phone hotspot ready as venue-wifi fallback

---

## Persona A — Portsmouth voter

Payload base: `district="VA-03"`, `hod_district=88`, `sd_district=18`,
`tier="pro"`, `session_type="research"`

### Beat 1 — warm open (Sonnet, fast)
> **Who represents me?**

Expect: Bobby Scott (VA-03), Del. Don L. Scott Jr. (Portsmouth), Sen. L. Louise
Lucas (Chesapeake/Portsmouth). *Talking point: one lookup, three levels of
government — this is the entry point every voter gets for free.*

### Beat 2 — financial conflicts (the lead story)
> **Does Senator Lucas have any conflicts of interest between her committee role and her personal financial holdings?**

Expect: Lucas chairs **Finance & Appropriations** while her disclosure lists
**Towne Bank** ($50k–$250k) and other Finance-sector holdings — flagged, with
the descriptive framing (no accusation, just the overlap). *Talking point: this
cross-reference is built from her own Statement of Economic Interests against
committee rosters — a story a reporter would otherwise assemble by hand.*

### Beat 3 — lobbyist pressure map
> **Which registered lobbyists are active in the policy areas Senator Lucas oversees?**

Expect: ~16 Finance-sector principals, ~31 lobbyists — Virginia Bankers
Association, League of Credit Unions, AFLAC, Genworth. *Talking point:
2026–27 registrations matched to her committee's oversight area.*

### Beat 4 — voting coalitions
> **Who does Delegate Don Scott vote most similarly with, and who opposes him most?**

Expect: same-party allies in the high 90s%, cross-party bipartisan partners,
and near-perfect opposition (Phillip A. Scott, R — ~3% agreement). *Talking
point: every roll call of the 2026 session, pairwise. And note the platform
keeps the two Delegates Scott straight — name disambiguation is built in.*

---

## Persona B — Virginia Beach voter

Payload base: `district="VA-02"`, `sd_district=22`, `tier="pro"`,
`session_type="research"`

### Beat 5 — federal money + votes (descriptive, verifiable)
> **How much defense industry money has Jen Kiggans received, and how does she vote on defense bills?**

Expect: Defense & Aerospace ~$65k (Colonna's Shipyard, Bollinger Shipyards —
local employers), plus final-passage votes with bill numbers (S 1071, the
FY2026 NDAA — Yea). *Talking point: donations and votes are listed separately
and every count cites the roll call — we deliberately don't assert causation;
the journalist draws the conclusion.*

### Beat 6 — outside money
> **What outside groups have spent money for or against Jen Kiggans?**

Expect: FEC independent expenditures — Super PAC totals for/against with
committee names and cycles. *Talking point: this is Schedule E outside money,
distinct from the donations in Beat 5.*

---

## If something goes wrong

| Symptom | Do this |
|---|---|
| Slow first token on a scripted beat | Cache expired — keep talking; the answer lands. Re-warm at next break. |
| "temporarily unavailable" | Re-ask once (transient API error, the client retries 3×). Second failure → switch to the profile-page tabs. |
| Answer missing an expected datapoint | Profile tabs show the same data visually — pivot: "here's the same analysis on the legislator's page." |
| Off-script audience question about April election results | "Different dashboard — let me show you" → election results page, not `/chat`. |
