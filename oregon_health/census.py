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
from oregon_health.profile import county_names

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


def normalize(payload):
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
        if fips not in county_names() or fips in seen:
            raise ValueError("Unexpected or duplicate Oregon county in Census data")
        seen.add(fips)
        nums = [_number(row[field], percentage=index >= 2) for index, field in enumerate(FIELDS)]
        records.append((fips, *nums))
    if seen != set(county_names()):
        raise ValueError("Census data must include all 36 Oregon counties")
    return records


def fetch(year):
    url = release_url(year)
    # Confirm variable meanings for this release; row numbers can change over time.
    response = requests.get(f"{url}/variables.json", timeout=(5, 30))
    response.raise_for_status()
    variables = response.json()["variables"]
    expected = {"DP05_0001E": "Total population", "DP05_0024PE": "65 years and over", "DP03_0128PE": "All people"}
    if any(field not in variables for field in FIELDS) or any(
        not variables[field]["label"].endswith(label) for field, label in expected.items()
    ):
        raise ValueError("ACS variable definitions differ from the expected population, age and poverty measures")
    response = requests.get(url, params={"get": ",".join(["NAME", *FIELDS]), "for": "county:*", "in": "state:41"}, timeout=(5, 30))
    response.raise_for_status()
    return normalize(response.json())


def store(records, year, path):
    """Publish a complete snapshot; readers and failed refreshes retain old data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, filename = tempfile.mkstemp(dir=path.parent, suffix=".duckdb")
    os.close(handle)
    temp = Path(filename)
    temp.unlink()
    try:
        with duckdb.connect(str(temp)) as db:
            db.execute("CREATE TABLE demographics (fips VARCHAR PRIMARY KEY, population BIGINT, population_moe BIGINT, age65_pct DOUBLE, age65_moe DOUBLE, poverty_pct DOUBLE, poverty_moe DOUBLE)")
            db.executemany("INSERT INTO demographics VALUES (?, ?, ?, ?, ?, ?, ?)", records)
            db.execute("CREATE TABLE provenance (year INTEGER, source VARCHAR, imported_at TIMESTAMP)")
            db.execute("INSERT INTO provenance VALUES (?, ?, current_timestamp)", [year, release_url(year)])
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def context(fips, year, cache_dir=None):
    """Return county context and source metadata, with an optional stale warning."""
    if fips not in county_names():
        raise ValueError("Unknown Oregon county")
    release_url(year)
    path = Path(cache_dir if cache_dir is not None else DB_PATH.parent) / f"census_{year}.duckdb"
    stale = True
    if path.exists():
        with duckdb.connect(str(path), read_only=True) as db:
            imported = db.execute("SELECT imported_at FROM provenance").fetchone()[0]
        stale = datetime.now(timezone.utc) - imported.replace(tzinfo=timezone.utc) >= CACHE_TTL
    warning = None
    if stale:
        try:
            _reserve_refresh(path)
            store(fetch(year), year, path)
        except (requests.RequestException, ValueError, KeyError, TypeError, duckdb.Error):
            if not path.exists():
                raise
            logging.getLogger(__name__).warning("Census refresh failed; using cached data", exc_info=True)
            warning = "Census could not be refreshed. Showing previously downloaded demographics; check the import date."
    with duckdb.connect(str(path), read_only=True) as db:
        # LEFT JOIN keeps a missing county record visible as unavailable fields.
        row = db.execute("SELECT d.* FROM (SELECT ? AS fips) selected LEFT JOIN demographics d USING (fips)", [fips]).fetchone()
        source_year, source, imported = db.execute("SELECT year, source, imported_at FROM provenance").fetchone()
    return {**dict(zip(["fips", *COLUMNS], row)), "year": source_year, "source": source, "imported_at": imported, "warning": warning}
