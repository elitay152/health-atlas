# Chart design

Start with the reader's question, the actual data grain, and existing metric
contracts. Design changes must not change the statistical result.

## Encoding

- Use lines for time trends, bars for categorical comparison, and appropriate
  distribution views when actual distribution data exists. Prefer the simplest
  chart that answers the question; avoid decorative 3D or invented detail.
- Start quantitative bar axes at zero. For line charts, use a defensible scale
  and clearly disclose truncation or logarithmic transformations.
- Label axes and units, date ranges, and cohort/filter scope. Keep comparable
  charts on consistent scales or make the differences explicit.
- Keep gaps and unavailable values distinct from zeros. Do not invent smoothing,
  interpolation, precision, sample sizes, confidence, or causal explanations.
- Use direct labels or readable legends, with restrained gridlines and decoration.
  Annotation should explain a real feature, not manufacture a favorable story.

## Color and interaction

- Reuse stable category colors. Use sequential palettes for ordered magnitude,
  diverging palettes for meaningful signed differences, and distinct hues for categories.
- Keep prevalence, uncertainty, facility supply and administrative designation encodings distinct across views.
  Add line styles, labels, or shapes so color is never the only distinction.
- Explain what each control filters and update its associated labels and summaries.
  Preserve useful selections; use the existing navigation/state conventions.
- Provide exact accessible values through a table or equivalent text alternative.
  Hover alone is insufficient; make details reachable by keyboard and touch.

## Mobile and uncertainty

- Inspect labels at their actual rendered size, especially scaled SVG text. Prefer
  fewer ticks, compact units, or an accessible table to illegibly small type.
- Keep controls comfortably touchable; aim for 44px targets where practical.
  A scrollable table must have a clear accessible region and keyboard access.
- Keep CDC adult prevalence with 95% intervals distinct from ACS all-resident five-year estimates with 90% margins. Never sum or average prevalence or infer demand from rates.
- Keep source release, estimate period, geographic grain and boundary vintage visible where needed.
- Presentation work must not change ingestion, denominators, source joins or analytical calculations.
