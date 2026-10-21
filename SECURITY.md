# Security notes

Health Atlas serves aggregate public CDC estimates. It does not accept patient records, file uploads, or user accounts.

## Controls

- CDC HTTPS destination is fixed and dataset IDs are validated. Only the deployment maintainer can select the release through `CDC_PLACES_DATASET`.
- Web refreshes are limited to one attempt per five minutes for all visitors and datasets sharing a cache path, including failures. SQLite reserves the attempt before network access, across processes and restarts. Other visitors can continue reading cached data. The command-line importer is a maintainer operation and does not use this limiter.
- Requests have connection/read timeouts and the importer rejects responses of 1,000 or more records. Download and validation errors preserve the existing database.
- SQL values are parameterized; cache paths are server-controlled.
- Detailed exceptions are logged on the server, not shown to visitors. CORS and XSRF protection remain enabled in `.streamlit/config.toml`.
- Local databases, `.env` files and Streamlit secrets are ignored by Git. Ignoring a file does not remove anything already committed.
- Bandit runs on pushes, pull requests and a weekly schedule. Dependabot checks dependencies and Actions weekly. Workflow permissions are read-only.

## Known dependency findings

The October 21, 2026 scan audited 39 installed packages with pip-audit 2.10.1. It reported eight Pillow 12.3.0 advisories: CVE-2026-112895, CVE-2026-112400, CVE-2026-112397, CVE-2026-112896, CVE-2026-112897, CVE-2026-112395, CVE-2026-112404 and CVE-2026-112899. All list Pillow 13.0.0 as the fix. Streamlit 1.66.0 requires Pillow below 13, so a compatible fix is currently unavailable in that dependency chain. Do not override that constraint without compatibility validation. Review Streamlit updates and re-audit after upgrading. The app currently exposes no uploaded image/PDF processing, which reduces exposure but does not remove these findings.

Bandit 1.9.4 found no issues in application Python. detect-secrets 1.5.0 found no credentials in the tracked working tree; Git history and platform settings were not included. These checks are not a penetration test.

To repeat the audit after installing the frozen dependencies:

```sh
uvx pip-audit==2.10.1 --path .venv/lib/python3.12/site-packages
uvx bandit==1.9.4 -r app.py oregon_health
uvx --from detect-secrets==1.5.0 detect-secrets scan
```

The pip-audit site-packages path is for Python 3.12 on Linux. On read-only cloud home directories, set `UV_CACHE_DIR` and `UV_TOOL_DIR` to writable locations and pass a writable `--cache-dir` to pip-audit. Inspect the audit exit code; the known findings currently make it fail.

## Before public deployment

Use managed HTTPS hosting, keep repository write access limited to collaborators, and require pull requests plus passing test/security checks. Enable GitHub secret scanning and push protection where available. Check those repository settings directly; this project cannot enable them through application code.

The local cooldown limits CDC fetches, not browser connections, Streamlit reruns or a distributed denial-of-service attack. Use hosting traffic limits where available. Multiple replicas with separate disks need a shared limiter; each currently has an independent quota. Keep XSRF protection enabled behind proxies and configure proxy origins correctly rather than disabling it. Store future credentials in deployment secrets, never in source or public controls. Add authentication and an authorization review before adding private data or writable user features.

For a suspected vulnerability, avoid posting credentials or exploit details in public issues; contact the repository maintainer privately or use GitHub private vulnerability reporting if enabled.
