"""Small deterministic state fixtures; never depend on downloaded app caches."""
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, census, data, regions, status, status_ui
from oregon_health import hospital_ui, hrsa_ui, service_area_ui, tract_ui
from oregon_health.compare import comparison_rows
from oregon_health.links import parse_counties
from oregon_health.map import county_features, MISSING

APP = Path(__file__).resolve().parents[1] / 'app.py'
DATASET = api_cache.DEFAULT_DATASET
SOURCE = f'Synthetic county release | https://data.cdc.gov/d/{DATASET}'


@pytest.fixture
def state_caches(tmp_path, monkeypatch):
    base = tmp_path / 'oregon.duckdb'
    monkeypatch.setattr(data, 'DB_PATH', base)
    monkeypatch.setattr(api_cache, 'DB_PATH', base)
    monkeypatch.setattr(census, 'DB_PATH', base)
    for region in regions.REGIONS.values():
        names = region.counties()
        first = next(iter(names))
        rows = [dict(stateabbr=region.abbr, locationid=code, locationname=name,
                     measureid=measure, year='2023', data_value_type='Crude prevalence',
                     data_value=10, low_confidence_limit=9, high_confidence_limit=11)
                for code, name in names.items() for measure in data.MEASURES
                if not (code == first and measure == 'CASTHMA')]
        data.store(rows, SOURCE, regions.health_path(region, tmp_path), region=region)
        records = [(code, 10000, 100, 20, 2, None if code == first else 10, None if code == first else 1)
                   for code in names]
        census.store(records, 2023, regions.cache_dir(region, tmp_path) / 'census_2023.duckdb', region=region)
    def forbidden(*args, **kwargs):
        pytest.fail('Saved fixture views must not request live sources')
    monkeypatch.setattr(requests, 'get', forbidden)
    monkeypatch.setattr(requests, 'post', forbidden)
    return tmp_path


def view(region, **params):
    app = AppTest.from_file(APP, default_timeout=20)
    app.query_params.update(state=region.abbr, year='2023', **params)
    return app.run()


@pytest.mark.parametrize('region', list(regions.REGIONS.values()))
def test_state_views_links_missingness_and_exports(state_caches, region):
    first = next(iter(region.counties()))
    all_codes = sorted(region.counties())
    assert parse_counties(','.join(all_codes), region=region) == (all_codes, False)
    assert parse_counties(','.join(all_codes + [first]), region=region) == ([], True)
    app = view(region, county=first, compare=first, census='1')
    assert not app.exception
    assert app.selectbox(key='profile_county').value == first
    assert len(app.selectbox(key='profile_county').options) == region.county_count
    assert app.selectbox(key='region_selector').value == region.abbr
    assert any(f'{region.county_count} {region.name} counties' in item.value for item in app.caption)
    assert any('2019–2023' in item.value for item in app.caption)
    assert any(m.label == 'Population' and m.value == '10,000' for m in app.metric)
    assert any(m.label == 'Below poverty level' and m.value == 'Unavailable' for m in app.metric)
    table = next(item.value for item in app.dataframe if 'Measure' in item.value.columns)
    name = region.counties()[first]
    assert table[table.Measure == 'Current asthma'][name].iloc[0] == 'Unavailable'
    shared = dict(app.query_params)
    assert shared.get('state', 'OR') == region.abbr
    other = AppTest.from_file(APP, default_timeout=20)
    other.query_params.update(shared)
    other.run()
    assert not other.exception
    assert other.selectbox(key='profile_county').value == first
    path = regions.health_path(region, state_caches)
    with duckdb.connect(str(path), read_only=True) as db:
        contexts = {first: census.context(first, 2023, path.parent, region=region)}
        rows = comparison_rows(db, [first], 2023, SOURCE, 'synthetic date', contexts, region=region)
        assert rows.state.eq(region.abbr).all() and rows.state_fips.eq(region.state_fips).all()
        assert rows[rows.metric == 'Current asthma'].status.iloc[0] == 'unavailable'
        assert rows[rows.metric == 'Below poverty level'].status.iloc[0] == 'unavailable'
        assert rows[rows.confidence == 95].period_start.eq(2023).all()
        assert rows[rows.confidence == 90].period_start.eq(2019).all()
        assert rows[rows.confidence == 90].period_end.eq(2023).all()
        assert 'population_universe' in rows.to_csv(index=False)
        frame = data.comparison(db, 'CASTHMA', 2023, region=region)
    features = county_features(frame, [], region=region)['features']
    assert len(features) == region.county_count
    missing = next(f for f in features if f['properties']['fips'] == first)
    assert missing['properties']['color'] == MISSING
    assert missing['properties']['estimate'] == 'No estimate available'
    report = status.health_report(path, 2023, DATASET, region=region)
    assert report['coverage']['Missing estimates'].sum() == 1
    assert report['missing'].iloc[0]['FIPS'] == first


