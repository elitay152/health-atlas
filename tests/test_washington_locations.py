"""Washington matching uses synthetic public-address fixtures; no Census requests."""
from datetime import datetime, timedelta
import io
import json

import duckdb
import pandas as pd
import pytest
import requests

from oregon_health import access_quality, geocoding, hospitals, hospital_ui, regions, storage
from oregon_health.map import hospital_deck
from test_geocoding import response_bytes
from test_washington_hospitals import app_with_wa

WA = regions.WASHINGTON


def record(code='005001', **changes):
    return dict(facility_id=code, name='Synthetic Hospital', address='1 Example St', city='Seattle',
                state='WA', zip_code='00123', county_fips='53033', **changes)


def match(code='005001', **changes):
    values = dict(result='Match', quality='Exact', address='1 EXAMPLE ST, SEATTLE, WA, 00123', coords='-122.33,47.6')
    values.update(changes)
    return [code, 'Published source address', values['result'], values['quality'], values['address'], values['coords'], '123', 'L']


def source(records):
    return dict(source_url=geocoding.SOURCE_URL, benchmark=geocoding.BENCHMARK, benchmark_id='4',
                fetched_at=datetime.now().astimezone(), fingerprint=geocoding.fingerprint(records),
                state_fips='53', boundary_sha256=geocoding.boundary_hash(WA))


def test_washington_match_classification_and_map_catalog():
    records = [record(str(500000 + i)) for i in range(9)]
    records[7]['county_fips'] = None
    records[8]['county_fips'] = '53053'
    rows = [match(r['facility_id']) for r in records]
    rows[1][2] = 'No_Match'; rows[2][2] = 'Tie'; rows[3][3] = 'Non_Exact'
    rows[4][5] = 'nan,47.6'; rows[5][5] = '-130,47.6'; rows[6][4] = ''
    matches = geocoding.parse_matches(response_bytes(rows), {r['facility_id']: r for r in records}, region=WA)
    assert [r[2] for r in matches] == ['matched_exact', 'unmatched', 'ambiguous', 'non_exact',
        'invalid_coordinates', 'invalid_coordinates', 'missing_match_address', 'county_unresolved', 'county_mismatch']
    assert all(r[3:5] == (None, None) for r in matches[1:])
    assert geocoding.in_county(-122.33, 47.6, '53033', region=WA)
    assert not geocoding.in_county(-122.33, 47.6, '41051', region=WA)
    deck = json.loads(hospital_deck([{**records[0], 'longitude': -122.33, 'latitude': 47.6,
                                    'matched_address': 'Synthetic address'}], region=WA).to_json())
    assert len(deck['layers'][0]['data']['features']) == 39
    assert deck['initialViewState']['latitude'] == WA.center[1]
    assert deck['layers'][1]['data'][0]['state'] == 'WA'


def test_washington_batch_payload_and_mixed_state_rejection(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def __init__(self, raw): self.raw = raw
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield self.raw
    def get(url, **kwargs):
        return Response(json.dumps({'benchmarks': [{'id': '4', 'benchmarkName': geocoding.BENCHMARK}]}).encode())
    def post(url, **kwargs):
        calls.append(kwargs)
        return Response(response_bytes([match()]))
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(requests, 'post', post)
    records = [record()]
    matches, meta = geocoding.fetch(records, region=WA)
    assert matches[0][2] == 'matched_exact' and meta['state_fips'] == '53'
    sent = calls[0]['files']['addressFile'][1].decode()
    assert ',Seattle,WA,00123' in sent and 'Synthetic Hospital' not in sent
    assert meta['boundary_sha256'] == geocoding.boundary_hash(WA)
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: pytest.fail('Reject before network'))
    with pytest.raises(ValueError): geocoding.fetch([dict(records[0], state='OR')], region=WA)
    with pytest.raises(ValueError): geocoding.fetch([dict(records[0], county_fips='41051')], region=WA)


def test_location_cache_identity_changed_addresses_and_corrupt_points(tmp_path, monkeypatch):
    records = [record()]
    matches = geocoding.parse_matches(response_bytes([match()]), {'005001': records[0]}, region=WA)
    path = tmp_path / 'hospital_locations.duckdb'
    geocoding.store(matches, source(records), path, region=WA)
    assert geocoding.snapshot(records, path, region=WA)[0]['005001']['location_status'] == 'matched_exact'
    before = path.read_bytes()
    with pytest.raises(ValueError): geocoding.snapshot(records, path)
    with pytest.raises(ValueError): access_quality._read(path, 'Address matches')
    monkeypatch.setattr(geocoding, 'fetch', lambda *a, **kw: pytest.fail('Reject foreign cache before fetch'))
    or_records = [dict(records[0], state='OR', county_fips='41051')]
    with pytest.raises(ValueError): geocoding.refresh(or_records, path, force=True)
    with pytest.raises(ValueError): geocoding.store(matches, source(records), path)
    assert path.read_bytes() == before
    changed = [dict(records[0], address='2 Changed St')]
    current, _ = geocoding.snapshot(changed, path, region=WA)
    assert current['005001']['location_status'] == 'address_changed' and current['005001']['longitude'] is None
    with duckdb.connect(str(path)) as db:
        db.execute("UPDATE provenance SET boundary_sha256 = ?", ['b' * 64])
    assert geocoding.needs_refresh(records, path, region=WA)
    with duckdb.connect(str(path)) as db: db.execute('UPDATE locations SET longitude = -130')
    current, _ = geocoding.snapshot(records, path, region=WA)
    assert current['005001']['location_status'] == 'invalid_coordinates' and current['005001']['longitude'] is None
    before = path.read_bytes()
    def fail(*args): raise OSError('Synthetic disk failure')
    monkeypatch.setattr(storage.os, 'replace', fail)
    with pytest.raises(OSError): geocoding.store(matches, source(records), path, region=WA)
    assert path.read_bytes() == before
    assert list(tmp_path.glob('*.duckdb')) == [path]


