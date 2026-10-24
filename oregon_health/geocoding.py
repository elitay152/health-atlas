"""Census batch address matches; approximate street points, never site verification.

Only published CMS addresses are sent. Matches are tied to address fingerprints,
validated against source county boundaries and cached separately from hospitals.
"""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path

import duckdb
import requests

from oregon_health.regions import OREGON, get_region, cache_dir, check_identity, ROOT
from oregon_health.storage import snapshot as publish_snapshot
from oregon_health.network import reserve_request
from oregon_health.api_cache import _reserve_refresh
from oregon_health.data import DB_PATH as HEALTH_PATH
from oregon_health.map import boundaries
from oregon_health.status import record_refresh

BASE = "https://geocoding.geo.census.gov/geocoder"
BENCHMARK_URL = BASE + "/benchmarks"
BATCH_URL = BASE + "/locations/addressbatch"
SOURCE_URL = "https://geocoding.geo.census.gov/"
BENCHMARK = "Public_AR_Current"
DB_PATH = HEALTH_PATH.parent / "hospital_locations.duckdb"
CACHE_TTL = timedelta(days=30)
MAX_ADDRESSES = 200
COLUMNS = ["facility_id", "address_hash", "location_status", "longitude", "latitude", "matched_address"]


def address_hash(record):
    values = [" ".join((record.get(k) or "").upper().split())
              for k in ("address", "city", "state", "zip_code", "county_fips")]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def fingerprint(records):
    return hashlib.sha256(json.dumps(sorted((r["facility_id"], address_hash(r)) for r in records)).encode()).hexdigest()


