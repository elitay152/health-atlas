"""Two-state membership definitions stay independent of health/summary sources."""
import json
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, data, regions, service_areas as areas, service_area_ui, service_area_summary
from oregon_health.service_area_ui import area_deck
from test_washington_hospitals import app_with_wa


@pytest.mark.parametrize('region,count', [(regions.OREGON,36), (regions.WASHINGTON,39)])
def test_all_counties_empty_state_and_revision_roundtrips(region,count):
    codes=sorted(region.counties())
    raw=areas.dumps(codes,region=region).encode()
    value=json.loads(raw)
    assert len(raw)<areas.MAX_BYTES and len(value['features'])==count
    assert value['health_atlas']['state_fips']==region.state_fips
    assert value['health_atlas']['boundary_source']['artifact']==region.boundary_file
    assert areas.loads(raw,region=region)==codes
    params=areas.link_params(codes,region=region)
    assert areas.parse_link(params['area'],params['area_rev'],region=region)==codes
    assert params.get('state','OR')==region.abbr
    assert areas.loads(areas.dumps([],region=region).encode(),region=region)==[]
    assert areas.normalize(codes+[codes[0]],region=region)==codes
    assert areas.parse_link('',params['area_rev'],region=region)==[]
    other=regions.WASHINGTON if region==regions.OREGON else regions.OREGON
    with pytest.raises(ValueError):areas.loads(raw,region=other)
    with pytest.raises(ValueError):areas.loads(areas.dumps([],region=region).encode(),region=other)
    with pytest.raises(ValueError):areas.parse_link('',areas.source(region=other)['sha256'],region=region)
    with pytest.raises(ValueError):areas.parse_link(params['area']+',99999',params['area_rev'],region=region)
    deck=json.loads(area_deck(codes[:2],region=region).to_json())
    features=deck['layers'][0]['data']['features']
    assert len(features)==count
    assert sum(f['properties']['membership']=='Included whole county' for f in features)==2
    assert deck['initialViewState']['longitude']==region.center[0]
    assert all('prevalence' not in f['properties'] for f in features)


@pytest.mark.parametrize('change',['state','geometry','revision','metadata','rule'])
def test_washington_import_rejects_tampering(change):
    value=areas.definition(['53033'],region=regions.WASHINGTON)
    if change=='state':value['health_atlas']['state_fips']='41'
    if change=='geometry':value['features'][0]['geometry']['coordinates']=[]
    if change=='revision':value['health_atlas']['boundary_source']['sha256']='old'
    if change=='metadata':value['health_atlas']['extra']='unexpected'
    if change=='rule':value['health_atlas']['inclusion_rule']='partial county'
    with pytest.raises(ValueError):areas.loads(json.dumps(value).encode(),region=regions.WASHINGTON)


def test_washington_area_links_state_switch_and_source_independence(tmp_path,monkeypatch):
    app,_=app_with_wa(tmp_path,monkeypatch)
    def forbidden(*args,**kwargs):pytest.fail('Washington definitions must not invoke Oregon-only summaries')
    monkeypatch.setattr(service_area_summary,'summarize',forbidden)
    monkeypatch.setattr(service_area_ui,'render_comparison',forbidden)
    monkeypatch.setattr(requests,'get',forbidden)
    monkeypatch.setattr(requests,'post',forbidden)
    app.query_params['view']='areas'
    app.run()
    assert not app.exception and 'area' not in app.query_params
    assert len(app.multiselect(key='area_counties').options)==39
    assert not any(c.key in {'compare_areas','area_compare_enabled'} for c in app.checkbox)
    app.multiselect(key='area_counties').set_value(['53033','53061']).run()
    assert not app.exception
    assert app.query_params['state']=='WA' and app.query_params['area']=='53033,53061'
    assert app.query_params['area_rev']==areas.source(region=regions.WASHINGTON)['sha256']
    linked=dict(app.query_params)
    other=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    other.query_params.update(linked);other.run()
    assert not other.exception and other.multiselect(key='area_counties').value==['53033','53061']
    app.selectbox(key='view_selector').select('compare').run()
    app.selectbox(key='health_measure').set_value('OBESITY').run()
    app.multiselect(key='compare_counties').set_value([]).run()
    app.selectbox(key='view_selector').select('areas').run()
    assert app.multiselect(key='area_counties').value==['53033','53061']
    app.button(key='area_clear').click().run()
    assert app.query_params['area']=='' and app.multiselect(key='area_counties').value==[]
    # Switching regions must clear membership and its revision.
    monkeypatch.setattr(service_area_summary,'summarize',lambda *a,**kw:None)
    monkeypatch.setattr(service_area_ui,'render_comparison',lambda *a,**kw:{})
    app.selectbox(key='region_selector').set_value('OR').run()
    assert not app.exception and 'state' not in app.query_params
    assert len(app.multiselect(key='area_counties').options)==36
    assert app.multiselect(key='area_counties').value==[]


def test_washington_area_is_available_without_cdc(tmp_path,monkeypatch):
    app,_=app_with_wa(tmp_path,monkeypatch,health=False,cached=False)
    params=areas.link_params(['53011','53033'],region=regions.WASHINGTON)
    app.query_params.update(params | {'view':'areas'});app.run()
    assert not app.exception
    assert app.query_params['state']=='WA' and app.query_params['area']=='53011,53033'
    assert app.multiselect(key='area_counties').value==['53011','53033']
    assert not any(s.key=='health_measure' for s in app.selectbox)
    assert any('Area comparisons are currently available for Oregon only' in i.value for i in app.info)
