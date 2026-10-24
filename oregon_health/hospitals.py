"""CMS hospital snapshots. Run: python -m oregon_health.hospitals [--refresh].

Contract: GitHub issue #18. Facility IDs are source strings, not NPIs. Listed
facilities do not establish service capacity; absent records do not mean no care.
The source has no coordinates, so this importer does not create map markers.
"""
import argparse
from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import duckdb
import requests

from oregon_health.regions import OREGON, get_region, cache_dir, check_identity
from oregon_health.storage import snapshot
from oregon_health.network import reserve_request
from oregon_health.api_cache import _reserve_refresh
from oregon_health.data import DB_PATH as HEALTH_PATH
from oregon_health.profile import county_names
from oregon_health.status import record_refresh

DATASET = "xubh-q36u"
SOURCE_URL = f"https://data.cms.gov/provider-data/dataset/{DATASET}"
METADATA_URL = f"https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/{DATASET}"
DB_PATH = HEALTH_PATH.parent / "hospitals.duckdb"
CACHE_TTL = timedelta(hours=24)
MAX_ROWS = 25000
REQUIRED = {
    "Facility ID", "Facility Name", "Address", "City/Town", "State", "ZIP Code",
    "County/Parish", "Hospital Type", "Hospital Ownership", "Emergency Services",
}
COLUMNS = ["facility_id", "name", "address", "city", "state", "zip_code", "county",
           "county_fips", "hospital_type", "ownership", "emergency_services"]
MISSING = {"", "not available", "n/a", "null"}


def _cms_url(url):
    if not isinstance(url, str):
        raise ValueError("CMS metadata has no valid download URL")
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "data.cms.gov"
            or not parts.path.startswith("/provider-data/") or parts.query or parts.fragment):
        raise ValueError("CMS resources must use the official HTTPS provider-data host")
    return url


