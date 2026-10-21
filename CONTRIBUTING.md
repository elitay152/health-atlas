# Building together

Use the existing checkout in cloud tasks; each task already has an isolated environment. Do not create an extra Git worktree unless requested.

1. Pick a focused issue describing a user need, acceptance criteria and data sources.
2. Create a feature branch, for example `feature/county-profile`.
3. Own a complete slice: Python ingestion/validation or SQL query, interface where relevant, tests, and documentation.
4. Run `uv sync --frozen` and `uv run pytest -q` from the repository root.
5. Open a pull request describing the behavior, relevant SQL/data decisions, checks run, and a screenshot if the interface changed.
6. Ask another contributor to review; the repository maintainer merges after checks pass.

Use synthetic records for tests and label demo values clearly. Never commit patient-level information, credentials or downloaded databases. Parameterize SQL values. Preserve geography identifiers as strings with leading zeros. Show data sources, estimate years and uncertainty in new displays.

Contributors can link their merged pull requests in portfolios and explain their design choices, review feedback, and validation. Agree on issue ownership before starting overlapping changes.
