"""Synthetic source and snapshot checks; tests never call CDC."""
from copy import deepcopy
from datetime import datetime, timezone

import duckdb
import pytest

from oregon_health import tracts
from oregon_health.data import MEASURES


def row(**changes):
    value = dict(locationid='41051000101', countyfips='41051', countyname='Synthetic County',
                 locationname='Synthetic tract', stateabbr='OR', measureid='DIABETES',
                 measure='Synthetic adult diagnosed diabetes definition', year='2023',
                 data_value='8.2', low_confidence_limit='7.1', high_confidence_limit='9.4',
                 data_value_type='Crude prevalence', data_value_unit='%', datasource='BRFSS',
                 category='Health Outcomes', categoryid='HLTHOUT', datavaluetypeid='CrdPrv',
                 short_question_text='Synthetic diabetes', totalpopulation='100', totalpop18plus='80')
    return value | changes


def provenance(count=1):
    return dict(dataset=tracts.DATASET, release_year=2025, rows=count,
                catalog=list(MEASURES), fetched_at=datetime.now(timezone.utc).isoformat())


def metadata():
    return dict(id=tracts.DATASET, name='PLACES: Local Data for Better Health, Census Tract Data, 2025 release',
                columns=[{'fieldName': field} for field in tracts.FIELDS.split(',')], rowsUpdatedAt=123)


def test_nulls_definitions_periods_and_queries(tmp_path):
    path = tmp_path / 'tracts.duckdb'
    rows = [row(), row(locationid='41051000102', year='2022', data_value=None,
                       low_confidence_limit=None, high_confidence_limit=None,
                       data_value_footnote='Synthetic unavailable estimate', data_value_footnote_symbol='*'),
            row(locationid='41051000103', low_confidence_limit=None)]
    tracts.store(rows, provenance(3), path)
    saved = tracts.estimates('41051', 'DIABETES', 2022, path)
    assert len(saved) == 1 and saved[0]['prevalence'] is None
    assert saved[0]['footnote'] == 'Synthetic unavailable estimate'
    assert saved[0]['footnote_symbol'] == '*'
    assert saved[0]['tract_fips'] == '41051000102'
    assert saved[0]['definition'] == rows[0]['measure']
    assert saved[0]['unit'] == '%' and saved[0]['estimate_type'] == 'Crude prevalence'
    report = tracts.coverage(path)
    by_year = {x['year']: x for x in report['coverage']}
    assert by_year[2022]['unavailable'] == 1 and by_year[2022]['available'] == 0
    assert by_year[2023]['without_uncertainty'] == 1
    assert tracts.estimates('41039', 'DIABETES', 2023, path) == []
    assert not tracts.needs_refresh(path)
    with duckdb.connect(str(path), read_only=True) as db:
        assert db.execute("SELECT table_name FROM information_schema.tables ORDER BY table_name").fetchall() == [('tract_estimates',), ('tract_provenance',)]
    with pytest.raises(ValueError):
        tracts.estimates("41051' OR TRUE--", 'DIABETES', 2023, path)


@pytest.mark.parametrize('changes', [
    {'locationid': '41051'}, {'locationid': 41051000101}, {'countyfips': '41039'},
    {'locationid': '41099000101', 'countyfips': '41099'}, {'stateabbr': 'WA'},
    {'measureid': 'UNKNOWN'}, {'data_value_type': 'Age-adjusted prevalence'},
    {'data_value_unit': 'count'}, {'year': '2023.5'}, {'year': None}, {'year': '2026'},
    {'data_value': 'NaN'}, {'data_value': True}, {'high_confidence_limit': 'inf'},
    {'low_confidence_limit': '8.3'}, {'high_confidence_limit': '8.1'},
    {'data_value': None, 'low_confidence_limit': 99, 'high_confidence_limit': 98},
    {'data_value': None, 'high_confidence_limit': 101}, {'totalpopulation': '1.5'},
    {'totalpop18plus': '101'}, {'measure': None}, {'datasource': None},
])
def test_invalid_rows_rejected(changes):
    with pytest.raises((ValueError, TypeError)):
        tracts.normalize([row(**changes)])


def test_duplicate_unavailable_rows_also_rejected():
    unavailable = row(data_value=None, low_confidence_limit=None, high_confidence_limit=None)
    with pytest.raises(ValueError, match='Duplicate'):
        tracts.normalize([unavailable, unavailable])


def test_store_and_refresh_failures_preserve_snapshot(tmp_path, monkeypatch):
    path = tmp_path / 'tracts.duckdb'
    tracts.store([row()], provenance(), path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        tracts.store([row(data_value='NaN')], provenance(), path)
    monkeypatch.setattr(tracts.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('synthetic publication failure')))
    with pytest.raises(OSError):
        tracts.store([row()], provenance(), path)
    assert path.read_bytes() == original
    assert list(tmp_path.glob('*.duckdb')) == [path]
    monkeypatch.setattr(tracts, 'fetch', lambda: (_ for _ in ()).throw(ValueError('synthetic source failure')))
    with pytest.raises(ValueError):
        tracts.refresh(path, force=True)
    assert path.read_bytes() == original
    assert tracts.estimates('41051', 'DIABETES', 2023, path)[0]['prevalence'] == 8.2
    with pytest.raises(ValueError, match='five minutes|cooldown|recent|minutes'):
        tracts.refresh(path, force=True)


