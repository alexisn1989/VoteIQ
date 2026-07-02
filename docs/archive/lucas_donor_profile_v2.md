# VoteIQ — Donor Profile
**Schema version:** 1.0
**Hardening pass applied:** All fixes A–F
**Source input:** lucas_donors_raw.md

---

## A. Metadata
> **[RAW DATA — LAYER 1]**

| Field | Value |
|---|---|
| Subject | L. Louise Lucas |
| Role | Virginia State Senator |
| Chamber | Virginia Senate |
| District | SD-18 |
| Party | Democrat |
| Data Sources | Virginia SBE campaign finance filings; VoteIQ donor taxonomy |
| Time Range | 2012–2025 (14 cycles) |
| Report Generated | 2026 |

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
| Total Raised (as reported) | $9,085,920 |
| Cycle range | 2012–2025 |
| Cycles on record | 14 |
| Source | Virginia SBE campaign finance filings |

> **[VERIFICATION REQUIRED]**
> The Lucas for Senate Campaign ($579,393) was classified as a donor entry in the
> source dataset and has been reclassified as a committee self-transfer (see Section D2).
> Confirm whether this $579,393 is included in the Total Raised figure of $9,085,920.
> If included, the incoming contribution total is overstated by $579,393 and the
> corrected figure would be $8,506,527. Do not alter either figure until source data
> confirms inclusion or exclusion.

---

## C. Industry Breakdown
> **[DERIVED METRIC — LAYER 2]**
> Source: Computed aggregation from donor-level VoteIQ taxonomy classification.
> These figures are computed, not sourced directly from SBE. Classification confidence
> is not guaranteed. Sector totals may shift if classification is revised.

| Rank | Sector | Estimated Total | Share of Classified (%) |
|---|---|---:|---:|
| 1 | Tobacco | $1,908,043 | 21.0% |
| 2 | Utilities | $1,726,324 | 19.0% |
| 3 | Ideological | $1,104,590 | 12.2% |
| 4 | Real Estate | $882,315 | 9.7% |
| 5 | Legal | $763,940 | 8.4% |
| 6 | Finance / Banking | $641,820 | 7.1% |
| 7 | Healthcare | $498,270 | 5.5% |
| 8 | Organized Labor | $394,610 | 4.3% |
| 9 | Entertainment / Media | $377,500 | 4.2% |
| 10 | Other / Unclassified | ~$788,508 | 8.7% |

> Percentages are share of classified donor dollars. Unclassified donors are excluded
> from percentage calculations.
>
> **Ideological category definition:** Includes non-party-aligned PACs and entities
> that do not map to an industry sector in the VoteIQ donor taxonomy.
> Self-transfers are excluded from this total and reported separately in Section D2.
>
> **[VERIFICATION REQUIRED — Ideological sector]**
> The Lucas for Senate Campaign ($579,393) was previously classified under Ideological
> in the source dataset. If that amount was included in the $1,104,590 Ideological
> total, the corrected Ideological sector figure would be $525,197 (5.8% of classified).
> Do not alter this figure until source data confirms inclusion or exclusion.

---

## D. Donor Entities
> **[RAW DATA — LAYER 1]**

### D1. Top Donors — Incoming Contributions Only

> Incoming contributions from external donors only. Self-transfers excluded — see D2.

| Rank | Donor Name | Category | Amount |
|---|---|---|---:|
| 1 | Dominion Energy Inc. PAC | Industry-tagged (Utilities) | $1,600,000 |
| 2 | Urban One | Industry-tagged (Entertainment / Media) | $377,500 |
| 3 | Christopher Clemente | Individual | $200,000 |
| 4 | Altria Client Services PAC | Industry-tagged (Tobacco) | $185,000 |
| 5 | Reynolds American Inc. PAC | Industry-tagged (Tobacco) | $172,000 |
| 6 | Virginia Trial Lawyers Assoc. PAC | PAC / Committee | $155,000 |
| 7 | Virginia Education Association PAC | PAC / Committee | $142,500 |
| 8 | IBEW Local 666 PAC | PAC / Committee | $118,000 |
| 9 | Senate Democratic Caucus | PAC / Committee | $95,000 |

