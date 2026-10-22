import csv
from datetime import datetime, timedelta, timezone
import io
import json

import duckdb
import pytest
import requests

from oregon_health import geocoding
from oregon_health.api_cache import RefreshLimited
from oregon_health.map import hospital_deck, selected_hospital


def hospital(facility_id="003801", **changes):
    return dict(facility_id=facility_id, name="Synthetic Hospital", address="1 Example St",
                city="Portland", state="OR", zip_code="00123", county_fips="41051", **changes)


def response_bytes(rows):
    out = io.StringIO(newline="")
    csv.writer(out).writerows(rows)
    return out.getvalue().encode()


def match(facility_id="003801", result="Match", quality="Exact", coords="-122.65,45.52"):
    return [facility_id, "1 Example St, Portland, OR, 00123", result, quality,
            "1 EXAMPLE ST, PORTLAND, OR, 00123", coords, "123", "L"]


def source(records):
    return dict(source_url=geocoding.SOURCE_URL, benchmark=geocoding.BENCHMARK,
                benchmark_id="4", fetched_at=datetime.now(timezone.utc), fingerprint=geocoding.fingerprint(records))


def test_only_exact_finite_county_consistent_matches_are_mapped():
    records = [hospital(str(380000+i)) for i in range(9)]
    records[7]["county_fips"] = None
    records[8]["county_fips"] = "41039"
    rows = [match(r["facility_id"]) for r in records]
    rows[1][2] = "No_Match"
    rows[2][2] = "Tie"
    rows[3][3] = "Non_Exact"
    rows[4][5] = "nan,45.52"
    rows[5][5] = "45.52,-122.65"
    rows[6][4] = ""
    matched = geocoding.parse_matches(response_bytes(rows), {r["facility_id"]:r for r in records})
    assert [r[2] for r in matched] == ["matched_exact", "unmatched", "ambiguous", "non_exact",
        "invalid_coordinates", "invalid_coordinates", "missing_match_address", "county_unresolved", "county_mismatch"]
    assert matched[0][3:5] == (-122.65,45.52)
    assert all(r[3:5] == (None,None) for r in matched[1:])


@pytest.mark.parametrize("rows", [[], [match(),match()], [match("999999")],
                                     [["003801", "address"]], [match(result="Unknown")]])
def test_rejects_missing_duplicate_or_unexpected_batch_results(rows):
    with pytest.raises(ValueError):
        geocoding.parse_matches(response_bytes(rows), {"003801":hospital()})


def test_county_geometry_handles_holes_and_multipolygons(monkeypatch):
    outer = [[0,0],[5,0],[5,5],[0,5],[0,0]]
    hole = [[1,1],[2,1],[2,2],[1,2],[1,1]]
    monkeypatch.setattr(geocoding,"boundaries",lambda:{"features":[
        {"properties":{"fips":"41051"},"geometry":{"type":"Polygon","coordinates":[outer,hole]}},
        {"properties":{"fips":"41039"},"geometry":{"type":"MultiPolygon","coordinates":[[outer,hole]]}},
    ]})
    assert geocoding.in_county(3,3,"41051") and geocoding.in_county(3,3,"41039")
    assert not geocoding.in_county(1.5,1.5,"41051") and not geocoding.in_county(6,6,"41039")
    assert not geocoding.in_county(3,3,None)


