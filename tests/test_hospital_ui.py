from datetime import datetime, timedelta, timezone
import io
from pathlib import Path

import pandas as pd
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, census, data, hospitals
from oregon_health.hospital_ui import UNKNOWN, export_csv, filter_records
from oregon_health.status import record_refresh

APP = Path(__file__).resolve().parents[1] / "app.py"


def records():
    return [dict(zip(hospitals.COLUMNS, r)) for r in [
        ("003801", "Synthetic Hospital", "1 Example St", "Eugene", "OR", "00123", "LANE",
         "41039", "Critical Access Hospitals", "Government", True),
        ("003802", "Synthetic Hospital", "2 Example St", "Portland", "OR", "97201", "MULTNOMAH",
         "41051", "Acute Care Hospitals", None, False),
        ("003803", "Other Synthetic Facility", "3 Example St", None, "OR", None, "Unknown county",
         None, None, None, None),
    ]]


def source():
    return dict(dataset=hospitals.DATASET, source_url=hospitals.SOURCE_URL,
                download_url="https://data.cms.gov/provider-data/example.csv",
                released="2026-08-13", modified="2026-07-22",
                fetched_at=datetime.now(timezone.utc), sha256="a" * 64)


def make_app(tmp_path, monkeypatch, *, cached=True, health=True):
    health_path = tmp_path / "health.duckdb"
    if health:
        data.store(data.demo_rows(), "Synthetic test fixture", health_path)
    monkeypatch.setattr(data, "DB_PATH", health_path)
    monkeypatch.setattr(api_cache, "needs_refresh", lambda *a, **kw: not health)
    def no_cdc(*a, **kw):
        raise requests.ConnectionError("CDC unavailable")
    monkeypatch.setattr(api_cache, "refresh", no_cdc)
    def no_census(*a, **kw):
        raise census.CensusKeyRequired("Not configured for test")
    monkeypatch.setattr(census, "context", no_census)
    path = tmp_path / "hospitals.duckdb"
    monkeypatch.setattr(hospitals, "DB_PATH", path)
    if cached:
        hospitals.store([tuple(r[k] for k in hospitals.COLUMNS) for r in records()], source(), path)
    app = AppTest.from_file(APP, default_timeout=15)
    app.query_params["view"] = "access"
    return app, path


def result_table(app):
    return next(d.value for d in app.dataframe if "Facility ID" in d.value.columns)


def test_literal_filters_and_unresolved_counties():
    rows = records()
    assert len(filter_records(rows, query="sYnThEtIc HoSpItAl")) == 2
    assert filter_records(rows, query="00123")[0]["facility_id"] == "003801"
    assert filter_records(rows, query="003802")[0]["city"] == "Portland"
    assert filter_records(rows, query=".*") == []
    assert filter_records(rows, query="' OR TRUE --") == []
    assert len(filter_records(rows, county="41039", types=["Critical Access Hospitals"])) == 1
    assert filter_records(rows, county="41039", types=["Acute Care Hospitals"]) == []
    assert filter_records(rows, county=UNKNOWN)[0]["county_fips"] is None
    assert filter_records(rows, types=["Not reported"])[0]["facility_id"] == "003803"


def test_exports_provenance_unknowns_leading_zeros_and_spreadsheet_safety():
    rows = records()
    rows[0]["name"] = '=HYPERLINK("https://example.test")'
    rows[1]["address"] = "  +1+1"
    rows[2]["city"] = "\t=1+1"
    exported = pd.read_csv(io.StringIO(export_csv(rows, source())), dtype=str, keep_default_na=False)
    assert exported.facility_id.tolist() == ["003801", "003802", "003803"]
    assert exported.zip_code.iloc[0] == "00123"
    assert exported.name.iloc[0].startswith("'=")
    assert exported.address.iloc[1].startswith("'  +")
    assert exported.city.iloc[2].startswith("'\t=")
    assert exported.emergency_status.tolist() == ["Yes", "No", "Not reported"]
    assert exported.emergency_services.iloc[2] == "" and exported.county_fips.iloc[2] == ""
    assert set(exported.dataset) == {hospitals.DATASET}
    assert set(exported.source_url) == {hospitals.SOURCE_URL}
    assert set(exported.released) == {"2026-08-13"} and set(exported.sha256) == {"a" * 64}
    assert set(exported.coordinate_status) == {"Not supplied by CMS"}


