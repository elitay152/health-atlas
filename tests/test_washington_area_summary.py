"""Saved Washington summaries: source isolation, complete counts and no fetching."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import duckdb
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import census, data, geocoding, hospitals, hrsa, regions, service_areas, service_area_summary
from oregon_health.service_area_summary import summarize
from oregon_health.status import record_refresh
from test_service_area_summary import health as or_health, population as or_population, cms as or_cms, hrsa_records as or_hrsa

WA = regions.WASHINGTON
CODES = ['53033', '53061']
FILES = {'CDC health': 'counties.duckdb', 'ACS context': 'census_2023.duckdb',
         'CMS hospitals': 'hospitals.duckdb', 'Address matches': 'hospital_locations.duckdb',
         'HRSA designations': 'hrsa.duckdb'}


@pytest.fixture
def snapshots(tmp_path):
    root = regions.cache_dir(WA, tmp_path)
    rows = [dict(stateabbr='WA', locationid=code, locationname=WA.counties()[code],
                 measureid=measure, year='2023', data_value_type='Crude prevalence',
                 data_value=10, low_confidence_limit=9, high_confidence_limit=11)
            for code in CODES for measure in data.MEASURES]
    data.store(rows, 'Synthetic Washington county source', root / FILES['CDC health'], region=WA)
    census.store([(code, 100*(i+1), 10, 20, 2, 10, 1) for i,code in enumerate(CODES)],
                 2023, root / FILES['ACS context'], region=WA)
    records = [dict(zip(hospitals.COLUMNS, row)) for row in [
        ('005001','Synthetic Seattle hospital','1 Example St','Seattle','WA','00123','King','53033','Acute','Public',True),
        ('005002','Synthetic Everett hospital','2 Example St','Everett','WA','00124','Snohomish','53061','Acute','Public',False),
        ('005003','Synthetic unresolved hospital','3 Example St','Unknown','WA','00125',None,None,'Acute','Public',None)]]
    hospitals.store([tuple(r[k] for k in hospitals.COLUMNS) for r in records],
        dict(dataset=hospitals.DATASET,source_url=hospitals.SOURCE_URL,download_url='https://data.cms.gov/provider-data/example.csv',
             released='2026-08-13',modified='2026-07-22',fetched_at=datetime.now(timezone.utc),sha256='a'*64),
        root / FILES['CMS hospitals'],region=WA)
    matches = [(r['facility_id'],geocoding.address_hash(r),'matched_exact',lon,lat,'Synthetic exact address')
               for r,(lon,lat) in zip(records,[(-122.33,47.6),(-122.2,47.98),(-122.33,47.6)])]
    geocoding.store(matches,dict(source_url=geocoding.SOURCE_URL,benchmark=geocoding.BENCHMARK,benchmark_id='4',
        fetched_at=datetime.now(timezone.utc),fingerprint=geocoding.fingerprint(records),state_fips='53'),
        root / FILES['Address matches'],region=WA)
    base=dict(state='WA',state_fips='53',designation_id='1530000001',discipline='Primary Care',category='Geographic',
              status='Designated',geometry=None,source_date='2026-10-23')
    records=[base|dict(feature_key=f'11:{i}',county_fips=code) for i,code in enumerate([*CODES,None],1)]
    records.append(base|dict(feature_key='11:4',county_fips='53033',status='Proposed For Withdrawal'))
    hrsa.store(records,dict(fetched_at=datetime.now(timezone.utc).isoformat(),source_url=hrsa.SOURCE_URL,state_fips='53'),
               root / FILES['HRSA designations'],region=WA)
    return root


def result(root, **kwargs):
    return summarize(CODES, 2023, cache_dir=root, region=WA, **kwargs)


def sources(report):
    return {s['source']:s for s in report['sources']}


def test_washington_counts_periods_provenance_and_json(snapshots, monkeypatch):
    def forbidden(*a, **kw): pytest.fail('Summaries may not request sources')
    monkeypatch.setattr(requests,'get',forbidden);monkeypatch.setattr(requests,'post',forbidden)
    before={p.name:p.read_bytes() for p in snapshots.iterdir()}
    report=result(snapshots)
    assert report['state']=='WA' and report['state_fips']=='53'
    assert report['area']==service_areas.definition(CODES,region=WA)
    assert report['population']['estimate']==300 and report['population']['moe90'] is None
    assert report['hospitals']['listed']==2 and report['hospitals']['approximate_locations']==2
    assert report['hospitals']['accepted_location_ids']==['005001','005002']
    designated=next(r for r in report['shortages'] if r['status']=='Designated')
    assert designated['designations']==1 and designated['components']==2
    assert len(report['shortages'])==2
    assert all(s['state']=='Loaded' and s['region']=='WA' and s['state_fips']=='53' for s in report['sources'])
    assert sources(report)['CMS hospitals']['unresolved_counties']==1
    assert sources(report)['HRSA designations']['unresolved_counties']==1
    assert sources(report)['HRSA designations']['provenance']['warehouse_dates']==['2026-10-23']
    assert len(report['health'])==18 and all(h['confidence']==95 and h['year']==2023 for h in report['health'])
    assert all(d['period']=='2019–2023' and d['moe_confidence']==90 for d in report['demographics'])
    assert 'area_prevalence' not in report and 'Health prevalence total/average' in report['unsupported_aggregations']
    assert json.loads(json.dumps(report,allow_nan=False))['area']['health_atlas']['state_fips']=='53'
    assert before=={p.name:p.read_bytes() for p in snapshots.iterdir()}
    assert summarize(['53033'],2023,cache_dir=snapshots,region=WA)['population']['moe90']==10


@pytest.mark.parametrize('kind', list(FILES))
def test_foreign_snapshots_rejected_independently(snapshots,tmp_path,kind):
    other=tmp_path/'foreign';other.mkdir()
    or_health(other);or_population(other);records=or_cms(other);or_hrsa(other)
    geocoding.store([(records[0]['facility_id'],geocoding.address_hash(records[0]),'unmatched',None,None,None)],
        dict(source_url=geocoding.SOURCE_URL,benchmark=geocoding.BENCHMARK,benchmark_id='4',
             fetched_at=datetime.now(timezone.utc),fingerprint=geocoding.fingerprint(records)),other/'hospital_locations.duckdb')
    source=other/('oregon.duckdb' if kind=='CDC health' else FILES[kind])
    shutil.copyfile(source,snapshots/FILES[kind])
    before=(snapshots/FILES[kind]).read_bytes()
    report=result(snapshots)
    assert sources(report)[kind]['state']=='Unreadable / incompatible'
    assert all(s['state']=='Loaded' for s in report['sources'] if s['source']!=kind)
    assert (snapshots/FILES[kind]).read_bytes()==before
    if kind=='ACS context': assert report['population']['estimate'] is None
    if kind=='CMS hospitals': assert report['hospitals']['listed'] is None
    if kind=='Address matches': assert report['hospitals']['approximate_locations'] is None
    if kind=='CDC health': assert all(h['status']=='Source unavailable' for h in report['health'])
    if kind=='HRSA designations': assert report['shortages']==[]


@pytest.mark.parametrize('scenario',['missing_county','wrong_period','bad_state','duplicate_county','negative','missing_moe'])
def test_population_integrity_and_missing_uncertainty(snapshots,scenario):
    with duckdb.connect(str(snapshots/FILES['ACS context'])) as db:
        if scenario=='missing_county':db.execute("DELETE FROM demographics WHERE fips='53061'")
        if scenario=='wrong_period':db.execute('UPDATE provenance SET year=2022')
        if scenario=='bad_state':db.execute("UPDATE provenance SET state_fips='41'")
        if scenario=='duplicate_county':
            db.execute('CREATE TABLE copy AS SELECT * FROM demographics');db.execute('DROP TABLE demographics')
            db.execute('ALTER TABLE copy RENAME TO demographics');db.execute('INSERT INTO demographics SELECT * FROM demographics LIMIT 1')
        if scenario=='negative':db.execute('UPDATE demographics SET population=-1')
        if scenario=='missing_moe':db.execute('UPDATE demographics SET population_moe=NULL')
    report=result(snapshots)
    assert report['population']['estimate']==(300 if scenario=='missing_moe' else None)
    assert report['population']['moe90'] is None
    assert report['hospitals']['listed']==2


def test_current_addresses_and_counties_required_for_points(snapshots):
    with duckdb.connect(str(snapshots/FILES['Address matches'])) as db:
        db.execute("UPDATE locations SET address_hash='changed' WHERE facility_id='005001'")
        db.execute("UPDATE locations SET longitude=-122.33,latitude=47.6 WHERE facility_id='005002'")
    assert result(snapshots)['hospitals']['approximate_locations']==0
    with duckdb.connect(str(snapshots/FILES['CMS hospitals'])) as db:
        db.execute("UPDATE hospitals SET address=NULL WHERE facility_id='005001'")
    current=hospitals.directory(path=snapshots/FILES['CMS hospitals'],region=WA)[0]
    with duckdb.connect(str(snapshots/FILES['Address matches'])) as db:
        db.execute("UPDATE locations SET address_hash=?,longitude=-122.33,latitude=47.6 WHERE facility_id='005001'",[geocoding.address_hash(current)])
    assert result(snapshots)['hospitals']['approximate_locations']==0


def test_default_washington_path_never_falls_back_to_oregon(tmp_path,monkeypatch):
    or_health(tmp_path);or_population(tmp_path);or_cms(tmp_path);or_hrsa(tmp_path)
    monkeypatch.setattr(service_area_summary,'DB_PATH',tmp_path/'oregon.duckdb')
    report=summarize(CODES,2023,region=WA)
    assert all(s['state']=='Not loaded' for s in report['sources'])
    assert report['population']['estimate'] is None and report['hospitals']['listed'] is None
    assert not (tmp_path/'regions').exists()
    with pytest.raises(ValueError):summarize(['41051'],2023,region=WA)
    with pytest.raises(ValueError):summarize(['53033'],2023)


def test_retained_failure_and_corrupt_source_are_visible(snapshots):
    record_refresh(snapshots/FILES['HRSA designations'],'hrsa-hpsa','failed')
    (snapshots/FILES['CMS hospitals']).write_bytes(b'broken')
    report=result(snapshots)
    assert sources(report)['HRSA designations']['retained_after_failure']
    assert sources(report)['CMS hospitals']['state']=='Unreadable / incompatible'
    assert report['hospitals']['listed'] is None and report['population']['estimate']==300


def test_ui_region_summary_and_navigation_retention(snapshots,monkeypatch):
    def forbidden(*a,**kw):pytest.fail('Area summary must not request sources')
    monkeypatch.setattr(requests,'get',forbidden);monkeypatch.setattr(requests,'post',forbidden)
    monkeypatch.setattr(data,'DB_PATH',snapshots.parents[1]/'oregon.duckdb')
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    app.query_params.update(service_areas.link_params(CODES,region=WA)|{'view':'areas','year':'2023'})
    app.run()
    assert not app.exception and not app.checkbox(key='show_area_summary').value
    app.checkbox(key='show_area_summary').check().run()
    assert not app.exception and any(m.label=='ACS population' and m.value=='300' for m in app.metric)
    assert any(m.label=='CMS-listed hospitals' and m.value=='2' for m in app.metric)
    assert not app.checkbox(key='compare_areas').value
    app.radio(key='view_selector').set_value('sources').run()
    app.radio(key='view_selector').set_value('areas').run()
    assert not app.exception and app.checkbox(key='show_area_summary').value
    assert app.query_params['state']=='WA' and app.query_params['area']=='53033,53061'
    other=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    other.query_params.update(dict(app.query_params));other.run()
    assert not other.exception and other.multiselect(key='area_counties').value==CODES
    app.multiselect(key='area_counties').set_value([]).run()
    assert app.checkbox(key='show_area_summary').disabled
