import json
import pytest
import requests
from oregon_health import network
from oregon_health.import_data import fetch
from oregon_health.data import demo_rows


def test_download_checks_metadata_and_filters(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, chunk_size): yield json.dumps(self.data).encode()
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
    assert len(rows) == 27
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


class _JSONResponse:
    status_code = 200
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, chunk_size): yield json.dumps(self.data).encode()


def _synthetic_source(url):
    return _JSONResponse({"name": "PLACES: County Data (GIS Friendly), Test Release"} if "/api/views/" in url else demo_rows())


def test_download_retries_transient_failures(monkeypatch):
    sleeps, failures = [], [requests.ReadTimeout("slow"), requests.ConnectionError("reset")]
    def get(url, **kwargs):
        if failures:
            raise failures.pop(0)
        return _synthetic_source(url)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(network.time, "sleep", sleeps.append)
    rows, _ = fetch("abcd-1234")
    assert len(rows) == 27 and sleeps == [1, 2]


def test_download_gives_up_after_three_timeouts(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        raise requests.ReadTimeout("slow")
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(network.time, "sleep", lambda seconds: None)
    with pytest.raises(requests.ReadTimeout):
        fetch("abcd-1234")
    assert len(calls) == 3


@pytest.mark.parametrize("status, expected_calls", [(503, 2), (403, 1)])
def test_download_retries_only_transient_statuses(monkeypatch, status, expected_calls):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            response = requests.Response()
            response.status_code = status
            raise requests.HTTPError(str(status), response=response)
        return _synthetic_source(url)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(network.time, "sleep", lambda seconds: None)
    if status == 403:
        with pytest.raises(requests.HTTPError):
            fetch("abcd-1234")
    else:
        fetch("abcd-1234")
    assert calls.count(calls[0]) == expected_calls


def test_download_rejects_oversized_result(monkeypatch):
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, chunk_size): yield json.dumps(self.value).encode()
        def __init__(self, value):
            self.value = value
        def raise_for_status(self):
            pass
        def json(self):
            return self.value
    def get(url, **kwargs):
        return Response({"name": "PLACES County Test"} if "/api/views/" in url else [data for data in demo_rows() for _ in range(112)])
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(ValueError, match="1,000-record"):
        fetch("abcd-1234")
