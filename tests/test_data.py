from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from oregon_health.data import comparison, demo_rows, normalize, store


def test_import_queries_and_repeatability(tmp_path):
    path = tmp_path / "health.duckdb"
    for _ in range(2):
        assert store(demo_rows(), "synthetic test", path) == 27
    with duckdb.connect(str(path)) as db:
        frame = comparison(db, "DIABETES", 2022)
        assert list(frame.county) == ["Clackamas", "Lane", "Multnomah"]
        assert list(frame.prevalence) == [12, 11, 10]
        assert comparison(db, "DIABETES' OR 1=1 --", 2022).empty


def test_invalid_import_preserves_database(tmp_path):
    path = tmp_path / "health.duckdb"
    store(demo_rows(), "original", path)
    rows = demo_rows()
    rows[0]["data_value"] = 101
    with pytest.raises(ValueError):
        store(rows, "invalid", path)
    with duckdb.connect(str(path)) as db:
        assert db.execute("SELECT count(*) FROM estimates").fetchone()[0] == 27
        assert db.execute("SELECT source FROM provenance").fetchone()[0] == "original"


def test_missing_and_filtered_records():
    rows = demo_rows()
    rows[0]["data_value"] = None
    rows[1]["stateabbr"] = "WA"
    rows[2]["data_value_type"] = "Age-adjusted prevalence"
    assert len(normalize(rows)) == 24
    with pytest.raises(ValueError, match="Duplicate"):
        normalize(demo_rows() * 2)


def test_app_comparison(monkeypatch):
    import requests
    def offline(*args, **kwargs):
        raise requests.ConnectionError("Offline test")
    monkeypatch.setattr("oregon_health.api_cache.refresh", offline)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=15).run()
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert "DEMO" in app.warning[0].value
    assert len(app.dataframe[0].value) == 3
    app.selectbox(key="health_measure").select("OBESITY").run()
    assert not app.exception
    assert min(app.dataframe[0].value["Estimate (%)"]) == 30
    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert any("Select at least" in item.value for item in app.info)
