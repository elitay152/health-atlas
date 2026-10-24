"""Synthetic Washington HRSA source contracts, isolation and opt-in views."""
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd
import pytest
import requests

from oregon_health import access_quality, hrsa, regions, status, storage
from oregon_health.hrsa_ui import designation_deck, export_csv
from test_hrsa import feature, provenance, records as or_records
from test_washington_hospitals import app_with_wa

WA = regions.WASHINGTON


def wa_feature(oid=1, discipline='Primary Care', point=False, status='D'):
    f = feature(oid, discipline, status, point)
    f['properties'].update(PRIMARY_STATE_FIPS_CD='53', CMN_STATE_FIPS_CD='53', STATE_FIPS_CD='53',
                           CMN_STATE_COUNTY_FIPS_CD='53033', STATE_COUNTY_FIPS_CD='53033', GEO_ID='53033000100')
    f['geometry'] = {'type': 'Point', 'coordinates': [-122.33, 47.6]} if point else {
        'type': 'Polygon', 'coordinates': [[[-122.4,47.5],[-122.3,47.5],[-122.3,47.6],[-122.4,47.5]]]}
    return f


def records():
    pop = wa_feature()
    pending = wa_feature(2, status='P'); pending['geometry'] = None
    geo = wa_feature(3); geo['properties']['HPSA_TYP_CD'] = 'Hpsa Geo'
    return hrsa.normalize([pop, pending, geo], 'Primary Care', 11, False, region=WA) + hrsa.normalize(
        [wa_feature(4, point=True)], 'Primary Care', 9, True, region=WA)


def test_washington_source_filters_membership_and_missingness(monkeypatch):
    calls = []
    def get(path, params=None):
        calls.append(params)
        layer = int(path.split('/')[1]); point = layer in {1,5,9}
        discipline = next(d for d, layers in hrsa.DISCIPLINES.items() if layer in layers)
        if not path.endswith('/query'):
            return dict(name=f"{discipline} HPSA - {'points' if point else 'component polygons'}",
                        fields=[{'name': k} for k in hrsa.COMMON + (hrsa.POINT if point else hrsa.COMPONENT)])
        if params.get('returnIdsOnly'): return {'objectIds': [1]}
        return {'features': [wa_feature(discipline=discipline, point=point)]}
    monkeypatch.setattr(hrsa, '_get', get)
    rows, p = hrsa.fetch(region=WA)
    assert len(rows) == 6 and len(p['layers']) == 6 and p['state_fips'] == '53'
    assert all("'53'" in c['where'] and "'41'" not in c['where'] for c in calls if c and 'where' in c)
    assert all(r['state'] == 'WA' and r['county_fips'] == '53033' for r in rows)
    outside = wa_feature(); outside['properties']['STATE_FIPS_CD'] = '41'
    with pytest.raises(ValueError): hrsa.normalize([outside], 'Primary Care', 11, False, region=WA)
    cross = wa_feature(point=True)
    cross['properties'].update(CMN_STATE_FIPS_CD='41', CMN_STATE_COUNTY_FIPS_CD='41051')
    row = hrsa.normalize([cross], 'Primary Care', 9, True, region=WA)[0]
    assert row['county_fips'] is None and row['source_county_fips'] == '41051'
    assert row['source_primary_state_fips'] == '53' and row['source_common_state_fips'] == '41'
    frame = pd.read_csv(io.StringIO(export_csv(records(), dict(provenance(), state='WA', state_fips='53'))), dtype=str)
    assert set(frame.state) == {'WA'} and set(frame.state_fips) == {'53'}
    assert frame.has_geometry.tolist().count('False') == 1
    deck = json.loads(designation_deck(records()[:2], 'Population group', region=WA).to_json())
    assert len(deck['layers'][0]['data']['features']) == 39
    assert len(deck['layers'][1]['data']['features']) == 1
    assert deck['initialViewState']['longitude'] == WA.center[0]


