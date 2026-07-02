# Public-Record Research Brief (Refactored)

**Subject:** L. Louise Lucas
**Scope:** Campaign finance patterns, donor concentration, lobbying registry overlap, donor-vote alignment — Virginia state records
**Sources:** Virginia SBE campaign finance filings; VoteIQ donor-vote alignment database; OpenStates VA roll-call records; Virginia lobbyist registration registry
**Current through:** 2026 session data; finance records through 2025
**Refactored per:** VoteIQ Civic Analytics Report Integrity Protocol v1

---

## Important Framing Note

"Conflict of interest" is a legal term with a specific meaning under Virginia law. VoteIQ does not make legal determinations. What follows are **public-record patterns** — donor concentrations, vote alignment rates, and registry overlaps — drawn from official filings. **Correlation does not imply causation. No motive or intent is inferred.**

---

## Layer 1 — Raw Data
**Trust level: HIGH | Source: official filings and registries**

> **[RAW DATA]**
> Total raised (incoming contributions, 2012–2025): $9.1M across 14 election cycles
> *Source: Virginia SBE campaign finance filings*

> **[RAW DATA]**
> Top recorded incoming contributions by donor:
> | Donor | Amount |
> |---|---:|
> | Dominion Energy Inc. PAC | $1,600,000 |
> | Urban One | $377,500 |
> | Christopher Clemente | $200,000 |
> *Source: Virginia SBE campaign finance filings (incoming contributions only)*

> **[RAW DATA — RECLASSIFIED]**
> "Lucas for Senate Campaign" line ($579,393): this entry represents a **committee self-transfer or outgoing disbursement**, not an incoming contribution from an external donor. It has been removed from the donor table above.
>
> [DATA GAP: outgoing transfer recipients not available in source dataset — figure represents committee disbursements, not incoming contributions]

> **[RAW DATA]**
> Voting record (2025–2026 session):
> - Total votes cast: 4,628
> - YES: 4,422 (95.5%) | NO: 108 (2.3%) | Not voting: 97
> *Source: OpenStates VA roll-call records*

> **[RAW DATA]**
> Sector-specific vote counts (Alcohol/Gambling bills, 2025–2026 session):
> - YES on Alcohol/Gambling bills: 14 votes
> - YES on all other bills: 392 votes
> *Source: VoteIQ donor_vote_alignment table*

> **[RAW DATA]**
> Lobbyist registry counts (registered Virginia lobbyists by principal, as reported):
> | Principal | Registered VA Lobbyists |
> |---|---:|
> | Dominion Energy Inc. | 16 |
> | Urban One | 18 |
> *Source: Virginia lobbyist registration records (principal-based registry)*

> **[RAW DATA]**
> 2025–2026 session incoming donor activity by industry (classified):
> | Industry | Amount | Donor Count |
> |---|---:|---:|
> | Legal | $30,200 | 42 |
> | Finance | $24,050 | 72 |
> | Nonprofit | $15,188 | 7 |
> | Government | $13,540 | 45 |
> | Manufacturing | $12,500 | 5 |
> *Source: VoteIQ donor_influence analysis, 2025–2026 session*

---

## Layer 2 — Derived Metrics
**Trust level: MEDIUM | Source: computed from Layer 1 data**

> **[DERIVED METRIC]**
> Tobacco sector: 21% of total incoming contributions (~$1.9M) — computed from Virginia SBE filings

> **[DERIVED METRIC]**
> Utilities sector: 19% of total incoming contributions (~$1.7M) — computed from Virginia SBE filings

> **[DERIVED METRIC]**
> Legal sector concentration (2025–2026): 27% of classified incoming donor dollars

> **[DERIVED METRIC — LOW STATISTICAL SIGNIFICANCE: n=14, result within noise threshold, no meaningful pattern established]**
> Donor-vote alignment (Alcohol/Gambling sector vs. all other bills):
>
> | Metric | Rate |
> |---|---:|
> | YES rate on Alcohol/Gambling bills | 100% (14 votes) |
> | YES rate on all other bills | 99% (392 votes) |
> | Alignment delta | +0.8 percentage points |
>
> **Analyst note:** The +0.8 point delta is computed from 14 sector-specific votes. This sample size is insufficient to establish a statistically meaningful pattern. The result falls within expected noise for any legislator voting on a small number of bills in a narrow sector. This figure is **not presented as a finding or pattern.**

