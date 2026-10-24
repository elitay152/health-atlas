"""Local county boundaries joined to estimates by FIPS; no tile service needed."""
from copy import deepcopy
from pathlib import Path

import pydeck as pdk
from oregon_health.regions import OREGON, boundary_collection, get_region

BOUNDARIES = Path(__file__).resolve().parents[1] / "assets" / "oregon-counties.geojson"
LOW = (216, 237, 224)
HIGH = (16, 91, 83)
MISSING = [220, 222, 216, 255]


def boundaries(region=OREGON):
    return boundary_collection(get_region(region))


def county_features(frame, selected, *, region=OREGON):
    """Return a fresh feature collection; never mutate the cached geometry."""
    collection = deepcopy(boundaries(region))
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


def county_deck(frame, selected, *, region=OREGON):
    region=get_region(region)
    return pdk.Deck(
        layers=[pdk.Layer(
            "GeoJsonLayer", county_features(frame, selected, region=region), id=region.layer,
            pickable=True, filled=True, stroked=True,
            get_fill_color="properties.color", get_line_color="properties.outline",
            get_line_width="properties.line_width", line_width_units="'pixels'", line_width_min_pixels=1, line_width_max_pixels=3,
            auto_highlight=True, highlight_color=[184, 106, 64, 80],
        )],
        initial_view_state=pdk.ViewState(latitude=region.center[1], longitude=region.center[0], zoom=min(region.zoom, 4.5), min_zoom=4, max_zoom=10),
        map_provider=None, map_style=None,
        tooltip={"text": "{name} County\nPrevalence: {estimate}\n95% confidence interval: {interval}"},
    )


def hospital_deck(records, selected=None, *, region=OREGON):
    """Approximate address points over county boundaries; no prevalence encoding."""
    region = get_region(region)
    collection = deepcopy(boundaries(region))
    for feature in collection["features"]:
        props = feature["properties"]
        props.update(name=f"{props['name']} County", detail="Generalized county boundary")
    points = [{**r, "detail": f"Approximate address match · {r['matched_address']}",
               "color": [23, 107, 99, 255] if r["facility_id"] == selected else [184, 106, 64, 235]}
              for r in records]
    return pdk.Deck(
        layers=[
            pdk.Layer("GeoJsonLayer", collection, id="hospital-counties", pickable=False,
                      get_fill_color=[234, 237, 229, 160], get_line_color=[92, 120, 96, 170],
                      line_width_min_pixels=1),
            pdk.Layer("ScatterplotLayer", points, id="hospital-points", pickable=True,
                      get_position="[longitude, latitude]", get_fill_color="color",
                      get_line_color=[250, 249, 244], stroked=True, line_width_min_pixels=1,
                      get_radius=500, radius_min_pixels=6, radius_max_pixels=12),
        ],
        initial_view_state=pdk.ViewState(latitude=region.center[1], longitude=region.center[0], zoom=min(region.zoom, 4.5), min_zoom=4, max_zoom=13),
        map_provider=None, map_style=None,
        tooltip={"text": "{name}\n{detail}"},
    )


def selected_hospital(event, allowed_ids):
    """Only accept a current filtered facility ID from the expected point layer."""
    if not isinstance(event, dict):
        return None
    selection = event.get("selection")
    objects = selection.get("objects") if isinstance(selection, dict) else None
    points = objects.get("hospital-points") if isinstance(objects, dict) else None
    if not isinstance(points, list) or not points or not isinstance(points[0], dict):
        return None
    value = points[0].get("facility_id")
    return value if isinstance(value, str) and value in allowed_ids else None