def test_legacy_oregon_location_cache_remains_oregon_only(tmp_path):
    from test_geocoding import hospital, match as or_match, source as or_source
    rows = [hospital()]
    path = tmp_path / 'legacy.duckdb'
    matches = geocoding.parse_matches(response_bytes([or_match()]), {'003801': rows[0]})
    geocoding.store(matches, or_source(rows), path)
    with duckdb.connect(str(path)) as db:
        db.execute('ALTER TABLE provenance DROP COLUMN state_fips')
        db.execute('ALTER TABLE provenance DROP COLUMN boundary_sha256')
    assert geocoding.snapshot(rows, path)[0]['003801']['location_status'] == 'matched_exact'
    with pytest.raises(ValueError): geocoding.snapshot([record()], path, region=WA)
    geocoding.store(matches, or_source(rows), path)
    assert geocoding.snapshot(rows, path)[1]['state_fips'] == '41'


def test_optional_washington_map_exports_and_retained_failure(tmp_path, monkeypatch):
    real_refresh = geocoding.refresh
    app, cms_path = app_with_wa(tmp_path, monkeypatch)
    monkeypatch.setattr(geocoding, 'DB_PATH', tmp_path / 'hospital_locations.duckdb')
    records = hospitals.directory(path=cms_path, region=WA)
    matches = [(r['facility_id'], geocoding.address_hash(r), 'matched_exact' if i == 0 else 'missing_address',
                -122.33 if i == 0 else None, 47.6 if i == 0 else None, 'Synthetic WA address' if i == 0 else None)
               for i, r in enumerate(records)]
    path = geocoding.location_path(WA)
    geocoding.store(matches, source(records), path, region=WA)
    app.run().checkbox(key='show_hospitals').check().run()
    app.checkbox(key='show_hospital_locations').check().run()
    assert not app.exception
    assert any('1 of 2 filtered hospitals have mapped' in item.value for item in app.caption)
    app.selectbox(key='hospital_detail').set_value('005001').run()
    assert any('not a verified building or entrance' in item.value for item in app.caption)
    locations, meta = geocoding.snapshot(records, path, region=WA)
    exported = pd.read_csv(io.StringIO(hospital_ui.export_csv(records, hospitals.coverage(cms_path, region=WA)['provenance'], locations, meta)), dtype=str, keep_default_na=False)
    assert exported.location_status.tolist() == ['matched_exact', 'missing_address']
    assert exported.longitude.tolist() == ['-122.33', '']
    assert set(exported.location_state_fips) == {'53'}
    assert set(exported.location_boundary_sha256) == {geocoding.boundary_hash(WA)}
    with duckdb.connect(str(path)) as db:
        db.execute('UPDATE provenance SET fetched_at = ?', [datetime.now() - timedelta(days=31)])
    def offline(*a, **kw): raise requests.ConnectionError('Synthetic outage')
    monkeypatch.setattr(geocoding, 'fetch', offline)
    # Exercise a new session's automatic stale refresh and retained coordinates.
    other, _ = app_with_wa(tmp_path, monkeypatch)
    monkeypatch.setattr(geocoding, 'refresh', real_refresh)
    other.run().checkbox(key='show_hospitals').check().run()
    other.checkbox(key='show_hospital_locations').check().run()
    assert not other.exception
    assert any('Only unchanged source addresses retain points' in item.value for item in other.warning)
    assert any('1 of 2 filtered hospitals have mapped' in item.value for item in other.caption)
    changed = [dict(r, address='Changed address') if r['facility_id'] == '005001' else r for r in records]
    hospitals.store([tuple(r[k] for k in hospitals.COLUMNS) for r in changed], hospitals.coverage(cms_path, region=WA)['provenance'], cms_path, region=WA)
    other.run()
    assert any('No accepted address matches' in item.value for item in other.info)
    assert not other.exception
    path.unlink()
    other.run()
    assert not other.exception
    assert any('No accepted address matches' in item.value for item in other.info)
    table = next(item.value for item in other.dataframe if 'Facility ID' in item.value.columns)
    assert len(table) == 2