> **[NOT CALCULATED — Reason: insufficient sector-specific vote linkage in current session dataset. Top donor sector (Legal) has inadequate bill-vote coverage to compute a meaningful alignment rate. Confidence: low. This is a data coverage limitation, not a finding.]**
> CoI Score (2025–2026 session): not calculated

---

## Layer 3 — Interpretive Signals
**Trust level: LOW | Pattern detection only — no causal inference**

> **[INTERPRETIVE SIGNAL — LOW CONFIDENCE: temporal co-occurrence, no causal inference]**
> **Fundraising volatility / outlier detection — 2023 and 2024**
>
> The following figures reflect activity associated with the Lucas Campaign Fund — a committee entity. These figures represent disbursement or transfer activity from that fund, **not incoming contributions to Sen. Lucas's campaign.**
>
> | Year | Amount | vs. Prior Baseline | Notes |
> |---|---:|---:|---|
> | 2023 | $112,900 | 3.2x prior baseline | Activity level elevated relative to prior cycles |
> | 2024 | $1,491,000 | 7.7x prior baseline | Activity level substantially elevated relative to prior cycles |
>
> [DATA GAP: outgoing transfer recipients not available in source dataset for either cycle]
>
> Bills observed in the same period as the 2023 elevated activity: HB2368, HB1598 (medical marijuana/cannabis bills).
> Bills observed in the same period as the 2024 elevated activity: HB1016 (charitable gaming), HB1102 (marijuana/driving).
>
> [INTERPRETIVE SIGNAL — LOW CONFIDENCE: temporal co-occurrence, no causal inference. The presence of these bills in the same legislative periods is noted for auditability only. VoteIQ does not conclude that donations were temporally overlapping with or related to these specific bills.]

