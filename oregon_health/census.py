"""ACS five-year county context, cached separately from CDC estimates."""
from datetime import datetime, timedelta, timezone
import math
import logging
from pathlib import Path
import tempfile
import os

import duckdb
import requests

from oregon_health.api_cache import _reserve_refresh
from oregon_health.data import DB_PATH
from oregon_health.regions import OREGON, get_region, cache_dir as region_cache_dir, check_identity
from oregon_health.profile import county_names
from oregon_health.status import record_refresh

FIELDS = ["DP05_0001E", "DP05_0001M", "DP05_0024PE", "DP05_0024PM", "DP03_0128PE", "DP03_0128PM"]
COLUMNS = ["population", "population_moe", "age65_pct", "age65_moe", "poverty_pct", "poverty_moe"]
CACHE_TTL = timedelta(days=30)
SENTINELS = {-111111111, -222222222, -333333333, -444444444, -555555555,
             -666666666, -777777777, -888888888, -999999999}


def release_url(year):
    if type(year) is not int or not 2015 <= year <= 2024:
        raise ValueError("This ACS integration supports releases ending in 2015–2024.")
    return f"https://api.census.gov/data/{year}/acs/acs5/profile"


def _number(value, percentage=False):
    if value in (None, "", "null", "N", "(X)", "**", "***", "*****"):
        return None
    number = float(value)
    if number in SENTINELS:
        return None
    if not math.isfinite(number) or number < 0 or (percentage and number > 100):
        raise ValueError("Unexpected ACS estimate or margin of error")
    if not percentage and not number.is_integer():
        raise ValueError("Population estimates and margins must be whole numbers")
    return number


def normalize(payload, *, region=OREGON):
    region=get_region(region)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError("Unexpected Census API response")
    header = payload[0]
    required = {"NAME", "state", "county", *FIELDS}
    if len(header) != len(set(header)) or not required.issubset(header):
        raise ValueError("Census response is missing required fields")
    records, seen = [], set()
    for values in payload[1:]:
        if not isinstance(values, list) or len(values) != len(header):
            raise ValueError("Malformed Census row")
        row = dict(zip(header, values))
        fips = str(row["state"]) + str(row["county"])
        if fips not in county_names(region) or fips in seen:
            raise ValueError(f"Unexpected or duplicate {region.name} county in Census data")
        seen.add(fips)
        nums = [_number(row[field], percentage=index >= 2) for index, field in enumerate(FIELDS)]
        records.append((fips, *nums))
    if seen != set(county_names(region)):
        raise ValueError(f"Census data must include all {region.county_count} {region.name} counties")
    return records


class CensusUnavailable(ValueError):
    """Public-safe API failure that never includes credential-bearing URLs."""


class CensusKeyRequired(CensusUnavailable):
    """The deployment needs a Census API key."""


def _get_json(url, params=None):
    try:
        response = requests.get(url, params=params, timeout=(5, 30), allow_redirects=False)
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise CensusUnavailable("Census rejected the request; check API access and key configuration.")
        return response.json()
    except requests.RequestException:
        # Requests exceptions may contain ?key=...; do not retain their chain.
        raise CensusUnavailable("Census request failed; check API access and key configuration.") from None


def validate_variables(variables):
    expected = {"DP05_0001E": "Total population", "DP05_0024PE": "65 years and over", "DP03_0128PE": "All people"}
    for estimate, label in expected.items():
        metadata = variables.get(estimate, {})
        margin = estimate[:-1] + "M"
        attributes = metadata.get("attributes", "").split(",")
        if not metadata.get("label", "").endswith(label) or (margin not in variables and margin not in attributes):
            raise ValueError("ACS variable definitions differ from the expected population, age and poverty measures")


