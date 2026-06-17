# VoteIQ — Donor Profile Schema Enforcement Checklist
**Version:** 1.0
**Applies to:** All reports generated from donor_profile_template.md
**Required:** Run this checklist before publishing any donor profile report. All pre-publish gate items must pass.

---

## 1. Allowed Phrases

Use only these approved forms when describing patterns, quantities, and comparisons.

### Finance / Quantity
- "highest single recorded donor in dataset"
- "top-ranked in dataset"
- "majority of recorded sector total in this dataset"
- "accounts for [X]% of the [SECTOR] sector total in this dataset"
- "X of the top N incoming donors"
- "[N] registered principals per VA lobbyist registry"
- "principal-based registry count; not bill-specific"
- "incoming contributions only"
- "outgoing expenditures / committee transfers are separate"
- "[AMOUNT] across [N] cycles"

### Temporal / Observational
- "temporally overlaps"
- "co-occurs in dataset"
- "observed within same time window"
- "elevated relative to prior cycles" (with stated baseline)
- "outlier cycle" (with stated baseline)

### Epistemic / Confidence
- "[VERIFICATION REQUIRED]"
- "[DATA GAP: description]"
- "[CONTEXT GAP: description]"
- "[NOT CALCULATED]"
- "data coverage limitation, not a finding"
- "approximation based on donor name matching"
- "subject to classification error"

### Layer Labels (required, verbatim)
- `[RAW DATA — LAYER 1]`
- `[DERIVED METRIC — LAYER 2]`
- `[INTERPRETIVE SIGNAL — LAYER 3 — LOW TRUST]`
- `[CONTEXT — NORMALIZATION REQUIRED]`

---

## 2. Forbidden Phrases

Every instance must be replaced or removed. No exceptions.

| Forbidden Phrase | Required Replacement |
|---|---|
| "by far the largest" | "highest single recorded donor in dataset" |
| "largest single donor" | "highest single recorded donor in dataset" |
| "largest donor" | "highest single recorded donor in dataset" |
| "nearly the entire" | "majority of recorded sector total in this dataset" |
| "almost all of the" | "majority of recorded sector total in this dataset" |
| "most active lobbying presence" | "[N] registered principals per VA lobbyist registry" |
| "dominant" | "top-ranked in dataset" |
| "major influence" | *(remove entirely — no influence language permitted)* |
| "significant" (without a cited statistic) | *(remove entirely or replace with observed quantity)* |
| "notable" (without a cited statistic) | *(remove entirely or replace with observed quantity)* |
| "outsized" | *(remove entirely or replace with observed quantity)* |
| "disproportionate" | *(remove entirely or replace with observed quantity)* |
| "heavy hitter" | *(remove entirely)* |
| "powerful donor" | *(remove entirely)* |
| "key funder" | *(remove entirely)* |
| "coincides with" | "temporally overlaps" |
| "linked to" | *(remove entirely — no causal language permitted)* |
| "driven by" | *(remove entirely)* |
| "suggests influence" | *(remove entirely)* |
| "indicates" (implying causation) | *(remove entirely)* |
| "shows alignment with policy" | *(remove entirely)* |
| "reflects priorities" | *(remove entirely)* |
| "tied to" | *(remove entirely)* |
| "follows the money" | *(remove entirely)* |
| "pay to play" | *(remove entirely)* |

---

## 3. Section Validation Rules

One required rule per section. All must hold before publishing.

| Section | Rule |
|---|---|
| **A. Metadata** | All required fields populated. No [FIELD_NAME] placeholders remain. Classification system note is present verbatim. |
| **B. Raw Finance Data** | Total Raised figure present. [VERIFICATION REQUIRED] block present on Total Raised. No sector commentary. No external links. |
| **C. Industry Breakdown** | Layer 2 label present. Table has Rank, Sector, Amount, Share columns. Percentages stated as "share of classified." Ideological category definition present verbatim. Self-transfers explicitly excluded from Ideological total. |
| **D1. Top Donors** | No self-transfers in this table. No commentary. No external links. No lobbyist counts. Every row uses exactly one permitted category tag (Industry-tagged / Individual / PAC / Committee / Self-transfer). |
| **D2. Committee Transfers** | Self-transfers only. [VERIFICATION REQUIRED] block present. No self-transfer amount appears in D1, B total, or C sector totals without a verification flag. |
| **E. Observational Notes** | Layer 3 label present with full non-causal disclaimer. Zero forbidden phrases. Lobbyist counts appear here only, with full [CONTEXT — NORMALIZATION REQUIRED] block. No source-system limitation notes. No VPAP links. |
| **F. Data Limitations** | Classification uncertainty present. Sub-industry disaggregation limitation present. Self-transfer verification status present. VPAP link and SBE link present here (and only here). Verbatim line "External databases may contain additional records not captured in the VoteIQ ingestion pipeline." present. |
| **G. Global Safety Block** | Present as the final element. Preceded by horizontal rule (---). Verbatim — not summarized, merged, or moved. |

---

