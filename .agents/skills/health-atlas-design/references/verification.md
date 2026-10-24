# Verification

Match verification to scope; preserve the existing branch/PR workflow.

| Scope | Checks |
| --- | --- |
| Skill/documentation only | Validate metadata, local links, project-specific instructions and retained licenses. No runtime test suite is needed. |
| Review only | Inspect current desktop/mobile renders and relevant code. Exercise task states that support findings; distinguish inference from observation. |
| Layout or interaction | Run relevant AppTest/link/map tests, inspect desktop/mobile screenshots, exercise keyboard controls and confirm no clipping or page errors. |
| Navigation/state architecture | Verify legacy and new shared URLs, region switching, back navigation, selection retention, demo isolation, missing/corrupt caches, independent sources and no fetches from inactive views. |
| Runtime implementation | Follow CONTRIBUTING.md: frozen sync, full pytest suite, Bandit and dependency audit. Include a screenshot for visible changes. |

Use existing tooling. Do not refresh large sources, introduce a new test framework
or require a new API key for design verification. Use saved snapshots read-only for
browser review and synthetic fixtures for deterministic tests. Never log secrets or
credential-bearing URLs. Keep routine screenshots/traces local; update curated
screenshots only for meaningful UI changes. Separate local checks, CI and deployed
verification. Do not merge the PR on the user's behalf.
