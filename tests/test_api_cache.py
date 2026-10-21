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
    clock = [1000.0]
    monkeypatch.setattr(api_cache.time, "time", lambda: clock[0])
    monkeypatch.setattr(api_cache, "fetch", lambda dataset: (data.demo_rows(), SOURCE))
    assert api_cache.refresh(DATASET, path) == 27
    assert not api_cache.needs_refresh(DATASET, path)
    assert api_cache.needs_refresh("efgh-5678", path)
    assert api_cache.needs_refresh(DATASET, path, now=datetime.now(timezone.utc) + timedelta(days=2))
    def offline(dataset):
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(api_cache, "fetch", offline)
    clock[0] += 301
    with pytest.raises(requests.ConnectionError):
        api_cache.refresh(DATASET, path)
    with duckdb.connect(str(path)) as db:
        assert db.execute("SELECT count(*) FROM estimates").fetchone()[0] == 27
        assert db.execute("SELECT source FROM provenance").fetchone()[0] == SOURCE


def test_app_download_cache_and_refresh_failure(tmp_path, monkeypatch):
    path = tmp_path / "cache.duckdb"
    monkeypatch.setattr(data, "DB_PATH", path)
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(api_cache.time, "time", lambda: clock[0])
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
    assert not app.text_input  # Visitors cannot replace the shared release.
    app.selectbox(key="health_measure").select("OBESITY").run()
    assert len(calls) == 1  # Widget interactions use cached SQL data.
    app.sidebar.button[0].click().run()
    assert len(calls) == 1
    assert any("five minutes" in item.value for item in app.info)
    clock[0] += 301
    app.sidebar.button[0].click().run()
    assert len(calls) == 2
    def offline(dataset):
        calls.append(dataset)
        raise requests.ConnectionError("Offline test")
    monkeypatch.setattr(api_cache, "fetch", offline)
    clock[0] += 301
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert "Could not refresh" in app.error[0].value
    assert "Offline test" not in app.error[0].value
    assert "previously downloaded" in app.warning[0].value
    assert len(app.dataframe[0].value) == 3
    app.selectbox(key="health_measure").select("DIABETES").run()
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


def test_cooldown_covers_failed_downloads_and_different_sources(tmp_path, monkeypatch):
    path = tmp_path / "cache.duckdb"
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(api_cache.time, "time", lambda: clock[0])
    def offline(dataset):
        calls.append(dataset)
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(api_cache, "fetch", offline)
    with pytest.raises(requests.ConnectionError):
        api_cache.refresh(DATASET, path)
    with pytest.raises(api_cache.RefreshLimited):
        api_cache.refresh("efgh-5678", path)
    assert calls == [DATASET]
    clock[0] += 301
    with pytest.raises(requests.ConnectionError):
        api_cache.refresh(DATASET, path)
    assert len(calls) == 2


def test_simultaneous_refresh_reservations_allow_only_one(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(8)
    path = tmp_path / "cache.duckdb"
    def reserve(_):
        barrier.wait()
        try:
            api_cache._reserve_refresh(path)
            return True
        except api_cache.RefreshLimited:
            return False
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert sum(workers.map(reserve, range(8))) == 1
