"""Bounded comparison links containing view state, never credentials."""
from oregon_health.profile import county_names


def parse_counties(value):
    """An absent list uses app defaults; an empty list preserves no selection."""
    if value is None:
        return None, False
    if not isinstance(value, str) or len(value) > 215:
        return [], True
    if value == "":
        return [], False
    valid, invalid = [], False
    for code in value.split(","):
        if code not in county_names():
            invalid = True
        elif code not in valid:
            valid.append(code)
    return valid, invalid


def link_params(codes, year, measure, demo, census, profile=None):
    params = dict(compare=",".join(codes), year=str(year), measure=measure,
                  demo="1" if demo else "0", census="1" if census and not demo else "0")
    if profile:
        params["county"] = profile
    return params
