from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import hrsa
from oregon_health.api_cache import RefreshLimited
from oregon_health.hrsa_ui import designation_deck,export_csv,filter_records


def feature(oid=1, discipline="Primary Care", status="D", point=False):
    return {"type":"Feature","properties":{
        "OBJECTID":oid,"HPSA_SOURCE_ID":"141000000I","HPSA_NM":"Synthetic designation",
        "HPSA_TYP_CD":"RHC" if point else "Hpsa Pop",
        "HPSA_TYP_DESC":"Rural Health Clinic" if point else "HPSA Population",
        "HPSA_STATUS_CD":status,"HPSA_STATUS_DESC":hrsa.STATUSES[status],
        "DISCIPLINE_CLASS_DESC":discipline,"HPSA_DESIGNATION_DT_TXT":"2020/01/01",
        "HPSA_DESIG_LAST_UPD_DT_TXT":"2026/10/01","HPSA_WITHDRAWAL_DT_TXT":"2026/10/02" if status=="W" else None,
        "DW_RECORD_CREATE_DT_TXT":"2026/10/21","CMN_STATE_COUNTY_FIPS_CD":"41051",
        "PRIMARY_STATE_FIPS_CD":"41","CMN_STATE_FIPS_CD":"41","STATE_FIPS_CD":"41",
        "STATE_COUNTY_FIPS_CD":"41051","GEO_ID":"41051000100","COMP_TYP_CD":"CT",
        "COMP_NM":"Synthetic tract","HPSA_POPULATION_TYP_DESC":"Low income population",
    },"geometry":{"type":"Point","coordinates":[-122.65,45.52]} if point else {
        "type":"Polygon","coordinates":[[[-122.7,45.5],[-122.6,45.5],[-122.6,45.6],[-122.7,45.5]]]}}


def records():
    a=feature();b=feature(2,status="P");c=feature(3,status="W",point=True)
    b["geometry"]=None
    return hrsa.normalize([a,b],"Primary Care",11,False)+hrsa.normalize([c],"Primary Care",9,True)


def provenance():
    return dict(source_url=hrsa.SOURCE_URL,service_url=hrsa.BASE,fetched_at=datetime.now(timezone.utc).isoformat(),
                generalization_degrees=0.005,layers=[dict(layer=11,name="Synthetic components",features=2)])


def test_normalize_preserves_alphanumeric_ids_components_status_and_missing_geometry():
    rows=records()
    assert len(rows)==3 and len({r['designation_id'] for r in rows})==1
    assert rows[0]["designation_id"]=="141000000I" and rows[0]["geography_id"]=="41051000100"
    assert rows[0]["category"]=="Population group" and rows[1]["geometry"] is None
    assert rows[2]["category"]=="Facility" and rows[2]["status"]=="Withdrawn"
    assert rows[2]["withdrawn"]=="2026-10-02"
    f=feature();f["properties"]["STATE_COUNTY_FIPS_CD"]="XXXXX"
    assert hrsa.normalize([f],"Primary Care",11,False)[0]["county_fips"] is None


@pytest.mark.parametrize("field,value",[("OBJECTID","1"),("HPSA_SOURCE_ID",1410000001),
    ("HPSA_STATUS_DESC","Changed meaning"),("DISCIPLINE_CLASS_DESC","Dental Health"),
    ("STATE_FIPS_CD","53"),("HPSA_TYP_CD","Unknown"),("DW_RECORD_CREATE_DT_TXT",None)])
def test_normalize_rejects_invalid_source_records(field,value):
    f=feature();f["properties"][field]=value
    with pytest.raises(ValueError):hrsa.normalize([f],"Primary Care",11,False)


def test_rejects_duplicate_object_ids_and_invalid_geometries():
    with pytest.raises(ValueError,match="duplicate"):hrsa.normalize([feature(),feature()],"Primary Care",11,False)
    for geometry in [{"type":"Point","coordinates":[-122,45]},
                     {"type":"Polygon","coordinates":[[[-122,45],[-122,46]]]},
                     {"type":"Polygon","coordinates":[[[float('nan'),45],[-122,46],[-121,46],[float('nan'),45]]]}]:
        f=feature();f['geometry']=geometry
        with pytest.raises(ValueError):hrsa.normalize([f],"Primary Care",11,False)


def test_bounded_id_batches_validate_schema_and_truncation(monkeypatch):
    calls=[];truncated=[False]
    def get(path,params=None):
        calls.append((path,params))
        layer=int(path.split('/')[1]);point=layer in [1,5,9]
        discipline=next(d for d,ids in hrsa.DISCIPLINES.items() if layer in ids)
        if not path.endswith('/query'):
            return dict(name=f"{discipline} HPSA - {'points' if point else 'component polygons'}",
                        fields=[{'name':k} for k in hrsa.COMMON+(hrsa.POINT if point else hrsa.COMPONENT)])
        if params.get('returnIdsOnly'):return {'objectIds':[1]}
        return {'features':[] if truncated[0] else [feature(discipline=discipline,point=point)]}
    monkeypatch.setattr(hrsa,'_get',get)
    rows,p=hrsa.fetch()
    assert len(rows)==6 and len(p['layers'])==6
    assert all(c[1]['objectIds']=='1' and c[1]['outSR']=='4326' for c in calls if c[1] and 'objectIds' in c[1])
    assert any("STATE_FIPS_CD = '41'"==c[1].get('where') for c in calls if c[1])
    truncated[0]=True
    with pytest.raises(ValueError,match='truncated'):hrsa.fetch()


