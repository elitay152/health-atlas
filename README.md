# Health Atlas

A shared Python and SQL project exploring county health in Oregon. The first version compares diabetes, current asthma, and obesity prevalence with CDC PLACES county estimates.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen
uv run streamlit run app.py --server.address 0.0.0.0
```

Run commands from the repository root. If your home directory is read-only, set `UV_CACHE_DIR=/workspace/.cache/uv` before using uv. The app requests Oregon estimates from the public CDC Socrata API on first use, stores them in DuckDB, and refreshes a cache older than 24 hours on the next app interaction. **Refresh data** forces a download. No API key is needed. Failed requests retain previously downloaded data; an empty cache shows an error and a retry button. Synthetic data is available only through the explicit **Use synthetic demo data** toggle. No real CDC data ships in this repository.

## CDC API source

The app starts with dataset ID `swc5-untb`. Live API validation confirmed this is the 2025 county release and returned 108 crude-prevalence estimates: three measures for all 36 Oregon counties. You can change the ID under **CDC data source** in the sidebar, or set `CDC_PLACES_DATASET` before starting the app. The importer checks the source title and expected fields before writing data; verify the returned release title and county coverage before interpreting results. A dataset ID selects a specific release rather than silently following an unspecified latest release.

Requests go from the Python server to `https://data.cdc.gov`, not from the browser. Hosting must permit outbound HTTPS to that domain. Anonymous requests are sufficient for this small Oregon query; no token or secret setup is required. Changing county/measure selections queries DuckDB without another API request. A failed automatic refresh is attempted only once per source per session; **Refresh data** retries immediately.

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
- `tests/`: import, SQL and application interaction checks.

## First feature backlog

- **Data coverage report:** show missing counties/measures and explain suppressed records.
- **County profiles:** add a reusable SQL query and a single-county detail view.
- **Age-adjusted comparisons:** import separately, expose an explicit estimate-type selector and document the difference.
- **County map:** join boundaries using five-digit county FIPS and preserve attribution.
- **Census integration:** join demographics by FIPS, with explicit source years and denominator checks.

Keep the first release focused on county comparisons. Add Census data once the live CDC import and coverage checks pass.
