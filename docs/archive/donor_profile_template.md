# VoteIQ — Donor Profile Schema Template
**Version:** 1.0
**System:** VoteIQ Civic Analytics Engine
**Constraint:** Structured civic data reporting only. No causal inference. No intent attribution. No political narrative.
**Usage:** Copy this template for every donor profile report. Replace every [FIELD_NAME] placeholder with source data. Insert [DATA GAP: description] where data is unavailable. Do not estimate or infer missing values. Fixed schema elements (layer labels, context blocks, safety block) must not be modified or removed.

---

## A. Metadata
> **[RAW DATA — LAYER 1]**

| Field | Value |
|---|---|
| Subject | [SUBJECT_FULL_NAME] |
| Role | [ROLE_TITLE] |
| Chamber | [CHAMBER_NAME] |
| District | [DISTRICT_ID] |
| Party | [PARTY_NAME] |
| Data Sources | [DATA_SOURCE_1]; [DATA_SOURCE_2] |
| Time Range | [CYCLE_START_YEAR]–[CYCLE_END_YEAR] ([CYCLE_COUNT] cycles) |
| Report Generated | [REPORT_YEAR] |

> Industry classification uses VoteIQ donor taxonomy mapped from Virginia SBE employer
> fields. This is not an official SBE categorization. Sector assignments are
> approximations based on donor name matching and are subject to classification error.

---

## B. Raw Finance Data
> **[RAW DATA — LAYER 1]**
> Source: Virginia SBE campaign finance filings
> Incoming contributions only. Outgoing expenditures and committee transfers are reported separately in Section D2.

| Metric | Value |
|---|---|
| Total Raised (incoming contributions) | [TOTAL_RAISED_USD] |
| Cycle range | [CYCLE_START_YEAR]–[CYCLE_END_YEAR] |
| Cycles on record | [CYCLE_COUNT] |
| Source | Virginia SBE campaign finance filings |

> **[VERIFICATION REQUIRED]**
> Confirm whether the Total Raised figure ([TOTAL_RAISED_USD]) excludes all
> committee self-transfers and inter-committee transfers. If any self-transfer
> is included in this total, the figure is overstated and must be corrected
> before publishing. Do not alter the figure until source data confirms
> inclusion or exclusion.

---

## C. Industry Breakdown
> **[DERIVED METRIC — LAYER 2]**
> Source: Computed aggregation from donor-level VoteIQ taxonomy classification.
> These figures are computed, not sourced directly from SBE. Classification confidence
> is not guaranteed. Sector totals may shift if classification is revised.

| Rank | Sector | Estimated Total | Share of Classified (%) |
|---|---|---:|---:|
| [RANK] | [SECTOR_NAME] | [SECTOR_AMOUNT_USD] | [SECTOR_PCT] |
| [RANK] | [SECTOR_NAME] | [SECTOR_AMOUNT_USD] | [SECTOR_PCT] |
| [RANK] | [SECTOR_NAME] | [SECTOR_AMOUNT_USD] | [SECTOR_PCT] |
| [RANK] | [SECTOR_NAME] | [SECTOR_AMOUNT_USD] | [SECTOR_PCT] |
| [RANK] | [SECTOR_NAME] | [SECTOR_AMOUNT_USD] | [SECTOR_PCT] |

> Percentages are share of classified donor dollars. Unclassified donors are excluded
> from percentage calculations.
>
> **Ideological category definition:** Includes non-party-aligned PACs and entities
> that do not map to an industry sector in the VoteIQ donor taxonomy.
> Self-transfers are excluded from this total and reported separately in Section D2.

---

## D. Donor Entities
> **[RAW DATA — LAYER 1]**

### D1. Top Donors — Incoming Contributions Only

> No commentary in this section. No external links. No lobbyist counts.
> Self-transfers excluded — see D2.
> Every donor entry must use exactly one permitted category tag.

| Rank | Donor Name | Category | Amount |
|---|---|---|---:|
| [RANK] | [DONOR_NAME] | [DONOR_CATEGORY] | [DONOR_AMOUNT_USD] |
| [RANK] | [DONOR_NAME] | [DONOR_CATEGORY] | [DONOR_AMOUNT_USD] |
| [RANK] | [DONOR_NAME] | [DONOR_CATEGORY] | [DONOR_AMOUNT_USD] |
| [RANK] | [DONOR_NAME] | [DONOR_CATEGORY] | [DONOR_AMOUNT_USD] |
| [RANK] | [DONOR_NAME] | [DONOR_CATEGORY] | [DONOR_AMOUNT_USD] |

**Permitted category tags (use exactly one per row):**
- `Industry-tagged ([SECTOR_NAME])`
- `Individual`
- `PAC / Committee`
- `Self-transfer` *(D2 only)*

### D2. Committee Transfers
> **[RAW DATA — LAYER 1]**
> The following entries represent internal committee transfers or self-transfers
> between campaign committees. They are not incoming contributions from external donors
> and are excluded from donor rankings and sector totals.

| Entity | Category | Amount | Note |
|---|---|---:|---|
| [TRANSFER_ENTITY] | Self-transfer | [TRANSFER_AMOUNT_USD] | [TRANSFER_NOTE] |

> **[VERIFICATION REQUIRED]**
> Confirm whether the [TRANSFER_AMOUNT_USD] self-transfer is included in the
> Total Raised figure ([TOTAL_RAISED_USD]). If included, the Total Raised figure
> is overstated by this amount and must be corrected before publishing.
> Do not alter the figure until source data confirms inclusion or exclusion.

---

## E. Observational Notes
> **[INTERPRETIVE SIGNAL — LAYER 3 — LOW TRUST]**
> **Non-Causal Observational Notes (Low Confidence Layer)**
> This section contains observational patterns only. No causal inference is drawn.
> No intent, influence, motivation, or policy impact is implied.

- [OBSERVATIONAL_NOTE_1]
- [OBSERVATIONAL_NOTE_2]

*If lobbyist registry data is included, the following context block is required:*

> **[CONTEXT — NORMALIZATION REQUIRED]**
> Virginia lobbyist registry is principal-based and broad. Count reflects all
> registered principals for this entity, not bill-specific activity.
> No baseline comparison is available in this dataset to assess whether this
> count is typical, elevated, or low for organizations of this type and size.
> [CONTEXT GAP: no baseline lobbyist count available for comparison]

| Principal | Registered VA Lobbyists | Note |
|---|---:|---|
| [LOBBYIST_PRINCIPAL] | [LOBBYIST_COUNT] | Principal-based registry count; not bill-specific |

---

## F. Data Limitations
> **[REQUIRED — ALL REPORTS]**

- **Classification uncertainty:** Industry sectors are assigned by VoteIQ donor taxonomy via donor name matching. This is not an official SBE categorization. Sector assignments are approximations subject to classification error.
- **Incomplete donor segmentation:** [DATA GAP: describe any known gaps in donor coverage for this record]
- **Sub-industry disaggregation:** Virginia SBE filings do not disaggregate donor records by sub-industry (e.g. individual companies within a sector). Sector totals represent aggregations of all donors classified under that sector tag by VoteIQ taxonomy. Individual company breakdowns are not available in the current dataset.
- **Self-transfer verification:** [VERIFICATION_STATUS — confirmed excluded / unconfirmed — see Section D2]
- **External verification:** Full donor-level detail available at [VPAP_URL] and Virginia SBE ([SBE_URL]). External databases may contain additional records not captured in the VoteIQ ingestion pipeline.

---

## G. Global Safety Block

---

This report is descriptive only. It does not infer causation, intent, corruption,
influence, or policy outcomes from any financial or voting data.