def test_atomic_refresh_cache_and_failed_snapshot_retention(tmp_path,monkeypatch):
    path=tmp_path/'hrsa.duckdb';clock=[1000.0];calls=[]
    monkeypatch.setattr('oregon_health.api_cache.time.time',lambda:clock[0])
    def fetch():calls.append(True);return records(),provenance()
    monkeypatch.setattr(hrsa,'fetch',fetch)
    hrsa.refresh(path);hrsa.refresh(path)
    assert len(calls)==1
    before=hrsa.snapshot(path)
    with pytest.raises(duckdb.Error):hrsa.store(records()+records(),provenance(),path)
    assert hrsa.snapshot(path)==before
    with pytest.raises(RefreshLimited):hrsa.refresh(path,force=True)
    clock[0]+=301
    def offline():raise requests.ConnectionError('Offline')
    monkeypatch.setattr(hrsa,'fetch',offline)
    with pytest.raises(requests.ConnectionError):hrsa.refresh(path,force=True)
    assert hrsa.snapshot(path)==before
    with pytest.raises(RefreshLimited):hrsa.refresh(path,force=True)


def test_filters_exports_and_map_keep_grains_separate():
    rows=records()
    selected=filter_records(rows,'Primary Care','Population group',['Designated'],'41051')
    assert len(selected)==1 and filter_records(rows,'Primary Care','Population group',[])==[]
    assert filter_records(rows,'Primary Care','Facility',['Designated'])==[]
    selected[0]['name']='=1+1'
    frame=pd.read_csv(io.StringIO(export_csv(selected,provenance())),dtype=str)
    assert frame['name'].iloc[0]=="'=1+1" and frame.designation_id.iloc[0]=='141000000I'
    assert frame.source_url.iloc[0]==hrsa.BASE and 'geometry' not in frame.columns
    deck=json.loads(designation_deck(selected,'Population group').to_json())
    assert deck.get('mapProvider') is None and len(deck['layers'][1]['data']['features'])==1


def app_fixture(tmp_path,monkeypatch,cached=True):
    from oregon_health import data,api_cache
    path=tmp_path/'health.duckdb';data.store(data.demo_rows(),'Synthetic health fixture',path)
    monkeypatch.setattr(data,'DB_PATH',path)
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *a:False)
    path=tmp_path/'hrsa.duckdb';monkeypatch.setattr(hrsa,'DB_PATH',path)
    if cached:hrsa.store(records(),provenance(),path)
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=15)
    app.query_params['view']='access'
    return app


def test_opt_in_filters_population_warning_details_and_demo(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch)
    def forbidden(*a,**kw):raise AssertionError('No HRSA network expected')
    monkeypatch.setattr(hrsa,'refresh',forbidden)
    app.run();assert not app.checkbox(key='show_hrsa').value
    app.checkbox(key='show_hrsa').check().run()
    app.selectbox(key='hrsa_category').set_value('Population group').run()
    assert not app.exception
    assert any('not every resident' in i.value for i in app.info)
    assert any(m.label=='Distinct designations' and m.value=='1' for m in app.metric)
    app.selectbox(key='hrsa_detail').set_value('11:1').run()
    assert any('HPSA ID: 141000000I' in t.value for t in app.text)
    app.multiselect(key='hrsa_statuses').set_value(['Proposed For Withdrawal']).run()
    assert any(m.label=='Records without geometry' and m.value=='1' for m in app.metric)
    app.sidebar.toggle(key='demo_mode').set_value(True).run()
    assert not app.exception and app.checkbox(key='show_hrsa').disabled


def test_outage_leaves_health_usable_and_no_retries_on_filter_changes(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,cached=False);calls=[]
    def offline(*a,**kw):calls.append(True);raise requests.ConnectionError('Private diagnostic')
    monkeypatch.setattr(hrsa,'refresh',offline)
    app.run().checkbox(key='show_hrsa').check().run()
    assert not app.exception and len(calls)==1
    app.run()
    assert not app.exception and len(calls)==1
    assert any('HRSA designations are unavailable' in i.value for i in app.info)
    assert 'Private diagnostic' not in ' '.join(w.value for w in app.warning)


def test_map_events_accept_only_current_source_features():
    from oregon_health.hrsa_ui import selected_component
    event={'selection':{'objects':{'hrsa-designations':[{'properties':{'feature_key':'11:1'}}]}}}
    assert selected_component(event,['11:1'])=='11:1'
    assert selected_component(event,['11:2']) is None
    for bad in [None,{}, {'selection':[]}, {'selection':{'objects':None}},
                {'selection':{'objects':{'hrsa-designations':[{'properties':{'feature_key':['11:1']}}]}}}]:
        assert selected_component(bad,['11:1']) is None


def test_http_limits_redirects_and_arcgis_error_payload(monkeypatch):
    class Response:
        status_code=200
        raw=b'{"error":{"code":400}}'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_content(self,size):yield self.raw
    response=Response();calls=[]
    def get(url,**kw):calls.append((url,kw));return response
    monkeypatch.setattr(requests,'get',get)
    with pytest.raises(ValueError,match='unexpected'):hrsa._get('/9')
    assert calls[0][0]==hrsa.BASE+'/9' and calls[0][1]['allow_redirects'] is False
    response.status_code=302
    with pytest.raises(ValueError,match='complete response'):hrsa._get('/9')
    response.status_code=200;response.raw=b'x'*20_000_001
    with pytest.raises(ValueError,match='download limit'):hrsa._get('/9')