**Ideological category definition (where applicable):** Includes non-party-aligned PACs and entities that do not map to an industry sector in the VoteIQ donor taxonomy. Self-transfers excluded and reported in D2.

### D2. Committee Transfers
> **[RAW DATA — LAYER 1]**
> The following entries represent internal committee transfers or self-transfers
> between campaign committees. They are not incoming contributions from external donors
> and are excluded from donor rankings and sector totals.

| Entity | Category | Amount | Note |
|---|---|---:|---|
| Lucas for Senate Campaign | Self-transfer | $579,393 | Inter-committee transfer per SBE filings |

> **[VERIFICATION REQUIRED]**
> Confirm whether the $579,393 self-transfer is included in the Total Raised
> figure ($9,085,920). If included, the Total Raised figure is overstated
> by this amount and must be corrected before publishing.
> Do not alter the figure until source data confirms inclusion or exclusion.

---

## E. Observational Notes
> **[INTERPRETIVE SIGNAL — LAYER 3 — LOW TRUST]**
> **Non-Causal Observational Notes (Low Confidence Layer)**
> This section contains observational patterns only. No causal inference is drawn.
> No intent, influence, motivation, or policy impact is implied.

- Dominion Energy Inc. PAC is the highest single recorded donor in dataset at $1,600,000 across multiple cycles. The recorded Utilities sector total ($1,726,324) is majority accounted for by this single entity within the VoteIQ dataset.
- The Tobacco sector is the top-ranked industry funding source by long-term percentage (21.0% of classified contributions across 14 cycles).
- Two of the top 9 incoming donors also appear in the Virginia lobbyist registry. These are separate, legally distinct channels of legislative engagement.

> **[CONTEXT — NORMALIZATION REQUIRED]**
> Virginia lobbyist registry is principal-based and broad. The following counts reflect all
> registered principals for each entity, not bill-specific activity.
> No baseline comparison is available in this dataset to assess whether these
> counts are typical, elevated, or low for organizations of this type and size.
> [CONTEXT GAP: no baseline lobbyist count available for comparison]

| Principal | Registered VA Lobbyists | Note |
|---|---:|---|
| Urban One | 18 | Principal-based registry count; not bill-specific |
| Dominion Energy Inc. | 16 | Principal-based registry count; not bill-specific |

---

## F. Data Limitations
> **[REQUIRED — ALL REPORTS]**

- **Classification uncertainty:** Industry sectors are assigned by VoteIQ donor taxonomy via donor name matching. This is not an official SBE categorization. Sector assignments are approximations subject to classification error.
- **Incomplete donor segmentation:** SBE data reflects reported contributions only. Late filings, amendments, and federal PAC activity filed through FEC (not SBE) may not be captured. Federal PAC contributions are not disaggregated from state totals in the current pipeline.
- **Sub-industry disaggregation:** Virginia SBE filings do not disaggregate donor records by sub-industry (e.g. individual tobacco companies within the Tobacco sector). Sector totals represent aggregations of all donors classified under that sector tag by VoteIQ taxonomy. Individual company breakdowns are not available in the current dataset.
- **Self-transfer verification:** Unconfirmed — see Section B and D2. The $579,393 Lucas for Senate Campaign entry has been reclassified as a self-transfer and removed from donor rankings. Whether it is included in the $9,085,920 Total Raised figure has not been confirmed from source data.
- **External verification:** Full donor-level detail available at [vpap.org/candidates/252/top-donors/](https://www.vpap.org/candidates/252/top-donors/) and Virginia SBE ([elections.virginia.gov](https://www.elections.virginia.gov/)). External databases may contain additional records not captured in the VoteIQ ingestion pipeline.
- **Corrections applied from source dataset:**
  - Lucas for Senate Campaign ($579,393): reclassified from "Ideological" donor entry → committee self-transfer. Removed from D1 donor ranking; placed in D2.
  - IBEW Local 666 PAC: category tag reclassified from "Organized Labor" (non-permitted tag) → "PAC / Committee".

---

## G. Global Safety Block

---

This report is descriptive only. It does not infer causation, intent, corruption,
influence, or policy outcomes from any financial or voting data.
