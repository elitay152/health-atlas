"""Synthetic join failures plus independent checks of the shipped public asset."""
from copy import deepcopy
import hashlib
import json

import pytest

from oregon_health import tract_boundaries as tb
from oregon_health.profile import county_names


def feature(geoid='41051000101', geometry=None):
    return dict(type='Feature', id=geoid, properties=dict(geoid=geoid, county_fips=geoid[:5], name='Synthetic tract'),
                geometry=geometry or dict(type='Polygon', coordinates=[[
                    [-123, 45], [-122, 45], [-122, 46], [-123, 46], [-123, 45]]]))


def collection(*features):
    return dict(type='FeatureCollection', features=list(features))


def estimate(geoid='41051000101', **changes):
    return dict(tract_fips=geoid, county_fips=geoid[:5], measure='DIABETES', year=2023,
                prevalence=8, low=7, high=9) | changes


def synthetic(monkeypatch):
    data = collection(feature(), feature('41051000102'), feature('41051000103'))
    monkeypatch.setattr(tb, '_load', lambda: (data, {'boundary_year': 2023}))
    return data


def test_bundled_asset_count_ids_crs_and_digest():
    data = tb.boundaries()
    manifest = tb.provenance()
    assert len(data['features']) == 994
    assert len({f['id'] for f in data['features']}) == 994
    assert {f['properties']['county_fips'] for f in data['features']} == set(county_names())
    assert manifest['output_crs'] == 'EPSG:4326' and manifest['source_crs'] == 'EPSG:4269'
    assert manifest['boundary_year'] == 2023 and manifest['scale'] == '1:500,000'
    assert manifest['geojson_sha256'] == hashlib.sha256(tb.BOUNDARIES.read_bytes()).hexdigest()
    assert manifest['geojson_bytes'] == tb.BOUNDARIES.stat().st_size
    multnomah = tb.boundaries('41051')
    assert len(multnomah['features']) == 197
    assert all(f['id'].startswith('41051') for f in multnomah['features'])
    # RFC 7946 ring winding, including holes, and expected Oregon axis order.
    for f in data['features']:
        geo = f['geometry']
        for poly in [geo['coordinates']] if geo['type'] == 'Polygon' else geo['coordinates']:
            for i, ring in enumerate(poly):
                signed = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring,ring[1:]))
                assert (signed > 0) == (i == 0)


def test_join_reports_unavailable_absent_and_unmatched_without_mutation(monkeypatch):
    original = synthetic(monkeypatch)
    rows = [estimate(), estimate('41051000102', prevalence=None, low=None, high=None), estimate('41051000999')]
    saved = deepcopy(rows)
    joined, report = tb.join_estimates(rows, '41051')
    props = {f['id']: f['properties'] for f in joined['features']}
    assert props['41051000101']['prevalence'] == 8
    assert props['41051000102']['estimate_status'] == 'unavailable'
    assert props['41051000103']['estimate_status'] == 'no_record'
    assert props['41051000103']['prevalence'] is None
    assert report['estimates_without_boundary'] == ['41051000999']
    assert report['boundaries_without_record'] == ['41051000103']
    assert report['unavailable_estimates'] == ['41051000102']
    assert report['matched_tracts'] == 2 and report['changes'] is None
    assert rows == saved and 'prevalence' not in original['features'][0]['properties']
    joined['features'][0]['geometry']['coordinates'][0][0][0] = 0
    assert tb.boundaries()['features'][0]['geometry']['coordinates'][0][0][0] == -123
    assert tb.join_estimates([], '41051')[1]['boundaries_without_record'] == [f['id'] for f in original['features']]


def test_county_filter_and_missing_boundaries(monkeypatch):
    synthetic(monkeypatch)
    _, report = tb.join_estimates([estimate(), estimate('41039000101')], '41051')
    assert report['matched_tracts'] == 1 and report['estimates_without_boundary'] == []
    _, empty = tb.join_estimates([estimate('41039000101')], '41039')
    assert empty['boundary_tracts'] == 0 and empty['estimates_without_boundary'] == ['41039000101']
    with pytest.raises(ValueError): tb.boundaries('41099')


