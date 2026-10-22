"""Bundled 2023 Census tracts for small-scale display, never address matching."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re

from oregon_health.profile import county_names

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
BOUNDARIES = ASSETS / 'oregon-tracts-2023.geojson'
MANIFEST = ASSETS / 'oregon-tracts-2023.provenance.json'
BOUNDARY_YEAR = 2023


def _geoid(value, county=None):
    if (not isinstance(value, str) or not re.fullmatch(r'41[0-9]{9}', value)
            or value[:5] not in county_names() or (county is not None and value[:5] != county)):
        raise ValueError('Invalid Oregon tract GEOID/county prefix')
    return value


def validate(collection):
    """Validate IDs and Polygon/MultiPolygon rings without substituting geometry."""
    if not isinstance(collection, dict) or collection.get('type') != 'FeatureCollection':
        raise ValueError('Expected a tract FeatureCollection')
    features = collection.get('features')
    if not isinstance(features, list) or not features or len(features) > 5000:
        raise ValueError('Invalid tract feature count')
    seen = set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get('type') != 'Feature':
            raise ValueError('Invalid tract feature')
        props = feature.get('properties')
        if not isinstance(props, dict) or props.get('county_fips') not in county_names():
            raise ValueError('Invalid tract county')
        geoid = _geoid(props.get('geoid'), props['county_fips'])
        if feature.get('id') != geoid or geoid in seen:
            raise ValueError('Duplicate or inconsistent tract feature ID')
        seen.add(geoid)
        if not isinstance(props.get('name'), str) or not props['name'].strip():
            raise ValueError('Missing tract name')
        geometry = feature.get('geometry')
        if not isinstance(geometry, dict) or geometry.get('type') not in {'Polygon', 'MultiPolygon'}:
            raise ValueError('Expected tract polygon geometry')
        coords = geometry.get('coordinates')
        polygons = [coords] if geometry['type'] == 'Polygon' else coords
        if not isinstance(polygons, list) or not polygons:
            raise ValueError('Empty tract polygon geometry')
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError('Empty tract polygon')
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError('Tract rings must be closed')
                for point in ring:
                    if (not isinstance(point, list) or len(point) != 2
                            or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in point)
                            or not (-125 <= point[0] <= -116 and 41 <= point[1] <= 47)):
                        raise ValueError('Invalid Oregon longitude/latitude')
                area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
                if area == 0:
                    raise ValueError('Degenerate tract ring')
    return collection


@lru_cache(maxsize=1)
def _load():
    raw = BOUNDARIES.read_bytes()
    provenance = json.loads(MANIFEST.read_text())
    if (provenance['boundary_year'] != BOUNDARY_YEAR or provenance['output_crs'] != 'EPSG:4326'
            or hashlib.sha256(raw).hexdigest() != provenance['geojson_sha256']):
        raise ValueError('Tract geometry does not match its provenance')
    collection = validate(json.loads(raw))
    if len(collection['features']) != provenance['features']:
        raise ValueError('Tract feature count differs from provenance')
    return collection, provenance


def boundaries(county_fips=None):
    """Fresh, independently mutable polygons; no fetch or filesystem writes."""
    if county_fips is not None and county_fips not in county_names():
        raise ValueError('Unknown Oregon county')
    collection = deepcopy(_load()[0])
    if county_fips is not None:
        collection['features'] = [f for f in collection['features'] if f['properties']['county_fips'] == county_fips]
    return collection


def provenance():
    return deepcopy(_load()[1])


def join_estimates(rows, county_fips=None, *, estimate_boundary_year=2023, previous=None):
    """Join one measure/year's rows by GEOID; leave absent/null estimates explicit.

    `previous` is an optional prior FeatureCollection. Geometry differences are
    coordinate differences, not a certified geographic crosswalk. Missing IDs
    never inherit estimates from names, points, ZIP codes or another vintage.
    """
    if estimate_boundary_year != BOUNDARY_YEAR:
        raise ValueError('Estimate and boundary vintages require an explicit compatible crosswalk')
    collection = boundaries(county_fips)
    estimates = {}
    periods = set()
    for row in rows:
        geoid = _geoid(row.get('tract_fips'), row.get('county_fips'))
        if row.get('county_fips') not in county_names():
            raise ValueError('Missing estimate county prefix')
        if county_fips is not None and row['county_fips'] != county_fips:
            continue
        periods.add((row.get('measure'), row.get('year')))
        if len(periods) > 1:
            raise ValueError('Select a single measure/year before joining tract estimates')
        if geoid in estimates:
            raise ValueError('Select a single measure/year before joining tract estimates')
        estimates[geoid] = row
    ids = {f['id'] for f in collection['features']}
    unavailable = []
    for feature in collection['features']:
        props = feature['properties']
        row = estimates.get(feature['id'])
        value = row.get('prevalence') if row is not None else None
        low, high = (row.get('low'), row.get('high')) if row is not None else (None, None)
        for number in (value, low, high):
            if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float))
                                       or not math.isfinite(number) or not 0 <= number <= 100):
                raise ValueError('Invalid joined tract estimate/interval')
        if ((low is not None and high is not None and low > high)
                or (value is not None and low is not None and low > value)
                or (value is not None and high is not None and high < value)):
            raise ValueError('Invalid joined confidence interval')
        status = 'no_record' if row is None else 'unavailable' if value is None else 'available'
        if row is not None and value is None:
            unavailable.append(feature['id'])
        props.update(prevalence=value, low=low, high=high, estimate_status=status)
    changes = None
    if previous is not None:
        prior = validate(previous)
        old = {f['id']: f['geometry'] for f in prior['features']
               if county_fips is None or f['properties']['county_fips'] == county_fips}
        current = {f['id']: f['geometry'] for f in collection['features']}
        changes = dict(added_ids=sorted(ids - old.keys()), removed_ids=sorted(old.keys() - ids),
                       changed_geometry_ids=sorted(k for k in ids & old.keys() if current[k] != old[k]))
    report = dict(boundary_year=BOUNDARY_YEAR, boundary_tracts=len(ids),
                  matched_tracts=len(ids & estimates.keys()),
                  estimates_without_boundary=sorted(estimates.keys() - ids),
                  boundaries_without_record=sorted(ids - estimates.keys()),
                  unavailable_estimates=sorted(unavailable), changes=changes)
    return collection, report