## 4. Pre-Publish Gate

Binary pass/fail. **All 20 items must pass before publishing.**

### Layer Integrity
- [ ] Every section has the correct layer label (`[RAW DATA — LAYER 1]`, `[DERIVED METRIC — LAYER 2]`, `[INTERPRETIVE SIGNAL — LAYER 3 — LOW TRUST]`)
- [ ] No Layer 1 facts appear only in Section E without a corresponding entry in Sections B–D
- [ ] No Layer 3 interpretive signals appear in Sections A–D

### Finance Data Integrity
- [ ] Self-transfer ($579,393 / [TRANSFER_AMOUNT]) is in D2 only — not in D1 or included in B total without a verification flag
- [ ] Total Raised has a [VERIFICATION REQUIRED] block confirming whether self-transfer is included or excluded
- [ ] Ideological sector total has a verification note if self-transfer was previously classified there
- [ ] Every dollar figure in D1 has a named source and defined cycle range
- [ ] No outgoing transfer, expenditure, or committee disbursement is labeled as a donation or contribution

### Causal Language
- [ ] Zero instances of: "by far", "largest", "dominant", "most active", "nearly the entire", "major influence", "coincides with", "linked to", "driven by", "suggests", "indicates" (causal), "shows alignment with"
- [ ] All temporal observations use only approved phrasing: "temporally overlaps", "co-occurs in dataset", "observed within same time window"
- [ ] No statement implies a donation caused, motivated, influenced, or is linked to a vote or policy outcome

### Category Tags
- [ ] Every D1 donor row uses exactly one of four permitted tags: `Industry-tagged ([sector])`, `Individual`, `PAC / Committee`, `Self-transfer`
- [ ] No non-permitted tags appear (e.g. "Organized Labor", "Ideological", sector names used as standalone tags)

### Lobbyist Section
- [ ] Lobbyist counts appear in Section E only — not in D1, Key Notes, or any other section
- [ ] Every lobbyist count entry has a [CONTEXT — NORMALIZATION REQUIRED] block immediately above or below it
- [ ] [CONTEXT GAP] note is present if no baseline is available

### VPAP / External Links
- [ ] VPAP links appear in Section F only — zero instances in Sections A–E
- [ ] SBE link appears in Section F only

### Structure
- [ ] Section order is A → B → C → D (D1 then D2) → E → F → G with no additions or reordering
- [ ] Section G is present last, preceded by ---, verbatim text, not modified

---

## 5. Category Tag Reference Card

| Donor Type | Correct Tag |
|---|---|
| Industry PAC (e.g. Dominion Energy PAC, Altria PAC) | `Industry-tagged ([sector])` |
| Trade association PAC | `Industry-tagged ([sector])` |
| Named individual (natural person) | `Individual` |
| Political party committee | `PAC / Committee` |
| Non-party PAC (e.g. trial lawyers, educators) | `PAC / Committee` |
| Labor union PAC | `PAC / Committee` |
| Media company (e.g. Urban One) | `Industry-tagged (Entertainment / Media)` |
| Real estate developer (individual) | `Individual` |
| Inter-committee transfer / self-transfer | `Self-transfer` *(D2 only)* |

---

## 6. Ideological Sector Definition (verbatim — include in every C and D section)

> Ideological category includes: non-party-aligned PACs and entities that do not map
> to an industry sector in the VoteIQ donor taxonomy.
> Self-transfers are excluded from this total and reported separately in Section D2.

---

## 7. Required Context Blocks (verbatim — copy exactly)

### Lobbyist Normalization Block (required in Section E whenever lobbyist counts appear)
```
> [CONTEXT — NORMALIZATION REQUIRED]
> Virginia lobbyist registry is principal-based and broad. Count reflects all
> registered principals for this entity, not bill-specific activity.
> No baseline comparison is available in this dataset to assess whether this
> count is typical, elevated, or low for organizations of this type and size.
> [CONTEXT GAP: no baseline lobbyist count available for comparison]
```

### Self-Transfer D2 Block (required in D2)
```
> [RAW DATA — LAYER 1]
> The following entries represent internal committee transfers or self-transfers
> between campaign committees. They are not incoming contributions from external donors
> and are excluded from donor rankings and sector totals.
```

### Verification Block (required in D2 and B whenever self-transfer status is unconfirmed)
```
> [VERIFICATION REQUIRED]
> Confirm whether the [TRANSFER_AMOUNT] self-transfer is included in the Total Raised
> figure ([TOTAL_RAISED_USD]). If included, the Total Raised figure is overstated
> by this amount and must be corrected before publishing.
> Do not alter the figure until source data confirms inclusion or exclusion.
```

### Global Safety Block (required as final element, verbatim)
```
---

This report is descriptive only. It does not infer causation, intent, corruption,
influence, or policy outcomes from any financial or voting data.
```

---

*Schema version 1.0 — VoteIQ Civic Analytics Engine*
*All donor profile reports generated under this schema are structured civic data outputs. They are not legal determinations, editorial opinions, or investigative findings.*
