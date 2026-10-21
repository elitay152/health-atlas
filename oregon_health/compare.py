"""FIPS-based comparison rows with explicit units, periods and uncertainty."""
import math

import pandas as pd

from oregon_health.data import MEASURES
from oregon_health.profile import county_names

DEMOGRAPHICS = [
    ("Population", "population", "population_moe", "people", "All residents"),
    ("Residents age 65+", "age65_pct", "age65_moe", "percent", "All residents"),
    ("Below poverty level", "poverty_pct", "poverty_moe", "percent", "People for whom poverty status is determined"),
]


def comparison_rows(db, fips_codes, year, source, imported_at, contexts=None, demo=False):
    """Use the selected year only; missing health/Census values never become zero."""
    names = county_names()
    codes = list(dict.fromkeys(fips_codes))
    if any(code not in names for code in codes):
        raise ValueError("Unknown Oregon county in comparison")
    health = db.execute("""
        SELECT fips, measure, prevalence, low, high FROM estimates
        WHERE fips IN (SELECT unnest(?::VARCHAR[])) AND year = ?
    """, [codes, year]).fetchall()
    health = {(row[0], row[1]): row[2:] for row in health}
    rows = []
    for fips in codes:
        common = {"fips": fips, "county": names[fips]}
        for measure, label in MEASURES.items():
            value, low, high = health.get((fips, measure), (None, None, None))
            rows.append(dict(common, metric=label, estimate=value, lower=low, upper=high, margin_of_error=None,
                             unit="percent", confidence=95, period_start=year, period_end=year,
                             population_universe="Adults", source=source, imported_at=str(imported_at),
                             status="available" if value is not None else "unavailable"))
        context = (contexts or {}).get(fips)
        if context and context["year"] != year:
            raise ValueError("Census release year does not match the comparison year")
        for label, field, moe, unit, universe in DEMOGRAPHICS:
            value = context[field] if context else None
            status = "available" if value is not None else ("demo_not_available" if demo else "not_requested" if contexts is None else "unavailable")
            rows.append(dict(common, metric=label, estimate=value, lower=None, upper=None,
                             margin_of_error=context[moe] if context and value is not None else None,
                             unit=unit, confidence=90, period_start=year-4, period_end=year,
                             population_universe=universe, source=context["source"] if context else None,
                             imported_at=str(context["imported_at"]) if context else None, status=status))
    return pd.DataFrame(rows)


def display_table(rows):
    """Metrics as rows and counties as columns, retaining selected county order."""
    if rows.empty:
        return pd.DataFrame()
    result = []
    for metric in [*MEASURES.values(), *(item[0] for item in DEMOGRAPHICS)]:
        selected = rows[rows.metric == metric]
        first = selected.iloc[0]
        period = str(first.period_end) if first.confidence == 95 else f"{first.period_start}–{first.period_end}"
        row = {"Measure": metric, "Period · uncertainty": f"{period} · {'95% CI' if first.confidence == 95 else '90% MOE'}"}
        for value in selected.itertuples():
            if value.status != "available":
                text = "Not loaded" if value.status == "not_requested" else "Unavailable"
            else:
                text = f"{value.estimate:,.0f}" if value.unit == "people" else f"{value.estimate:.1f}%"
                if value.confidence == 95:
                    text += f" ({value.lower:.1f}–{value.upper:.1f}%)"
                elif value.margin_of_error is not None and math.isfinite(value.margin_of_error):
                    text += f" ±{value.margin_of_error:,.0f}" if value.unit == "people" else f" ±{value.margin_of_error:.1f} pp"
                else:
                    text += " (MOE unavailable)"
            row[value.county] = text
        result.append(row)
    return pd.DataFrame(result)
