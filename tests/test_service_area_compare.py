import csv
from copy import deepcopy
import io
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from oregon_health import service_areas, data, api_cache
from oregon_health.service_area_compare import comparison, assemble, export_csv, export_rows, link_params
from oregon_health.service_area_compare_ui import health_chart
from oregon_health.service_area_summary import summarize


def fixtures(root):
    rows=data.demo_rows()
    for r in rows:r['year']='2023'
    data.store(rows,'Synthetic source',root/'oregon.duckdb')


def test_overlap_missing_states_csv_and_reproducibility(tmp_path):
    fixtures(tmp_path)
    r=comparison(['41039','41051','41039'],['41051','41005'],2023,2023,cache_dir=tmp_path)
    assert r['shared_counties']==['41051']
    assert r['shared_cms_ids'] is None and r['shared_hrsa_designations'] is None
    assert any('share counties' in w for w in r['warnings'])
    assert any('unavailable' in w for w in r['warnings'])
    assert r['periods_match']
    rows=export_rows(r)
    assert sum(x['metric']=='DIABETES' for x in rows)==4
    shared=[x for x in rows if x['county_fips']=='41051' and x['metric']=='DIABETES']
    assert len(shared)==2 and all(x['shared_county'] for x in shared)
    assert all(x['confidence']==95 and x['unit']=='percent' and x['low'] is not None for x in shared)
    csv_rows=list(csv.DictReader(io.StringIO(export_csv(r))))
    assert any(x['source_state']=='Not loaded' and x['value']=='' for x in csv_rows)
    assert all('source_provenance' in x and 'comparison_warnings' in x for x in csv_rows)
    assert export_csv(r)==export_csv(assemble(deepcopy(r['areas']['Area A']),deepcopy(r['areas']['Area B'])))
    assert 'opportunity_score' not in r and 'population_difference' not in r


def test_mismatched_periods_and_changed_boundary_rejected(tmp_path):
    fixtures(tmp_path)
    a=summarize(['41039'],2023,cache_dir=tmp_path)
    b=summarize(['41051'],2022,cache_dir=tmp_path)
    r=assemble(a,b)
    assert not r['periods_match']
    assert any('years and ACS periods differ' in w for w in r['warnings'])
    assert any(h['status']=='No saved estimate' for h in b['health'])
    b['area']['health_atlas']['boundary_source']['sha256']='old'
    with pytest.raises(ValueError):assemble(a,b)
    with pytest.raises(ValueError):comparison([],['41051'],2023,2023,cache_dir=tmp_path)


def test_shared_access_ids_and_cross_county_designations(tmp_path):
    fixtures(tmp_path)
    a=summarize(['41039'],2023,cache_dir=tmp_path)
    b=summarize(['41051'],2023,cache_dir=tmp_path)
    for r in (a,b):
        for s in r['sources']:s['state']='Loaded'
        r['hospitals']['facility_ids']=['003801']
        r['hospitals']['accepted_location_ids']=['003801']
        r['shortages']=[dict(discipline='Primary Care',category='Geographic',status='Designated',
             designation_ids=['1410000001'],designations=1,components=2,without_geometry=0)]
    r=assemble(a,b)
    assert r['shared_counties']==[]
    assert r['shared_cms_ids']==['003801'] and r['shared_accepted_point_ids']==['003801']
    assert r['shared_hrsa_designations']==[dict(discipline='Primary Care',designation_id='1410000001')]
    assert any('span counties' in w for w in r['warnings'])
    b['sources'][0]['provenance']={'source':'different saved revision'}
    assert any('snapshots/periods differ' in w for w in assemble(a,b)['warnings'])


def test_safe_csv_and_equal_chart_scales(tmp_path):
    fixtures(tmp_path)
    r=comparison(['41039'],['41051'],2023,2023,cache_dir=tmp_path)
    r['areas']['Area A']['health'][0]['definition']='=SYNTHETIC()'
    text=export_csv(r)
    assert "'=SYNTHETIC()" in text
    for a in r['areas'].values():
        chart=health_chart(a['health']).to_dict()
        assert all(layer['encoding']['x']['scale']['domain']==[0,100] for layer in chart['layer'])


def app(tmp_path,monkeypatch,params):
    fixtures(tmp_path)
    monkeypatch.setattr(data,'DB_PATH',tmp_path/'oregon.duckdb')
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *a:False)
    v=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    v.query_params.update(params|dict(year='2023'))
    return v.run()


def test_links_empty_selection_roundtrip_and_independence(tmp_path,monkeypatch):
    params=service_areas.link_params(['41039','41051'])|link_params(['41005','41051'],2023)
    v=app(tmp_path,monkeypatch,params)
    assert not v.exception
    assert v.checkbox(key='compare_areas').value
    assert v.multiselect(key='area_b_counties').value==['41005','41051']
    assert v.query_params['area_b']=='41005,41051'
    other=app(tmp_path,monkeypatch,dict(v.query_params))
    assert not other.exception and other.multiselect(key='area_b_counties').value==['41005','41051']
    v.selectbox(key='area_b_year').select(2022).run()
    assert not v.exception and v.query_params['area_b_year']=='2022'
    assert any('years and ACS periods differ' in w.value for w in v.warning)
    v.multiselect(key='area_b_counties').set_value([]).run()
    assert not v.exception and v.query_params['area_b']==''
    assert any('at least one county' in i.value for i in v.info)
    assert v.query_params['area']=='41039,41051'


def test_invalid_links_and_demo(tmp_path,monkeypatch):
    params=service_areas.link_params(['41039'])|dict(area_compare='1',area_b='41051',area_b_rev='old',area_b_year='invalid')
    v=app(tmp_path,monkeypatch,params)
    assert not v.exception and v.multiselect(key='area_b_counties').value==[]
    assert any('No Area B was loaded' in w.value for w in v.warning)
    assert any('year is unsupported' in w.value for w in v.warning)
    demo=app(tmp_path,monkeypatch,params|dict(demo='1'))
    assert not demo.exception
    assert 'area_compare' not in demo.query_params and 'area_b' not in demo.query_params


@pytest.mark.parametrize('requested', ['²²²²', '9999', '-123', '2023.0', '２０２３'])
def test_malformed_area_year_links_fall_back_safely(tmp_path, monkeypatch, requested):
    params = service_areas.link_params(['41039']) | dict(area_compare='1', area_b_year=requested)
    v = app(tmp_path, monkeypatch, params)
    assert not v.exception
    assert v.selectbox(key='area_b_year').value == 2023
    assert v.query_params['area_b_year'] == '2023'
    assert any('year is unsupported' in warning.value for warning in v.warning)
