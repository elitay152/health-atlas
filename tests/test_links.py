from pathlib import Path

from streamlit.testing.v1 import AppTest

from oregon_health import api_cache, census
from oregon_health.links import link_params, parse_counties


def test_bounded_county_list():
    assert parse_counties(None) == (None, False)
    assert parse_counties("") == ([], False)
    assert parse_counties("41051,41039,41051") == (["41051", "41039"], False)
    assert parse_counties("99999,41039,not-sql") == (["41039"], True)
    assert parse_counties("41051," * 100) == ([], True)
    params = link_params(["41051", "41039"], 2023, "OBESITY", False, True, "41051")
    assert params == dict(compare="41051,41039", year="2023", measure="OBESITY", demo="0", census="1", county="41051")
    assert link_params([], 2022, "DIABETES", True, True)["census"] == "0"


def app(monkeypatch, params):
    def unexpected(*args, **kwargs):
        raise AssertionError("Demo link must not fetch real data")
    monkeypatch.setattr(api_cache, "refresh", unexpected)
    monkeypatch.setattr(census, "context", unexpected)
    view = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=20)
    view.query_params.update(params)
    return view.run()


def test_comparison_links_roundtrip_and_profile_action(monkeypatch):
    view = app(monkeypatch, dict(compare="41039", county="41051", year="2022", measure="OBESITY", demo="1", census="1"))
    assert not view.exception
    assert view.multiselect[0].value == ["Lane"]
    assert view.selectbox(key="health_measure").value == "OBESITY"
    assert view.checkbox[0].value is False
    assert view.query_params["census"] == "0"
    next(b for b in view.button if b.label == "Add county to comparison").click().run()
    assert view.query_params["compare"] == "41039,41051"
    assert view.query_params["county"] == "41051"
    # A fresh session reconstructs the view without loading real demographics.
    other = app(monkeypatch, dict(view.query_params))
    assert not other.exception
    assert other.multiselect[0].value == ["Lane", "Multnomah"]
    assert other.selectbox(key="profile_county").value == "41051"
    other.selectbox(key="health_measure").select("CASTHMA").run()
    other.run()
    assert other.query_params["measure"] == "CASTHMA"
    assert other.multiselect[0].value == ["Lane", "Multnomah"]


def test_empty_selection_and_invalid_link_values(monkeypatch):
    view = app(monkeypatch, dict(compare="", demo="1", year="2022"))
    assert not view.exception
    assert view.multiselect[0].value == []
    view.run()
    assert view.query_params["compare"] == ""
    invalid = app(monkeypatch, dict(compare="99999,41001,41039,41051,41039", measure="invalid", year="1900", demo="1", token="synthetic-do-not-share"))
    assert not invalid.exception
    assert invalid.multiselect[0].value == ["Lane", "Multnomah"]
    assert invalid.query_params["compare"] == "41039,41051"
    assert invalid.query_params["year"] == "2022"
    assert "token" not in invalid.query_params
    assert any("linked counties" in w.value for w in invalid.warning)
    assert any("linked estimate year" in w.value for w in invalid.warning)
