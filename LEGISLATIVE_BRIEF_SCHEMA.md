# VoteIQ — Legislative Record Brief Schema
**Version:** 1.0  
**System:** VoteIQ Civic Analytics Engine  
**Constraint:** Structured civic data reporting only. No causal inference. No intent attribution. No political narrative.

---

---

# PART 1 — EMPTY SCHEMA TEMPLATE

*Copy this template for every legislator report. Fill each field from source data only. Insert [DATA GAP] placeholders where data is unavailable. Do not estimate or infer missing values.*

---

## Legislative Record Brief

---

### A. Metadata Block
> **[RAW DATA — LAYER 1]**

| Field | Value |
|---|---|
| Subject | |
| Role | |
| Chamber | |
| District | |
| Party | |
| Session Range | |
| Report Generated | |
| Data Sources | Virginia SBE; OpenStates; VA Lobbyist Registry; VoteIQ donor_vote_alignment table |
| Coverage Notes | |

---

### B. Voting Record Summary
> **[RAW DATA — LAYER 1]**  
> Source: OpenStates / LegiScan  
> Time range: [define explicitly, e.g. "2026 session YTD" or "2025–2026 regular session"]

| Metric | Value |
|---|---|
| Total votes cast | |
| YES votes | |
| NO votes | |
| Not voting / Abstain | |
| Coverage statement | [e.g. "Covers all recorded roll-call votes in OpenStates for defined session range"] |

---

### C. Recent Votes
> **[RAW DATA — LAYER 1]**  
> Source: OpenStates  
> Last 10–20 recorded roll-call votes. Neutral bill descriptions only — no editorial framing.

| Bill ID | Vote | Bill Description (neutral) | Date |
|---|---|---|---|
| | | | |

*If fewer than 10 votes available: [DATA GAP: insufficient roll-call records for defined session range]*

---

### D. Committee Assignments
> **[RAW DATA — LAYER 1]**  
> Source: Virginia General Assembly official records

| Committee Name | Role | Session | Validity Note |
|---|---|---|---|
| | Member / Chair / Vice Chair | | [e.g. "Current as of [date]" or "Historical — confirm current status"] |

---

### E. Sponsorships
> **[RAW DATA — LAYER 1]**  
> Source: OpenStates  
> **OpenStates limitation note:** Sponsorship and co-sponsorship fields reflect only what is recorded in OpenStates data feeds. Underreporting is possible. These counts are not independently verified against General Assembly official records.

| Metric | Value |
|---|---|
| Bills sponsored | |
| Bills co-sponsored | |
| Definition note | [e.g. "Co-sponsorship = listed as secondary sponsor in OpenStates record"] |

---

### F. Campaign Finance

#### F1a. Incoming Contributions
> **[RAW DATA — LAYER 1]**  
> Source: Virginia SBE campaign finance filings  
> **IMPORTANT:** Incoming contributions and outgoing expenditures/transfers are reported in separate subsections. They must never be combined.

| Metric | Value |
|---|---|
| Total raised (all cycles on record) | |
| Cycle range | |
| Source | Virginia SBE campaign finance filings |

**Top donors (incoming contributions only):**

| Donor Name | Amount | Cycle |
|---|---:|---|
| | | |

#### F1b. Outgoing Expenditures / Transfers (if present in dataset)
> **[RAW DATA — LAYER 1]**  
> Source: Virginia SBE campaign finance filings

| Metric | Value |
|---|---|
| Total disbursed | |
| Largest disbursement category | |
| Transfer recipients | [list if available, or: DATA GAP: recipient breakdown not available in source dataset] |

---

#### F2. Sector Breakdown
> **[DERIVED METRIC — LAYER 2]**  
> Source: Computed from SBE filings using VoteIQ donor taxonomy  
> **Classification note:** Sectors assigned by VoteIQ taxonomy via donor name matching. Not independently audited. These are approximations.

