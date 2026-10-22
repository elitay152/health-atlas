from datetime import datetime,timedelta,timezone
import io
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import access_quality,geocoding,hospitals,hrsa
from oregon_health.access_quality_ui import _csv
from oregon_health.status import record_refresh

NOW=datetime.now(timezone.utc)


def fixtures(root):
    cms=[('003801','Synthetic hospital','1 Example St','Eugene','OR','00123','LANE','41039','Hospital','Government',True),
         ('003802','Incomplete hospital','2 Example St',None,'OR',None,'UNKNOWN',None,'Hospital',None,None),
         ('003803','Changed hospital','3 Current St','Portland','OR','97201','MULTNOMAH','41051','Hospital',None,False)]
    hospitals.store(cms,dict(dataset=hospitals.DATASET,source_url=hospitals.SOURCE_URL,
        download_url='https://data.cms.gov/provider-data/example.csv',released='2026-08-13',modified='2026-07-22',
        fetched_at=NOW,sha256='a'*64),root/'hospitals.duckdb')
    records=[dict(zip(hospitals.COLUMNS,r)) for r in cms]
    matches=[('003801',geocoding.address_hash(records[0]),'matched_exact',-123.08,44.05,'Example'),
             ('003802',geocoding.address_hash(records[1]),'matched_exact',float('nan'),45.5,'Bad point'),
             ('003803','old-address-hash','matched_exact',None,None,'Old address'),
             ('003804','orphan-hash','unmatched',None,None,None)]
    geocoding.store(matches,dict(source_url=geocoding.SOURCE_URL,benchmark=geocoding.BENCHMARK,benchmark_id='4',
        fetched_at=NOW,fingerprint=geocoding.fingerprint(records)),root/'hospital_locations.duckdb')
    polygon=dict(type='Polygon',coordinates=[[[-123.1,44],[-123,44],[-123,44.1],[-123.1,44]]])
    base=dict(designation_id='141000000I',name='Synthetic designation',discipline='Primary Care',category='Geographic',
        county_fips='41039',source_county_fips='41039',source_date='2026-10-21',status='Designated',geometry=polygon)
    rows=[dict(base,feature_key='11:1'),dict(base,feature_key='11:2',geometry=None),
          dict(base,feature_key='9:3',designation_id='1410000002',category='Facility',county_fips=None,
               geometry=dict(type='Point',coordinates=[999,45]))]
    hrsa.store(rows,dict(source_url=hrsa.SOURCE_URL,service_url=hrsa.BASE,fetched_at=NOW.isoformat(),
        generalization_degrees=0.005,layers=[]),root/'hrsa.duckdb')
    return records


def checks(report):
    return {(r['Source'],r['Check']):r for r in report['checks']}


