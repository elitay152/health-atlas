# Washington tract exploration

In **Explore counties**, choose Washington and a county in the sidebar, then enable
**Explore census tracts in this county**. The tract year and accessible tract
selector appear in the sidebar. County and tract years stay independent, with a
warning when they differ. Links carry state, county, tract, measure and tract year;
foreign-state/county IDs are rejected. Oregon legacy links remain compatible.

## Source and geometry verification — October 24, 2026

Official CDC PLACES dataset `cwsq-ngmh`, 2025 release, revision `1765560443`, returned
**15,930 rows / 1,770 Washington tracts** for the nine supported conditions. Every
supported estimate is from **2023**, with a point estimate and complete 95% interval
in this observed snapshot. Source response payloads totaled **8,287,935 bytes**.
These are verification baselines, not permanent row-count assumptions. Source
metadata notes BRFSS, Census 2020 populations and ACS inputs; prevalence is modeled
adult community data, not patient data or evidence of program effects.

The official 2023 Census cartographic boundary ZIP contains **1,772** tracts across
all **39** Washington counties. All 1,770 CDC tract IDs join. Two additional boundary
IDs have no CDC row: `53005012000` (Benton) and `53021980100` (Franklin). The reason
is unverified; neither zero nor suppression is inferred. King has **494/494** and
Snohomish **173/173** matching tracts.

See [asset provenance and reproduction](../assets/README.md#washington-census-tracts-2023).
The bundled asset is a NAD83-to-WGS84 transformation with six decimal places and no
additional simplification. Matching IDs do not certify unchanged boundaries or a
crosswalk. The selected 2023 display vintage is an app compatibility contract, not
an assertion that CDC source metadata certifies these generalized polygons.

Census permits display only at scales no larger than **1:500,000**. The map uses a
fixed overview at zoom ≤8, disables controller zoom and omits independent basemap
controls. This is conservative at both states' latitudes. Small tracts remain
reachable by keyboard selection/table. No geocoding, eligibility, area/perimeter,
precise spatial analysis or inferred demand uses these boundaries.

## Cache, validation and exports

Washington defaults to `data/regions/wa/tracts.duckdb`; Oregon retains
`data/tracts.duckdb`. State provenance is validated before read, refresh or replacement.
Legacy marker-less snapshots are Oregon only. Snapshots publish through the shared
atomic publication helper and per-path lock. Failed imports retain previous data;
missing first-load data never falls back to another state or synthetic values.

Imports keep bounded pagination, response bytes, source revision checks, retries,
shared host request budgets and the five-minute refresh reservation. Nine supported
conditions are filtered in the source query; unexpected units, IDs, duplicates or
intervals are rejected. No map API key or new runtime dependency is needed.

The accessible table and CSV include every county display tract, distinguishing
absent records, unavailable source estimates and incomplete uncertainty. CSV adds
state/FIPS, source release/fetch date, 95% confidence, adult/crude units, definition,
source footnotes/symbols and the boundary hash. Spreadsheet formula prefixes are
escaped. Tract percentages are never averaged to reconstruct county prevalence.

Tests cover Washington source filters, foreign state/county rejection, cache identity,
legacy Oregon compatibility, publication failure retention, official asset checks,
missingness, fixed map scale, map-event layer validation, sidebar/link persistence
and no network access for saved views. Desktop/mobile browser checks exercise
keyboard selection, exports and navigation.

[Desktop](images/washington-tracts-desktop.png) · [Mobile](images/washington-tracts-mobile.png)