| Sector | Amount | % of Total Classified |
|---|---:|---:|
| | | |

---

### G. Derived Analytics
> **[DERIVED METRIC — LAYER 2]**  
> All values in this section are computed from source data. They are not raw facts.

#### G1. Donor-Vote Alignment Rate (if available)

| Sector | Sample Size (n=) | YES Rate | NO Rate | Confidence |
|---|---:|---:|---:|---|
| | | | | HIGH (n≥100) / MEDIUM (n=30–99) / LOW (n<30) |

#### G2. Statistical Warning Block
*Required whenever any alignment metric has n < 30:*

> **[STATISTICAL WARNING]**  
> One or more alignment rates above are computed from fewer than 30 votes (n < 30). Rates with small samples are within expected statistical noise. These figures do not constitute a meaningful pattern and must not be used as the basis for any inference about legislative behavior.

#### G3. CoI Score
*If not calculable, use this block — do not leave blank or write "None":*

> **[NOT CALCULATED]**  
> Reason: [e.g. insufficient sector-specific vote linkage; top donor sector lacks bill-vote coverage]  
> Confidence: Low — data coverage limitation, not a finding.  
> Action required: [describe what data is needed to enable calculation]

---

### H. Interpretive Signals
> **[INTERPRETIVE SIGNAL — LAYER 3 — LOW TRUST]**  
> **Non-causal observational patterns only. No causal inference is drawn. No intent or influence is implied.**

#### H1. Fundraising Volatility

| Cycle | Amount | vs. Baseline | Observation |
|---|---:|---:|---|
| | | | [e.g. "Elevated disbursement cycle. No causal inference drawn."] |

*If baseline unavailable: [CONTEXT GAP: no multi-cycle baseline available for comparison]*

---

#### H2. Lobbyist Overlap

> **[CONTEXT — NORMALIZATION REQUIRED]**  
> Virginia lobbyist registry is principal-based and broad. Large corporations, utilities, and media organizations typically maintain high lobbyist counts across all sessions. No baseline comparison is available in the current dataset. Counts below are reported as-is and do not indicate abnormal engagement without a baseline for comparison.

| Donor / Principal | Registered VA Lobbyists | Note |
|---|---:|---|
| | | [e.g. "Count reflects all registered principals, not bill-specific activity"] |

*If no baseline: [CONTEXT GAP: no baseline lobbyist count available for comparison]*

---

### I. Data Integrity Notes
> **[REQUIRED — ALL REPORTS]**

**Missing fields:**
- [ ] 

**Dataset limitations:**
- [ ] 

**Proxy data used:**
- [ ] 

**Aggregation assumptions:**
- [ ] 

**Known API / source constraints:**
- [ ] OpenStates co-sponsorship underreporting (if applicable)
- [ ] SBE classification limitations (if applicable)
- [ ] VoteIQ taxonomy approximation (if applicable)

**Corrections applied:**
- [ ] 

---
---

# PART 2 — EXAMPLE FILLED VERSION (L. Louise Lucas)

---

## Legislative Record Brief

---

### A. Metadata Block
> **[RAW DATA — LAYER 1]**

| Field | Value |
|---|---|
| Subject | L. Louise Lucas |
| Role | State Senator |
| Chamber | Virginia Senate |
| District | SD-18 |
| Party | Democrat |
| Session Range | 2012–2026 (finance); 2025–2026 (vote alignment) |
| Report Generated | 2026 |
| Data Sources | Virginia SBE campaign finance filings; OpenStates roll-call records; VA Lobbyist Registry; VoteIQ donor_vote_alignment table |
| Coverage Notes | Campaign finance covers 14 recorded election cycles. Vote alignment covers 2025–2026 regular session only. CoI Score not calculated — see Section G3. |

---

### B. Voting Record Summary
> **[RAW DATA — LAYER 1]**  
> Source: OpenStates  
> Time range: 2025–2026 regular session

