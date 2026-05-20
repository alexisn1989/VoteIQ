# VoteIQ Data Integrity Notes

## Virginia Legislative Finance Analysis

### Known Limitations

1. **Name matching is fuzzy.** Cross-referencing OpenStates voter names to `va_cf_schedule_a` candidate names uses last-name `LIKE` matching. "Cole" matches Nicole Cole but could theoretically match other candidates; "Thomas" matches Joshua E. Thomas but also Thomas A. Garrett, Jr. and Thomas C. Wright, Jr. in the votes table. All matches were manually spot-checked for district consistency but are not guaranteed collision-free.

2. **No canonical legislator-to-finance ID linkage.** There is no shared key between the OpenStates vote records and the Virginia SBE finance records. All analysis relies on name string matching. Official VPAP or SBE reconciliation would be required for production-grade accuracy.

3. **Defection count is very small.** Five true defectors across three bills in one session is a thin dataset. Any pattern-level conclusions (donor sectors, timing, shared networks) should be treated as hypothesis-generating, not statistically significant.

4. **Most apparent "defections" were procedural, not ideological.** The bulk of cross-party votes in the 2026 data (including ~20 apparent Democratic defections on SB2) were Senate concurrence votes on "Concur House Substitute" motions — unanimous or near-unanimous procedural votes, not genuine breaks from party position. Only `H VOTE:` motions with confirmed `pass` results are used in defection analysis.

5. **Finance data coverage is strong for 2021–2025, thinner for earlier cycles.** The bulk download from SBE added 213,942 report headers back to 2012, improving match rates. However, Schedule A (individual contributions) coverage for cycles before 2021 is less complete — older defection history or donor relationships predating 2021 may not be fully captured.

6. **FEC / federal donor data is very sparse.** `fec_individual_contributions` contains only ~931 rows. Federal-level donor cross-referencing (PAC coordination, federal vs. state donor overlap) is not feasible without a full FEC bulk pull.

7. **Timing gap caveat.** All identified 2025-cycle shared-donor donations to the 2026 defectors predate the contested floor votes by at least 3 months, consistent with standard pre-election campaign fundraising ahead of the November 4, 2025 General election. The public record cannot establish whether donors anticipated specific votes or whether legislators changed their positions after receiving funds. No causal inference is made.

8. **Scope is 2026 session only.** This analysis covers one legislative session. Longitudinal patterns (multi-cycle defection trends, donor relationship persistence) would require the same methodology applied across 2019–2026.

---

## Vote Classification Rules

- **True floor votes:** Only `H VOTE:` motions with `result = pass` are counted as genuine passage votes for defection analysis.
- **Senate concurrence motions:** `Concur House Substitute R` and similar motion types are procedural — senators voting Nay are rejecting an amendment process, not the bill itself. These are excluded from defection counts.
- **Senate vs. House yes rates:** Senate yes rates are structurally lower than House yes rates due to the higher volume of procedural, cloture, and motion votes where minority-party senators routinely vote Nay. Never compare a senator's yes rate to a representative's without flagging this difference.

---

## Finance Data Sources

| Table | Source | Coverage | Rows (approx.) |
|---|---|---|---|
| `va_cf_schedule_a` | Virginia SBE Schedule A CSVs | 2012–2026 | 2.2M |
| `va_cf_reports` | Virginia SBE Report CSVs (bulk download) | 2012–2026 | 213,942 |
| `fec_individual_contributions` | FEC API | Partial | ~931 |
| `congress_bill_details` | Congress.gov API | Partial | ~10 |

---

*Correlation does not imply causation. No motive or intent is inferred. All analysis relies entirely on public records from Virginia SBE, OpenStates, and the Virginia House of Delegates.*

Last updated: May 2026
