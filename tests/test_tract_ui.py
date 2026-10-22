from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, census, data, tracts, tract_boundaries as tb
from oregon_health.links import parse_tract, link_params
from oregon_health.tract_ui import overview, tract_deck, selected_tract, MAX_ZOOM

APP = Path(__file__).resolve().parents[1] / 'app.py'


def tract_row(geoid='41051000101', measure='DIABETES', year='2023', **changes):
    return dict(locationid=geoid, countyfips=geoid[:5], countyname='Synthetic County',
                locationname='Synthetic tract', stateabbr='OR', measureid=measure,
                measure='Synthetic condition definition', year=year, data_value='8.2',
                low_confidence_limit='7.1', high_confidence_limit='9.4',
                data_value_type='Crude prevalence', data_value_unit='%', datasource='BRFSS',
                category='Synthetic category') | changes


def app(tmp_path, monkeypatch, *, rows=None, saved=True, **params):
    path = tmp_path / 'county.duckdb'
    county_rows = data.demo_rows()
    for r in county_rows:
        r['year'] = '2023'
    data.store(county_rows,'Synthetic county fixture',path)
    monkeypatch.setattr(data,'DB_PATH',path)
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *args:False)
    def no_census(*args, **kwargs):
        raise census.CensusKeyRequired()
    monkeypatch.setattr(census,'context',no_census)
    tract_path = tmp_path / 'tracts.duckdb'
    monkeypatch.setattr(tracts,'DB_PATH',tract_path)
    if saved:
        rows = rows if rows is not None else [tract_row(),tract_row(measure='OBESITY')]
        p = dict(dataset=tracts.DATASET, release_year=2025, rows=len(rows),catalog=list(data.MEASURES),
                 fetched_at=datetime.now(timezone.utc).isoformat(),source_url='https://data.cdc.gov/d/cwsq-ngmh',
                 recommended_boundary_year=2023)
        tracts.store(rows,p,tract_path)
    v = AppTest.from_file(APP,default_timeout=20)
    v.query_params.update(dict(county='41051',year='2023',measure='DIABETES') | params)
    return v,tract_path


def test_opt_in_shared_view_roundtrip_and_persistent_geography(tmp_path, monkeypatch):
    v,path=app(tmp_path,monkeypatch,local='1',tract='41051000101',tract_year='2023')
    def forbidden(*args, **kwargs):
        pytest.fail('fresh snapshot must not fetch')
    monkeypatch.setattr(tracts,'refresh',forbidden)
    v.run()
    assert not v.exception
    assert v.selectbox(key='tract_selection').value=='41051000101'
    assert any(m.value=='8.2%' for m in v.metric)
    assert v.query_params['tract']=='41051000101'
    assert v.query_params['tract_year']=='2023'
    other=AppTest.from_file(APP,default_timeout=20)
    other.query_params.update(dict(v.query_params))
    other.run()
    assert not other.exception and other.selectbox(key='tract_selection').value=='41051000101'
    v.selectbox(key='health_measure').select('OBESITY').run()
    assert not v.exception
    assert v.selectbox(key='tract_selection').value=='41051000101'
    v.selectbox(key='profile_county').select('41039').run()
    assert not v.exception
    assert v.selectbox(key='tract_selection').value is None
    assert 'tract' not in v.query_params
    assert v.query_params['county']=='41039'
    assert any('No saved tract estimates' in i.value for i in v.info)


