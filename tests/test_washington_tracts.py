"""Washington tract ingestion, immutable geography, isolation and UI contracts."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import duckdb
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache,census,data,regions,tracts,tract_boundaries as tb
from oregon_health.links import parse_tract,link_params
from oregon_health.tract_ui import tract_deck,selected_tract,MAX_ZOOM
from test_tracts import row,provenance,metadata

WA=regions.WASHINGTON
CODE='53033000101'


def wa_row(**kw):
    return row(locationid=CODE,countyfips='53033',countyname='King',stateabbr='WA')|kw


def test_source_query_and_normalization_are_region_bound(monkeypatch):
    calls=[]
    def get(url,params=None):
        if url==tracts.METADATA_URL:return metadata(),100
        calls.append(params)
        if params['$select']=='count(*) as rows':return [{'rows':'1'}],10
        return [wa_row()],100
    monkeypatch.setattr(tracts,'_get',get)
    rows,p=tracts.fetch(region=WA)
    assert len(rows)==1 and p['state_fips']=='53'
    assert all("stateabbr='WA'" in c['$where'] and "'OR'" not in c['$where'] for c in calls)
    assert tracts.normalize(rows,region=WA)[0][0]==CODE
    with pytest.raises(ValueError):tracts.normalize(rows)
    with pytest.raises(ValueError):tracts.normalize([row()],region=WA)
    with pytest.raises(ValueError):tracts.normalize([wa_row(countyfips='53061')],region=WA)


def test_state_provenance_guard_and_failed_publication(tmp_path,monkeypatch):
    path=tmp_path/'tracts.duckdb'
    tracts.store([wa_row()],provenance(),path,region=WA)
    assert tracts.coverage(path,region=WA)['provenance']['state_fips']=='53'
    assert tracts.estimates('53033','DIABETES',2023,path,region=WA)[0]['prevalence']==8.2
    before=path.read_bytes()
    for action in [lambda:tracts.coverage(path),lambda:tracts.needs_refresh(path),
                   lambda:tracts.store([row()],provenance(),path),lambda:tracts.refresh(path,force=True)]:
        with pytest.raises(ValueError):action()
    assert before==path.read_bytes()
    with monkeypatch.context() as ctx:
        ctx.setattr(tracts.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('Synthetic disk failure')))
        with pytest.raises(OSError):tracts.store([wa_row()],provenance(),path,region=WA)
    assert before==path.read_bytes()
    legacy=tmp_path/'or.duckdb';tracts.store([row()],provenance(),legacy)
    with duckdb.connect(str(legacy)) as db:
        p=json.loads(db.execute('SELECT payload FROM tract_provenance').fetchone()[0]);p.pop('state');p.pop('state_fips')
        db.execute('UPDATE tract_provenance SET payload=?',[json.dumps(p)])
    assert tracts.coverage(legacy)['provenance']['rows']==1
    with pytest.raises(ValueError):tracts.coverage(legacy,region=WA)


def test_bundled_washington_geometry_join_and_fixed_scale():
    manifest=tb.provenance(region=WA);collection=tb.boundaries(region=WA)
    assert len(collection['features'])==1772
    assert {f['properties']['county_fips'] for f in collection['features']}==set(WA.counties())
    assert manifest['state_fips']=='53' and manifest['scale']=='1:500,000'
    assert manifest['geojson_sha256']==hashlib.sha256((tb.ASSETS/'washington-tracts-2023.geojson').read_bytes()).hexdigest()
    with pytest.raises(ValueError):tb.validate(collection)
    with pytest.raises(ValueError):tb.boundaries('41051',region=WA)
    rows=[dict(tract_fips=CODE,county_fips='53033',measure='DIABETES',year=2023,prevalence=None,low=None,high=None)]
    joined,report=tb.join_estimates(rows,'53033',region=WA)
    assert report['matched_tracts']==1 and report['unavailable_estimates']==[CODE]
    assert report['boundaries_without_record']
    deck=json.loads(tract_deck(joined,region=WA).to_json())
    assert deck['initialViewState']['zoom']<=MAX_ZOOM
    assert deck['views'][0]['controller'] is False and deck['layers'][0]['id']=='washington-tracts'
    event={'selection':{'objects':{'washington-tracts':[{'properties':{'geoid':CODE}}]}}}
    assert selected_tract(event,{CODE},region=WA)==CODE
    assert selected_tract(event,{CODE}) is None
    with pytest.raises(ValueError):tb.join_estimates(rows,'53033',region=WA,estimate_boundary_year=2020)


def test_washington_links_reject_other_states_and_counties():
    assert parse_tract(CODE,'53033',region=WA)==(CODE,False)
    assert parse_tract(CODE,'41051')[1]
    assert parse_tract('41051000101','53033',region=WA)[1]
    p=link_params([],2023,'DIABETES',False,False,'53033',local=True,tract=CODE,tract_year=2023,region=WA)
    assert p['state']=='WA' and p['local']=='1' and p['tract']==CODE


def test_ui_sidebar_links_missingness_and_no_source_requests(tmp_path,monkeypatch):
    base=tmp_path/'oregon.duckdb';root=regions.cache_dir(WA,tmp_path)
    monkeypatch.setattr(data,'DB_PATH',base);monkeypatch.setattr(tracts,'DB_PATH',tmp_path/'tracts.duckdb')
    rows=[dict(stateabbr='WA',locationid='53033',locationname='King',measureid=m,year='2023',data_value_type='Crude prevalence',data_value=10,low_confidence_limit=9,high_confidence_limit=11) for m in data.MEASURES]
    data.store(rows,'Synthetic test source',root/'counties.duckdb',region=WA)
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *a,**kw:False)
    monkeypatch.setattr(census,'context',lambda *a,**kw:(_ for _ in ()).throw(census.CensusKeyRequired()))
    tracts.store([wa_row(data_value=None,low_confidence_limit=None,high_confidence_limit=None,data_value_footnote='Synthetic unavailable')],
        provenance()|dict(source_url=tracts.RESOURCE_URL,recommended_boundary_year=2023),tracts.tract_path(WA),region=WA)
    def forbidden(*a,**kw):pytest.fail('Saved tract views must not request sources')
    monkeypatch.setattr(requests,'get',forbidden);monkeypatch.setattr(tracts,'refresh',forbidden)
    v=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    v.query_params.update(state='WA',view='explore',county='53033',local='1',tract=CODE,tract_year='2023')
    v.run();assert not v.exception
    assert v.sidebar.selectbox(key='tract_selection').value==CODE
    assert any('Source footnote: Synthetic unavailable' in t.value for t in v.text)
    assert v.query_params['tract']==CODE and v.query_params['state']=='WA'
    v.radio(key='view_selector').set_value('sources').run()
    v.radio(key='view_selector').set_value('explore').run()
    assert not v.exception and v.selectbox(key='tract_selection').value==CODE
    assert v.query_params['local']=='1'


def test_washington_outage_retains_its_cache_without_oregon_fallback(tmp_path,monkeypatch):
    from oregon_health.status import last_refresh
    monkeypatch.setattr(tracts,'DB_PATH',tmp_path/'tracts.duckdb')
    tracts.store([row()],provenance())
    with pytest.raises(duckdb.Error):tracts.coverage(region=WA)
    assert not (tmp_path/'regions').exists()
    path=tracts.tract_path(WA)
    tracts.store([wa_row()],provenance(),region=WA)
    before=path.read_bytes()
    def offline(**kw):
        assert kw['region']==WA
        raise requests.ConnectionError('Synthetic outage')
    monkeypatch.setattr(tracts,'fetch',offline)
    with pytest.raises(requests.ConnectionError):tracts.refresh(force=True,region=WA)
    assert path.read_bytes()==before
    assert last_refresh(path,tracts.DATASET)['outcome']=='failed'
    assert tracts.coverage()['provenance']['state_fips']=='41'
    with pytest.raises(ValueError):tracts.refresh(force=True,region=WA)