def test_missing_snapshots_are_not_zero_and_audit_never_creates_or_fetches(tmp_path,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Read-only audit must not fetch')
    monkeypatch.setattr(requests,'get',forbidden);monkeypatch.setattr(requests,'post',forbidden)
    root=tmp_path/'does-not-exist'
    report=access_quality.audit(root,NOW)
    assert all(r['Snapshot']=='Not loaded' and r['Cached records'] is None for r in report['summary'])
    assert all(r['CMS-listed hospitals'] is None and r['HRSA components / facility points'] is None for r in report['counties'])
    assert not root.exists()


def test_reports_missing_invalid_unsupported_and_changed_joins_separately(tmp_path,monkeypatch):
    fixtures(tmp_path)
    monkeypatch.setattr(requests,'get',lambda *a,**kw:pytest.fail('No network'))
    report=access_quality.audit(tmp_path,NOW);c=checks(report)
    assert c['CMS hospitals','Source coordinate fields']['State']=='Unsupported'
    assert c['CMS hospitals','Source coordinate fields']['Count'] is None
    assert c['CMS hospitals','Incomplete addresses']['Count']==1
    assert c['CMS hospitals','Emergency services not reported']['Count']==1
    assert c['CMS hospitals','Unresolved county joins']['Count']==1
    assert c['Address matches','Accepted matches missing coordinates']['Count']==1
    assert c['Address matches','Invalid or contradictory coordinates']['Count']==1
    assert c['Address matches','Current hospital match status: Address changed']['Count']==1
    assert c['Address matches','Cached IDs absent from current CMS snapshot']['Count']==1
    assert c['HRSA designations','Duplicate source feature keys']['Count']==0
    assert c['HRSA designations','Distinct discipline/designation pairs']['Count']==2
    assert c['HRSA designations','Records without source geometry']['Count']==1
    assert c['HRSA designations','Records with invalid geometry']['Count']==1
    assert c['HRSA designations','Unresolved county joins']['Count']==1
    lane=next(r for r in report['counties'] if r['County FIPS']=='41039')
    assert lane['CMS-listed hospitals']==1 and lane['HRSA components / facility points']==2
    assert lane['HRSA distinct discipline/designation pairs']==1
    absent=next(r for r in report['counties'] if r['County FIPS']=='41001')
    assert absent['CMS-listed hospitals']==0  # Loaded snapshot records, not actual supply.


def test_unreadable_source_is_independent_and_join_unevaluated(tmp_path):
    fixtures(tmp_path)
    (tmp_path/'hospitals.duckdb').write_bytes(b'Not a DuckDB database')
    report=access_quality.audit(tmp_path,NOW)
    assert report['summary'][0]['Snapshot']=='Unreadable snapshot'
    assert report['summary'][0]['Cached records'] is None
    assert report['summary'][1]['Snapshot']=='Loaded' and report['summary'][2]['Snapshot']=='Loaded'
    assert checks(report)['Address matches','Current-address join']['State']=='Not evaluated'
    assert all(r['CMS-listed hospitals'] is None for r in report['counties'])


def test_snapshot_duplicates_are_reported_without_flagging_component_multiplicity(tmp_path,monkeypatch):
    records=fixtures(tmp_path)
    original=access_quality._read
    def read(path,kind):
        rows,meta=original(path,kind)
        if kind=='CMS hospitals':rows.append(rows[0])
        return rows,meta
    monkeypatch.setattr(access_quality,'_read',read)
    report=access_quality.audit(tmp_path,NOW)
    assert checks(report)['CMS hospitals','Duplicate facility IDs']['Count']==1
    assert any(r['Record ID']=='003801' and r['Check']=='Duplicate facility IDs' for r in report['findings'])
    assert checks(report)['HRSA designations','Duplicate source feature keys']['Count']==0


def test_age_and_failed_refresh_are_not_source_vintage_or_service_uptime(tmp_path):
    fixtures(tmp_path)
    record_refresh(tmp_path/'hrsa.duckdb','hrsa-hpsa','failed')
    report=access_quality.audit(tmp_path,NOW+timedelta(days=2))
    cms,geo,hpsa=report['summary']
    assert cms['Fetch freshness']=='Refresh due' and geo['Fetch freshness']=='Within refresh window'
    assert cms['Source release / warehouse date / benchmark']=='2026-08-13'
    assert hpsa['Retained after failed refresh'] is True
    rows,p=hrsa.snapshot(tmp_path/'hrsa.duckdb');p['fetched_at']=(NOW+timedelta(minutes=5)).isoformat()
    hrsa.store(rows,p,tmp_path/'hrsa.duckdb')
    newer=access_quality.audit(tmp_path,NOW+timedelta(minutes=6))['summary'][2]
    assert newer['Retained after failed refresh'] is False
    assert newer['Last recorded refresh']=='failed'


def test_csv_formula_escape_and_identifiers_remain_strings():
    text=_csv([{'Record ID':'003801','Detail':'=1+1','Count':None}])
    row=pd.read_csv(io.StringIO(text),dtype=str,keep_default_na=False).iloc[0]
    assert row['Record ID']=='003801' and row['Detail']=="'=1+1" and row['Count']==''
    assert _csv([],['Source','Record ID','Check','Detail']).strip()=='Source,Record ID,Check,Detail'


def test_panel_integrates_without_loading_sources_and_demo_skips_audit(tmp_path,monkeypatch):
    from oregon_health import data,api_cache,access_quality_ui
    fixtures(tmp_path)
    path=tmp_path/'health.duckdb';data.store(data.demo_rows(),'Synthetic test fixture',path)
    monkeypatch.setattr(data,'DB_PATH',path)
    monkeypatch.setattr(api_cache,'needs_refresh',lambda *a:False)
    def forbidden(*a,**kw):raise AssertionError('Status panel cannot load sources')
    monkeypatch.setattr(hospitals,'refresh',forbidden);monkeypatch.setattr(hrsa,'refresh',forbidden)
    monkeypatch.setattr(geocoding,'refresh',forbidden)
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20).run()
    assert not app.exception
    summary=next(d.value for d in app.dataframe if 'Snapshot' in d.value.columns)
    assert summary.Source.tolist()==['CMS hospitals','Address matches','HRSA designations']
    monkeypatch.setattr(access_quality_ui,'audit',forbidden)
    app.sidebar.toggle(key='demo_mode').set_value(True).run()
    assert not app.exception
    assert any('Facility and shortage snapshots are not evaluated' in c.value for c in app.caption)


def test_county_mismatch_and_withheld_coordinates_are_separate_checks(tmp_path):
    fixtures(tmp_path)
    with duckdb.connect(str(tmp_path/'hospital_locations.duckdb')) as db:
        db.execute("UPDATE locations SET longitude=-122.65,latitude=45.52 WHERE facility_id='003801'")
        db.execute("UPDATE locations SET longitude=-122.65,latitude=45.52 WHERE facility_id='003804'")
    report=access_quality.audit(tmp_path,NOW);c=checks(report)
    assert c['Address matches','Accepted points failing current county check']['Count']==1
    assert c['Address matches','Invalid or contradictory coordinates']['Count']==2
    assert any(r['Record ID']=='003804' and r['Check']=='Cached ID absent from current CMS snapshot' for r in report['findings'])


def test_empty_loaded_snapshot_is_distinct_from_no_snapshot(tmp_path):
    fixtures(tmp_path)
    with duckdb.connect(str(tmp_path/'hospitals.duckdb')) as db:db.execute('DELETE FROM hospitals')
    report=access_quality.audit(tmp_path,NOW)
    assert report['summary'][0]['Snapshot']=='Loaded empty snapshot'
    assert report['summary'][0]['Cached records']==0
    assert all(r['CMS-listed hospitals']==0 for r in report['counties'])
    assert all(r['CMS-listed hospitals'] is None for r in access_quality.audit(tmp_path/'absent',NOW)['counties'])
