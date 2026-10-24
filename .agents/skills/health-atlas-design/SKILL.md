---
name: health-atlas-design
description: Review or refine Health Atlas navigation, Streamlit views, charts, maps and responsive interactions while preserving public-health data meaning, region isolation, provenance and accessibility.
---

# Health Atlas design

Adapted from Careflow's design skill. Help public-health analysts and healthcare
service planners explore aggregate public sources without implying patient-level
knowledge, clinical advice, capacity or commercial demand. Preserve the warm ivory,
teal and copper identity. A design review supplies judgment, not a new framework.

## Read the project first

Read README.md, SECURITY.md, .streamlit/config.toml, app.py, relevant renderers and
tests. Inspect the current desktop/mobile render for visual claims. Identify the
primary task, active region, selected geography, source periods and intended next
action. Distinguish global state from view-specific controls and source loading.

## Scope the work

Default to refine a bounded flow. Use extend for matching new elements and redesign
only when requested. State a brief goal, mode, preserved behavior and likely changes.
For a review-only request, report prioritized observations and concrete fixes;
do not change runtime layout. Do not create extra reports or brand documents unless
requested. Routine design decisions need no additional approval checkpoint.

## Preserve these contracts

- Keep working shared URLs, region switching, county/tract selection, map picking,
  accessible selectors, saved-area files, downloads and recovery paths.
- Separate Oregon/Washington caches and capabilities. Never substitute another
  region, year or synthetic snapshot when a source is missing.
- Keep CDC adult crude prevalence/95% intervals separate from ACS five-year context/
  90% margins. Missing uncertainty is not zero. No summed/averaged prevalence,
  composite opportunity scores or inferred healthcare demand.
- Keep CMS listings, approximate Census address matches and HRSA designations
  distinct; no guessed facility joins, capacity or eligibility claims.
- Preserve source dates, units, geographic grain, provenance and boundary limits.
  Critical caveats accompany decisions; detailed methodology can be disclosed nearby.
- Saved summaries read snapshots only. Keep independent sources opt-in and retain
  previous snapshots on failed refresh. Never weaken request budgets or validation.
- No patient records, new browser credentials, external fonts/scripts or trackers.
  Preserve XSRF/CORS and safe exceptions. Design work does not change source meaning.

## Review and implement

1. Start with task hierarchy and navigation: county exploration, county comparison,
   service-area analysis, healthcare access, and sources/methods.
2. Prefer a shared shell with explicit selected-view rendering. Verify Streamlit
   execution semantics: visually hidden tabs/expanders can still execute code.
3. Scope controls to their view. Retain global region and applicable context; make
   transitions explicit and support direct links/back navigation without losing work.
4. Reduce repeated guidance, competing headings and unnecessary map repetition.
   Keep essential interpretation beside results, with detailed methods one action away.
5. Preserve semantic palette roles, readable typography, keyboard focus, labeled
   controls and accessible alternatives to hover/click-only map interactions.
6. Use the existing Python/Streamlit, Altair and pydeck stack. Extract renderers and
   state/link helpers where useful; do not migrate frameworks or alter ingestion.
7. Inspect desktop and narrow mobile renders, active/empty/error/unsupported-region
   states and complete task paths. Separate observed failures from design preferences.

Load relevant references only:

- [Page critique](references/page-critique.md)
- [Chart design](references/chart-design.md)
- [Verification](references/verification.md)

Follow verification before completion. Explain changes or findings, checks and
remaining limits without inventing user research or measured usability gains.

## Attribution

Adapted from careflow-design and selected Garden Skills/Designer Skills material.
See [LICENSES.md](LICENSES.md) for the pinned source and retained MIT notices.
Do not activate unrelated upstream skills automatically.
