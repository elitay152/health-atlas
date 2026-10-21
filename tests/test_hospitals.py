"""Synthetic hospital fixtures; CI never calls CMS."""
import csv
from datetime import datetime, timedelta, timezone
import io
import json

import duckdb
import pytest
import requests

from oregon_health import hospitals
from oregon_health.api_cache import RefreshLimited


def row(**changes):
    return {"Facility ID": "003801", "Facility Name": "Synthetic Hospital",
            "Address": "1 Example St", "City/Town": "Example", "State": "OR",
            "ZIP Code": "00123", "County/Parish": "LANE",
            "Hospital Type": "Critical Access Hospitals", "Hospital Ownership": "Government",
            "Emergency Services": "Yes", **changes}


def csv_bytes(rows, columns=None):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=columns or list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def provenance():
    return dict(dataset=hospitals.DATASET, source_url=hospitals.SOURCE_URL,
                download_url="https://data.cms.gov/provider-data/example.csv",
                released="2026-08-13", modified="2026-07-22",
                fetched_at=datetime.now(timezone.utc), sha256="a" * 64)


def test_normalize_filters_states_preserves_ids_and_missing_locations():
    records = hospitals.normalize(csv_bytes([
        row(), row(**{"Facility ID": "003802", "County/Parish": "Unknown county",
                      "Address": "", "Emergency Services": "Not Available"}),
        row(**{"Facility ID": "003803", "County/Parish": "", "Emergency Services": "No"}),
        row(**{"State": "WA"}),
    ]))
    first, unresolved, unnamed = [dict(zip(hospitals.COLUMNS, r)) for r in records]
    assert first["facility_id"] == "003801" and first["zip_code"] == "00123"
    assert first["county_fips"] == "41039" and first["emergency_services"] is True
    assert unresolved["county"] == "Unknown county" and unresolved["county_fips"] is None
    assert unresolved["address"] is None and unresolved["emergency_services"] is None
    assert unnamed["county"] is None and unnamed["emergency_services"] is False


@pytest.mark.parametrize("rows", [
    [row(), row()], [row(**{"Facility ID": "123"})],
    [row(**{"Facility Name": ""})], [row(**{"State": ""})],
    [row(**{"State": "WA"})], [row(**{"Emergency Services": "Maybe"})],
])
def test_normalize_rejects_duplicates_and_invalid_records(rows):
    with pytest.raises(ValueError):
        hospitals.normalize(csv_bytes(rows))


def test_rejects_schema_drift_malformed_and_oversized_csv(monkeypatch):
    raw = csv_bytes([row()])
    with pytest.raises(ValueError, match="required"):
        hospitals.normalize(b"State,Facility ID\nOR,003801\n")
    with pytest.raises(ValueError, match="malformed"):
        hospitals.normalize(raw.replace(b"Yes", b"Yes,extra"))
    monkeypatch.setattr(hospitals, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="row limit"):
        hospitals.normalize(csv_bytes([row(), row(**{"State": "WA"})]))


@pytest.mark.parametrize("url", [
    "http://data.cms.gov/provider-data/test.csv", "https://example.com/provider-data/test.csv",
    "https://data.cms.gov.evil.test/provider-data/test.csv",
    "https://user@data.cms.gov/provider-data/test.csv",
    "https://data.cms.gov:443/provider-data/test.csv",
    "https://data.cms.gov/elsewhere/test.csv", "https://data.cms.gov/provider-data/test.csv?key=x",
])
def test_rejects_unexpected_resource_destinations_before_network(url, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: pytest.fail("Unexpected network request"))
    with pytest.raises(ValueError):
        hospitals._download(url, 100)


