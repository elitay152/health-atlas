"""Washington area comparisons preserve region, periods, overlap and missingness."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import shutil

import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import data, regions, service_areas, service_area_compare
from oregon_health.service_area_compare import comparison, assemble, export_csv, export_rows, link_params
from oregon_health.service_area_compare_ui import health_chart
from oregon_health.service_area_summary import summarize
from test_washington_area_summary import snapshots, FILES
from test_service_area_summary import health as or_health, cms as or_cms

WA=regions.WASHINGTON


def test_overlap_csv_json_periods_and_common_scales(snapshots,monkeypatch):
    def forbidden(*a,**kw):pytest.fail('Comparisons must not request sources')
    monkeypatch.setattr(requests,'get',forbidden);monkeypatch.setattr(requests,'post',forbidden)
    before={p.name:p.read_bytes() for p in snapshots.iterdir()}
    report=comparison(['53033'],['53033','53061'],2023,2023,cache_dir=snapshots,region=WA)
    assert report['state']=='WA' and report['state_fips']=='53'
    assert report['shared_counties']==['53033']
    assert report['shared_cms_ids']==['005001'] and report['shared_accepted_point_ids']==['005001']
    assert report['shared_hrsa_designations']==[dict(discipline='Primary Care',designation_id='1530000001')]
    assert report['areas']['Area A']['population']['estimate']==100
    assert report['areas']['Area A']['population']['moe90']==10
    assert report['areas']['Area B']['population']['estimate']==300
    assert report['areas']['Area B']['population']['moe90'] is None
    assert any('share counties' in w for w in report['warnings'])
    assert before=={p.name:p.read_bytes() for p in snapshots.iterdir()}
    rows=list(csv.DictReader(io.StringIO(export_csv(report))))
    assert all(r['state']=='WA' and r['state_fips']=='53' for r in rows)
    assert any(r['county_fips']=='53033' and r['shared_county']=='True' and r['metric']=='DIABETES' for r in rows)
    assert any(r['metric']=='DIABETES' and r['confidence']=='95' and r['unit']=='percent' for r in rows)
    assert any(r['metric']=='Area population' and r['confidence']=='90' and r['period_start']=='2019' for r in rows)
    json.dumps(report,allow_nan=False)
    for area in report['areas'].values():
        chart=health_chart(area['health']).to_dict()
        assert all(layer['encoding']['x']['scale']['domain']==[0,100] for layer in chart['layer'])
    assert 'opportunity_score' not in report and 'population_difference' not in report
    changed=deepcopy(report);changed['areas']['Area A']['health'][0]['definition']='=SYNTHETIC()'
    assert "'=SYNTHETIC()" in export_csv(changed)


def test_disjoint_counties_can_share_designations_and_different_years(snapshots):
    report=comparison(['53033'],['53061'],2023,2023,cache_dir=snapshots,region=WA)
    assert report['shared_counties']==[] and report['shared_cms_ids']==[]
    assert report['shared_hrsa_designations'] and any('span counties' in w for w in report['warnings'])
    report=comparison(['53033'],['53061'],2023,2022,cache_dir=snapshots,region=WA)
    assert not report['periods_match'] and any('years and ACS periods differ' in w for w in report['warnings'])
    assert report['areas']['Area B']['population']['estimate'] is None
    assert all(h['status']=='No saved estimate' for h in report['areas']['Area B']['health'])
    assert report['areas']['Area B']['hospitals']['listed']==1


def test_foreign_reports_and_caches_and_missing_overlap_are_rejected(snapshots,tmp_path):
    report=comparison(['53033'],['53061'],2023,2023,cache_dir=snapshots,region=WA)
    a=report['areas']['Area A'];b=report['areas']['Area B']
    with pytest.raises(ValueError):assemble(a,b)
    bad=deepcopy(b);bad['state_fips']='41'
    with pytest.raises(ValueError):assemble(a,bad,region=WA)
    bad=deepcopy(b);bad['area']=service_areas.definition(['41051'])
    with pytest.raises(ValueError):assemble(a,bad,region=WA)
    other=tmp_path/'foreign';other.mkdir();or_cms(other)
    shutil.copyfile(other/'hospitals.duckdb',snapshots/FILES['CMS hospitals'])
    report=comparison(['53033'],['53061'],2023,2023,cache_dir=snapshots,region=WA)
    assert report['shared_cms_ids'] is None and report['shared_accepted_point_ids'] is None
    assert any('incompatible sources' in w for w in report['warnings'])
    assert all(r['hospitals']['listed'] is None for r in report['areas'].values())
    assert any(r['metric']=='CMS-listed hospitals' and r['value'] is None for r in export_rows(report))
    with pytest.raises(ValueError):comparison(['41051'],['53033'],2023,2023,region=WA)


def app(snapshots,monkeypatch,params=None):
    monkeypatch.setattr(data,'DB_PATH',snapshots.parents[1]/'oregon.duckdb')
    def forbidden(*a,**kw):pytest.fail('Area view must never fetch sources')
    monkeypatch.setattr(requests,'get',forbidden);monkeypatch.setattr(requests,'post',forbidden)
    v=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    v.query_params.update(service_areas.link_params(['53033'],region=WA)|link_params(['53033','53061'],2023,region=WA)|{'view':'areas','year':'2023'}|(params or {}))
    return v.run()


def test_ui_links_sidebar_navigation_and_state_transition(snapshots,monkeypatch):
    v=app(snapshots,monkeypatch)
    assert not v.exception and v.checkbox(key='compare_areas').value
    assert v.multiselect(key='area_b_counties').value==['53033','53061']
    assert v.query_params['state']=='WA' and v.query_params['area_b']=='53033,53061'
    assert any('Shared counties: King' in i.value for i in v.info)
    assert any('view_selector'==r.key for r in v.sidebar.radio)
    assert v.sidebar.multiselect(key='area_counties') and v.sidebar.multiselect(key='area_b_counties')
    v.radio(key='view_selector').set_value('sources').run()
    v.radio(key='view_selector').set_value('areas').run()
    assert not v.exception and v.multiselect(key='area_b_counties').value==['53033','53061']
    linked=app(snapshots,monkeypatch,dict(v.query_params))
    assert not linked.exception and linked.multiselect(key='area_b_counties').value==['53033','53061']
    v.multiselect(key='area_b_counties').set_value([]).run()
    assert not v.exception and v.query_params['area_b']=='' and v.query_params['area']=='53033'
    or_health(snapshots.parents[1])
    v.radio(key='region_selector').set_value('OR').run()
    assert not v.exception and 'state' not in v.query_params
    assert v.multiselect(key='area_counties').value==[] and not v.checkbox(key='compare_areas').value


@pytest.mark.parametrize('params',[
    {'area_b':'41051'}, {'area_b_rev':'old'}, {'area_b_year':'²²²²'}, {'area_b_year':'9999'}])
def test_bad_washington_links_fall_back_safely(snapshots,monkeypatch,params):
    v=app(snapshots,monkeypatch,params)
    assert not v.exception
    if 'area_b_year' in params:
        assert v.selectbox(key='area_b_year').value==2023
        assert any('year is unsupported' in w.value for w in v.warning)
    else:
        assert v.multiselect(key='area_b_counties').value==[]
        assert any('No Area B was loaded' in w.value for w in v.warning)
    assert v.query_params['state']=='WA' and v.query_params['area']=='53033'
