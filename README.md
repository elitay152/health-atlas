# Health Atlas

A shared Python and SQL project exploring county health in Oregon. The first version compares diabetes, current asthma, and obesity prevalence with CDC PLACES county estimates.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen
uv run streamlit run app.py --server.address 0.0.0.0
```

Run commands from the repository root. If your home directory is read-only, set `UV_CACHE_DIR=/workspace/.cache/uv` before using uv. The app requests Oregon estimates from the public CDC Socrata API on first use, stores them in DuckDB, and refreshes a cache older than 24 hours on the next app interaction. **Refresh data** requests a download, subject to a shared five-minute cooldown. No API key is needed. Failed requests retain previously downloaded data; an empty cache shows an error and a retry button. Synthetic data is available only through the explicit **Use synthetic demo data** toggle. No real CDC data ships in this repository.

## CDC API source

The app starts with dataset ID `swc5-untb`. Live API validation confirmed this is the 2025 county release and returned 108 crude-prevalence estimates: three measures for all 36 Oregon counties. The maintainer can set `CDC_PLACES_DATASET` before starting the app. Visitors can view the configured ID but cannot change the shared dataset. The importer checks the source title and expected fields before writing data; verify the returned release title and county coverage before interpreting results. A dataset ID selects a specific release rather than silently following an unspecified latest release.

Requests go from the Python server to `https://data.cdc.gov`, not from the browser. Hosting must permit outbound HTTPS to that domain. Anonymous requests are sufficient for this small Oregon query; no token or secret setup is required. Changing county/measure selections queries DuckDB without another API request. Refresh reservations are persisted in `data/.refresh.sqlite3` and shared across sessions and processes using the same data directory. Separate hosting replicas need a shared rate limiter; this local limit is not an edge traffic control. The importer rejects responses of 1,000 or more records, well above the 108 expected for this release. A failed automatic refresh is attempted only once per source per session; **Refresh data** retries after the shared cooldown. Failed attempts count toward the cooldown to prevent repeated requests during outages.

## Optional command-line import

