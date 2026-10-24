# Washington saved-source area comparisons

In the sidebar, select **Washington → Service areas**. Choose Area A counties and
an estimate year, enable **Compare with a second service area**, then choose Area B
counties and its independent year. Both states support comparisons; Oregon's
existing links, default API arguments and download filenames remain compatible.

## Contracts

- Both membership definitions and all saved source readers use the active region.
  Saved Area B uploads accept only the current state's exact Health Atlas county
  definition and boundary revision. Rejected files retain the existing Area B.
- Comparison reads existing snapshots only, with no API requests or cache writes.
  Missing, corrupt and foreign snapshots remain unavailable, never zero or Oregon
  substitutes. Source periods, provenance, uncertainty and failure-retention status
  are exported alongside values.
- County health charts share a 0–100% scale. Health rates stay county-specific;
  no average, combined prevalence, difference, significance test or opportunity
  score is calculated. Complete compatible ACS populations can be summed within
  an area; a combined population MOE remains unavailable.
- Shared counties, CMS IDs, accepted address-point IDs and discipline/HPSA IDs are
  explicit. Missing-source overlap is “Not evaluated”; a loaded empty intersection
  is zero. HPSA IDs can span counties even when county sets are disjoint. Areas
  sharing counties or IDs must not be added as independent totals.

Links carry `state=WA`, both county sets, their pinned boundary revisions and both
years. Empty selections are shareable unfinished comparisons. Changing state clears
both areas; task navigation and browser Back/Forward preserve valid selections.
Washington CSV/JSON filenames include `washington`, and both exports explicitly
identify state/FIPS. CSV strings are escaped for spreadsheet formula safety. Shared
links use current saved sources, not immutable historical snapshots.

## Verification — October 24, 2026

Read-only checks of saved official-source snapshots compared **Area A: King +
Snohomish** with **Area B: King + Pierce**, both using 2023 county estimates:

| Saved-source value | Area A | Area B |
| --- | ---: | ---: |
| ACS 2019–2023 population | 3,097,361 | 3,186,819 |
| CMS-listed hospital IDs | 25 | 28 |
| Accepted approximate address points | 20 | 20 |
| County/condition rows | 18 | 18 |

Overlap: King County (`53033`), **20 CMS IDs**, **15 accepted point IDs** and
**79 discipline/HPSA ID pairs**. Source dates differ: CDC 2025 county release
(estimates 2023), ACS 2019–2023, CMS release 2026-08-13, Public_AR_Current address
benchmark ID 4 and HRSA warehouse date 2026-10-23. These counts describe saved
public sources; they are not current capacity or demand estimates.

Synthetic tests cover same/disjoint/overlapping counties, cross-county HPSA IDs,
different years, foreign definitions/reports/snapshots, missing overlap, complete
versus partial population, common chart scales, safe CSV/JSON, malformed links,
empty selections, task persistence and state resets. Desktop/mobile browser checks
verify sidebar navigation and filters, both downloads, valid Washington and rejected
Oregon Area B uploads, independent membership and mismatched-period warnings.

[Desktop comparison](images/washington-area-comparison-desktop.png) · [Mobile comparison](images/washington-area-comparison-mobile.png)
