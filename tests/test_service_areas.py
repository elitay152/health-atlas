import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from oregon_health import service_areas as areas, api_cache, data
from oregon_health.service_area_ui import area_deck

APP = Path(__file__).resolve().parents[1] / 'app.py'


def test_roundtrip_deduplication_empty_and_overlap():
    codes=['41051','41039','41051']
    definition=areas.definition(codes)
    assert definition['health_atlas']['county_fips']==['41039','41051']
    assert len(definition['features'])==2
    assert areas.loads(areas.dumps(codes).encode())==['41039','41051']
    assert areas.loads(areas.dumps([]).encode())==[]
    assert areas.definition([])['features']==[]
    other=areas.definition(['41005','41051'])
    shared=set(definition['health_atlas']['county_fips']) & set(other['health_atlas']['county_fips'])
    assert shared=={'41051'}  # Overlap is county membership, never additive area/population.
    assert definition['health_atlas']['boundary_source']['boundary_vintage'] is None
    link=areas.link_params(codes)
    assert areas.parse_link(link['area'],link['area_rev'])==['41039','41051']
    empty=areas.link_params([])
    assert areas.parse_link(empty['area'],empty['area_rev'])==[]


@pytest.mark.parametrize('change',['revision','geometry','rules','duplicate','extra','bool','vintage'])
def test_incompatible_or_modified_files_rejected(change):
    value=areas.definition(['41051'])
    if change=='revision': value['health_atlas']['boundary_source']['sha256']='old'
    if change=='geometry': value['features'][0]['geometry']['coordinates']=[]
    if change=='rules': value['health_atlas']['inclusion_rule']='partial counties'
    if change=='duplicate': value['features']*=2
    if change=='extra': value['private_addresses']=['Synthetic unexpected field']
    if change=='bool': value['health_atlas']['schema_version']=True
    if change=='vintage': value['health_atlas']['boundary_source']['boundary_vintage']=2023
    with pytest.raises(ValueError): areas.loads(json.dumps(value).encode())


@pytest.mark.parametrize('raw',[b'',b'[]',b'null',b'{"a":1,"a":2}',b'{"x":NaN}',b'\xff',b'x'*250001])
def test_bounded_strict_json(raw):
    with pytest.raises(ValueError): areas.loads(raw)


def test_unknown_ids_or_boundary_changes_never_partially_load(tmp_path,monkeypatch):
    with pytest.raises(ValueError): areas.definition(['41051','41099'])
    with pytest.raises(ValueError): areas.parse_link('41051,41099',areas.source()['sha256'])
    with pytest.raises(ValueError): areas.parse_link('41051',None)
    saved=areas.dumps(['41051']).encode()
    linked=areas.link_params(['41051'])
    changed=tmp_path/'county.geojson'
    changed.write_bytes(areas.BOUNDARIES.read_bytes()+b' ')
    monkeypatch.setattr(areas,'BOUNDARIES',changed)
    with pytest.raises(ValueError): areas.loads(saved)
    with pytest.raises(ValueError): areas.parse_link(linked['area'],linked['area_rev'])


def view(tmp_path,monkeypatch,params=None):
    path=tmp_path/'health.duckdb'
    data.store(data.demo_rows(),'Synthetic fixture',path)
    monkeypatch.setattr(data,'DB_PATH',path)
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *a:False)
    app=AppTest.from_file(APP,default_timeout=20)
    app.query_params.update({'view':'areas'} | (params or {}))
    return app.run()


def test_ui_selection_share_clear_and_independence(tmp_path,monkeypatch):
    app=view(tmp_path,monkeypatch)
    assert not app.exception
    assert app.multiselect(key='area_counties').value==[]
    assert 'area' not in app.query_params
    app.multiselect(key='area_counties').set_value(['41051','41039']).run()
    assert not app.exception
    assert app.query_params['area']=='41039,41051'
    linked=dict(app.query_params)
    other=view(tmp_path,monkeypatch,linked)
    assert other.multiselect(key='area_counties').value==['41039','41051']
    # Selecting a different health measure/comparison cannot change membership.
    app.radio(key='view_selector').set_value('compare').run()
    app.selectbox(key='health_measure').select('OBESITY').run()
    app.multiselect(key='compare_counties').set_value([]).run()
    app.radio(key='view_selector').set_value('areas').run()
    assert app.multiselect(key='area_counties').value==['41051','41039']
    assert app.query_params['area']=='41039,41051'
    next(b for b in app.button if b.label=='Clear service area').click().run()
    assert not app.exception and app.query_params['area']==''
    assert app.multiselect(key='area_counties').value==[]


def test_bad_area_link_and_demo(tmp_path,monkeypatch):
    bad=view(tmp_path,monkeypatch,dict(area='41051',area_rev='old'))
    assert not bad.exception
    assert bad.multiselect(key='area_counties').value==[]
    assert any('No area was loaded' in w.value for w in bad.warning)
    assert 'area' not in bad.query_params
    demo=view(tmp_path,monkeypatch,dict(demo='1',**areas.link_params(['41051'])))
    assert not demo.exception
    assert not any(m.key=='area_counties' for m in demo.multiselect)
    assert 'area' not in demo.query_params


def test_area_still_available_without_cdc(tmp_path,monkeypatch):
    missing=tmp_path/'missing.duckdb'
    monkeypatch.setattr(data,'DB_PATH',missing)
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *a:False)
    app=AppTest.from_file(APP,default_timeout=20)
    app.query_params.update(areas.link_params(['41051']))
    app.run()
    assert not app.exception
    assert app.multiselect(key='area_counties').value==['41051']
    assert app.query_params['area']=='41051'


def test_preview_is_membership_only_and_does_not_mutate_county_cache():
    from oregon_health.map import boundaries
    before=json.dumps(boundaries(),sort_keys=True)
    deck=json.loads(area_deck(['41051']).to_json())
    rows=deck['layers'][0]['data']['features']
    assert sum(f['properties']['membership']=='Included whole county' for f in rows)==1
    assert all('prevalence' not in f['properties'] for f in rows)
    assert before==json.dumps(boundaries(),sort_keys=True)