def test_directory_is_opt_in_and_synthetic_mode_never_loads(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch, cached=False)
    def forbidden(*a, **kw):
        raise AssertionError("CMS should not be requested")
    monkeypatch.setattr(hospitals, "refresh", forbidden)
    app.run()
    assert not app.exception and not app.checkbox(key="show_hospitals").value
    assert not any(s.key == "health_measure" for s in app.selectbox)
    app.checkbox(key="show_hospitals").set_value(True)
    app.sidebar.toggle(key="demo_mode").set_value(True).run()
    assert not app.exception
    assert app.checkbox(key="show_hospitals").disabled and not app.checkbox(key="show_hospitals").value


def test_directory_filters_details_and_county_context(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    app.run().checkbox(key="show_hospitals").check().run()
    assert not app.exception
    assert any(m.label == "Oregon hospitals listed" and m.value == "3" for m in app.metric)
    table = result_table(app)
    assert len(table) == 3 and set(table["Emergency services"]) == {"Yes", "No", "Not reported"}
    app.selectbox(key="hospital_detail").set_value("003801").run()
    assert any(t.value == "CMS Facility ID: 003801 · This identifier is not an NPI." for t in app.caption)
    app.button(key="hospital_open_county").click().run()
    assert app.selectbox(key="profile_county").value == "41039"
    assert not app.exception
    app.radio(key="view_selector").set_value("access").run()
    app.text_input(key="hospital_query").set_value("Portland").run()
    assert len(result_table(app)) == 1
    assert app.selectbox(key="hospital_detail").value is None
    app.selectbox(key="hospital_detail").set_value("003802").run()
    assert any(t.value == "Emergency services: No" for t in app.text)
    app.selectbox(key="hospital_county").set_value("41039").run()
    assert any("No listed hospitals match" in i.value for i in app.info)
    assert not app.exception
    app.text_input(key="hospital_query").set_value("").run()
    app.selectbox(key="hospital_county").set_value(UNKNOWN).run()
    app.selectbox(key="hospital_detail").set_value("003803").run()
    assert any("could not be linked" in i.value for i in app.info)
    assert any(t.value == "Emergency services: Not reported" for t in app.text)


def test_cms_outage_without_cache_does_not_retry_on_filter_changes(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch, cached=False)
    calls = []
    def offline(*a, **kw):
        calls.append(True)
        raise requests.ConnectionError("private diagnostic")
    monkeypatch.setattr(hospitals, "refresh", offline)
    app.run().checkbox(key="show_hospitals").check().run()
    assert not app.exception and len(calls) == 1
    assert any("Hospital listings are currently unavailable" in i.value for i in app.info)
    app.run()
    assert len(calls) == 1 and not app.exception
    assert "private diagnostic" not in " ".join(w.value for w in app.warning)
    app.button(key="hospital_refresh").click().run()
    assert len(calls) == 2


def test_failed_refresh_keeps_snapshot_and_warning_across_reruns(tmp_path, monkeypatch):
    app, path = make_app(tmp_path, monkeypatch)
    old = source()
    old["fetched_at"] -= timedelta(days=2)
    hospitals.store([tuple(r[k] for k in hospitals.COLUMNS) for r in records()], old, path)
    calls = []
    def offline(*a, **kw):
        calls.append(True)
        record_refresh(path, hospitals.DATASET, "failed")
        raise requests.ConnectionError("private diagnostic")
    monkeypatch.setattr(hospitals, "refresh", offline)
    app.run().checkbox(key="show_hospitals").check().run()
    app.text_input(key="hospital_query").set_value("Eugene").run()
    assert not app.exception and len(calls) == 1
    assert len(result_table(app)) == 1
    assert any("previous hospital snapshot after a failed refresh" in w.value for w in app.warning)
    assert any("Hospital refresh is due" in w.value for w in app.warning)


def test_directory_remains_available_without_cdc(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch, health=False)
    app.run().checkbox(key="show_hospitals").check().run()
    assert not app.exception and len(result_table(app)) == 3
    app.selectbox(key="hospital_detail").set_value("003801").run()
    assert any("County profiles need CDC health data" in i.value for i in app.info)


def geocode_cache(tmp_path, monkeypatch):
    from oregon_health import geocoding
    path=tmp_path/"locations.duckdb"
    monkeypatch.setattr(geocoding,"DB_PATH",path)
    rows=records()
    matches=[(r["facility_id"],geocoding.address_hash(r),
              "matched_exact" if index<2 else "missing_address",
              [-123.08,-122.65,None][index], [44.05,45.52,None][index],
              r["address"] if index<2 else None) for index,r in enumerate(rows)]
    provenance=dict(source_url=geocoding.SOURCE_URL,benchmark=geocoding.BENCHMARK,benchmark_id="4",
                    fetched_at=datetime.now(timezone.utc),fingerprint=geocoding.fingerprint(rows))
    geocoding.store(matches,provenance,path)
    return path,provenance


def test_optional_location_map_details_and_export(tmp_path,monkeypatch):
    from oregon_health import geocoding
    app,_=make_app(tmp_path,monkeypatch)
    path,provenance=geocode_cache(tmp_path,monkeypatch)
    def forbidden(*a,**kw):raise AssertionError("Fresh matches must not call Census")
    monkeypatch.setattr(geocoding,"refresh",forbidden)
    app.run().checkbox(key="show_hospitals").check().run()
    app.checkbox(key="show_hospital_locations").check().run()
    assert not app.exception
    assert any("2 of 3 filtered hospitals have mapped" in c.value for c in app.caption)
    app.selectbox(key="hospital_detail").set_value("003801").run()
    assert any("not a verified building or entrance" in c.value for c in app.caption)
    locations,_=geocoding.snapshot(records(),path)
    exported=pd.read_csv(io.StringIO(export_csv(records(),source(),locations,provenance)),dtype=str,keep_default_na=False)
    assert exported.location_status.tolist()==["matched_exact","matched_exact","missing_address"]
    assert exported.longitude.iloc[0]=="-123.08" and exported.longitude.iloc[2]==""
    assert set(exported.location_source)=={geocoding.SOURCE_URL}
    app.selectbox(key="hospital_county").set_value(UNKNOWN).run()
    assert any("No accepted address matches" in i.value for i in app.info)
    assert not app.exception


def test_geocoder_outage_keeps_directory_and_does_not_repeat_on_search(tmp_path,monkeypatch):
    from oregon_health import geocoding
    app,_=make_app(tmp_path,monkeypatch)
    monkeypatch.setattr(geocoding,"DB_PATH",tmp_path/"no-locations.duckdb")
    calls=[]
    def offline(*a,**kw):
        calls.append(True)
        raise requests.ConnectionError("private diagnostic")
    monkeypatch.setattr(geocoding,"refresh",offline)
    app.run().checkbox(key="show_hospitals").check().run()
    assert calls==[]
    app.checkbox(key="show_hospital_locations").check().run()
    app.text_input(key="hospital_query").set_value("Eugene").run()
    assert not app.exception and len(result_table(app))==1 and len(calls)==1
    assert any("No accepted address matches" in i.value for i in app.info)
    assert "private diagnostic" not in " ".join(w.value for w in app.warning)
    app.button(key="geocode_refresh").click().run()
    assert len(calls)==2
    app.sidebar.toggle(key="demo_mode").set_value(True).run()
    assert not app.exception and len(calls)==2


def test_cached_locations_retain_failure_warning_and_hide_changed_address(tmp_path,monkeypatch):
    from oregon_health import geocoding
    app,cms_path=make_app(tmp_path,monkeypatch)
    path,provenance=geocode_cache(tmp_path,monkeypatch)
    changed=records();changed[0]["address"]="Different source address"
    hospitals.store([tuple(r[k] for k in hospitals.COLUMNS) for r in changed],source(),cms_path)
    def offline(*a,**kw):
        record_refresh(path,"xubh-q36u","failed")
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(geocoding,"refresh",offline)
    app.run().checkbox(key="show_hospitals").check().run()
    app.checkbox(key="show_hospital_locations").check().run()
    assert not app.exception
    assert any("1 of 3 filtered hospitals have mapped" in c.value for c in app.caption)
    assert any("Only unchanged source addresses retain points" in w.value for w in app.warning)
    app.selectbox(key="hospital_detail").set_value("003801").run()
    assert any("Source address changed" in c.value for c in app.caption)