def test_download_enforces_redirect_and_stream_limits(monkeypatch):
    class Response:
        status_code = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield b"12345"; yield b"67890"
    calls = []
    response = Response()
    def get(url, **kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(ValueError, match="download limit"):
        hospitals._download(hospitals.METADATA_URL, 8)
    assert calls[0]["allow_redirects"] is False and calls[0]["stream"] is True
    response.status_code = 302
    with pytest.raises(ValueError, match="complete resource"):
        hospitals._download(hospitals.METADATA_URL, 100)
    response.status_code = 200
    response.headers = {"Content-Length": "500"}
    with pytest.raises(ValueError, match="download limit"):
        hospitals._download(hospitals.METADATA_URL, 100)


def test_fetch_resolves_release_url_and_provenance(monkeypatch):
    metadata = dict(identifier=hospitals.DATASET, title="Hospital General Information", accessLevel="public",
                    released="2026-08-13", modified="2026-07-22", distribution=[
                        dict(mediaType="text/csv", downloadURL=provenance()["download_url"])])
    calls = []
    def download(url, limit):
        calls.append(url)
        return json.dumps(metadata).encode() if url == hospitals.METADATA_URL else csv_bytes([row()])
    monkeypatch.setattr(hospitals, "_download", download)
    records, source = hospitals.fetch()
    assert len(records) == 1 and calls == [hospitals.METADATA_URL, provenance()["download_url"]]
    assert source["released"].isoformat() == "2026-08-13"
    assert source["modified"].isoformat() == "2026-07-22"
    assert len(source["sha256"]) == 64 and source["fetched_at"].tzinfo
    metadata["identifier"] = "wrong"
    with pytest.raises(ValueError, match="expected public"):
        hospitals.fetch()
    metadata["identifier"] = hospitals.DATASET
    metadata["distribution"][0]["downloadURL"] = "https://example.com/test.csv"
    with pytest.raises(ValueError, match="official HTTPS"):
        hospitals.fetch()


def test_atomic_storage_changed_records_and_coverage(tmp_path):
    path = tmp_path / "hospitals.duckdb"
    records = hospitals.normalize(csv_bytes([row()]))
    hospitals.store(records, provenance(), path)
    with pytest.raises(duckdb.Error):
        hospitals.store(records + records, provenance(), path)
    assert hospitals.directory("41039", path)[0]["name"] == "Synthetic Hospital"
    assert hospitals.directory("41051", path) == []
    with pytest.raises(ValueError, match="Unknown Oregon county"):
        hospitals.directory("41039' OR TRUE --", path)
    records = hospitals.normalize(csv_bytes([row(**{"Facility Name": "Changed synthetic name",
                                                    "County/Parish": "UNKNOWN", "Address": ""})]))
    hospitals.store(records, provenance(), path)
    assert hospitals.directory(path=path)[0]["name"] == "Changed synthetic name"
    report = hospitals.coverage(path)
    assert report["records"] == 1 and report["unresolved_counties"] == 1
    assert report["located_facilities"] == 0 and report["missing_address"] == 1
    assert len(report["counties"]) == 36 and all(c["listed_hospitals"] == 0 for c in report["counties"])
    assert report["provenance"]["sha256"] == "a" * 64
    assert not list(tmp_path.glob("*.tmp"))


def test_cache_cooldown_failed_refresh_preserves_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "hospitals.duckdb"
    calls, clock = [], [1000.0]
    monkeypatch.setattr("oregon_health.api_cache.time.time", lambda: clock[0])
    def fetch():
        calls.append(True)
        return hospitals.normalize(csv_bytes([row()])), provenance()
    monkeypatch.setattr(hospitals, "fetch", fetch)
    assert hospitals.needs_refresh(path)
    assert hospitals.refresh(path) == 1
    assert hospitals.refresh(path) == 1 and len(calls) == 1
    with pytest.raises(RefreshLimited):
        hospitals.refresh(path, force=True)
    clock[0] += 301
    with duckdb.connect(str(path)) as db:
        db.execute("UPDATE provenance SET fetched_at = ?", [datetime.now(timezone.utc) - timedelta(days=2)])
    before = hospitals.coverage(path)["provenance"]
    def fail():
        raise requests.ConnectionError("Offline")
    monkeypatch.setattr(hospitals, "fetch", fail)
    with pytest.raises(requests.ConnectionError):
        hospitals.refresh(path)
    assert hospitals.directory(path=path)[0]["facility_id"] == "003801"
    assert hospitals.coverage(path)["provenance"] == before
    with pytest.raises(RefreshLimited):
        hospitals.refresh(path)
