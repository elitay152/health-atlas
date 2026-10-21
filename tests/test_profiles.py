from pathlib import Path

import duckdb
from streamlit.testing.v1 import AppTest

from oregon_health import api_cache
from oregon_health.data import county_profile, demo_rows, store
from oregon_health.profile import selected_county

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_profile_queries_keep_years_and_counties_separate(tmp_path):
    rows = demo_rows()
    extra = dict(rows[0], year="2021", data_value=8, low_confidence_limit=7, high_confidence_limit=9)
    path = tmp_path / "test.duckdb"
    store([*rows, extra], "synthetic test", path)
    with duckdb.connect(str(path)) as db:
        result = county_profile(db, "41051", 2022)
        assert set(result.measure) == {"DIABETES", "CASTHMA", "OBESITY"}
        assert result[result.measure == "DIABETES"].prevalence.iloc[0] == 10
        assert len(county_profile(db, "41051", 2021)) == 1
        assert county_profile(db, "41001", 2022).empty
        assert county_profile(db, "41051' OR 1=1 --", 2022).empty


def test_browser_selection_is_validated():
    event = {"selection": {"objects": {"oregon-counties": [{"properties": {"fips": "41051"}}]}}}
    assert selected_county(event) == "41051"
    for value in [None, {}, {"selection": None}, {"selection": {"objects": []}},
                  {"selection": {"objects": {"other-layer": [{"fips": "41051"}]}}},
                  {"selection": {"objects": {"oregon-counties": [{"fips": "99999"}]}}},
                  {"selection": {"objects": {"oregon-counties": [{"fips": ["41051"]}]}}}]:
        assert selected_county(value) is None


def demo_app(monkeypatch, county="41051", year="2022"):
    def offline(*args, **kwargs):
        raise AssertionError("Demo profiles must not request CDC data")
    monkeypatch.setattr(api_cache, "refresh", offline)
    app = AppTest.from_file(APP, default_timeout=15)
    app.query_params.update(county=county, year=year, measure="DIABETES", demo="1")
    return app.run()


def test_shared_demo_profile_and_add_comparison(monkeypatch):
    app = demo_app(monkeypatch)
    assert not app.exception
    assert any(title.value == "Multnomah County" for title in app.subheader)
    assert any(m.label == "Diabetes" and m.value == "10.0%" for m in app.metric)
    assert app.selectbox(key="profile_county").value == "41051"
    assert app.query_params["county"] == "41051"
    # Lane and Clackamas remain selected; add Multnomah via the profile action.
    app.multiselect[0].set_value(["Lane", "Clackamas"]).run()
    button = next(b for b in app.button if b.label == "Add county to comparison")
    assert not button.disabled
    button.click().run()
    assert "Multnomah" in app.multiselect[0].value
    assert not app.exception
    # Profile selector updates shareable link without changing comparison.
    app.selectbox(key="profile_county").select("41039").run()
    assert app.query_params["county"] == "41039"
    assert any(title.value == "Lane County" for title in app.subheader)
    app.sidebar.selectbox[0].select("OBESITY").run()
    assert app.query_params["measure"] == "OBESITY"
    app.run()
    assert app.sidebar.selectbox[0].value == "OBESITY"
    app.selectbox(key="profile_county").set_value(None).run()
    assert "county" not in app.query_params
    assert not app.exception


def test_missing_and_invalid_profiles(monkeypatch):
    app = demo_app(monkeypatch, county="41001", year="not-a-year")
    assert not app.exception
    assert sum(m.value == "Unavailable" for m in app.metric) == 3
    assert any("Missing values are not zero" in item.value for item in app.info)
    assert next(b for b in app.button if b.label == "Add county to comparison").disabled
    invalid = demo_app(monkeypatch, county="not-a-county")
    assert not invalid.exception
    assert invalid.selectbox(key="profile_county").value is None
    assert any("not a recognized Oregon county" in w.value for w in invalid.warning)
