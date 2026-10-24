from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import data, api_cache, census, navigation
from oregon_health.regions import OREGON, WASHINGTON, health_path

APP = Path(__file__).resolve().parents[1] / 'app.py'


@pytest.fixture
def saved(tmp_path, monkeypatch):
    for region in (OREGON, WASHINGTON):
        rows = [dict(stateabbr=region.abbr, locationid=code, locationname=name,
                     measureid=measure, year='2023', data_value_type='Crude prevalence',
                     data_value=10, low_confidence_limit=9, high_confidence_limit=11)
                for code, name in region.counties().items() for measure in data.MEASURES]
        data.store(rows, 'Synthetic navigation fixture', health_path(region, tmp_path), region=region)
    monkeypatch.setattr(data, 'DB_PATH', tmp_path / 'oregon.duckdb')
    monkeypatch.setattr(api_cache, 'needs_refresh', lambda *a, **kw: False)
    monkeypatch.setattr(census, 'context', lambda *a, **kw: (_ for _ in ()).throw(census.CensusKeyRequired('Test')))
    def forbidden(*a, **kw):
        pytest.fail('Navigation must not fetch unrelated sources')
    monkeypatch.setattr(requests, 'get', forbidden)
    monkeypatch.setattr(requests, 'post', forbidden)
    return tmp_path


def app(**params):
    v = AppTest.from_file(APP, default_timeout=20)
    v.query_params.update(params)
    return v.run()


def test_durable_selections_across_hidden_widget_cleanup(saved):
    v = app(county='41051')
    assert not v.exception
    assert v.selectbox(key='profile_county').value == '41051'
    v.radio(key='view_selector').set_value('compare').run()
    v.multiselect(key='compare_counties').set_value(['Lane']).run()
    v.radio(key='view_selector').set_value('areas').run()
    v.multiselect(key='area_counties').set_value(['41039', '41051']).run()
    v.run()  # Allow hidden widgets to undergo actual Streamlit cleanup.
    v.radio(key='view_selector').set_value('explore').run()
    assert not v.exception and v.selectbox(key='profile_county').value == '41051'
    assert v.query_params['compare'] == '41039'
    assert v.query_params['area'] == '41039,41051'
    v.radio(key='view_selector').set_value('compare').run()
    assert v.multiselect(key='compare_counties').value == ['Lane']
    v.radio(key='view_selector').set_value('areas').run()
    assert v.multiselect(key='area_counties').value == ['41039', '41051']
    linked = app(**dict(v.query_params))
    assert not linked.exception and linked.multiselect(key='area_counties').value == ['41039', '41051']


@pytest.mark.parametrize('view', ['areas', 'access', 'sources'])
def test_independent_views_do_not_check_or_refresh_cdc(saved, monkeypatch, view):
    def forbidden(*a, **kw):
        pytest.fail('Inactive CDC task must not check freshness or fetch data')
    monkeypatch.setattr(api_cache, 'needs_refresh', forbidden)
    monkeypatch.setattr(api_cache, 'refresh', forbidden)
    monkeypatch.setattr(census, 'context', forbidden)
    v = app(view=view, county='41051', census='1')
    assert not v.exception
    assert not any(s.key == 'health_measure' for s in v.selectbox)
    assert not any(d.key == 'full_comparison' for d in v.dataframe)
    assert not any(s.key == 'profile_county' for s in v.selectbox)


def test_external_links_and_region_switch_replace_linked_state(saved):
    v = app(county='41051', year='2023')
    prior = dict(v.query_params)
    v.selectbox(key='profile_county').set_value('41039').run()
    v.query_params.clear(); v.query_params.update(prior); v.run()
    assert not v.exception and v.selectbox(key='profile_county').value == '41051'
    v.radio(key='view_selector').set_value('areas').run()
    v.multiselect(key='area_counties').set_value(['41051']).run()
    v.radio(key='region_selector').set_value('WA').run()
    assert not v.exception and v.multiselect(key='area_counties').value == []
    assert v.query_params['state'] == 'WA' and 'area' not in v.query_params
    v.radio(key='view_selector').set_value('explore').run()
    assert v.selectbox(key='profile_county').value is None
    assert len(v.selectbox(key='profile_county').options) == 39


def test_legacy_and_invalid_view_resolution():
    assert navigation.resolve({'county': '41051'}) == ('explore', False)
    assert navigation.resolve({'compare': '41051'}) == ('compare', False)
    assert navigation.resolve({'area': ''}) == ('areas', False)
    assert navigation.resolve({'view': '<invalid>'}) == ('explore', True)


def test_invalid_view_falls_back_without_retaining_unknown_url_fields(saved):
    v = app(view='unknown', token='never-share')
    assert not v.exception and v.radio(key='view_selector').value == 'explore'
    assert v.query_params['view'] == 'explore' and 'token' not in v.query_params
    assert any('view is not supported' in w.value for w in v.warning)


def test_sidebar_shows_navigation_and_active_filters(saved):
    v=app(county='41051')
    assert not v.exception
    assert v.sidebar.radio(key='view_selector').options==list(navigation.VIEWS.values())
    assert v.sidebar.radio(key='region_selector').options==['Oregon','Washington']
    assert v.sidebar.selectbox(key='health_measure').value=='DIABETES'
    assert v.sidebar.selectbox(key='profile_county').value=='41051'
    v.radio(key='view_selector').set_value('compare').run()
    assert v.sidebar.multiselect(key='compare_counties')
    assert v.sidebar.checkbox(key='include_census')
    v.radio(key='view_selector').set_value('areas').run()
    assert v.sidebar.multiselect(key='area_counties')
    assert v.sidebar.checkbox(key='compare_areas')
