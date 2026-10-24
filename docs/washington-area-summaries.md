# Washington saved-source service-area summaries

Choose **Service areas**, select Washington in the state selector, choose whole
counties and an estimate year, then enable **Show service-area summary**. This
reads the saved Washington CDC, ACS, CMS, Census address-match and HRSA snapshots;
it never refreshes sources, creates caches or substitutes Oregon data. Use
**Explore counties** to load Census context and **Healthcare access** to load
facility/designation sources, then return to update the summary.

## Identity and calculation contracts

The default source directory is `data/regions/wa/`, with `counties.duckdb` for CDC
and the existing separate Census/hospital/address-match/HRSA files. An explicit
`cache_dir` means the selected region's source directory, not a shared parent.
`health_path` can override only the health snapshot; all snapshots must pass region
identity and source checks. Legacy snapshots without state markers remain Oregon
only. Foreign or corrupt files remain untouched and are reported as incompatible;
other sources stay usable.

Population is summed once per selected county only when every county has a usable
estimate from the requested compatible ACS release. Single-county source MOEs are
retained; multi-county MOEs remain unavailable because covariance is not supplied.
Health prevalence and age/poverty percentages stay county-specific, with source
periods and uncertainty. No area-wide prevalence or healthcare demand is inferred.

CMS counts deduplicate source facility IDs. Approximate point counts additionally
require unchanged, complete source addresses, an Exact match, finite active-state
coordinates and the current source-county polygon check. HRSA discipline/category/
status groups remain separate; HPSA IDs deduplicate across selected counties within
each group. Unresolved counties stay in statewide coverage metadata and are excluded
from selected-area counts. Source record counts do not measure capacity, eligibility
or utilization. Proposed withdrawal remains distinct; the GIS source is not complete
withdrawn history.

JSON exports include Washington state/FIPS at report and county-row level, each
source's region/coverage/provenance, the pinned county definition, values and
unsupported aggregations. The Washington filename is
`health-atlas-washington-area-summary.json`; Oregon's filename and default call
signature stay compatible. Washington also supports [saved-source area comparisons](washington-area-comparisons.md).

## Verification — October 24, 2026

Read-only inspection of existing official-source snapshots for King (`53033`) and
Snohomish (`53061`), without source requests:

- ACS 2019–2023 population: **3,097,361**, complete two-county coverage; combined MOE unavailable.
- CDC 2025 release, estimate year 2023: **18 county/condition rows** (nine conditions each), with 95% intervals.
- CMS Hospital General Information, released 2026-08-13 and modified 2026-07-22: **25 listed IDs** in the area.
- Census Geocoder Public_AR_Current benchmark ID 4: **20 accepted approximate points**, five listed IDs without accepted points.
- HRSA warehouse date 2026-10-23: **14 discipline/category/status groups**, keeping component counts and distinct HPSA IDs separate.

Statewide snapshots contained 351 CDC rows, 39 ACS counties, 100 CMS records, 100
address-match records and 3,536 HRSA component/point records. HRSA had one unresolved
county statewide; CMS had none. These saved-source counts are not current-live
inventory guarantees, and source dates differ.

Synthetic tests verify foreign and legacy state rejection for every source,
incomplete/duplicate/negative/wrong-period populations, missing uncertainty,
changed/incomplete addresses, county mismatches, corrupt sources, retained-failure
status, no network/filesystem writes and no Oregon fallback. UI checks cover opt-in
loading, membership links, navigation retention, empty areas and opt-in Washington comparison controls. Desktop/mobile browser evidence is included below.

[Desktop](images/washington-area-summary-desktop.png) · [Mobile](images/washington-area-summary-mobile.png)
