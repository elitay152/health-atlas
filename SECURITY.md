# Security notes

Health Atlas serves aggregate public CDC estimates. It does not accept patient records or user accounts. Saved service-area uploads accept only bounded, current Health Atlas county definitions; their geometry and metadata must exactly match the bundled definition. Uploaded files are not decoded as images or PDFs.

## Controls

- CDC HTTPS destination is fixed and dataset IDs are validated. Only the deployment maintainer can select the release through `CDC_PLACES_DATASET`.
- Web refreshes are limited to one attempt per five minutes for all visitors and datasets sharing a cache path, including failures. SQLite reserves the attempt before network access, across processes and restarts. Other visitors can continue reading cached data. The command-line importer uses the same limiter.
- Requests have connection/read timeouts and the importer rejects responses of 1,000 or more records. Download and validation errors preserve the existing database.
- SQL values are parameterized; cache paths are server-controlled.
- Detailed exceptions are logged on the server, not shown to visitors. CORS and XSRF protection remain enabled in `.streamlit/config.toml`.
- Local databases, `.env` files and Streamlit secrets are ignored by Git. Ignoring a file does not remove anything already committed.
- Bandit and a frozen production-dependency audit run on pushes, pull requests and a weekly schedule. Dependabot checks dependencies and Actions weekly. Workflow permissions are read-only.

## Known dependency findings

The October 24, 2026 review found 37 advisories against the previously locked Pillow 12.3.0, with fixes listed in Pillow 13.0.0. Streamlit 1.66.1 permits Pillow below 14, so the app now requires Streamlit 1.66.1 or later and locks Pillow 13.0.0. A fresh pip-audit scan of the frozen production dependency export reported no known vulnerabilities. CI repeats this audit so future advisories produce a failing check. The app exposes no uploaded image/PDF decoding; this reduces exposure but does not replace dependency updates.

Bandit 1.9.4 found no issues in application Python. detect-secrets 1.5.0 found no credentials in the tracked working tree; Git history and platform settings were not included. These checks are not a penetration test.

To repeat the audit after installing the frozen dependencies:

```sh
uvx pip-audit==2.10.1 --path .venv/lib/python3.12/site-packages
uvx bandit==1.9.4 -r app.py oregon_health
uvx --from detect-secrets==1.5.0 detect-secrets scan
```

The pip-audit site-packages path is for Python 3.12 on Linux. On read-only cloud home directories, set `UV_CACHE_DIR` and `UV_TOOL_DIR` to writable locations and pass a writable `--cache-dir` to pip-audit. Inspect the audit exit code; newly reported advisories must be reviewed and resolved.

## Before public deployment

Use managed HTTPS hosting, keep repository write access limited to collaborators, and require pull requests plus passing test/security checks. Enable GitHub secret scanning and push protection where available. Check those repository settings directly; this project cannot enable them through application code.

The local cooldown limits CDC fetches, not browser connections, Streamlit reruns or a distributed denial-of-service attack. Use hosting traffic limits where available. Multiple replicas with separate disks need a shared limiter; each currently has an independent quota. Keep XSRF protection enabled behind proxies and configure proxy origins correctly rather than disabling it. Store future credentials in deployment secrets, never in source or public controls. Add authentication and an authorization review before adding private data or writable user features.

For a suspected vulnerability, avoid posting credentials or exploit details in public issues; contact the repository maintainer privately or use GitHub private vulnerability reporting if enabled.

## Census integration

Census requests use a fixed HTTPS destination (`api.census.gov`), a bounded set of variable names and a validated year. Visitor links cannot change the destination or requested fields. Only counties in the selected region are accepted, and a refresh requires every expected county FIPS code (36 Oregon or 39 Washington). Census cache files are separate from CDC data and are published as complete snapshots so failed updates preserve prior records. Each release shares the existing five-minute reservation mechanism, including failed requests. This remains a per-storage limiter, not a substitute for hosting-level traffic limits.

Census data requests require `CENSUS_API_KEY`, read from server environment or Streamlit deployment secrets. Metadata has no key. The data API uses a query parameter, so request failures are converted to safe exceptions with the original request exception chain suppressed before logging. Redirects are disabled to avoid forwarding the key to another destination. Credentials are never included in stored provenance or shared county links. Configure hosting/proxy access-log redaction for query strings as well; application exception redaction does not control upstream logs.

Data-status metadata contains only bounded dataset/year identifiers, outcome enums and timestamps. It never stores exception strings or request URLs. Status inspection is read-only and does not initiate source requests. Status-write failures do not block normal data loading. Detailed missing-estimate exports contain only public county identifiers and measure availability.
