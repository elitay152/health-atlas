"""Reproducible whole-county membership, not a computed healthcare catchment."""
from copy import deepcopy
import hashlib
import json

from oregon_health.map import BOUNDARIES
from oregon_health.profile import county_names

MAX_BYTES = 250_000
SOURCE = 'https://github.com/plotly/datasets/blob/0c447c47b757ad74edecab31f0d72f849d2e67c2/geojson-counties-fips.json'
RULE = 'Include each selected county once in full; exclude unselected counties.'


def normalize(codes):
    if not isinstance(codes, list) or len(codes) > 72:
        raise ValueError('Expected a bounded list of Oregon county IDs')
    if any(not isinstance(code, str) or code not in county_names() for code in codes):
        raise ValueError('Area contains an unknown Oregon county ID')
    return sorted(set(codes))


def source():
    return dict(url=SOURCE, artifact='oregon-counties.geojson',
                sha256=hashlib.sha256(BOUNDARIES.read_bytes()).hexdigest(),
                boundary_vintage=None, license='MIT', attribution='Plotly datasets')


def definition(codes):
    """Embed the exact selected display polygons and a versioned membership rule."""
    codes = normalize(codes)
    collection = json.loads(BOUNDARIES.read_bytes())
    features = {f['properties']['fips']: f for f in collection['features']}
    if set(features) != set(county_names()):
        raise ValueError('Bundled county geography is incomplete')
    return dict(type='FeatureCollection',features=[deepcopy(features[c]) for c in codes],
                health_atlas=dict(schema_version=1,geography='county',state_fips='41',
                                  county_fips=codes,inclusion_rule=RULE,boundary_source=source()))


def dumps(codes):
    return json.dumps(definition(codes),separators=(',', ':'),allow_nan=False) + '\n'


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def loads(raw):
    """Only accept our current exact definition, never external URLs or geometry.

    Geometry/source changes require explicit review and a new selection. File
    contents are never logged or rendered, and no addresses/free text are used.
    """
    if not isinstance(raw,bytes) or len(raw) > MAX_BYTES:
        raise ValueError('Area file exceeds the supported size')
    try:
        value = json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Invalid number')))
        codes = value['health_atlas']['county_fips']
        expected = definition(codes)
        # Compare canonical encodings too: JSON booleans are not numeric values.
        if json.dumps(value,sort_keys=True,allow_nan=False) != json.dumps(expected,sort_keys=True,allow_nan=False):
            raise ValueError('Area definition or boundary revision is incompatible')
    except (KeyError,TypeError,UnicodeError,RecursionError) as exc:
        raise ValueError('Invalid service-area definition') from exc
    return normalize(codes)


def parse_link(value, revision):
    if value is None:
        return None
    if not isinstance(value,str) or len(value)>215:
        raise ValueError('Invalid service-area link')
    codes = normalize(value.split(',') if value else [])
    if revision != source()['sha256']:
        raise ValueError('The linked boundary revision differs or is missing')
    return codes


def link_params(codes):
    return dict(area=','.join(normalize(codes)),area_rev=source()['sha256'])