def test_previous_snapshot_changes_reported_without_crosswalking(monkeypatch):
    synthetic(monkeypatch)
    previous = collection(feature(), feature('41051000102'), feature('41051000999'))
    previous['features'][1]['geometry']['coordinates'][0][1][0] = -121.5
    _, report = tb.join_estimates([estimate()], previous=previous)
    assert report['changes'] == dict(added_ids=['41051000103'], removed_ids=['41051000999'],
                                     changed_geometry_ids=['41051000102'])
    with pytest.raises(ValueError, match='vintages'):
        tb.join_estimates([estimate()], estimate_boundary_year=2020)


@pytest.mark.parametrize('changes', [dict(tract_fips='41051'), dict(county_fips='41039'),
    dict(tract_fips='41099000101', county_fips='41099'), dict(prevalence=float('nan')),
    dict(prevalence=-1), dict(prevalence=101), dict(low=9), dict(high=7), dict(prevalence=True)])
def test_bad_estimate_joins_rejected(monkeypatch, changes):
    synthetic(monkeypatch)
    with pytest.raises(ValueError): tb.join_estimates([estimate(**changes)])


def test_duplicate_join_rejected(monkeypatch):
    synthetic(monkeypatch)
    with pytest.raises(ValueError, match='single measure/year'):
        tb.join_estimates([estimate(), estimate()])


@pytest.mark.parametrize('bad', ['duplicate', 'prefix', 'id', 'empty', 'open', 'nonfinite', 'swapped', 'degenerate', 'point'])
def test_invalid_geometry_or_ids_rejected(bad):
    data = collection(feature())
    f = data['features'][0]
    if bad == 'duplicate': data['features'].append(deepcopy(f))
    if bad == 'prefix': f['properties']['county_fips'] = '41039'
    if bad == 'id': f['id'] = 41051000101
    if bad == 'empty': f['geometry']['coordinates'] = []
    if bad == 'open': f['geometry']['coordinates'][0][-1] = [-122,45]
    if bad == 'nonfinite': f['geometry']['coordinates'][0][1] = [float('nan'),45]
    if bad == 'swapped': f['geometry']['coordinates'][0][1] = [45,-123]
    if bad == 'degenerate': f['geometry']['coordinates'][0] = [[-123,45]] * 4
    if bad == 'point': f['geometry'] = dict(type='Point', coordinates=[-123,45])
    with pytest.raises(ValueError): tb.validate(data)


def test_polygon_holes_and_multipolygons_preserved():
    outer = feature()['geometry']['coordinates'][0]
    hole = [[-122.8,45.2],[-122.8,45.4],[-122.6,45.4],[-122.6,45.2],[-122.8,45.2]]
    geo = dict(type='MultiPolygon', coordinates=[[outer, hole], [outer]])
    data = collection(feature(geometry=geo))
    assert tb.validate(data)['features'][0]['geometry'] == geo


def test_asset_tampering_rejected(tmp_path, monkeypatch):
    manifest = tb.provenance()
    raw = tb.BOUNDARIES.read_bytes()
    geometry = tmp_path / 'boundaries.json'
    metadata = tmp_path / 'metadata.json'
    geometry.write_bytes(raw + b' ')
    metadata.write_text(json.dumps(manifest))
    with monkeypatch.context() as ctx:
        ctx.setattr(tb, 'BOUNDARIES', geometry)
        ctx.setattr(tb, 'MANIFEST', metadata)
        tb._load.cache_clear()
        with pytest.raises(ValueError, match='provenance'): tb.boundaries()
    tb._load.cache_clear()


def test_mixed_periods_are_not_joined_as_one_map(monkeypatch):
    synthetic(monkeypatch)
    with pytest.raises(ValueError, match='single measure/year'):
        tb.join_estimates([estimate(), estimate('41051000102', year=2022)])