def test_county_path_guard_and_read_only_queries(tmp_path, monkeypatch):
    path = tmp_path / 'county.duckdb'
    monkeypatch.setattr(tracts, 'COUNTY_PATH', path)
    with pytest.raises(ValueError, match='county database'):
        tracts.store([row()], provenance(), path)
    assert not path.exists()
    missing = tmp_path / 'missing' / 'tracts.duckdb'
    with pytest.raises(duckdb.Error):
        tracts.coverage(missing)
    assert not missing.parent.exists()


def test_fresh_cache_skips_network_and_cooldown(tmp_path, monkeypatch):
    path = tmp_path / 'tracts.duckdb'
    tracts.store([row()], provenance(), path)
    def forbidden(*a, **kw):
        pytest.fail('fresh snapshot should not fetch/reserve')
    monkeypatch.setattr(tracts, 'fetch', forbidden)
    monkeypatch.setattr(tracts, '_reserve_refresh', forbidden)
    assert tracts.refresh(path) == 1


def install_source(monkeypatch, count, pages, revision_changed=False):
    calls = []
    def get(url, params=None):
        calls.append((url, params))
        if url == tracts.METADATA_URL:
            value = metadata()
            if revision_changed and len(calls) > 1:
                value['rowsUpdatedAt'] = 124
            return value, 100
        if params['$select'] == 'count(*) as rows':
            return [{'rows': str(count)}], 10
        return deepcopy(pages[params['$offset']]), 100
    monkeypatch.setattr(tracts, '_get', get)
    return calls


@pytest.mark.parametrize('count', [3, 4])
def test_pagination_short_and_exact_boundary(monkeypatch, count):
    monkeypatch.setattr(tracts, 'PAGE_SIZE', 2)
    rows = [row(locationid=f'4105100010{i}') for i in range(count)]
    pages = {0: rows[:2], 2: rows[2:], 4: []}
    calls = install_source(monkeypatch, count, pages)
    fetched, source = tracts.fetch()
    assert len(fetched) == count and source['release_year'] == 2025
    assert source['confidence_level'] == 95
    page_calls = [p for u, p in calls if p and '$offset' in p]
    assert [p['$offset'] for p in page_calls] == ([0, 2] if count == 3 else [0, 2, 4])
    assert all(p['$order'] == 'locationid,measureid,year' for p in page_calls)


@pytest.mark.parametrize('scenario', ['short', 'duplicate', 'revision', 'count', 'bytes', 'shape'])
def test_partial_or_changed_source_rejected(monkeypatch, scenario):
    monkeypatch.setattr(tracts, 'PAGE_SIZE', 2)
    pages = {0: [row(), row(locationid='41051000102')], 2: [row(locationid='41051000103')]}
    count = 3
    if scenario == 'short': pages[0] = [row()]
    if scenario == 'duplicate': pages[2] = [row()]
    if scenario == 'count': count = tracts.MAX_ROWS + 1
    if scenario == 'bytes': monkeypatch.setattr(tracts, 'MAX_BYTES', 1)
    if scenario == 'shape': pages[0] = {'error': 'synthetic'}
    install_source(monkeypatch, count, pages, revision_changed=scenario == 'revision')
    with pytest.raises(ValueError):
        tracts.fetch()


def test_metadata_contract(monkeypatch):
    for bad in [metadata() | {'id': 'wrong'}, metadata() | {'columns': []}, metadata() | {'name': 'County release'}]:
        monkeypatch.setattr(tracts, '_get', lambda *a: (bad, 1))
        with pytest.raises(ValueError):
            tracts._metadata()


def test_download_bounds_and_redirect_rejection(monkeypatch):
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield b'x' * 2_000_001
    response = Response()
    def get(url, **kwargs):
        assert url == tracts.RESOURCE_URL
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        return response
    monkeypatch.setattr(tracts.requests, 'get', get)
    with pytest.raises(ValueError, match='byte limit'):
        tracts._get(tracts.RESOURCE_URL)
    response.status_code = 302
    with pytest.raises(ValueError, match='complete resource'):
        tracts._get(tracts.RESOURCE_URL)


@pytest.mark.parametrize('status,attempts', [(503, 3), (429, 3), (400, 1)])
def test_transient_retries_are_bounded(monkeypatch, status, attempts):
    calls = []
    def download(*args):
        calls.append(1)
        response = tracts.requests.Response()
        response.status_code = status
        raise tracts.requests.HTTPError(response=response)
    monkeypatch.setattr(tracts, '_download', download)
    monkeypatch.setattr(tracts.time, 'sleep', lambda seconds: None)
    with pytest.raises(tracts.requests.HTTPError):
        tracts._get(tracts.RESOURCE_URL)
    assert len(calls) == attempts


def test_transient_failure_then_success(monkeypatch):
    calls = []
    def download(*args):
        calls.append(1)
        if len(calls) == 1:
            raise tracts.requests.Timeout()
        return [{'public': 'synthetic'}], 1
    monkeypatch.setattr(tracts, '_download', download)
    monkeypatch.setattr(tracts.time, 'sleep', lambda seconds: None)
    assert tracts._get(tracts.RESOURCE_URL)[0] == [{'public': 'synthetic'}]
    assert len(calls) == 2