def fetch(year, api_key=None, *, region=OREGON):
    region=get_region(region)
    region.require("census")
    url = release_url(year)
    key = api_key if api_key is not None else os.environ.get("CENSUS_API_KEY")
    if not key:
        raise CensusKeyRequired("A Census API key must be configured by the site maintainer.")
    # MOEs are often attributes of estimates, not top-level variable entries.
    validate_variables(_get_json(f"{url}/variables.json")["variables"])
    payload = _get_json(url, params={"get": ",".join(["NAME", *FIELDS]), "for": "county:*", "in": f"state:{region.state_fips}", "key": key})
    return normalize(payload,region=region)


def store(records, year, path, *, region=OREGON):
    """Publish a complete snapshot; readers and failed refreshes retain old data."""
    region=get_region(region)
    if any(r[0] not in county_names(region) for r in records):
        raise ValueError("Census records belong to another region")
    path = Path(path)
    if path.exists():
        with duckdb.connect(str(path),read_only=True) as previous:
            check_identity(previous,region)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, filename = tempfile.mkstemp(dir=path.parent, suffix=".duckdb")
    os.close(handle)
    temp = Path(filename)
    temp.unlink()
    try:
        with duckdb.connect(str(temp)) as db:
            db.execute("CREATE TABLE demographics (fips VARCHAR PRIMARY KEY, population BIGINT, population_moe BIGINT, age65_pct DOUBLE, age65_moe DOUBLE, poverty_pct DOUBLE, poverty_moe DOUBLE)")
            db.executemany("INSERT INTO demographics VALUES (?, ?, ?, ?, ?, ?, ?)", records)
            db.execute("CREATE TABLE provenance (year INTEGER, source VARCHAR, imported_at TIMESTAMP, state_fips VARCHAR)")
            db.execute("INSERT INTO provenance VALUES (?, ?, current_timestamp, ?)", [year, release_url(year), region.state_fips])
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def context(fips, year, cache_dir=None, api_key=None, *, region=OREGON):
    """Return county context and source metadata, with an optional stale warning."""
    region=get_region(region)
    region.require("census")
    if fips not in county_names(region):
        raise ValueError(f"Unknown {region.name} county")
    release_url(year)
    path = Path(cache_dir if cache_dir is not None else region_cache_dir(region,DB_PATH.parent)) / f"census_{year}.duckdb"
    stale = True
    if path.exists():
        with duckdb.connect(str(path), read_only=True) as db:
            check_identity(db,region)
            imported = db.execute("SELECT imported_at FROM provenance").fetchone()[0]
        stale = datetime.now(timezone.utc) - imported.replace(tzinfo=timezone.utc) >= CACHE_TTL
    warning = None
    if stale:
        attempted = False
        try:
            if not (api_key or os.environ.get("CENSUS_API_KEY")):
                record_refresh(path, year, "key_required")
                raise CensusKeyRequired("A Census API key must be configured by the site maintainer.")
            _reserve_refresh(path)
            attempted = True
            records=fetch(year,api_key=api_key) if region==OREGON else fetch(year,api_key=api_key,region=region)
            store(records, year, path,region=region)
            record_refresh(path, year, "success")
        except (requests.RequestException, ValueError, KeyError, TypeError, duckdb.Error):
            if attempted:
                record_refresh(path, year, "failed")
            if not path.exists():
                raise
            logging.getLogger(__name__).warning("Census refresh failed; using cached data", exc_info=True)
            warning = "Census could not be refreshed. Showing previously downloaded demographics; check the import date."
    with duckdb.connect(str(path), read_only=True) as db:
        check_identity(db,region)
        # LEFT JOIN keeps a missing county record visible as unavailable fields.
        row = db.execute("SELECT d.* FROM (SELECT ? AS fips) selected LEFT JOIN demographics d USING (fips)", [fips]).fetchone()
        source_year, source, imported = db.execute("SELECT year, source, imported_at FROM provenance").fetchone()
    if source_year!=year:
        raise ValueError("Census cache period differs from the requested release")
    return {**dict(zip(["fips", *COLUMNS], row)), "year": source_year, "source": source, "imported_at": imported, "warning": warning}
