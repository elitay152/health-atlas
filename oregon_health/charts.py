"""Descriptive charts: no composite scores or inferred causal relationships."""
import altair as alt

from oregon_health.data import MEASURES

TEAL = "#176B63"
COPPER = "#B86A40"


def intervals(frame):
    """County point estimates with explicit 95% interval endpoints."""
    base = alt.Chart(frame).encode(y=alt.Y("county:N", sort=alt.SortField("prevalence", order="descending"), title=None),
        tooltip=[alt.Tooltip("county:N", title="County"), alt.Tooltip("prevalence:Q", title="Estimate (%)", format=".1f"),
                 alt.Tooltip("low:Q", title="95% CI lower (%)", format=".1f"), alt.Tooltip("high:Q", title="95% CI upper (%)", format=".1f")])
    rules = base.mark_rule(color=TEAL, strokeWidth=2).encode(x=alt.X("low:Q", title="Adult crude prevalence (%)", scale=alt.Scale(zero=True)), x2="high:Q")
    points = base.mark_point(filled=True, color=COPPER, size=90).encode(x="prevalence:Q")
    return (rules + points).properties(height=max(160, 28*len(frame)))


def distribution(frame):
    """Each county has equal weight; the median is not a state estimate."""
    bars = alt.Chart(frame).mark_bar(color="#7BA99C", cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("prevalence:Q", bin=alt.Bin(maxbins=10), title="County prevalence (%)"),
        y=alt.Y("count():Q", title="Counties", axis=alt.Axis(tickMinStep=1)),
        tooltip=[alt.Tooltip("prevalence:Q", bin=True, title="Prevalence range"), alt.Tooltip("count():Q", title="Counties")])
    median = alt.Chart(frame).transform_aggregate(median="median(prevalence)").mark_rule(color=COPPER, strokeDash=[5, 4], strokeWidth=2).encode(
        x="median:Q", tooltip=[alt.Tooltip("median:Q", title="County median (%)", format=".1f")])
    return (bars + median).properties(height=170)


def condition_profile(profile):
    frame = profile.copy()
    frame["condition"] = frame.measure.map(MEASURES)
    base = alt.Chart(frame).encode(y=alt.Y("condition:N", title=None, sort=list(MEASURES.values()), axis=alt.Axis(labelLimit=150, labelOverlap=False, labelFontSize=11)),
        tooltip=[alt.Tooltip("condition:N", title="Condition"), alt.Tooltip("prevalence:Q", title="Estimate (%)", format=".1f"),
                 alt.Tooltip("low:Q", title="95% CI lower (%)", format=".1f"), alt.Tooltip("high:Q", title="95% CI upper (%)", format=".1f")])
    lines = base.mark_rule(color=TEAL, strokeWidth=2).encode(x=alt.X("low:Q", title="Adult crude prevalence (%)", scale=alt.Scale(zero=True)), x2="high:Q")
    dots = base.mark_point(filled=True, color=COPPER, size=80).encode(x="prevalence:Q")
    return (lines+dots).properties(height=max(160, len(frame)*32))


def heatmap(rows):
    frame = rows[rows.confidence == 95].copy()
    counties = list(dict.fromkeys(frame.county))[:3]
    frame = frame[frame.county.isin(counties)]
    frame["label"] = frame.apply(lambda row: f"{row.estimate:.1f}%" if row.status == "available" else "—", axis=1)
    base = alt.Chart(frame).encode(x=alt.X("county:N", sort=counties, title=None, axis=alt.Axis(labelAngle=-35, labelOverlap=False, labelFontSize=11)),
        y=alt.Y("metric:N", sort=list(MEASURES.values()), title=None, axis=alt.Axis(labelLimit=150, labelOverlap=False, labelFontSize=11)),
        tooltip=[alt.Tooltip("county:N", title="County"), alt.Tooltip("metric:N", title="Condition"),
                 alt.Tooltip("estimate:Q", title="Estimate (%)", format=".1f"), alt.Tooltip("lower:Q", title="95% CI lower (%)", format=".1f"),
                 alt.Tooltip("upper:Q", title="95% CI upper (%)", format=".1f"), alt.Tooltip("status:N", title="Availability")])
    cells = base.mark_rect(stroke="#F7F5EF", strokeWidth=2).encode(color=alt.condition(
        "datum.status === 'available'", alt.Color("estimate:Q", title="Prevalence (%)", legend=alt.Legend(orient="bottom", direction="horizontal", gradientLength=180), scale=alt.Scale(domain=[0, 100], range=["#DFEDE3", "#105B53"])), alt.value("#DCDDDA")))
    labels = base.mark_text(fontSize=11 if len(counties) > 3 else 12).encode(text="label:N", color=alt.condition("datum.estimate >= 50", alt.value("white"), alt.value("#233B35")))
    return (cells+labels).properties(height=len(MEASURES)*36)
