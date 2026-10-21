from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, data

DATASET = "abcd-1234"
SOURCE = f"PLACES County Test Release | https://data.cdc.gov/d/{DATASET}"


def test_cache_refresh_expiry_and_failure_preservation(tmp_path, monkeypatch):
    path = tmp_path / "cache.duckdb"
    assert api_cache.needs_refresh(DATASET, path)
    monkeypatch.setattr(api_cache, "fetch", lambda dataset: (data.demo_rows(), SOURCE))
    assert api_cache.refresh(DATASET, path) == 9
    assert not api_cache.needs_refresh(DATASET, path)
    assert api_cache.needs_refresh("efgh-5678", path)
    assert api_cache.needs_refresh(DATASET, path, now=datetime.now(timezone.utc) + timedelta(days=2))
    def offline(dataset):
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(api_cache, "fetch", offline)
    with pytest.raises(requests.ConnectionError):
        api_cache.refresh(DATASET, path)
    with duckdb.connect(str(path)) as db:
        assert db.execute("SELECT count(*) FROM estimates").fetchone()[0] == 9
        assert db.execute("SELECT source FROM provenance").fetchone()[0] == SOURCE


def test_app_download_cache_and_refresh_failure(tmp_path, monkeypatch):
    path = tmp_path / "cache.duckdb"
    monkeypatch.setattr(data, "DB_PATH", path)
    calls = []
    def fetch(dataset):
        calls.append(dataset)
        return data.demo_rows(), f"PLACES County Test | https://data.cdc.gov/d/{dataset}"
    monkeypatch.setattr(api_cache, "fetch", fetch)
    # app passes no path to helpers, so bind both to the isolated test database.
    original_refresh = api_cache.refresh
    original_needs_refresh = api_cache.needs_refresh
    monkeypatch.setattr(api_cache, "refresh", lambda dataset: original_refresh(dataset, path))
    monkeypatch.setattr(api_cache, "needs_refresh", lambda dataset: original_needs_refresh(dataset, path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=15).run()
    assert not app.exception
    assert len(calls) == 1
    assert len(app.dataframe[0].value) == 3
    app.sidebar.selectbox[0].select("OBESITY").run()
    assert len(calls) == 1  # Widget interactions use cached SQL data.
    app.sidebar.button[0].click().run()
    assert len(calls) == 2
    def offline(dataset):
        calls.append(dataset)
        raise requests.ConnectionError("Offline test")
    monkeypatch.setattr(api_cache, "fetch", offline)
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert "Offline test" in app.error[0].value
    assert "previously downloaded" in app.warning[0].value
    assert len(app.dataframe[0].value) == 3
    app.sidebar.selectbox[0].select("DIABETES").run()
    assert len(calls) == 3


def test_app_empty_cache_failure_does_not_silently_show_demo(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DB_PATH", tmp_path / "missing.duckdb")
    monkeypatch.setattr(api_cache, "needs_refresh", lambda dataset: True)
    calls = []
    def offline(dataset):
        calls.append(dataset)
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(api_cache, "refresh", offline)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=15).run()
    assert not app.exception
    assert app.error
    assert not app.dataframe
    app.run()
    assert len(calls) == 1
    app.sidebar.button[0].click().run()
    assert len(calls) == 2