def _inside_ring(x, y, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_county(lon, lat, fips, *, region=OREGON):
    """Conservative match to the bundled generalized county geometry."""
    collection = boundaries() if get_region(region) == OREGON else boundaries(region)
    for feature in collection["features"]:
        if feature["properties"]["fips"] != fips:
            continue
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        return any(_inside_ring(lon, lat, rings[0])
                   and not any(_inside_ring(lon, lat, hole) for hole in rings[1:]) for rings in polygons)
    return False


def _read(response, limit):
    with response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Census geocoder did not return a complete response")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > limit:
                raise ValueError("Census geocoder response exceeds the size limit")
            chunks.append(chunk)
        return b"".join(chunks)


def parse_matches(raw, requested, *, region=OREGON):
    """Accept only exact, finite, county-consistent matches; retain other statuses."""
    region = get_region(region)
    _validate_records(list(requested.values()), region)
    west, south, east, north = region.bounds
    seen, matches = set(), []
    for row in csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")):
        if len(row) < 3 or row[0] not in requested or row[0] in seen:
            raise ValueError("Census batch response contains unexpected or duplicate identifiers")
        facility_id = row[0]
        record = requested[facility_id]
        seen.add(facility_id)
        lon = lat = matched_address = None
        result = row[2].strip().lower()
        if result == "no_match":
            status = "unmatched"
        elif result == "tie":
            status = "ambiguous"
        elif result == "match":
            if len(row) < 6:
                raise ValueError("Census match is missing required fields")
            status = "non_exact"
            if row[3].strip().lower() == "exact":
                matched_address = row[4].strip() or None
                try:
                    lon, lat = (float(v) for v in row[5].split(","))
                except (ValueError, TypeError):
                    lon = lat = None
                if (lon is None or not math.isfinite(lon) or not math.isfinite(lat)
                        or not west <= lon <= east or not south <= lat <= north):
                    status = "invalid_coordinates"
                elif not matched_address:
                    status = "missing_match_address"
                elif not record.get("county_fips"):
                    status = "county_unresolved"
                elif not in_county(lon, lat, record["county_fips"], region=region):
                    status = "county_mismatch"
                else:
                    status = "matched_exact"
        else:
            raise ValueError("Census batch response contains an unknown result status")
        if status != "matched_exact":
            lon = lat = None
        matches.append((facility_id, address_hash(record), status, lon, lat, matched_address))
    if seen != set(requested):
        raise ValueError("Census batch response omits requested hospital identifiers")
    return matches


def location_path(region=OREGON):
    region = get_region(region)
    return DB_PATH if region == OREGON else cache_dir(region, DB_PATH.parent) / 'hospital_locations.duckdb'


def boundary_hash(region):
    return hashlib.sha256((ROOT / 'assets' / region.boundary_file).read_bytes()).hexdigest()


def _validate_records(records, region):
    if len(records) > MAX_ADDRESSES or len({r['facility_id'] for r in records}) != len(records):
        raise ValueError('Expected a bounded set of unique hospital identifiers')
    if any(r.get('state') != region.abbr or (r.get('county_fips') is not None
           and r['county_fips'] not in region.counties()) for r in records):
        raise ValueError('Hospital addresses belong to another region or county catalog')


def fetch(records, *, region=OREGON):
    region = get_region(region)
    region.require("hospital_locations")
    _validate_records(records, region)
    if not records:
        raise ValueError("Expected a bounded set of unique hospital identifiers")
    requested, missing = {}, []
    for record in records:
        if all(record.get(k) for k in ("address", "city", "zip_code")):
            requested[record["facility_id"]] = record
        else:
            missing.append((record["facility_id"], address_hash(record), "missing_address", None, None, None))
    benchmark_id = None
    matches = []
    if requested:
        reserve_request(BENCHMARK_URL)
        raw = _read(requests.get(BENCHMARK_URL, params={"format": "json"},
                                timeout=(5, 30), allow_redirects=False, stream=True), 100_000)
        choices = [b for b in json.loads(raw)["benchmarks"] if b["benchmarkName"] == BENCHMARK]
        if len(choices) != 1 or not str(choices[0]["id"]).isdigit():
            raise ValueError("Census current address benchmark is unavailable")
        benchmark_id = str(choices[0]["id"])
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        for facility_id, r in requested.items():
            writer.writerow([facility_id, r["address"], r["city"], region.abbr, r["zip_code"]])
        reserve_request(BATCH_URL)
        raw = _read(requests.post(BATCH_URL, data={"benchmark": benchmark_id},
                                 files={"addressFile": ("hospitals.csv", output.getvalue().encode(), "text/csv")},
                                 timeout=(5, 60), allow_redirects=False, stream=True), 1_000_000)
        matches = parse_matches(raw, requested, region=region)
    return matches + missing, dict(source_url=SOURCE_URL, benchmark=BENCHMARK, benchmark_id=benchmark_id,
                                    fetched_at=datetime.now(timezone.utc), fingerprint=fingerprint(records),
                                    state_fips=region.state_fips, boundary_sha256=boundary_hash(region))


def store(matches, provenance, path=None, *, region=OREGON):
    region = get_region(region)
    region.require('hospital_locations')
    if provenance.get('state_fips', region.state_fips) != region.state_fips:
        raise ValueError('Location provenance belongs to another region')
    path = Path(path if path is not None else location_path(region))
    def validate(previous):
        with duckdb.connect(str(previous), read_only=True) as db:
            check_identity(db, region)
    with publish_snapshot(path, validate) as temp:
        with duckdb.connect(str(temp)) as db:
            db.execute("""CREATE TABLE locations (facility_id VARCHAR PRIMARY KEY, address_hash VARCHAR,
                location_status VARCHAR, longitude DOUBLE, latitude DOUBLE, matched_address VARCHAR)""")
            db.executemany("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?)", matches)
            db.execute("""CREATE TABLE provenance (source_url VARCHAR, benchmark VARCHAR, benchmark_id VARCHAR,
                fetched_at TIMESTAMP, fingerprint VARCHAR, state_fips VARCHAR, boundary_sha256 VARCHAR)""")
            db.execute("INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?, ?)", [provenance["source_url"], provenance["benchmark"],
                       provenance["benchmark_id"], provenance["fetched_at"].astimezone(timezone.utc).replace(tzinfo=None),
                       provenance["fingerprint"], region.state_fips, provenance.get("boundary_sha256", boundary_hash(region))])


def snapshot(records, path=None, *, region=OREGON):
    """Join only current addresses; changed addresses never inherit old coordinates."""
    region = get_region(region)
    _validate_records(records, region)
    path = path if path is not None else location_path(region)
    cached, provenance = {}, None
    if Path(path).exists():
        with duckdb.connect(str(path), read_only=True) as db:
            check_identity(db, region)
            cached = {r[0]: dict(zip(COLUMNS, r)) for r in db.execute("SELECT * FROM locations").fetchall()}
            cursor = db.execute("SELECT * FROM provenance")
            provenance = dict(zip([c[0] for c in cursor.description], cursor.fetchone()))
            provenance["fetched_at"] = provenance["fetched_at"].replace(tzinfo=timezone.utc)
    joined = {}
    for record in records:
        previous = cached.get(record["facility_id"])
        if previous and previous["address_hash"] == address_hash(record):
            previous = dict(previous)
            if previous['location_status'] == 'matched_exact':
                lon, lat = previous['longitude'], previous['latitude']
                west, south, east, north = region.bounds
                if not all(record.get(k) for k in ('address', 'city', 'zip_code')):
                    previous.update(location_status='missing_address', longitude=None, latitude=None)
                elif (lon is None or lat is None or not math.isfinite(lon) or not math.isfinite(lat)
                        or not west <= lon <= east or not south <= lat <= north):
                    previous.update(location_status='invalid_coordinates', longitude=None, latitude=None)
                elif not previous['matched_address']:
                    previous.update(location_status='missing_match_address', longitude=None, latitude=None)
                elif not record.get('county_fips'):
                    previous.update(location_status='county_unresolved', longitude=None, latitude=None)
                elif not in_county(lon, lat, record['county_fips'], region=region):
                    previous.update(location_status='county_mismatch', longitude=None, latitude=None)
            else:
                previous.update(longitude=None, latitude=None)
            joined[record["facility_id"]] = previous
        else:
            joined[record["facility_id"]] = dict(facility_id=record["facility_id"], address_hash=address_hash(record),
                location_status="address_changed" if previous else "not_geocoded", longitude=None, latitude=None,
                matched_address=None)
    return joined, provenance


def needs_refresh(records, path=None, *, region=OREGON):
    _, provenance = snapshot(records, path, region=region)
    return (provenance is None or provenance["fingerprint"] != fingerprint(records)
            or provenance.get("boundary_sha256") not in (None, boundary_hash(get_region(region)))
            or datetime.now(timezone.utc) - provenance["fetched_at"] >= CACHE_TTL)


def refresh(records, path=None, force=False, *, region=OREGON):
    region = get_region(region)
    region.require("hospital_locations")
    _validate_records(records, region)
    path = path if path is not None else location_path(region)
    if Path(path).exists():
        with duckdb.connect(str(path), read_only=True) as db:
            check_identity(db, region)
    if not force and not needs_refresh(records, path, region=region):
        return
    _reserve_refresh(path)
    try:
        matches, provenance = fetch(records) if region == OREGON else fetch(records, region=region)
        store(matches, provenance, path, region=region)
    except Exception:
        record_refresh(path, "xubh-q36u", "failed")
        raise
    record_refresh(path, "xubh-q36u", "success")
