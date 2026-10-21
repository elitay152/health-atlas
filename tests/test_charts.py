import duckdb

from oregon_health.api_cache import catalog_is_current, needs_refresh
from oregon_health.charts import intervals, distribution, condition_profile, heatmap
from oregon_health.compare import comparison_rows
from oregon_health.data import MEASURES, demo_rows, store, comparison, county_profile


def test_expanded_catalog_and_legacy_cache_upgrade(tmp_path):
    path = tmp_path / "old.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE provenance(source VARCHAR, imported_at TIMESTAMP)")
        db.execute("INSERT INTO provenance VALUES ('https://data.cdc.gov/d/abcd-1234', current_timestamp)")
    assert not catalog_is_current(path)
    assert needs_refresh("abcd-1234", path)
    store(demo_rows(), "https://data.cdc.gov/d/abcd-1234", path)
    assert catalog_is_current(path)
    assert not needs_refresh("abcd-1234", path)
    assert set(MEASURES) == {"DIABETES", "CASTHMA", "OBESITY", "DEPRESSION", "COPD", "CHD", "STROKE", "ARTHRITIS", "BPHIGH"}
    with duckdb.connect(str(path)) as db:
        assert len(county_profile(db, "41051", 2022)) == 9
        assert len(comparison(db, "DEPRESSION", 2022)) == 3


def test_charts_preserve_intervals_missing_cells_and_limits(tmp_path):
    path = tmp_path / "data.duckdb"
    rows = [r for r in demo_rows() if not (r["measureid"] == "COPD" and r["locationid"] == "41051")]
    store(rows, "test", path)
    with duckdb.connect(str(path)) as db:
        frame = comparison(db, "DIABETES", 2022)
        profile = county_profile(db, "41051", 2022)
        full = comparison_rows(db, ["41051", "41039"], 2022, "test", "date", demo=True)
    spec = intervals(frame).to_dict()
    assert spec["layer"][0]["encoding"]["x"]["field"] == "low"
    assert spec["layer"][0]["encoding"]["x2"]["field"] == "high"
    assert spec["layer"][1]["encoding"]["x"]["field"] == "prevalence"
    assert len(condition_profile(profile).to_dict()["datasets"].popitem()[1]) == 8
    histogram = distribution(frame).to_dict()
    assert histogram["layer"][1]["transform"][0]["aggregate"][0]["op"] == "median"
    matrix = heatmap(full).to_dict()
    values = next(iter(matrix["datasets"].values()))
    missing = next(row for row in values if row["county"] == "Multnomah" and row["metric"] == "COPD")
    assert missing["label"] == "—" and missing["estimate"] is None
    assert matrix["layer"][0]["encoding"]["color"]["condition"]["scale"]["domain"] == [0, 100]
    assert len(values) == 18


def test_new_condition_links_and_partial_snapshots(tmp_path, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from oregon_health import data, api_cache
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    demo = AppTest.from_file(app_path, default_timeout=20)
    demo.query_params.update(demo="1", measure="DEPRESSION", year="2022")
    demo.run()
    assert not demo.exception
    assert demo.selectbox(key="health_measure").value == "DEPRESSION"
    assert len(demo.selectbox(key="health_measure").options) == 9
    # An older/incomplete release stays usable and offers only loaded measures.
    path = tmp_path / "partial.duckdb"
    rows = [r for r in demo_rows() if r["measureid"] in {"DIABETES", "CASTHMA", "OBESITY"}]
    store(rows, "Retained test snapshot", path)
    monkeypatch.setattr(data, "DB_PATH", path)
    monkeypatch.setattr(api_cache, "needs_refresh", lambda dataset: False)
    partial = AppTest.from_file(app_path, default_timeout=20)
    partial.query_params.update(measure="DEPRESSION",year="2022")
    partial.run()
    assert not partial.exception
    assert partial.selectbox(key="health_measure").value == "DIABETES"
    assert len(partial.selectbox(key="health_measure").options) == 3
    assert any("linked health measure is unavailable" in w.value for w in partial.warning)
