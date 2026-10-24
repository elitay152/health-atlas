import json
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from oregon_health import regions, data, census, api_cache, import_data, status
from oregon_health.links import link_params, parse_counties
from oregon_health.map import boundaries, county_deck
from oregon_health.profile import county_names, selected_county

OR=regions.OREGON
WA=regions.WASHINGTON


def health_rows(region):
    return [dict(stateabbr=region.abbr,locationid=code,locationname=name,measureid=measure,year='2023',
        data_value_type='Crude prevalence',data_value=10,low_confidence_limit=9,high_confidence_limit=11)
        for code,name in region.counties().items() for measure in data.MEASURES]


def acs_payload(region):
    return [['NAME',*census.FIELDS,'state','county']]+[[name,'100','10','20','2','10','1',region.state_fips,code[2:]] for code,name in region.counties().items()]


@pytest.mark.parametrize('region,count',[(OR,36),(WA,39)])
def test_region_contract_and_county_pipeline(tmp_path,region,count):
    assert len(county_names(region))==count
    assert len(boundaries(region)['features'])==count
    assert all(code.startswith(region.state_fips) for code in region.counties())
    path=regions.health_path(region,tmp_path)
    assert data.store(health_rows(region),'Synthetic region source',path,region=region)==9*count
    with duckdb.connect(str(path),read_only=True) as db:
        frame=data.comparison(db,'DIABETES',2023,region=region)
        assert len(frame)==count
        assert len(data.county_profile(db,next(iter(region.counties())),2023,region=region))==9
    deck=json.loads(county_deck(frame,[],region=region).to_json())
    assert deck['layers'][0]['id']==region.layer
    assert deck['initialViewState']['longitude']==region.center[0]
    event={'selection':{'objects':{region.layer:[{'fips':frame.fips.iloc[0]}]}}}
    assert selected_county(event,region)==frame.fips.iloc[0]
    assert status.health_report(path,2023,'abcd-1234',region=region)['coverage']['Expected counties'].eq(count).all()


@pytest.mark.parametrize('region,count',[(OR,36),(WA,39)])
def test_census_region_and_coverage(tmp_path,region,count):
    rows=census.normalize(acs_payload(region),region=region)
    path=regions.cache_dir(region,tmp_path)/'census_2023.duckdb'
    census.store(rows,2023,path,region=region)
    result=census.context(next(iter(region.counties())),2023,path.parent,region=region)
    assert result['population']==100 and result['warning'] is None
    report=status.census_report(path.parent,2023,region=region)
    assert report['coverage']['Expected counties'].eq(count).all()
    assert report['coverage']['Available counties'].eq(count).all()
    with pytest.raises(ValueError):census.normalize(acs_payload(region)[:-1],region=region)


def test_cache_identity_and_legacy_oregon_migration(tmp_path,monkeypatch):
    path=tmp_path/'legacy.duckdb'
    data.store(data.demo_rows(),'Legacy source',path)
    with duckdb.connect(str(path)) as db:db.execute('ALTER TABLE provenance DROP COLUMN state_fips')
    assert api_cache.needs_refresh('abcd-1234',path)  # Source differs; legacy is readable, not a foreign region.
    before=path.read_bytes()
    with pytest.raises(ValueError):data.store(health_rows(WA),'WA source',path,region=WA)
    with pytest.raises(ValueError):api_cache.refresh('abcd-1234',path,region=WA)
    assert before==path.read_bytes()
    data.store(data.demo_rows(),'Migrated Oregon source',path)
    with duckdb.connect(str(path),read_only=True) as db:
        assert db.execute('SELECT state_fips FROM provenance').fetchone()[0]=='41'
    wa=regions.health_path(WA,tmp_path)
    data.store(health_rows(WA),'WA source',wa,region=WA)
    with pytest.raises(ValueError):api_cache.needs_refresh('abcd-1234',wa)
    with duckdb.connect(str(wa),read_only=True) as db:
        with pytest.raises(ValueError):data.comparison(db,'DIABETES',2023)
    assert regions.health_path(OR,tmp_path)==tmp_path/'oregon.duckdb'
    assert regions.cache_dir(WA,tmp_path)==tmp_path/'regions'/'wa'
    # Explicitly misrouted Census paths cannot be overwritten or silently reused.
    cp=tmp_path/'census_2023.duckdb'
    census.store(census.normalize(acs_payload(OR)),2023,cp)
    with pytest.raises(ValueError):census.store(census.normalize(acs_payload(WA),region=WA),2023,cp,region=WA)
    with pytest.raises(ValueError):census.context('53033',2023,tmp_path,region=WA)


def test_region_query_filters_without_network(monkeypatch):
    calls=[]
    def get(url,**kwargs):
        calls.append(kwargs)
        return {'name':'PLACES County Synthetic Release'} if '/api/views/' in url else health_rows(WA)
    monkeypatch.setattr(import_data,'get_json',get)
    rows,_=import_data.fetch('abcd-1234',region=WA)
    assert len(rows)==351 and "stateabbr = 'WA'" in calls[-1]['params']['$where']
    calls=[]
    def get_json(url,params=None):
        calls.append(params)
        return {'variables':{}} if url.endswith('variables.json') else acs_payload(WA)
    monkeypatch.setattr(census,'_get_json',get_json)
    monkeypatch.setattr(census,'validate_variables',lambda v:None)
    assert len(census.fetch(2023,api_key='synthetic-test-key',region=WA))==39
    assert calls[-1]['in']=='state:53'


def test_links_coverage_flags_and_live_guard(monkeypatch):
    assert parse_counties('53033,53061',region=WA)==(['53033','53061'],False)
    assert parse_counties('53033')[1]
    params=link_params(['53033'],2023,'DIABETES',False,False,'53033',region=WA)
    assert params['state']=='WA' and params['county']=='53033'
    assert 'state' not in link_params(['41051'],2023,'DIABETES',False,False)
    with pytest.raises(ValueError):WA.require('tract_health')
    with pytest.raises(ValueError):link_params([],2023,'DIABETES',False,False,local=True,region=WA)
    with pytest.raises(ValueError):regions.get_region('OR\' OR TRUE')
    def forbidden(*a,**kw):pytest.fail('unsupported region must not request Oregon data')
    monkeypatch.setattr(api_cache,'refresh',forbidden)
    for code in ['invalid']:
        view=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=15)
        view.query_params.update(state=code)
        view.run()
        assert not view.exception and not view.selectbox
        assert any('Choose Oregon or Washington' in i.value for i in view.info)
