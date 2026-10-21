import pytest
import requests
from oregon_health.import_data import fetch
from oregon_health.data import demo_rows


def test_download_checks_metadata_and_filters(monkeypatch):
    calls = []

    class Response:
        def __init__(self, data):
            self.data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self.data

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"name": "PLACES: County Data (GIS Friendly), Test Release"} if "/api/views/" in url else demo_rows())

    monkeypatch.setattr(requests, "get", get)
    rows, source = fetch("abcd-1234")
    assert len(rows) == 9
    assert "Test Release" in source
    assert "stateabbr = 'OR'" in calls[1][1]["params"]["$where"]
    assert calls[1][1]["params"]["$offset"] == 0


def test_download_propagates_network_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.HTTPError("403")
    monkeypatch.setattr(requests, "get", fail)
    with pytest.raises(requests.HTTPError):
        fetch("abcd-1234")
    with pytest.raises(ValueError):
        fetch("not-a-dataset")
