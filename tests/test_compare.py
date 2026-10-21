from datetime import datetime
from pathlib import Path

import duckdb
import pytest
import requests
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, census, data
from oregon_health.compare import comparison_rows, display_table


def context(fips, year, **kwargs):
    return dict(fips=fips, year=year, population=10000 if fips == "41051" else 20000,
                population_moe=250, age65_pct=20, age65_moe=1.5, poverty_pct=None, poverty_moe=None,
                source=census.release_url(year), imported_at=datetime(2026, 1, 1), warning=None)


def test_comparison_preserves_geography_year_missing_and_uncertainty(tmp_path):
    path = tmp_path / "health.duckdb"
    rows = data.demo_rows()
    rows = [r for r in rows if not (r["locationid"] == "41039" and r["measureid"] == "CASTHMA")]
    old = dict(rows[0], year="2021", data_value=8, low_confidence_limit=7, high_confidence_limit=9)
    data.store([*rows, old], "Synthetic source", path)
    contexts = {fips: context(fips, 2022) for fips in ["41039", "41051"]}
    with duckdb.connect(str(path), read_only=True) as db:
        result = comparison_rows(db, ["41039", "41051"], 2022, "Synthetic source", "2026-01-01", contexts)
        assert len(result) == 12
        assert result[(result.fips == "41039") & (result.metric == "Current asthma")].status.iloc[0] == "unavailable"
        assert result[(result.fips == "41051") & (result.metric == "Diabetes")].estimate.iloc[0] == 10
        assert result[result.metric == "Below poverty level"].status.eq("unavailable").all()
        population = result[result.metric == "Population"]
        assert list(population.estimate) == [20000, 10000]
        assert population.period_start.eq(2018).all()
        assert population.confidence.eq(90).all()
        table = display_table(result)
        assert list(table.columns) == ["Measure", "Period · uncertainty", "Lane", "Multnomah"]
        assert table[table.Measure == "Current asthma"].Lane.iloc[0] == "Unavailable"
        assert table[table.Measure == "Diabetes"].Multnomah.iloc[0] == "10.0% (9.0–11.0%)"
        assert table[table.Measure == "Residents age 65+"].Multnomah.iloc[0] == "20.0% ±1.5 pp"
        assert "population_universe" in result.to_csv(index=False)
        with pytest.raises(ValueError, match="release year"):
            comparison_rows(db, ["41051"], 2021, "source", "date", contexts)
        with pytest.raises(ValueError, match="Unknown Oregon"):
            comparison_rows(db, ["41051' OR 1=1 --"], 2022, "source", "date")
        assert display_table(comparison_rows(db, [], 2022, "source", "date")).empty


def app_with_health(tmp_path, monkeypatch):
    path = tmp_path / "health.duckdb"
    data.store(data.demo_rows(), "Synthetic test fixture", path)
    monkeypatch.setattr(data, "DB_PATH", path)
    monkeypatch.setattr(api_cache, "needs_refresh", lambda dataset: False)
    return AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=15)


def test_comparison_ui_opt_in_and_outage(tmp_path, monkeypatch):
    calls = []
    def fetch(fips, year, **kwargs):
        calls.append(fips)
        return context(fips, year)
    monkeypatch.setattr(census, "context", fetch)
    app = app_with_health(tmp_path, monkeypatch).run()
    assert not app.exception
    assert calls == []
    table = next(element.value for element in app.dataframe if "Measure" in element.value.columns)
    assert table[table.Measure == "Population"].Multnomah.iloc[0] == "Not loaded"
    app.checkbox[0].check().run()
    assert not app.exception
    assert len(calls) == 3
    table = next(element.value for element in app.dataframe if "Measure" in element.value.columns)
    assert table[table.Measure == "Population"].Multnomah.iloc[0] == "10,000 ±250"
    def offline(*args, **kwargs):
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(census, "context", offline)
    app.run()
    assert not app.exception
    table = next(element.value for element in app.dataframe if "Measure" in element.value.columns)
    assert "10.0%" in table[table.Measure == "Diabetes"].Multnomah.iloc[0]
    assert table[table.Measure == "Population"].Multnomah.iloc[0] == "Unavailable"
    assert any("Health comparisons remain available" in i.value for i in app.info)
    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert any("Choose counties above" in i.value for i in app.info)


def test_demo_comparison_never_loads_real_census(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Demo must not load real demographics")
    monkeypatch.setattr(census, "context", unexpected)
    app = app_with_health(tmp_path, monkeypatch)
    app.query_params["demo"] = "1"
    app.run()
    assert not app.exception
    assert app.checkbox[0].disabled
    table = next(element.value for element in app.dataframe if "Measure" in element.value.columns)
    assert table[table.Measure == "Population"].Multnomah.iloc[0] == "Unavailable"
