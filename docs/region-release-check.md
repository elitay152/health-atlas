# Oregon and Washington county release verification

This release enables Washington **county health and Census context** alongside Oregon. It does not establish nationwide coverage. Washington also supports a separate [CMS hospital directory](washington-hospitals.md), verified after this county release. Optional [hospital address matches](washington-hospital-locations.md) are now supported in both states. Tracts, HRSA shortage designations and whole-county service-area workflows remain Oregon-only. Their controls, source audits and shared-link settings are not used in Washington.

## Reproduce

From a clean checkout, with Python 3.12+ and uv:

```sh
uv sync --frozen
uv run pytest -q
# Optional live checks; configure CENSUS_API_KEY securely first.
uv run python scripts/verify_regions.py > /tmp/region-verification.json
uv run python scripts/benchmark_regions.py
uv run streamlit run app.py
```

CI uses small generated synthetic county/ACS records, mocked network responses and temporary databases. It needs no Census key, downloaded snapshots or private data. The live audit requests only official CDC/Census endpoints through the normal shared request budgets and does not replace app snapshots. Errors omit credential-bearing URLs. The benchmark's temporary snapshots are removed after measurement.

`verify_regions.py` checks the configured county IDs and names against official ACS `NAME`, state and county fields, validates ACS variable meanings and complete county catalogs, validates CDC crude prevalence/95% intervals and the 2023 estimate period, and checks CDC's source revision before/after download. The report retains source URLs, CDC title/revision, estimate year, ACS period, expected/available counts, missing county IDs and missing MOE IDs. Missing values stay unavailable, never zero. A source or schema failure returns a failing exit status; coverage gaps are reported explicitly rather than asserted to be zero.

## Recorded source evidence — 2026-10-23

| Check | Oregon | Washington |
| --- | --- | --- |
| County IDs/names: boundary catalog ↔ official ACS ↔ CDC | Exact match, 36 counties | Exact match, 39 counties |
| CDC PLACES county 2025 release, dataset `swc5-untb` | 324 estimates | 351 estimates |
| Nine curated conditions, estimate year 2023 | 36/36 each; all 95% intervals present | 39/39 each; all 95% intervals present |
| ACS 2019–2023 population, age 65+, poverty estimates | 36/36 each | 39/39 each |
| Population MOE unavailable | All 36 counties | All 39 counties |
| Age-65+ MOE unavailable | None | King `53033`, Kitsap `53035`, Pierce `53053` |
| Poverty MOE unavailable | None | None |

CDC title: **PLACES: Local Data for Better Health, County Data, 2025 release**; source revision `1764844506`. Official sources: [CDC county release](https://data.cdc.gov/d/swc5-untb), [ACS 2023 profile API](https://api.census.gov/data/2023/acs/acs5/profile), [CDC methodology](https://www.cdc.gov/places/methodology/index.html). Release year, estimate year and ACS five-year period remain distinct. A missing MOE does not mean zero uncertainty; the source-specific reason is not inferred by this audit.

The display boundary catalog is pinned and MIT-attributed, but its vintage is unverified. Matching IDs/names establish the county catalog join, not exact historic boundary alignment or survey precision. These generalized polygons are for display, not address eligibility, area calculation or precise spatial analysis. See [asset provenance](../assets/README.md).

## Release checks

- **Data and maps:** two-state fixtures validate configured county counts, identifiers and state-specific maps. A missing health row stays gray on the map and unavailable in comparisons/coverage exports; a missing ACS value or MOE stays unavailable. No percentage aggregation or demand score is added.
- **Views and links:** state selector, map selection and keyboard county selection use the active state's catalog. Switching states clears prior county/demo/tract/area state. Washington links carry `state=WA`; legacy Oregon links keep their format. Empty/invalid selections are handled, and unsupported Oregon-only parameters are warned about and omitted from Washington links.
- **Exports:** selected-county CSV includes state/FIPS, measure/year, 95% limits and source/import date. Full comparison CSV also retains population universe, units, ACS periods, 90% MOEs and explicit missing states. Geography IDs should be imported as text.
- **Outages:** saved Washington health estimates remain usable during CDC/Census failures. A first-load failure does not substitute Oregon or synthetic values. Source status uses the active state's expected county count; unsupported facility sources are not reported as zero.
- **Desktop/mobile:** Chromium checks at 1440×1000 and 390×844 cover Washington render, both CSV downloads, accessible county selection and legacy Oregon links; desktop map selection opens a Washington county. Check for browser errors, horizontal overflow and preserved state/period/uncertainty in downloads.
- **Required checks:** frozen dependency sync, full pytest suite and Bandit; GitHub push and PR checks must pass before the maintainer merges. Runtime, local process coordination and measured importer budgets remain documented in the README. Multiple independent writable replicas remain unsupported.

Screenshots: [Washington desktop](images/washington-desktop.png), [Washington mobile map](images/washington-mobile.png).