1. Open the [CDC PLACES data portal](https://www.cdc.gov/places/) and follow its data links to a **county, GIS-friendly** release on `data.cdc.gov`.
2. Copy its dataset ID from the URL (the `abcd-1234` portion). Choose the GIS-friendly release with one measure per row, not the wide/open-data format.
3. Run:

```sh
uv run python -m oregon_health.import_data --dataset YOUR_DATASET_ID
```

The importer verifies the dataset title, requests only Oregon crude-prevalence records for the three measures, validates FIPS codes, duplicates and confidence intervals, and transactionally replaces `data/oregon.duckdb`. Network/validation failures preserve the existing database. Repeating an import replaces rather than appends estimates. The app uses the imported database when demo mode is off; select the same dataset ID to reuse this cache.

Required source fields: `stateabbr`, `locationid`, `locationname`, `measureid`, `year`, `data_value_type`, `data_value`, `low_confidence_limit`, `high_confidence_limit`. Missing estimates are omitted, not converted to zero. The interface displays available county counts; verify expected coverage (Oregon has 36 counties) when reviewing a release. The source title, URL and import timestamp are stored in `provenance`; estimate years are kept separately from release titles.

Live CDC access and the default dataset were verified in the cloud environment after publication. Hosting must allow `data.cdc.gov`. On a new deployment, click **Refresh data** and check coverage and source metadata; no manual file download is required.

## Data interpretation

PLACES provides modeled estimates, not patient records. This app uses crude prevalence, which is affected by county age composition. Comparisons do not imply causes or establish statistically significant differences. Confidence intervals accompany each estimate. Consult the selected release's definitions, population denominators and CDC methodology. This tool is for exploration, not medical advice.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for the issue and pull request workflow. Run `uv run pytest -q` before opening a pull request. CI uses the frozen lockfile and synthetic fixtures; tests do not require CDC access. Keep downloads, databases and personal credentials out of Git.

Project structure:

- `app.py`: Streamlit county selector, chart, uncertainty table and CSV export.
- `oregon_health/data.py`: data validation and parameterized DuckDB queries.
- `oregon_health/import_data.py`: CDC download and source metadata.
- `oregon_health/api_cache.py`: persistent API cache, expiry and refresh.
- `oregon_health/map.py`: FIPS-based geometry joins and interactive map styling.
- `oregon_health/profile.py`: validation for county links and map selection events.
- `tests/`: import, SQL and application interaction checks.

## First feature backlog

- **Data coverage report:** show missing counties/measures and explain suppressed records.
- **County context:** explain patterns using carefully sourced demographic context.
- **Age-adjusted comparisons:** import separately, expose an explicit estimate-type selector and document the difference.
- **Census integration:** join demographics by FIPS, with explicit source years and denominator checks.

Keep the first release focused on county comparisons. Add Census data once the live CDC import and coverage checks pass.

## Security and deployment

See [SECURITY.md](SECURITY.md) for security controls, known dependency findings and deployment recommendations. Bandit runs as the `bandit` GitHub Actions check; Dependabot checks Python and GitHub Actions updates weekly. Keep `test` required on main and add `bandit` after its first successful workflow run.

## County map and visual theme

The interactive Oregon map supports pan, zoom, and hover details with estimates and confidence intervals. Choose counties in the comparison control to outline them on the map and populate the chart/table. Light-to-dark teal represents lower-to-higher prevalence for the current measure/year; gray means no estimate, including counties outside the synthetic demo. The scale adjusts for each measure/year and is not suitable for comparing colors between measures.

Boundaries are bundled locally, so deployment needs no map API key, tile provider or additional GIS network access. See [boundary provenance and licensing](assets/README.md). The app joins geography and CDC estimates by five-digit FIPS codes. The warm ivory/teal/copper theme is configured in `.streamlit/config.toml`.

## County profiles and sharing

Click a county on the map to open **County spotlight**, or use the keyboard-accessible **County profile** selector below the comparison. A profile shows diabetes, current asthma and obesity estimates with 95% confidence intervals for the selected estimate year. Missing measures say **Unavailable**; data from a different year is not silently substituted. **Add county to comparison** adds the county to the existing comparison when it has a record for the current map measure.

Selecting a profile updates the browser URL. Copy it to share the county, year, current measure and explicit synthetic demo mode. For example, `?county=41051&year=2023&measure=DIABETES&demo=0` opens Multnomah County using real data. Links refer to the deployment's configured CDC release, not an immutable historical snapshot. Unknown county IDs are ignored with a warning, and unavailable year/measure options fall back to available data shown in the controls. Clearing the profile removes the county parameter.

## Census community context

Real-data county profiles also request U.S. Census Bureau **ACS five-year Data Profiles** for the release ending in the selected health-estimate year. For example, 2023 health estimates use the ACS **2019–2023** period. No substitution to a different year is made; this integration supports release years 2015–2024 and validates variable labels before importing a release.

Fields from `https://api.census.gov/data/{year}/acs/acs5/profile`:

| Display | Estimate | Margin of error | Population universe |
| --- | --- | --- | --- |
| Population | `DP05_0001E` | `DP05_0001M` | All residents |
| Residents age 65+ | `DP05_0024PE` | `DP05_0024PM` | Share of all residents |
| Below poverty level | `DP03_0128PE` | `DP03_0128PM` | Share of people for whom poverty status is determined |

Census API requests are restricted to Oregon (`state:41`, `county:*`), require the expected set of all 36 county FIPS codes, and preserve missing/suppressed sentinel values as unavailable. Census uses **90%** margins of error; the CDC intervals shown separately use **95%** confidence. Percent margins are shown in percentage points. These datasets describe different populations and periods; displaying them together does not establish relationships or causes.

The app fetches the statewide Census release only when a real-data county profile is opened. It caches a complete snapshot in `data/census_{year}.duckdb` for 30 days, then refreshes on access. Separate county profiles reuse that same release. Failed attempts share the existing five-minute cooldown per Census release, and failed refreshes retain old snapshots with an explicit warning. A failed first fetch leaves the health profile usable with an unavailable-context message. Synthetic mode does not fetch or display real demographics.

Hosting must allow outbound HTTPS to **api.census.gov** as well as **data.cdc.gov**. Live metadata access is verified, including estimate meanings and margin-of-error attributes. The data endpoint currently redirects anonymous requests to Census’s “Missing Key” page, so a Census API key is required. After a key was configured, the 2023 Census release was validated live for all 36 Oregon counties. Multnomah County returned population 803,863, age 65+ share 14.4%, and poverty share 12.5%. Unit and UI tests use synthetic fixtures so CI does not require credentials or network access.

### Configure the Census API key

Request a key from https://api.census.gov/data/key_signup.html. Store it as `CENSUS_API_KEY` in Streamlit Community Cloud app secrets (TOML) or in the server process environment. The app reads either location; environment configuration takes precedence. Do not put the value in Git, browser controls, URLs you share, or chat.

For this Codex cloud environment, a `CENSUS_API_KEY` secret requirement with destination `api.census.gov` is saved in environment settings. Enter the value securely there, save and publish to enable live validation in this runtime. Streamlit deployment secrets and Codex environment secrets are separate configurations. Anonymous CDC PLACES access is unaffected.

The key is only sent to the fixed Census HTTPS data endpoint, as its required `key` query parameter. Requests do not follow redirects, and request exceptions are replaced with safe errors so credential-bearing request URLs do not reach application logs or the browser. Missing-key errors do not consume a refresh reservation; cached Census data remains usable without a new request.

## Side-by-side county comparison

**Compare the whole picture** uses the counties selected above the map to display all three health measures plus population, age 65+ share and poverty in one table. Estimates are joined by county FIPS, not names, and restricted to the selected health-estimate year. The comparison preserves county selection order. Missing/suppressed values are **Unavailable**, never zero; Census values are **Not loaded** until explicitly requested.

Choose **Include Census demographics** to load community context. It reuses the statewide Census cache used by county profiles; no extra request is needed per county when that cache is fresh. Census loading is opt-in to keep the initial map fast. A missing key or failed request leaves the health comparison usable, and previously cached demographics carry a stale-data warning. Synthetic mode never loads real demographics.

Health cells show prevalence and **95% confidence intervals**. Census cells show **90% margins of error**, using `pp` for percentage points. The period column distinguishes the CDC estimate year from the ACS five-year period. Population, age and poverty do not all use the same denominator as adult health measures. These are descriptive comparisons; they do not establish causes or statistical significance.

**Download full comparison CSV** exports one row per county/measure, preserving numeric values and FIPS identifiers. Fields include estimates, interval bounds or margins of error, units, confidence levels, source periods, population universes, source references, import dates and availability status. The browser address preserves the full comparison state, including an optional open county profile.

## Share a comparison

Select counties in the desired order, choose a measure and estimate year, and optionally enable Census demographics. Copy the browser address to share that view. A fresh session restores the county order, measure, year, Census toggle and demo mode. An open county profile is included too. Empty county selections stay empty when reopened.

Example: `?compare=41051,41039,41017&year=2023&measure=OBESITY&demo=0&census=1` compares Multnomah, Lane and Deschutes with Census context. Values use the deployment's current CDC release and matching-year Census cache; links are not immutable snapshots. Census loading on a shared link still needs a configured server-side key or cached data; no credentials are included in links.

Comparison links are bounded to Oregon's 36 known FIPS codes, with duplicate codes removed. Unknown counties and counties without an estimate for the selected map measure/year are omitted with a notice. Unavailable years/measures fall back to available options with a notice. Demo mode always disables real Census loading. Older county-only links still work and use the normal initial three-county comparison. Changing between real/demo data resets the comparison to available defaults; the browser address then reflects the new selection.

Only recognized view parameters are retained in the canonical address; unrelated parameters are removed. County IDs and user-supplied link values never become SQL text, destination URLs, cache paths or credentials.