| Metric | Value |
|---|---|
| Total votes cast | 406 |
| YES votes | 400 |
| NO votes | [DATA GAP: not available in current dataset] |
| Not voting / Abstain | [DATA GAP: not available in current dataset] |
| Coverage statement | Covers all recorded roll-call votes in OpenStates for 2025–2026 regular session. |

---

### C. Recent Votes
> **[RAW DATA — LAYER 1]**  
> Source: OpenStates

[DATA GAP: Bill-level roll-call list not available in source dataset. Populate from OpenStates API query for SD-18, 2025–2026 session.]

---

### D. Committee Assignments
> **[RAW DATA — LAYER 1]**  
> Source: Virginia General Assembly official records

[DATA GAP: Committee assignments not available in source dataset. Populate from VA General Assembly official roster.]

---

### E. Sponsorships
> **[RAW DATA — LAYER 1]**  
> Source: OpenStates  
> **OpenStates limitation note:** Counts reflect OpenStates recorded fields only. Underreporting is possible.

| Metric | Value |
|---|---|
| Bills sponsored | [DATA GAP: not available in source dataset] |
| Bills co-sponsored | [DATA GAP: not available in source dataset] |
| Definition note | Co-sponsorship = listed as secondary sponsor in OpenStates record |

---

### F. Campaign Finance

#### F1a. Incoming Contributions
> **[RAW DATA — LAYER 1]**  
> Source: Virginia SBE campaign finance filings

| Metric | Value |
|---|---|
| Total raised (all cycles on record) | $9,100,000 |
| Cycle range | 2012–2025 (14 cycles) |
| Source | Virginia SBE campaign finance filings |

**Top donors (incoming contributions only):**

| Donor Name | Amount | Cycle |
|---|---:|---|
| Dominion Energy Inc. PAC | $1,600,000 | Multi-cycle |
| Urban One | $377,500 | Multi-cycle |
| Christopher Clemente | $200,000 | Multi-cycle |

#### F1b. Outgoing Expenditures / Transfers
> **[RAW DATA — LAYER 1]**  
> Source: Virginia SBE campaign finance filings

| Metric | Value |
|---|---|
| Lucas for Senate Campaign Fund — outgoing transfers | $579,393 |
| Transfer recipients | [DATA GAP: recipient breakdown not available in source dataset — figure represents committee disbursements, not incoming contributions] |

> **[CORRECTION APPLIED]** The $579,393 Lucas Campaign Fund figure was previously misclassified as an incoming donor in the source report. It has been reclassified as an outgoing committee disbursement/transfer. It must not appear in donor totals or sector breakdowns.

---

#### F2. Sector Breakdown
> **[DERIVED METRIC — LAYER 2]**  
> Source: Computed from SBE filings using VoteIQ donor taxonomy

**Multi-cycle (2012–2025):**

| Sector | Amount | % of Total |
|---|---:|---:|
| Tobacco | ~$1,910,000 | 21% |
| Utilities | ~$1,710,000 | 19% |
| Other / Unclassified | ~$5,480,000 | 60% |

**Current session (2025–2026):**

| Sector | Amount | Donor Count | % of Classified |
|---|---:|---:|---:|
| Legal | $30,200 | 42 | 27% |
| Finance | $24,050 | 72 | 21% |
| Nonprofit | $15,188 | 7 | 14% |
| Government | $13,540 | 45 | 12% |
| Manufacturing | $12,500 | 5 | 11% |

---

### G. Derived Analytics
> **[DERIVED METRIC — LAYER 2]**

#### G1. Donor-Vote Alignment Rate

| Sector | Sample Size (n=) | YES Rate | NO Rate | Confidence |
|---|---:|---:|---:|---|
| Alcohol / Gambling | 14 | 100% | 0% | **LOW** |
| All other bills | 392 | 99% | 1% | HIGH |
| Delta | — | +0.8 points | — | **LOW** |

