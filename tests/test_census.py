import json
from datetime import datetime, timedelta

import duckdb
import pytest
import requests
from streamlit.testing.v1 import AppTest
from pathlib import Path

from oregon_health import census
from oregon_health.profile import county_names


def payload():
    header = ["NAME", *census.FIELDS, "state", "county"]
    return [header, *[[f"{name} County, Oregon", "10000", "250", "20.0", "1.5", "12.0", "2.0", fips[:2], fips[2:]] for fips, name in county_names().items()]]


def test_normalize_fips_missing_and_denominators():
    data = payload()
    records = census.normalize(data)
    assert len(records) == 36
    assert next(r for r in records if r[0] == "41001") == ("41001", 10000, 250, 20, 1.5, 12, 2)
    data[1][1] = "-666666666"
    data[1][3] = None
    assert census.normalize(data)[0][1] is None
    assert census.normalize(data)[0][3] is None
    for invalid in [payload()[:-1], [*payload(), payload()[1]]]:
        with pytest.raises(ValueError):
            census.normalize(invalid)
    data = payload()
    data[1][5] = "101"
    with pytest.raises(ValueError):
        census.normalize(data)
    with pytest.raises(ValueError):
        census.release_url(2050)


def test_fetch_checks_variable_meaning_and_oregon_scope(monkeypatch):
    calls = []
    variables = {f: {"label": "Margin of Error"} for f in census.FIELDS}
    variables["DP05_0001E"]["label"] = "Estimate!!Total population"
    variables["DP05_0024PE"]["label"] = "Percent!!65 years and over"
    variables["DP03_0128PE"]["label"] = "Percent!!All people"
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, chunk_size): yield json.dumps(self.value).encode()
        status_code = 200
        def __init__(self, value):
            self.value = value
        def raise_for_status(self):
            pass
        def json(self):
            return self.value
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"variables": variables} if url.endswith("variables.json") else payload())
    monkeypatch.setattr(requests, "get", get)
    assert len(census.fetch(2023, api_key="synthetic-test-key")) == 36
    assert calls[0][1]["params"] is None
    assert calls[-1][1]["params"]["key"] == "synthetic-test-key"
    assert calls[-1][1]["params"]["in"] == "state:41"
    assert calls[-1][1]["params"]["for"] == "county:*"
    variables["DP05_0024PE"]["label"] = "Percent!!Under 18 years"
    with pytest.raises(ValueError, match="definitions"):
        census.fetch(2023, api_key="synthetic-test-key")


def test_cache_reuse_year_isolation_and_failed_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "synthetic-test-key")
    calls = []
    clock = [1000.0]
    monkeypatch.setattr("oregon_health.api_cache.time.time", lambda: clock[0])
    def fetch(year, api_key=None):
        calls.append(year)
        records = census.normalize(payload())
        return [(fips, 39000 if fips == "41039" else population, *values) for fips, population, *values in records]
    monkeypatch.setattr(census, "fetch", fetch)
    result = census.context("41051", 2023, tmp_path)
    assert result["population"] == 10000
    assert result["year"] == 2023
    assert result["warning"] is None
    assert census.context("41039", 2023, tmp_path)["population"] == 39000
    assert calls == [2023]
    assert census.context("41051", 2022, tmp_path)["year"] == 2022
    assert calls == [2023, 2022]
    path = tmp_path / "census_2023.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("UPDATE provenance SET imported_at = ?", [datetime.now() - timedelta(days=31)])
    clock[0] += 301
    def offline(year, api_key=None):
        calls.append(year)
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(census, "fetch", offline)
    stale = census.context("41051", 2023, tmp_path)
    assert stale["population"] == 10000 and stale["warning"]
    census.context("41051", 2023, tmp_path)
    assert calls == [2023, 2022, 2023]  # Failed attempts count toward cooldown.
    with pytest.raises(requests.ConnectionError):
        census.context("41051", 2021, tmp_path)
    assert not (tmp_path / "census_2021.duckdb").exists()


def test_snapshot_failure_preserves_old_records(tmp_path):
    path = tmp_path / "census.duckdb"
    records = census.normalize(payload())
    census.store(records, 2023, path)
    with pytest.raises(duckdb.Error):
        census.store([records[0], records[0]], 2023, path)
    with duckdb.connect(str(path), read_only=True) as db:
        assert db.execute("SELECT count(*) FROM demographics").fetchone()[0] == 36


def test_profile_shows_context_and_survives_census_outage(tmp_path, monkeypatch):
    from oregon_health import data, api_cache
    # Use the existing health fixture but keep the profile in real-data mode.
    path = tmp_path / "health.duckdb"
    data.store(data.demo_rows(), "Synthetic test fixture", path)
    monkeypatch.setattr(data, "DB_PATH", path)
    monkeypatch.setattr(api_cache, "needs_refresh", lambda dataset: False)
    result = dict(zip(census.COLUMNS, [10000, 250, 20, 1.5, 12, 2]))
    result.update(source=census.release_url(2022), imported_at=datetime(2026, 1, 1), year=2022, warning=None)
    monkeypatch.setattr(census, "context", lambda *args, **kwargs: result)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=15)
    app.query_params.update(county="41051", year="2022")
    app.run()
    assert not app.exception
    assert any(m.label == "Population" and m.value == "10,000" for m in app.metric)
    assert any("2018–2022" in c.value for c in app.caption)
    assert any("90% margin of error" in c.value for c in app.caption)
    def offline(*args, **kwargs):
        raise requests.ConnectionError("Private diagnostic")
    monkeypatch.setattr(census, "context", offline)
    app.run()
    assert not app.exception
    assert any("Census context is currently unavailable" in item.value for item in app.info)
    assert any(m.label == "Diabetes" and m.value == "10.0%" for m in app.metric)
    assert "Private diagnostic" not in " ".join(i.value for i in app.info)


def test_live_style_metadata_lists_margins_as_attributes():
    variables = {
        "DP05_0001E": {"label": "Estimate!!SEX AND AGE!!Total population", "attributes": "DP05_0001EA,DP05_0001M,DP05_0001MA"},
        "DP05_0024PE": {"label": "Percent!!SEX AND AGE!!Total population!!65 years and over", "attributes": "DP05_0024PEA,DP05_0024PM,DP05_0024PMA"},
        "DP03_0128PE": {"label": "Percent!!POVERTY!!All people", "attributes": "DP03_0128PEA,DP03_0128PM,DP03_0128PMA"},
    }
    census.validate_variables(variables)
    variables["DP05_0024PE"]["attributes"] = "DP05_0024PEA"
    with pytest.raises(ValueError):
        census.validate_variables(variables)


def test_key_requirements_and_error_redaction(monkeypatch):
    import traceback
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    with pytest.raises(census.CensusKeyRequired):
        census.fetch(2023)
    secret = "synthetic-private-test-value"
    def fail(*args, **kwargs):
        raise requests.ConnectionError(f"Failed URL https://api.census.gov/data/?key={secret}")
    monkeypatch.setattr(requests, "get", fail)
    with pytest.raises(census.CensusUnavailable) as error:
        census._get_json("https://api.census.gov/data/", params={"key": secret})
    assert secret not in "".join(traceback.format_exception(error.type, error.value, error.tb))


def test_key_is_only_sent_to_data_endpoint_and_redirects_are_disabled(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        status_code = 302
        def raise_for_status(self):
            pass
        def json(self):
            raise AssertionError("Redirects must not be parsed or followed")
    def get(url, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(census.CensusUnavailable):
        census._get_json("https://api.census.gov/data/", params={"key": "synthetic-test-key"})
    assert calls[0]["allow_redirects"] is False
