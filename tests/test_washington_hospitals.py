"""State-isolated CMS contracts and Washington directory UI; no live requests."""
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import access_quality, api_cache, census, data, hospitals, hospital_ui, regions, storage
from test_hospitals import row, csv_bytes, provenance

WA = regions.WASHINGTON
OR = regions.OREGON


def wa_rows():
    return [row(**{'State': 'WA', 'Facility ID': '005001', 'County/Parish': 'KING',
                   'City/Town': 'Seattle'}),
            row(**{'State': 'WA', 'Facility ID': '005002', 'County/Parish': 'UNKNOWN',
                   'Address': '', 'Emergency Services': 'Not Available'})]


@pytest.mark.parametrize('region,count', [(OR, 36), (WA, 39)])
def test_two_state_cms_catalogs_identity_and_atomic_replacement(tmp_path, monkeypatch, region, count):
    raw = csv_bytes([row(), *wa_rows()])
    records = hospitals.normalize(raw, region=region)
    assert all(r[4] == region.abbr for r in records)
    path = tmp_path / region.abbr / 'hospitals.duckdb'
    hospitals.store(records, provenance(), path, region=region)
    report = hospitals.coverage(path, region=region)
    assert len(report['counties']) == count
    assert report['provenance']['state_fips'] == region.state_fips
    assert report['located_facilities'] == 0
    assert not hospitals.needs_refresh(path, region=region)
    if region == WA:
        with pytest.raises(ValueError): access_quality._read(path, 'CMS hospitals')
        assert records[0][0] == '005001' and records[0][5] == '00123'
        assert hospitals.directory('53033', path, region=WA)[0]['county_fips'] == '53033'
        assert report['unresolved_counties'] == report['missing_address'] == report['unknown_emergency_services'] == 1
    other = WA if region == OR else OR
    before = path.read_bytes()
    def forbidden(*a, **kw): pytest.fail('Foreign cache rejection must precede source requests')
    monkeypatch.setattr(hospitals, 'fetch', forbidden)
    for read in [hospitals.coverage, hospitals.needs_refresh, hospitals.directory]:
        with pytest.raises(ValueError): read(path=path, region=other)
    with pytest.raises(ValueError): hospitals.refresh(path, force=True, region=other)
    other_records = hospitals.normalize(raw, region=other)
    with pytest.raises(ValueError): hospitals.store(other_records, provenance(), path, region=other)
    assert path.read_bytes() == before
    def fail(*args): raise OSError('Synthetic disk failure')
    monkeypatch.setattr(storage.os, 'replace', fail)
    with pytest.raises(OSError): hospitals.store(records, provenance(), path, region=region)
    assert path.read_bytes() == before
    assert list(path.parent.glob('*.duckdb')) == [path]


def test_legacy_oregon_snapshot_and_default_paths(tmp_path, monkeypatch):
    path = tmp_path / 'hospitals.duckdb'
    monkeypatch.setattr(hospitals, 'DB_PATH', path)
    records = hospitals.normalize(csv_bytes([row()]))
    hospitals.store(records, provenance())
    with duckdb.connect(str(path)) as db: db.execute('ALTER TABLE provenance DROP COLUMN state_fips')
    assert hospitals.coverage()['records'] == 1
    with pytest.raises(ValueError): hospitals.coverage(path, region=WA)
    hospitals.store(records, provenance())
    assert hospitals.coverage()['provenance']['state_fips'] == '41'
    assert hospitals.hospital_path(WA) == tmp_path / 'regions/wa/hospitals.duckdb'


def app_with_wa(tmp_path, monkeypatch, *, health=True, cached=True):
    base = tmp_path / 'oregon.duckdb'
    monkeypatch.setattr(data, 'DB_PATH', base)
    monkeypatch.setattr(hospitals, 'DB_PATH', tmp_path / 'hospitals.duckdb')
    if health:
        rows = [dict(r, stateabbr='WA', locationid='53033', locationname='King')
                for r in data.demo_rows() if r['locationid'] == '41051']
        data.store(rows, 'Synthetic Washington health fixture', regions.health_path(WA, tmp_path), region=WA)
    monkeypatch.setattr(api_cache, 'needs_refresh', lambda *a, **kw: not health)
    def offline(*a, **kw): raise requests.ConnectionError('Synthetic unavailable source')
    monkeypatch.setattr(api_cache, 'refresh', offline)
    monkeypatch.setattr(census, 'context', offline)
    def forbidden(*a, **kw): pytest.fail('Washington must not attempt unvalidated geocoding')
    monkeypatch.setattr(hospital_ui, 'render_locations', forbidden)
    path = hospitals.hospital_path(WA)
    if cached: hospitals.store(hospitals.normalize(csv_bytes(wa_rows()), region=WA), provenance(), path, region=WA)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=20)
    app.query_params.update(state='WA')
    return app, path


def test_washington_directory_filters_details_export_and_profile(tmp_path, monkeypatch):
    app, path = app_with_wa(tmp_path, monkeypatch)
    app.run()
    assert not app.exception and not app.checkbox(key='show_hospitals').value
    app.checkbox(key='show_hospitals').check().run()
    assert not app.exception
    assert any(m.label == 'Washington hospitals listed' and m.value == '2' for m in app.metric)
    assert any(m.label == 'Counties represented' and m.value == '1 / 39' for m in app.metric)
    assert len(app.selectbox(key='hospital_county').options) == 41
    assert not any(c.key == 'show_hospital_locations' for c in app.checkbox)
    app.selectbox(key='hospital_detail').set_value('005001').run()
    assert any('Seattle, WA 00123' in t.value for t in app.text)
    app.button(key='hospital_open_county').click().run()
    assert not app.exception and app.selectbox(key='profile_county').value == '53033'
    assert app.query_params['state'] == 'WA' and app.query_params['county'] == '53033'
    app.selectbox(key='hospital_county').set_value(hospital_ui.UNKNOWN).run()
    table = next(d.value for d in app.dataframe if 'Facility ID' in d.value.columns)
    assert list(table['Facility ID']) == ['005002']
    assert app.selectbox(key='hospital_detail').value is None
    records = hospitals.directory(path=path, region=WA)
    exported = hospital_ui.export_csv(records, hospitals.coverage(path, region=WA)['provenance'])
    assert '005001' in exported and '00123' in exported
    assert 'Not supplied by CMS' in exported and 'not_geocoded' in exported


def test_washington_cms_outages_independent_of_health(tmp_path, monkeypatch):
    app, path = app_with_wa(tmp_path, monkeypatch, health=False)
    with duckdb.connect(str(path)) as db:
        db.execute('UPDATE provenance SET fetched_at = ?', [datetime.now() - timedelta(days=2)])
    calls = []
    def offline(*a, **kw):
        calls.append(True)
        raise requests.ConnectionError('Synthetic private diagnostic')
    monkeypatch.setattr(hospitals, 'fetch', offline)
    app.run().checkbox(key='show_hospitals').check().run()
    assert not app.exception and calls == [True]
    assert any(m.label == 'Washington hospitals listed' and m.value == '2' for m in app.metric)
    assert any('retained' in w.value for w in app.warning)
    app.selectbox(key='hospital_detail').set_value('005001').run()
    assert not any(b.key == 'hospital_open_county' for b in app.button)
    assert any('County profiles need CDC' in i.value for i in app.info)
    assert calls == [True]
    path.unlink()
    empty, _ = app_with_wa(tmp_path, monkeypatch, health=False, cached=False)
    empty.run().checkbox(key='show_hospitals').check().run()
    assert not empty.exception
    assert any('Hospital listings are currently unavailable' in i.value for i in empty.info)