> Note: Alignment analysis runs against Alcohol/Gambling sector based on prior-cycle donor concentration. Current-session top donor sector (Legal) has insufficient bill-vote coverage — see G3.

#### G2. Statistical Warning Block

> **[STATISTICAL WARNING — REQUIRED]**  
> The Alcohol/Gambling alignment rate is computed from **n=14 votes**. This is below the minimum threshold (n=30) for meaningful inference. The +0.8 point delta is within expected statistical noise. This figure does not constitute a meaningful pattern and must not be used as the basis for any inference about legislative behavior.

#### G3. CoI Score — Current Session

> **[NOT CALCULATED]**  
> Reason: Insufficient sector-specific vote linkage in the 2025–2026 session dataset. The top donor sector (Legal, 27% of classified contributions) lacks adequate bill-vote coverage in VoteIQ to compute a valid alignment rate.  
> Confidence: Low — data coverage limitation, not a finding.  
> Action required: Expand Legal sector bill-vote linkage in VoteIQ before CoI Score can be computed for this session.  
> Sen. Lucas does not appear on the current session top-10 CoI Score list. This reflects dataset coverage, not a determination about conflict of interest.

---

### H. Interpretive Signals
> **[INTERPRETIVE SIGNAL — LAYER 3 — LOW TRUST]**  
> **Non-causal observational patterns only. No causal inference is drawn. No intent or influence is implied.**

#### H1. Fundraising Volatility — 2023 and 2024

> The figures below represent **outgoing transfers** from the Lucas for Senate Campaign Fund — not incoming contributions to Sen. Lucas's campaign.

| Cycle | Outgoing Amount | vs. Typical Cycle | Observation |
|---|---:|---:|---|
| 2023 | $112,900 | 3.2x typical (+218%) | Elevated outgoing transfer cycle. No causal inference drawn. |
| 2024 | $1,491,000 | 7.7x typical (+6,383%) | Significant outlier in outgoing transfer activity. No causal inference drawn. |

**Temporal co-occurrence note:**  
Active Virginia General Assembly legislation in the Alcohol/Gambling category (HB2368, HB1598, HB1016, HB1102) was observed within the same time window as these elevated disbursement cycles. This is a temporal co-occurrence observation only. No policy linkage, donor influence, or legislative motivation is inferred or implied.

[CONTEXT GAP: Recipient breakdown for 2023 and 2024 outgoing transfers is not available in the current dataset.]

---

#### H2. Lobbyist Overlap

> **[CONTEXT — NORMALIZATION REQUIRED]**  
> Virginia lobbyist registry is principal-based and broad. Large corporations, utilities, and media organizations typically maintain high lobbyist counts across all sessions. No baseline comparison is available in the current dataset. Counts below are reported as-is and do not indicate abnormal engagement without a baseline.

| Donor / Principal | Registered VA Lobbyists | Note |
|---|---:|---|
| Dominion Energy Inc. PAC | 16 | Principal-based registry count; not bill-specific |
| Urban One | 18 | Principal-based registry count; not bill-specific |

[CONTEXT GAP: No baseline lobbyist count data available in current dataset to assess whether these figures are typical for organizations of this type and size.]

---

### I. Data Integrity Notes
> **[REQUIRED]**

**Missing fields:**
- Committee assignments (Section D): not in source dataset
- Bill-level roll-call list (Section C): not in source dataset
- Sponsorship / co-sponsorship counts (Section E): not in source dataset
- NO vote count and abstention count (Section B): not broken out in source dataset
- Outgoing transfer recipients 2023–2024 (Section H1): not in source dataset

**Dataset limitations:**
- Vote alignment computed for Alcohol/Gambling sector only; Legal sector (current top donor sector) lacks sufficient bill-vote linkage
- CoI Score cannot be calculated for 2025–2026 session due to Legal sector coverage gap
- SBE filings do not distinguish bill-specific lobbying; lobbyist counts are registry-wide

**Proxy data used:**
- Sector classification uses VoteIQ donor taxonomy (name-matching approximation), not SBE native classification