def _download(url, limit):
    """Bound decompressed bytes and reject redirects, including metadata URLs."""
    reserve_request(_cms_url(url))
    with requests.get(_cms_url(url), timeout=(5, 30), stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("CMS did not return a complete resource")
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise ValueError("CMS resource exceeds the download limit")
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > limit:
                raise ValueError("CMS resource exceeds the download limit")
            chunks.append(chunk)
        return b"".join(chunks)


def _text(value):
    value = value.strip()
    return None if value.lower() in MISSING else value


def normalize(raw, *, region=OREGON):
    """Validate national CSV structure, then retain the chosen state and unknowns."""
    region = get_region(region)
    region.require("hospitals")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    header = reader.fieldnames
    if not header or len(header) != len(set(header)) or not REQUIRED.issubset(header):
        raise ValueError("CMS CSV is missing required fields or has duplicate columns")
    counties = {name.upper(): fips for fips, name in county_names(region).items()}
    records, seen = [], set()
    for index, row in enumerate(reader, start=1):
        if index > MAX_ROWS:
            raise ValueError("CMS CSV exceeds the national row limit")
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CMS CSV contains a malformed row")
        if not re.fullmatch(r"[A-Z]{2}", row["State"].strip()):
            raise ValueError("CMS CSV contains an invalid state")
        if row["State"].strip() != region.abbr:
            continue
        facility_id = row["Facility ID"].strip()
        name = _text(row["Facility Name"])
        if not re.fullmatch(r"[A-Z0-9]{6}", facility_id) or not name:
            raise ValueError("CMS record has an invalid facility identifier or name")
        if facility_id in seen:
            raise ValueError(f"CMS CSV contains duplicate {region.name} facility identifiers")
        seen.add(facility_id)
        emergency = _text(row["Emergency Services"])
        if emergency is not None and emergency.lower() not in {"yes", "no"}:
            raise ValueError("CMS record has an unknown emergency-services value")
        county = _text(row["County/Parish"])
        records.append((facility_id, name, _text(row["Address"]), _text(row["City/Town"]),
                        region.abbr, _text(row["ZIP Code"]), county, counties.get((county or "").upper()),
                        _text(row["Hospital Type"]), _text(row["Hospital Ownership"]),
                        None if emergency is None else emergency.lower() == "yes"))
    if not records:
        raise ValueError(f"CMS CSV contains no {region.name} hospitals")
    return records


def fetch(*, region=OREGON):
    region = get_region(region)
    region.require("hospitals")
    metadata = json.loads(_download(METADATA_URL, 1_000_000))
    if (metadata.get("identifier") != DATASET
            or metadata.get("title") != "Hospital General Information"
            or metadata.get("accessLevel") != "public"):
        raise ValueError("CMS metadata does not describe the expected public hospital directory")
    urls = {_cms_url(d.get("downloadURL")) for d in metadata.get("distribution", [])
            if d.get("mediaType") == "text/csv"}
    if len(urls) != 1:
        raise ValueError("CMS metadata must identify one CSV resource")
    csv_url = urls.pop()
    # Source dates are distinct from fetched_at and from CDC estimate years.
    released = date.fromisoformat(metadata["released"])
    modified = date.fromisoformat(metadata["modified"])
    raw = _download(csv_url, 10_000_000)
    records = normalize(raw, region=region)
    provenance = dict(dataset=DATASET, source_url=SOURCE_URL, download_url=csv_url,
                      released=released, modified=modified,
                      fetched_at=datetime.now(timezone.utc), sha256=hashlib.sha256(raw).hexdigest())
    return records, provenance


def hospital_path(region=OREGON):
    region = get_region(region)
    return DB_PATH if region == OREGON else cache_dir(region, DB_PATH.parent) / 'hospitals.duckdb'


def store(records, provenance, path=None, *, region=OREGON):
    """Publish atomically; a rejected replacement cannot remove a good snapshot."""
    region = get_region(region)
    region.require("hospitals")
    if any(len(r) != len(COLUMNS) or r[4] != region.abbr
           or (r[7] is not None and r[7] not in county_names(region)) for r in records):
        raise ValueError('Hospital records belong to another region or county catalog')
    path = Path(path if path is not None else hospital_path(region))
    def validate(previous):
        with duckdb.connect(str(previous), read_only=True) as db:
            check_identity(db, region)
    with snapshot(path, validate) as temp:
        with duckdb.connect(str(temp)) as db:
            db.execute("""CREATE TABLE hospitals (
                facility_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
                address VARCHAR, city VARCHAR, state VARCHAR CHECK (state IN ('OR', 'WA')),
                zip_code VARCHAR, county VARCHAR, county_fips VARCHAR,
                hospital_type VARCHAR, ownership VARCHAR, emergency_services BOOLEAN)""")
            db.executemany("INSERT INTO hospitals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records)
            db.execute("""CREATE TABLE provenance (
                dataset VARCHAR, source_url VARCHAR, download_url VARCHAR,
                released DATE, modified DATE, fetched_at TIMESTAMP, sha256 VARCHAR, state_fips VARCHAR)""")
            db.execute("INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       [provenance[k] for k in ["dataset", "source_url", "download_url", "released", "modified"]]
                       + [provenance["fetched_at"].astimezone(timezone.utc).replace(tzinfo=None), provenance["sha256"], region.state_fips])


def needs_refresh(path=None, *, region=OREGON):
    region = get_region(region)
    path = path if path is not None else hospital_path(region)
    if not Path(path).exists():
        return True
    with duckdb.connect(str(path), read_only=True) as db:
        check_identity(db, region)
        fetched = db.execute("SELECT fetched_at FROM provenance").fetchone()[0]
    return datetime.now(timezone.utc) - fetched.replace(tzinfo=timezone.utc) >= CACHE_TTL


def refresh(path=None, force=False, *, region=OREGON):
    """Reuse the daily cache; all network attempts share the five-minute cooldown.

    Failures raise to the caller and leave the prior snapshot readable. A caller
    choosing to display it must explicitly mark it as retained after failure.
    """
    region = get_region(region)
    region.require("hospitals")
    path = path if path is not None else hospital_path(region)
    # Reject a foreign destination before cooldown reservation or network I/O.
    if Path(path).exists():
        with duckdb.connect(str(path), read_only=True) as db:
            check_identity(db, region)
    if not force and not needs_refresh(path, region=region):
        return len(directory(path=path, region=region))
    _reserve_refresh(path)
    try:
        records, provenance = fetch() if region == OREGON else fetch(region=region)
        store(records, provenance, path, region=region)
    except Exception:
        record_refresh(path, DATASET, "failed")
        raise
    record_refresh(path, DATASET, "success")
    return len(records)


def _directory(db, county_fips=None):
    result = db.execute("""SELECT * FROM hospitals
        WHERE (? IS NULL OR county_fips = ?) ORDER BY name, facility_id""", [county_fips, county_fips])
    return [dict(zip(COLUMNS, row)) for row in result.fetchall()]


def directory(county_fips=None, path=None, *, region=OREGON):
    """Read only; never fetch or invent coordinates. Unknown counties remain NULL."""
    region = get_region(region)
    path = path if path is not None else hospital_path(region)
    if county_fips is not None and county_fips not in county_names(region):
        raise ValueError(f"Unknown {region.name} county")
    with duckdb.connect(str(path), read_only=True) as db:
        check_identity(db, region)
        return _directory(db, county_fips)


def coverage(path=None, *, region=OREGON):
    """Snapshot coverage describes listed records, not healthcare availability."""
    region = get_region(region)
    path = path if path is not None else hospital_path(region)
    with duckdb.connect(str(path), read_only=True) as db:
        check_identity(db, region)
        records = _directory(db)
        cursor = db.execute("SELECT * FROM provenance")
        provenance = dict(zip([col[0] for col in cursor.description], cursor.fetchone()))
        provenance["fetched_at"] = provenance["fetched_at"].replace(tzinfo=timezone.utc)
    counts = Counter(row["county_fips"] for row in records)
    return dict(provenance=provenance, records=len(records),
                counties=[dict(fips=fips, county=name, listed_hospitals=counts[fips])
                          for fips, name in county_names(region).items()],
                unresolved_counties=counts[None],
                missing_address=sum(not all(row[k] for k in ("address", "city", "zip_code")) for row in records),
                located_facilities=0,  # This source supplies no coordinates.
                unknown_emergency_services=sum(row["emergency_services"] is None for row in records))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Refresh even if the daily cache is fresh")
    parser.add_argument("--region", choices=["OR", "WA"], default="OR")
    parser.add_argument("--path", type=Path, default=None, help="Hospital snapshot path")
    args = parser.parse_args()
    try:
        refresh(args.path, force=args.refresh, region=args.region)
    except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
        parser.exit(1, "CMS refresh failed; any previous hospital snapshot was retained. Retry later or check source access/schema.\n")
    print(json.dumps(coverage(args.path, region=args.region), default=str, indent=2))


if __name__ == "__main__":
    main()
