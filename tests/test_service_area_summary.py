from datetime import datetime, timezone
import json
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from oregon_health import census, data, hospitals, hrsa, geocoding, service_areas, api_cache
from oregon_health.service_area_summary import summarize
from oregon_health.status import record_refresh


def population(root,year=2023,missing=False):
    # Exact source contract, deliberately synthetic and only two county records.
    path=root/f'census_{year}.duckdb'
    with duckdb.connect(str(path)) as db:
        db.execute('CREATE TABLE demographics(fips VARCHAR,population BIGINT,population_moe BIGINT,age65_pct DOUBLE,age65_moe DOUBLE,poverty_pct DOUBLE,poverty_moe DOUBLE)')
        db.executemany('INSERT INTO demographics VALUES (?,?,?,?,?,?,?)',[
            ('41039',100,10,20,2,10,1),('41051',None if missing else 200,20,30,3,40,4)])
        db.execute('CREATE TABLE provenance(year INTEGER,source VARCHAR,imported_at TIMESTAMP)')
        db.execute('INSERT INTO provenance VALUES (?,?,current_timestamp)',[year,census.release_url(year)])


def health(root):
    rows=data.demo_rows()
    for r in rows:r['year']='2023'
    data.store(rows,'Synthetic county source',root/'oregon.duckdb')


def cms(root):
    records=[('003801','Synthetic hospital','1 Example St','Eugene','OR','97401','Lane','41039','Acute','Public',True),
             ('003802','Synthetic unknown','2 Example St','Unknown','OR','00000',None,None,'Acute','Public',None)]
    hospitals.store(records,dict(dataset=hospitals.DATASET,source_url=hospitals.SOURCE_URL,
        download_url='https://data.cms.gov/provider-data/example.csv',released='2026-08-13',modified='2026-07-22',
        fetched_at=datetime.now(timezone.utc),sha256='a'*64),root/'hospitals.duckdb')
    return [dict(zip(hospitals.COLUMNS,r)) for r in records]


def hrsa_records(root):
    base=dict(designation_id='1410000001',discipline='Primary Care',category='Geographic',
              status='Designated',geometry=None,source_date='2026-10-21')
    rows=[base|dict(feature_key='11:1',county_fips='41039'),base|dict(feature_key='11:2',county_fips='41051'),
          base|dict(feature_key='11:3',county_fips=None),
          base|dict(feature_key='11:4',county_fips='41039',status='Proposed For Withdrawal')]
    hrsa.store(rows,dict(fetched_at=datetime.now(timezone.utc).isoformat(),source_url=hrsa.SOURCE_URL),root/'hrsa.duckdb')


def test_compatible_counts_no_rate_or_uncertainty_aggregation(tmp_path):
    population(tmp_path);health(tmp_path);cms(tmp_path);hrsa_records(tmp_path)
    r=summarize(['41039','41051','41039'],2023,cache_dir=tmp_path)
    assert r['population']['estimate']==300 and r['population']['moe90'] is None
    assert r['population']['selected_counties']==2
    assert len(r['health'])==18
    assert all(h['unit']=='percent' and h['confidence']==95 and h['estimate_type']=='Crude prevalence' for h in r['health'])
    assert len(r['demographics'])==2
    assert [x['age65_pct'] for x in r['demographics']]==[20,30]
    assert 'Health prevalence total/average' in r['unsupported_aggregations']
    assert 'area_prevalence' not in r and 'area_poverty_pct' not in r
    assert r['hospitals']['listed']==1 and r['hospitals']['approximate_locations'] is None
    designated=next(x for x in r['shortages'] if x['status']=='Designated')
    assert designated['designations']==1 and designated['components']==2
    assert len(r['shortages'])==2  # proposed withdrawal is not merged with designated
    assert next(s for s in r['sources'] if s['source']=='HRSA designations')['unresolved_counties']==1
    assert next(s for s in r['sources'] if s['source']=='HRSA designations')['provenance']['warehouse_dates']==['2026-10-21']
    json.dumps(r,allow_nan=False)
    one=summarize(['41039'],2023,cache_dir=tmp_path)
    assert one['population']['moe90']==10


@pytest.mark.parametrize('scenario',['missing_county','wrong_period','missing_source','duplicate_county','negative'])
def test_population_total_is_withheld_not_partial(tmp_path,scenario):
    if scenario!='missing_source':population(tmp_path,missing=scenario=='missing_county')
    path=tmp_path/'census_2023.duckdb'
    if scenario in {'wrong_period','duplicate_county','negative'}:
        with duckdb.connect(str(path)) as db:
            if scenario=='wrong_period':db.execute('UPDATE provenance SET year=2022')
            if scenario=='duplicate_county':db.execute("INSERT INTO demographics SELECT * FROM demographics WHERE fips='41039'")
            if scenario=='negative':db.execute('UPDATE demographics SET population=-1')
    r=summarize(['41039','41051'],2023,cache_dir=tmp_path)
    assert r['population']['estimate'] is None
    assert r['population']['moe90'] is None