def test_hrsa_identity_legacy_migration_and_disk_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(hrsa, 'DB_PATH', tmp_path / 'hrsa.duckdb')
    hrsa.store(or_records(), provenance())
    with duckdb.connect(str(hrsa.DB_PATH)) as db:
        old = json.loads(db.execute('SELECT payload FROM provenance').fetchone()[0])
        old.pop('state_fips'); old.pop('state')
        db.execute('UPDATE provenance SET payload = ?', [json.dumps(old)])
    assert len(hrsa.snapshot()[0]) == 3
    with pytest.raises(ValueError): hrsa.snapshot(hrsa.DB_PATH, region=WA)
    hrsa.store(or_records(), provenance())
    assert hrsa.snapshot()[1]['state_fips'] == '41'
    path = hrsa.hrsa_path(WA)
    assert path == tmp_path / 'regions/wa/hrsa.duckdb'
    hrsa.store(records(), provenance(), region=WA)
    before = path.read_bytes()
    with pytest.raises(ValueError): hrsa.snapshot(path)
    with pytest.raises(ValueError): access_quality._read(path, 'HRSA designations')
    def forbidden(*a, **kw): pytest.fail('Foreign cache guard must precede requests')
    monkeypatch.setattr(hrsa, 'fetch', forbidden)
    with pytest.raises(ValueError): hrsa.refresh(path, force=True)
    with pytest.raises(ValueError): hrsa.store(or_records(), provenance(), path)
    assert path.read_bytes() == before
    def fail(*args): raise OSError('Synthetic disk failure')
    monkeypatch.setattr(storage.os, 'replace', fail)
    with pytest.raises(OSError): hrsa.store(records(), provenance(), region=WA)
    assert path.read_bytes() == before and list(path.parent.glob('*.duckdb')) == [path]


def test_hrsa_refresh_outcomes_and_legacy_status(tmp_path):
    path = tmp_path / 'hrsa.duckdb'
    status.record_refresh(path, 'invalid-source', 'failed')
    assert status.last_refresh(path, 'hrsa-hpsa')['outcome'] == 'failed'
    status.record_refresh(path, 'hrsa-hpsa', 'success')
    assert status.last_refresh(path, 'hrsa-hpsa')['outcome'] == 'success'
    with sqlite3.connect(tmp_path / '.source-status.sqlite3') as db:
        assert db.execute('SELECT identity FROM refreshes').fetchone()[0] == 'hrsa-hpsa'


def test_washington_opt_in_filters_details_and_failure_retention(tmp_path, monkeypatch):
    app, _ = app_with_wa(tmp_path, monkeypatch)
    monkeypatch.setattr(hrsa, 'DB_PATH', tmp_path / 'hrsa.duckdb')
    path = hrsa.hrsa_path(WA)
    old = dict(provenance(), fetched_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    hrsa.store(records(), old, region=WA)
    calls = []
    def offline(*args, **kwargs):
        calls.append(True)
        raise requests.ConnectionError('Synthetic private diagnostic')
    monkeypatch.setattr(hrsa, 'fetch', offline)
    app.run()
    assert not app.exception and not app.checkbox(key='show_hrsa').value and not calls
    app.checkbox(key='show_hrsa').check().run()
    assert not app.exception and calls == [True]
    assert len(app.selectbox(key='hrsa_county').options) == 41
    assert any('latest refresh failed' in w.value for w in app.warning)
    app.selectbox(key='hrsa_category').set_value('Population group').run()
    assert any('not every resident' in i.value for i in app.info)
    app.selectbox(key='hrsa_detail').set_value('11:1').run()
    assert any('HPSA ID: 141000000I' in t.value for t in app.text)
    app.multiselect(key='hrsa_statuses').set_value(['Proposed For Withdrawal']).run()
    assert any(m.label == 'Records without geometry' and m.value == '1' for m in app.metric)
    app.selectbox(key='hrsa_category').set_value('Facility').run()
    assert not any(s.key == 'hrsa_detail' for s in app.selectbox)
    app.multiselect(key='hrsa_statuses').set_value(['Designated']).run()
    assert app.selectbox(key='hrsa_detail').value is None
    assert any('not the CMS hospital directory' in i.value for i in app.info)
    assert calls == [True] and not app.exception
    # With no saved HRSA file, the health view and source controls remain usable.
    path.unlink()
    app.run()
    assert not app.exception and not any(s.key=='health_measure' for s in app.selectbox)
    assert any('HRSA designations are unavailable' in i.value for i in app.info)