def test_batch_sends_only_public_addresses_and_fixed_benchmark(monkeypatch):
    calls = []
    class Response:
        status_code=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_content(self,size):yield self.raw
    def get(url,**kwargs):
        calls.append((url,kwargs))
        return Response(json.dumps({"benchmarks":[{"id":"4","benchmarkName":geocoding.BENCHMARK}]}).encode())
    def post(url,**kwargs):
        calls.append((url,kwargs))
        return Response(response_bytes([match()]))
    monkeypatch.setattr(requests,"get",get)
    monkeypatch.setattr(requests,"post",post)
    records=[hospital(),hospital("003802")]
    records[1]["address"]=None
    matches,meta=geocoding.fetch(records)
    assert [r[2] for r in matches] == ["matched_exact","missing_address"]
    assert meta["benchmark_id"] == "4" and meta["fingerprint"] == geocoding.fingerprint(records)
    assert calls[0][0] == geocoding.BENCHMARK_URL and calls[1][0] == geocoding.BATCH_URL
    assert all(c[1]["allow_redirects"] is False for c in calls)
    assert calls[1][1]["data"] == {"benchmark":"4"}
    sent=list(csv.reader(io.StringIO(calls[1][1]["files"]["addressFile"][1].decode())))
    assert sent == [["003801","1 Example St","Portland","OR","00123"]]
    assert b"Synthetic Hospital" not in calls[1][1]["files"]["addressFile"][1]


def test_missing_addresses_do_not_call_census(monkeypatch):
    monkeypatch.setattr(requests,"get",lambda *a,**kw:pytest.fail("No complete addresses"))
    record=hospital();record["zip_code"]=None
    matches,meta=geocoding.fetch([record])
    assert matches[0][2] == "missing_address" and meta["benchmark_id"] is None


def test_response_limits_and_redirects():
    class Response:
        status_code=302
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_content(self,size):yield b"123456"
    response=Response()
    with pytest.raises(ValueError,match="complete response"):
        geocoding._read(response,10)
    response.status_code=200
    with pytest.raises(ValueError,match="size limit"):
        geocoding._read(response,5)


def test_cache_addresses_changes_atomic_failure_and_refresh_cooldown(tmp_path,monkeypatch):
    path=tmp_path/"locations.duckdb"
    records=[hospital()]
    matches=geocoding.parse_matches(response_bytes([match()]),{"003801":records[0]})
    calls=[];clock=[1000.0]
    monkeypatch.setattr("oregon_health.api_cache.time.time",lambda:clock[0])
    def fetch(rows):
        calls.append(True)
        return matches,source(rows)
    monkeypatch.setattr(geocoding,"fetch",fetch)
    geocoding.refresh(records,path)
    geocoding.refresh(records,path)
    assert len(calls)==1
    with pytest.raises(RefreshLimited):geocoding.refresh(records,path,force=True)
    before=geocoding.snapshot(records,path)
    with pytest.raises(duckdb.Error):geocoding.store(matches+matches,source(records),path)
    assert geocoding.snapshot(records,path)==before
    changed=[{**records[0],"address":"2 Different St"}]
    joined,meta=geocoding.snapshot(changed,path)
    assert joined["003801"]["location_status"]=="address_changed" and joined["003801"]["longitude"] is None
    assert geocoding.needs_refresh(changed,path)
    with duckdb.connect(str(path)) as db:
        db.execute("UPDATE provenance SET fetched_at = ?",[datetime.now(timezone.utc)-timedelta(days=31)])
    clock[0]+=301
    def offline(*a):raise requests.ConnectionError("Offline")
    monkeypatch.setattr(geocoding,"fetch",offline)
    with pytest.raises(requests.ConnectionError):geocoding.refresh(records,path)
    assert geocoding.snapshot(records,path)[0]["003801"]["longitude"]==-122.65
    with pytest.raises(RefreshLimited):geocoding.refresh(records,path)


def test_map_selection_is_limited_to_filtered_facility_ids():
    event={"selection":{"objects":{"hospital-points":[{"facility_id":"003801"}]}}}
    assert selected_hospital(event,["003801"])=="003801"
    for bad in [None,{}, {"selection":None}, {"selection":{"objects":[]}},
                {"selection":{"objects":{"hospital-points":[{"facility_id":["003801"]}]}}}]:
        assert selected_hospital(bad,["003801"]) is None
    assert selected_hospital(event,["003802"]) is None
    deck=json.loads(hospital_deck([{**hospital(),"longitude":-122.65,"latitude":45.52,"matched_address":"Example"}]).to_json())
    assert deck.get("mapProvider") is None and len(deck["layers"])==2
    assert deck["layers"][1]["data"][0]["facility_id"]=="003801"