**Aggregation assumptions:**
- Multi-cycle tobacco and utilities totals are percentage-derived from $9.1M total; exact figures are approximations
- "Typical cycle" baseline for Section H1 derived from mean of non-outlier cycles; methodology not documented in source dataset

**Known API / source constraints:**
- OpenStates co-sponsorship underreporting likely; counts should be treated as minimums
- Virginia does not require bill-specific lobbying disclosure; registry is by principal, not by bill

**Corrections applied from prior report version:**
- Lucas Campaign Fund ($579,393) reclassified from "incoming donor" to "outgoing committee disbursement/transfer"
- 2023–2024 section reframed from "donor trend spike" to "fundraising volatility / outgoing transfer outlier"
- CoI Score null replaced with structured non-calculation explanation (Section G3)
- Lobbyist section updated with normalization context and baseline gap note
- Alignment section updated with sample size label and statistical warning block
- Causal language ("coincides with", "linked to", "suggests") replaced throughout with temporal co-occurrence notation

---
---

# PART 3 — SCHEMA ENFORCEMENT CHECKLIST

*Run before publishing any Legislative Record Brief. All items must pass.*

---

## Layer Integrity
- [ ] Every data point in Sections A–E and F1 is sourced from a named official dataset
- [ ] Every derived metric in F2 and G is labeled `[DERIVED METRIC — LAYER 2]`
- [ ] Every item in Section H is labeled `[INTERPRETIVE SIGNAL — LAYER 3 — LOW TRUST]`
- [ ] No Layer 1 facts appear only in Section H without a corresponding entry in Sections A–F
- [ ] No Layer 3 signals appear in Sections A–F

## Finance Data Integrity
- [ ] Incoming contributions and outgoing expenditures/transfers are in separate subsections (F1a / F1b)
- [ ] No outgoing transfer or disbursement is labeled as a donor, donation, or incoming contribution
- [ ] Every dollar figure has a named source and defined cycle range
- [ ] Any "Campaign Fund as donor" entry from source data has been reclassified as outgoing disbursement

## Causal Language
- [ ] Zero instances of: "suggests influence", "indicates", "driven by", "linked to", "influenced", "coincides with", "shows alignment with policy"
- [ ] All temporal co-occurrence language uses approved phrasing: "temporally overlaps", "co-occurs in dataset", "observed within same time window"
- [ ] No statement implies a donation caused, affected, or motivated a vote

## Statistical Validity
- [ ] Every alignment rate includes a stated sample size (n=)
- [ ] Every alignment rate with n < 30 has a Statistical Warning Block (G2)
- [ ] CoI Score null is replaced with a structured non-calculation block (G3) including reason and confidence level
- [ ] No alignment rate with n < 30 is described as a "pattern", "finding", or "meaningful result"

## Data Gaps
- [ ] Every unpopulated field contains a `[DATA GAP: description]` placeholder
- [ ] Every context gap contains a `[CONTEXT GAP: description]` placeholder
- [ ] No field is left blank without a placeholder

## Lobbyist Section
- [ ] Lobbyist counts include normalization context note
- [ ] If no baseline is available, `[CONTEXT GAP]` is present
- [ ] No lobbyist count is described as "high", "elevated", or "notable" without a stated baseline

## Data Integrity Notes (Section I)
- [ ] All missing fields are listed
- [ ] All proxy data is disclosed
- [ ] All aggregation assumptions are listed
- [ ] All corrections from prior versions are listed

## Final Review
- [ ] Report contains zero political narrative language
- [ ] Report contains zero intent or motivation attribution
- [ ] Report contains zero corruption or improper influence implication
- [ ] All sections present in order: A → B → C → D → E → F → G → H → I

---

*Schema version 1.0 — VoteIQ Civic Analytics Engine*  
*All reports generated under this schema are structured civic data outputs. They are not legal determinations, editorial opinions, or investigative findings.*
