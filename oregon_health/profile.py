"""Validation for county links and browser map selection events."""
from oregon_health.map import boundaries

COUNTY_LAYER = "oregon-counties"


def county_names():
    return dict(sorted(
        ((f["properties"]["fips"], f["properties"]["name"]) for f in boundaries()["features"]),
        key=lambda item: item[1],
    ))


def selected_county(event):
    """Accept only an Oregon FIPS from an expected GeoJSON layer event."""
    if not isinstance(event, dict):
        return None
    selection = event.get("selection", {})
    if not isinstance(selection, dict):
        return None
    objects = selection.get("objects", {})
    if not isinstance(objects, dict):
        return None
    features = objects.get(COUNTY_LAYER, [])
    if not isinstance(features, list) or not features or not isinstance(features[0], dict):
        return None
    props = features[0].get("properties", features[0])
    if not isinstance(props, dict):
        return None
    fips = props.get("fips")
    return fips if isinstance(fips, str) and fips in county_names() else None