def test_read_only_no_network_and_unavailable_not_zero(tmp_path,monkeypatch):
    import requests
    def forbidden(*a,**k):pytest.fail('summary must not request sources')
    monkeypatch.setattr(requests,'get',forbidden)
    root=tmp_path/'absent'
    r=summarize(['41039'],2023,cache_dir=root)
    assert not root.exists()
    assert all(s['state']=='Not loaded' for s in r['sources'])
    assert r['hospitals']['listed'] is None and r['shortages']==[]
    assert all(h['prevalence'] is None and h['status']=='Source unavailable' for h in r['health'])
    root.mkdir();health(root)
    before={p.name:p.read_bytes() for p in root.iterdir()}
    summarize(['41039'],2023,cache_dir=root)
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}


def test_address_fingerprint_and_county_check_required(tmp_path):
    records=cms(tmp_path);r=records[0]
    matches=[(r['facility_id'],geocoding.address_hash(r),'matched_exact',-123.09,44.05,'1 EXAMPLE ST')]
    p=dict(source_url=geocoding.SOURCE_URL,benchmark=geocoding.BENCHMARK,benchmark_id='4',
           fetched_at=datetime.now(timezone.utc),fingerprint=geocoding.fingerprint(records))
    geocoding.store(matches,p,tmp_path/'hospital_locations.duckdb')
    result=summarize(['41039'],2023,cache_dir=tmp_path)
    assert result['hospitals']['approximate_locations']==1
    with duckdb.connect(str(tmp_path/'hospital_locations.duckdb')) as db:
        db.execute("UPDATE locations SET address_hash='changed'")
    assert summarize(['41039'],2023,cache_dir=tmp_path)['hospitals']['approximate_locations']==0
    with duckdb.connect(str(tmp_path/'hospital_locations.duckdb')) as db:
        db.execute('UPDATE locations SET address_hash=?,longitude=-122.67,latitude=45.52',[geocoding.address_hash(r)])
    assert summarize(['41039'],2023,cache_dir=tmp_path)['hospitals']['approximate_locations']==0


def test_source_conflicts_and_corrupt_cache_are_isolated(tmp_path):
    health(tmp_path);population(tmp_path);cms(tmp_path)
    (tmp_path/'hrsa.duckdb').write_bytes(b'broken')
    # Simulate a saved source without its normal primary-key constraint.
    with duckdb.connect(str(tmp_path/'hospitals.duckdb')) as db:
        db.execute('CREATE TABLE copy AS SELECT * FROM hospitals')
        db.execute('DROP TABLE hospitals');db.execute('ALTER TABLE copy RENAME TO hospitals')
        db.execute("INSERT INTO hospitals SELECT * FROM hospitals WHERE facility_id='003801'")
    assert summarize(['41039'],2023,cache_dir=tmp_path)['hospitals']['listed']==1
    with duckdb.connect(str(tmp_path/'hospitals.duckdb')) as db:
        db.execute("INSERT INTO hospitals SELECT * REPLACE ('Conflicting identity' AS name) FROM hospitals WHERE facility_id='003801' LIMIT 1")
    result=summarize(['41039'],2023,cache_dir=tmp_path)
    assert result['hospitals']['listed'] is None
    assert result['population']['estimate']==100
    assert any(h['status']=='Available' for h in result['health'])
    assert next(s for s in result['sources'] if s['source']=='HRSA designations')['state']=='Unreadable / incompatible'


def test_failure_after_snapshot_is_reported(tmp_path):
    cms(tmp_path)
    record_refresh(tmp_path/'hospitals.duckdb',hospitals.DATASET,'failed')
    result=summarize(['41039'],2023,cache_dir=tmp_path)
    s=next(s for s in result['sources'] if s['source']=='CMS hospitals')
    assert s['retained_after_failure'] is True


def test_ui_opt_in_source_isolation_and_empty_area(tmp_path,monkeypatch):
    from oregon_health import service_area_summary
    health(tmp_path);population(tmp_path);cms(tmp_path);hrsa_records(tmp_path)
    monkeypatch.setattr(data,'DB_PATH',tmp_path/'oregon.duckdb')
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *a:False)
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    app.query_params.update(service_areas.link_params(['41039','41051'])|dict(year='2023'))
    app.run();assert not app.exception
    assert not app.checkbox(key='show_area_summary').value
    app.checkbox(key='show_area_summary').check().run();assert not app.exception
    assert any(m.label=='ACS population' and m.value=='300' for m in app.metric)
    assert any(m.label=='CMS-listed hospitals' and m.value=='1' for m in app.metric)
    app.multiselect(key='area_counties').set_value([]).run();assert not app.exception
    assert app.checkbox(key='show_area_summary').disabled
