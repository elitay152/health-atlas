"""Local county boundaries joined to estimates by FIPS; no tile service needed."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

import pydeck as pdk

BOUNDARIES = Path(__file__).resolve().parents[1] / "assets" / "oregon-counties.geojson"
LOW = (216, 237, 224)
HIGH = (16, 91, 83)
MISSING = [220, 222, 216, 255]


@lru_cache(maxsize=1)
def boundaries():
    return json.loads(BOUNDARIES.read_text())


def county_features(frame, selected):
    """Return a fresh feature collection; never mutate the cached geometry."""
    collection = deepcopy(boundaries())
    estimates = {row.fips: row for row in frame.itertuples()}
    selected_fips = {row.fips for row in frame.itertuples() if row.county in selected}
    lower = float(frame.prevalence.min()) if not frame.empty else 0
    upper = float(frame.prevalence.max()) if not frame.empty else 0
    for feature in collection["features"]:
        props = feature["properties"]
        row = estimates.get(props["fips"])
        props["outline"] = [184, 106, 64, 255] if props["fips"] in selected_fips else [250, 249, 244, 255]
        props["line_width"] = 3 if props["fips"] in selected_fips else 1
        if row is None:
            props.update(color=MISSING, estimate="No estimate available", interval="Not available")
        else:
            ratio = (row.prevalence - lower) / (upper - lower) if upper > lower else 0.5
            color = [round(a + ratio * (b - a)) for a, b in zip(LOW, HIGH)] + [255]
            props.update(color=color, estimate=f"{row.prevalence:.1f}%", interval=f"{row.low:.1f}%–{row.high:.1f}%")
    return collection


def county_deck(frame, selected):
    return pdk.Deck(
        layers=[pdk.Layer(
            "GeoJsonLayer", county_features(frame, selected),
            pickable=True, filled=True, stroked=True,
            get_fill_color="properties.color", get_line_color="properties.outline",
            get_line_width="properties.line_width", line_width_units="'pixels'", line_width_min_pixels=1, line_width_max_pixels=3,
            auto_highlight=True, highlight_color=[184, 106, 64, 80],
        )],
        initial_view_state=pdk.ViewState(latitude=44.15, longitude=-120.55, zoom=4.8, min_zoom=4, max_zoom=10),
        map_provider=None, map_style=None,
        tooltip={"text": "{name} County\nPrevalence: {estimate}\n95% confidence interval: {interval}"},
    )
