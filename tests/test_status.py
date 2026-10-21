from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import requests
import pytest
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, census, data
from oregon_health.status import health_report, census_report, record_refresh, last_refresh


def test_health_coverage_selected_year_and_freshness(tmp_path):
    path = tmp_path / "health.duckdb"
    rows = data.demo_rows()
    rows = [r for r in rows if not (r["locationid"] == "41039" and r["measureid"] == "CASTHMA")]
    data.store([*rows, dict(rows[0], year="2021")], "PLACES County | https://data.cdc.gov/d/abcd-1234", path)
    report = health_report(path, 2022, "abcd-1234")
    assert report["freshness"] == "Within refresh window"
    assert report["source_matches"]
    asthma = report["coverage"].set_index("Measure").loc["Current asthma"]
    assert asthma["Available counties"] == 2
    assert asthma["Missing estimates"] == 34
    assert any(r.County == "Lane" and r.Measure == "Current asthma" for r in report["missing"].itertuples())
    due = health_report(path, 2022, "abcd-1234", now=datetime.now(timezone.utc)+timedelta(days=2))
    assert due["freshness"] == "Refresh due"
    assert not health_report(path, 2022, "efgh-5678")["source_matches"]
    assert health_report(path, 2022, "abcd-1234", demo=True)["freshness"] == "Synthetic demo"


def test_status_is_persistent_and_keeps_failure_separate_from_coverage(tmp_path, monkeypatch):
    path = tmp_path / "health.duckdb"
    data.store(data.demo_rows(), "Original", path)
    def offline(dataset):
        raise requests.ConnectionError("private diagnostic must not be stored")
    monkeypatch.setattr(api_cache, "fetch", offline)
    try:
        api_cache.refresh("abcd-1234", path)
    except requests.ConnectionError:
        pass
    assert last_refresh(path, "abcd-1234")["outcome"] == "failed"
    assert last_refresh(path, "efgh-5678") is None
    report = health_report(path, 2022, "abcd-1234")
    assert report["loaded"] and report["event"]["outcome"] == "failed"
    assert report["coverage"]["Available counties"].tolist() == [3] * 9
    assert "private diagnostic" not in str(last_refresh(path, "abcd-1234"))
    failed_event = last_refresh(path, "abcd-1234")
    with pytest.raises(api_cache.RefreshLimited):
        api_cache.refresh("abcd-1234", path)
    assert last_refresh(path, "abcd-1234") == failed_event
    original_clock = api_cache.time.time
    monkeypatch.setattr(api_cache.time, "time", lambda: original_clock() + 301)
    monkeypatch.setattr(api_cache, "fetch", lambda dataset: (data.demo_rows(), "Fresh source"))
    assert api_cache.refresh("abcd-1234", path) == 27
    assert last_refresh(path, "abcd-1234")["outcome"] == "success"
    empty = health_report(tmp_path / "missing.duckdb", 2022, "abcd-1234")
    assert not empty["loaded"] and empty["coverage"].empty


def test_census_missing_values_are_not_missing_snapshot(tmp_path, monkeypatch):
    assert not census_report(tmp_path, 2023)["loaded"]
    path = tmp_path / "census_2023.duckdb"
    census.store([("41051", 10000, None, 20, 1.5, None, None)], 2023, path)
    report = census_report(tmp_path, 2023)
    assert report["loaded"]
    indexed = report["coverage"].set_index("Measure")
    assert indexed.loc["Population", "Available counties"] == 1
    assert indexed.loc["Population", "Without uncertainty"] == 1
    assert indexed.loc["Below poverty level", "Available counties"] == 0
    assert report["freshness"] == "Within refresh window"
    record_refresh(path, 2023, "key_required")
    assert census_report(tmp_path, 2023)["event"]["outcome"] == "key_required"
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    with pytest.raises(census.CensusKeyRequired):
        census.context("41051", 2022, tmp_path)
    unloaded = census_report(tmp_path, 2022)
    assert not unloaded["loaded"] and unloaded["coverage"].empty
    assert unloaded["event"]["outcome"] == "key_required"


def test_panel_is_read_only_and_demo_labeled(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Panel must not fetch data")
    monkeypatch.setattr(api_cache, "refresh", unexpected)
    monkeypatch.setattr(census, "context", unexpected)
    app = AppTest.from_file(Path(__file__).resolve().parents[1]/"app.py", default_timeout=20)
    app.query_params.update(demo="1",year="2022")
    app.run()
    assert not app.exception
    assert any("Data status & coverage" == s.value for s in app.subheader)
    assert any("Synthetic demo only" in i.value for i in app.info)
    assert any("Opening this panel makes no network requests" in c.value for c in app.caption)
