"""Validation for county links and browser map selection events."""
from oregon_health.regions import OREGON, get_region

COUNTY_LAYER = "oregon-counties"


def county_names(region=OREGON):
    return get_region(region).counties()


def selected_county(event, region=OREGON):
    """Accept only a configured county FIPS from the active region layer."""
    region=get_region(region)
    if not isinstance(event, dict):
        return None
    selection = event.get("selection", {})
    if not isinstance(selection, dict):
        return None
    objects = selection.get("objects", {})
    if not isinstance(objects, dict):
        return None
    features = objects.get(region.layer, [])
    if not isinstance(features, list) or not features or not isinstance(features[0], dict):
        return None
    props = features[0].get("properties", features[0])
    if not isinstance(props, dict):
        return None
    fips = props.get("fips")
    return fips if isinstance(fips, str) and fips in county_names(region) else None