def test_disabled_view_and_demo_never_load_real_tracts(tmp_path,monkeypatch):
    v,path=app(tmp_path,monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail('opt-out/demo must not read or fetch tract snapshots')
    monkeypatch.setattr(tracts,'coverage',forbidden)
    monkeypatch.setattr(tracts,'refresh',forbidden)
    v.run()
    assert not v.exception and not v.toggle(key='show_tracts').value
    demo,_=app(tmp_path,monkeypatch,demo='1',local='1',tract='41051000101')
    demo.run()
    assert not demo.exception
    assert demo.toggle(key='show_tracts').disabled
    assert 'tract' not in demo.query_params and 'local' not in demo.query_params


@pytest.mark.parametrize('linked', ['41039000100','not-a-geoid','41051000999'])
def test_invalid_or_unmatched_links_are_explicit(tmp_path,monkeypatch,linked):
    v,_=app(tmp_path,monkeypatch,local='1',tract=linked,tract_year='1900')
    v.run()
    assert not v.exception
    assert v.selectbox(key='tract_selection').value is None
    assert any('linked tract is invalid' in w.value for w in v.warning)
    assert any('linked tract estimate year is unavailable' in w.value for w in v.warning)
    assert 'tract' not in v.query_params and v.query_params['tract_year']=='2023'


def test_null_row_missing_boundary_record_and_year_difference(tmp_path,monkeypatch):
    rows=[tract_row(year='2022',data_value=None,low_confidence_limit=None,high_confidence_limit=None,
                    data_value_footnote='Synthetic withheld value')]
    v,_=app(tmp_path,monkeypatch,rows=rows,local='1',tract='41051000101',tract_year='2022')
    v.run()
    assert not v.exception
    assert any('Tract estimates use 2022' in i.value for i in v.info)
    assert any('Synthetic withheld value' in t.value for t in v.text)
    table=next(x.value for x in v.dataframe if 'GEOID' in x.value.columns)
    assert table.loc[table.GEOID=='41051000101','Status'].iloc[0]=='Unavailable (source row)'
    assert table['Estimate (%)'].isna().all()
    # Widget values use GEOIDs rather than formatted option labels.
    v.selectbox(key='tract_selection').select('41051000102').run()
    assert not v.exception
    assert any('No CDC row is saved' in i.value for i in v.info)
    next(b for b in v.button if b.label=='Back to county overview').click().run()
    assert v.selectbox(key='tract_selection').value is None and 'tract' not in v.query_params


@pytest.mark.parametrize('saved',[True,False])
def test_outage_keeps_county_views_and_does_not_retry_on_widgets(tmp_path,monkeypatch,saved):
    v,path=app(tmp_path,monkeypatch,saved=saved,local='1')
    monkeypatch.setattr(tracts,'needs_refresh',lambda *args:True)
    calls=[]
    def offline(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError('Synthetic outage')
    monkeypatch.setattr(tracts,'refresh',offline)
    v.run()
    assert not v.exception and len(calls)==1
    assert any('tract data could not be refreshed' in w.value for w in v.warning)
    assert v.selectbox(key='health_measure').value=='DIABETES'
    v.selectbox(key='health_measure').select('OBESITY').run()
    assert not v.exception and len(calls)==1
    if saved:
        assert any('1 of 197 tracts with estimates' in m.value for m in v.markdown)
    else:
        assert any('Tract estimates are unavailable' in i.value for i in v.info)


def test_selection_event_only_accepts_current_layer_and_county():
    event={'selection':{'objects':{'oregon-tracts':[{'properties':{'geoid':'41051000101'}}]}}}
    assert selected_tract(event,{'41051000101'})=='41051000101'
    assert selected_tract(event,{'41039000100'}) is None
    assert selected_tract({'selection':{'objects':{'wrong':[{'geoid':'41051000101'}]}}},{'41051000101'}) is None
    assert selected_tract(None,set()) is None


def test_overview_scale_and_missing_colors():
    collection,_=tb.join_estimates([dict(tract_fips='41051000101',county_fips='41051',prevalence=8,low=7,high=9)],'41051')
    deck=json.loads(tract_deck(collection,'41051000101').to_json())
    assert deck['views'][0]['controller'] is False
    assert deck['mapStyle']=='__MAP_STYLE__'  # Suppress Streamlit's independent base-map zoom controls.
    assert deck['initialViewState']['zoom'] <= MAX_ZOOM
    assert deck['initialViewState']['zoom']==deck['initialViewState']['maxZoom']
    p={f['id']:f['properties'] for f in deck['layers'][0]['data']['features']}
    assert p['41051000102']['estimate']=='No estimate available'
    assert p['41051000102']['color']==[220,222,216,255]
    assert p['41051000101']['outline']==[184,106,64,255]
    # Every county, including small urban counties, respects the same scale cap.
    for county in ['41051','41071','41045','41001']:
        assert overview(tb.boundaries(county)).zoom <= MAX_ZOOM


def test_link_whitelist_and_tract_prefix():
    assert parse_tract('41051000101','41051')==('41051000101',False)
    assert parse_tract('41039000101','41051')==(None,True)
    p=link_params([],2023,'DIABETES',False,False,'41051',local=True,tract='41051000101',tract_year=2023)
    assert p['local']=='1' and p['tract']=='41051000101' and p['tract_year']=='2023'
    assert 'local' not in link_params([],2023,'DIABETES',True,False,'41051',local=True)
