"""Bounded comparison links containing view state, never credentials."""
import re

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


def parse_tract(value, county):
    if value is None:
        return None, False
    if (not isinstance(value, str) or not re.fullmatch(r'41[0-9]{9}', value)
            or county not in county_names() or value[:5] != county):
        return None, True
    return value, False


def link_params(codes, year, measure, demo, census, profile=None, *, local=False, tract=None, tract_year=None):
    params = dict(compare=",".join(codes), year=str(year), measure=measure,
                  demo="1" if demo else "0", census="1" if census and not demo else "0")
    if profile:
        params["county"] = profile
    if local and not demo and profile in county_names():
        params['local'] = '1'
        selected, invalid = parse_tract(tract, profile)
        if selected and not invalid:
            params['tract'] = selected
        if isinstance(tract_year, int) and 2000 <= tract_year <= 2100:
            params['tract_year'] = str(tract_year)
    return params
