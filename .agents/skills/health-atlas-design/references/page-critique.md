# Page critique

Use for a visual or UX review, or a short self-check during implementation. Inspect
actual content and interactive states. Code alone cannot establish visual quality.
For a review-only request, report findings without making unrequested changes.

## Review dimensions

| Dimension | Questions |
| --- | --- |
| Entry point | Can a visitor identify the page's purpose and most useful next action quickly? Are the active region and analytical scope clear? |
| Hierarchy | Does size, position, contrast, and whitespace emphasize the important finding? Are secondary details competing with it? |
| Composition | Are alignment, grouping, spacing, and line lengths coherent? Does the layout work with the actual content? |
| Typography | Is text readable at its rendered size? Are labels, numbers, units, and headings consistent? |
| Color | Do colors have stable meanings? Are text and controls sufficiently contrasted? Are category distinctions accessible? |
| Affordance | Do links and controls look actionable? Are focus, selected states, labels, and recovery paths clear? |
| Density | Can someone scan the findings without reading every caveat? Are detailed methods deferred without concealing important limitations? |
| Responsive behavior | Are charts, tables, navigation, and controls usable on mobile and with a keyboard? |

## Turn observations into fixes

For each material issue, give the location, observed behavior, user impact, and a
specific fix. Distinguish verified failures from subjective design preferences.
Prioritize access or functional failures, then clarity and consistency, then polish.
Do not invent test participants, conversion rates, heatmaps, or research evidence.

Keep the review concise: preserve the strongest elements and identify the highest
value fixes. Do not force arbitrary aesthetic scores or a fixed number of problems.
A successful review may conclude that a section needs no change.

## Health Atlas considerations

- Counties: distinguish map comparison selection from the spotlight county.
- Local exploration: preserve county/tract relationships and boundary scale limits.
- Service areas: explain whole-county membership, saved-source summaries and overlap.
- Healthcare access: keep CMS hospitals, approximate Census matches and HRSA designations distinct.
- Sources: preserve source periods, freshness, coverage, missingness and recovery.
- Navigation: separate tasks, keep global context visible, and preserve share links and selections.
- Architecture: identify which views execute on reruns; hidden content may still fetch data in Streamlit. Prefer explicit selected-view dispatch over eager rendering of every tab.

These prompts do not authorize a site-wide rewrite during a review.