def test_washington_never_renders_oregon_only_sources(state_caches, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Washington must not render/query Oregon-only workflows')
    for module, name in [(tract_ui, 'render_tracts'),
                         (status_ui, 'render_access_quality')]:
        monkeypatch.setattr(module, name, forbidden)
    app = view(regions.WASHINGTON, county='53033', compare='41051,53033', local='1',
               tract='41051000101', area='41051', area_compare='1', area_b='41039', demo='1')
    assert not app.exception
    assert app.toggle(key='demo_mode').disabled and not app.toggle(key='demo_mode').value
    assert app.query_params['compare'] == '53033'
    assert not any(key in app.query_params for key in ['local', 'tract', 'area', 'area_compare', 'area_b'])
    assert any('Oregon-only' in item.value for item in app.warning)
    assert any('Tract, service-area summary and area-comparison workflows remain Oregon-only' in item.value for item in app.caption)


def test_switching_states_resets_selection_and_demo(state_caches):
    app = view(regions.OREGON, county='41051', compare='41051', demo='1')
    assert not app.exception and app.toggle(key='demo_mode').value
    app.selectbox(key='region_selector').set_value('WA').run()
    assert not app.exception
    assert app.query_params['state'] == 'WA'
    assert not app.toggle(key='demo_mode').value
    assert app.selectbox(key='profile_county').value is None
    assert all(name in regions.WASHINGTON.counties().values() for name in app.multiselect(key='compare_counties').value)
    app.selectbox(key='profile_county').set_value('53033').run()
    assert app.query_params['county'] == '53033'
    app.selectbox(key='region_selector').set_value('OR').run()
    assert not app.exception
    assert 'state' not in app.query_params
    assert app.selectbox(key='profile_county').value is None
    assert len(app.selectbox(key='profile_county').options) == 36


def test_washington_cdc_and_census_outages_preserve_independence(state_caches, monkeypatch):
    path = regions.health_path(regions.WASHINGTON, state_caches)
    with duckdb.connect(str(path)) as db:
        db.execute('UPDATE provenance SET imported_at = ?', [datetime.now() - timedelta(days=2)])
    def offline(*args, **kwargs):
        raise requests.ConnectionError('Synthetic outage')
    monkeypatch.setattr(api_cache, 'fetch', offline)
    monkeypatch.setattr(census, 'context', offline)
    app = view(regions.WASHINGTON, county='53033', compare='53033', census='1')
    assert not app.exception
    assert any('previously downloaded' in item.value for item in app.warning)
    assert any('Census context is currently unavailable' in item.value for item in app.info)
    assert any(m.label == 'Diabetes' and m.value == '10.0%' for m in app.metric)
    app.run()
    assert not app.exception
    # An outage on the first Washington load must not fall back to Oregon.
    path.unlink()
    empty = view(regions.WASHINGTON)
    assert not empty.exception
    assert not any(item.key == 'health_measure' for item in empty.selectbox)
    assert any('No CDC data' in item.value for item in empty.info)
    assert empty.query_params['state'] == 'WA'


@pytest.mark.parametrize('region', list(regions.REGIONS.values()))
def test_live_audit_contract_with_synthetic_source_records(region):
    from oregon_health.verification import audit
    codes = list(region.counties())
    first = codes[0]
    rows = [dict(stateabbr=region.abbr, locationid=first, locationname=region.counties()[first],
                 measureid='DIABETES', year='2023', data_value_type='Crude prevalence',
                 data_value=10, low_confidence_limit=9, high_confidence_limit=11)]
    header = ['NAME', *census.FIELDS, 'state', 'county']
    payload = [header, *[[f'{name} County, {region.name}', '10000', None, '20', '2',
                          None if code == first else '10', '1', region.state_fips, code[2:]]
                         for code, name in region.counties().items()]]
    report = audit(region, rows, payload)
    assert report['counties'] == region.county_count
    assert report['health_coverage'][0]['available'] == 1
    assert len(report['health_coverage'][0]['missing_fips']) == region.county_count - 1
    assert len(report['census_coverage'][0]['missing_moe_fips']) == region.county_count
    assert report['census_coverage'][2]['missing_fips'] == [first]
    payload[1][0] = 'Incorrect County, ' + region.name
    with pytest.raises(ValueError, match='county names'):
        audit(region, rows, payload)
    payload[1][0] = f'{region.counties()[first]} County, {region.name}'
    rows[0]['year'] = '2022'
    with pytest.raises(ValueError, match='estimate years'):
        audit(region, rows, payload)