> **[INTERPRETIVE SIGNAL — LOW CONFIDENCE]**
> **Lobbyist registry overlap**
>
> [CONTEXT: Virginia's lobbyist registry is principal-based and broad in scope. Large utilities and media companies typically maintain high lobbyist counts across all legislative sessions as a standard practice. No baseline comparison is available in the current dataset to assess whether these counts are atypical for organizations of this size or sector.]
>
> [CONTEXT GAP: no baseline lobbyist count is available for comparison against organizations of similar size, sector, or legislative engagement level. The counts below cannot be characterized as elevated, low, or typical without such a baseline.]
>
> Two of Sen. Lucas's top incoming-contribution donors also have registered Virginia lobbyists. These are separate, legally distinct channels of legislative engagement.
>
> | Donor | Registered VA Lobbyists |
> |---|---:|
> | Dominion Energy Inc. | 16 |
> | Urban One | 18 |
>
> This is a **donor-registry overlap** — not evidence of improper influence.

---

## Data Limits

- Virginia does not require **bill-specific lobbying disclosure** — lobbyist registration is by principal/employer, not by bill. Sector overlaps are proxies, not direct linkages.
- The 2023 and 2024 elevated figures involve **committee disbursement activity**, not incoming contributions. Recipient breakdown is not available in the current dataset.
- CoI Score for the 2025–2026 session is **not calculated** due to insufficient sector-specific vote-bill linkage data. This is a data coverage limitation.
- Full bill-specific voting records for the Legal sector are not available in the current dataset.
- The $579,393 "Lucas for Senate Campaign" line originally listed in the donor table has been reclassified as a committee self-transfer and removed from the incoming donor figures.

---

## Suggested Follow-Up Queries

- [How did Sen. Lucas vote on energy/utilities bills?](/ask?q=How%20did%20Sen.%20Lucas%20vote%20on%20energy%20and%20utilities%20bills%3F)
- [Which legislators have the highest conflict-of-interest scores in Virginia?](/ask?q=Which%20Virginia%20legislators%20have%20the%20highest%20conflict%20of%20interest%20scores%3F)

---

---

## Data Integrity Notes

*(Appended per VoteIQ Civic Analytics Report Integrity Protocol v1)*

### Ambiguities Found in Original

1. **Incoming vs. outgoing confusion:** "L. Louise Lucas Campaign Fund (a donor entity, separate from her own committee)" was listed in the donor table alongside verified incoming contributions. The $579,393 and $1,491,000 figures represent outgoing disbursements or transfers from her fund — not incoming contributions. Presenting these alongside PAC contributions creates a false equivalence.

2. **Causal language:** "These elevated giving cycles coincide with active Alcohol/Gambling-category legislation in the Virginia General Assembly" — "coincide with" implies a meaningful temporal relationship. Replaced with explicit temporal co-occurrence framing and low-confidence label.

3. **+0.8 delta framing:** Despite the analyst note calling it "not analytically meaningful," the delta was still presented under the header "Donor-Vote Alignment" as a named metric with a table. Retained the table but added mandatory significance label and removed from findings framing.

4. **Lobbyist counts without baseline:** 16 and 18 registered lobbyists presented with no context for whether these are typical, elevated, or low for organizations of similar size and sector. No implied abnormality is now stated.

5. **CoI Score "None":** Original output listed score as "None" with partial explanation buried in the Data Limits section. Replaced with structured non-calculation block at point of use.

6. **"Donor trend spike" header:** The word "spike" implies detected abnormality. Replaced with "fundraising volatility / outlier detection" to accurately describe what the computation does (flag statistical outliers) without implying interpretation.

### Corrections Made

| Correction | Reason |
|---|---|
| Removed $579,393 from incoming donor table | Figure is a committee self-transfer, not an external contribution |
| Reclassified $1,491,000 as committee disbursement activity | Outgoing from her fund, not incoming to her campaign |
| Added [DATA GAP] placeholder for recipient breakdown | Data not available in source dataset; gap must be explicit |
| Added [DERIVED METRIC — LOW STATISTICAL SIGNIFICANCE] to alignment table | n=14 is insufficient for a meaningful pattern; original framing implied otherwise |
| Added [CONTEXT GAP] to lobbyist section | No baseline available; counts cannot be characterized as notable |
| Replaced CoI "None" with [NOT CALCULATED] block | Null output requires structured explanation, not bare null |
| Replaced "coincides with" throughout | Causal language removed per protocol |
| Replaced "Donor Trend Spike" header | Implied abnormality without stated baseline |

### Assumptions Removed or Downgraded

- Removed: implication that 2023/2024 fund activity is temporally linked to specific bills
- Removed: implication that lobbyist counts are elevated or notable
- Removed: +0.8 delta as a "finding" or "pattern"
- Downgraded: all temporal co-occurrence observations to [INTERPRETIVE SIGNAL — LOW CONFIDENCE]

### Data Gaps Flagged

1. Outgoing transfer recipients (2023: $112,900; 2024: $1,491,000) — not in source dataset
2. Baseline lobbyist counts for comparable organizations — not in source dataset
3. Bill-specific voting records for Legal sector (2025–2026) — insufficient coverage for CoI calculation

### Self-Audit Checklist

| Item | Result |
|---|---|
| No instance of "coincides with", "suggests", "indicates", "linked to", "influenced" remains | PASS |
| No outgoing transfer or expenditure labeled as incoming contribution or donor figure | PASS |
| No null/None output without structured explanatory block | PASS |
| Every interpretive signal labeled [INTERPRETIVE SIGNAL — LOW CONFIDENCE] | PASS |
| Every derived metric labeled [DERIVED METRIC] | PASS |
| Every raw fact labeled [RAW DATA] | PASS |
| Lobbyist section includes normalization context or explicit context gap note | PASS |
| Alignment section includes sample size and noise threshold label | PASS |
| CoI Score null replaced with structured non-calculation explanation | PASS |
