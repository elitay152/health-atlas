"""Reproducible whole-county membership, not a computed healthcare catchment."""
from copy import deepcopy
import hashlib
import json

from oregon_health.map import BOUNDARIES
from oregon_health.regions import OREGON, ROOT, get_region
from oregon_health.profile import county_names

MAX_BYTES = 250_000
SOURCE = 'https://github.com/plotly/datasets/blob/0c447c47b757ad74edecab31f0d72f849d2e67c2/geojson-counties-fips.json'
RULE = 'Include each selected county once in full; exclude unselected counties.'


def normalize(codes, *, region=OREGON):
    region = get_region(region)
    region.require("service_areas")
    if not isinstance(codes, list) or len(codes) > 2 * region.county_count:
        raise ValueError(f'Expected a bounded list of {region.name} county IDs')
    if any(not isinstance(code, str) or code not in county_names(region) for code in codes):
        raise ValueError(f'Area contains an unknown {region.name} county ID')
    return sorted(set(codes))


def _boundary_path(region):
    region = get_region(region)
    return BOUNDARIES if region == OREGON else ROOT / "assets" / region.boundary_file


def source(*, region=OREGON):
    region = get_region(region)
    return dict(url=SOURCE, artifact=region.boundary_file,
                sha256=hashlib.sha256(_boundary_path(region).read_bytes()).hexdigest(),
                boundary_vintage=None, license='MIT', attribution='Plotly datasets')


def definition(codes, *, region=OREGON):
    """Embed the exact selected display polygons and a versioned membership rule."""
    region = get_region(region)
    codes = normalize(codes, region=region)
    collection = json.loads(_boundary_path(region).read_bytes())
    features = {f['properties']['fips']: f for f in collection['features']}
    if set(features) != set(county_names(region)):
        raise ValueError('Bundled county geography is incomplete')
    return dict(type='FeatureCollection',features=[deepcopy(features[c]) for c in codes],
                health_atlas=dict(schema_version=1,geography='county',state_fips=region.state_fips,
                                  county_fips=codes,inclusion_rule=RULE,boundary_source=source(region=region)))


def dumps(codes, *, region=OREGON):
    return json.dumps(definition(codes, region=region),separators=(',', ':'),allow_nan=False) + '\n'


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def loads(raw, *, region=OREGON):
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
        expected = definition(codes, region=region)
        # Compare canonical encodings too: JSON booleans are not numeric values.
        if json.dumps(value,sort_keys=True,allow_nan=False) != json.dumps(expected,sort_keys=True,allow_nan=False):
            raise ValueError('Area definition or boundary revision is incompatible')
    except (KeyError,TypeError,UnicodeError,RecursionError) as exc:
        raise ValueError('Invalid service-area definition') from exc
    return normalize(codes, region=region)


def parse_link(value, revision, *, region=OREGON):
    if value is None:
        return None
    if not isinstance(value,str) or len(value)>6 * get_region(region).county_count - 1:
        raise ValueError('Invalid service-area link')
    codes = normalize(value.split(',') if value else [], region=region)
    if revision != source(region=region)['sha256']:
        raise ValueError('The linked boundary revision differs or is missing')
    return codes


def link_params(codes, *, region=OREGON):
    region = get_region(region)
    params = dict(area=','.join(normalize(codes, region=region)), area_rev=source(region=region)['sha256'])
    return params if region == OREGON else dict(state=region.abbr, **params)
