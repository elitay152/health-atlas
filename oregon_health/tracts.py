"""Separate CDC PLACES tract snapshots. Run: python -m oregon_health.tracts.

Modeled adult prevalence is not patient data, service demand or program impact.
Missing rows/values are unavailable, never zero. Boundary preparation is separate.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import time

import duckdb
import requests

from oregon_health.network import reserve_request
from oregon_health.api_cache import _reserve_refresh
from oregon_health.data import DB_PATH as COUNTY_PATH, MEASURES
from oregon_health.profile import county_names
from oregon_health.status import record_refresh
from oregon_health.regions import OREGON, get_region, cache_dir, health_path
from oregon_health.storage import snapshot

DATASET = 'cwsq-ngmh'
RELEASE_YEAR = 2025
BASE = 'https://data.cdc.gov'
METADATA_URL = f'{BASE}/api/views/{DATASET}.json'
RESOURCE_URL = f'{BASE}/resource/{DATASET}.json'
DB_PATH = COUNTY_PATH.parent / 'tracts.duckdb'
CACHE_TTL = timedelta(hours=24)
PAGE_SIZE = 1000
MAX_ROWS = 50_000
MAX_BYTES = 20_000_000
FIELDS = ('locationid,countyfips,countyname,locationname,stateabbr,measureid,measure,'
          'year,data_value,low_confidence_limit,high_confidence_limit,'
          'data_value_type,data_value_unit,datasource,category,categoryid,'
          'datavaluetypeid,short_question_text,totalpopulation,totalpop18plus,'
          'data_value_footnote,data_value_footnote_symbol')
COLUMNS = ['tract_fips', 'county_fips', 'county', 'tract', 'measure', 'year',
           'prevalence', 'low', 'high', 'definition', 'unit', 'estimate_type',
           'datasource', 'category', 'category_id', 'estimate_type_id',
           'short_question', 'population', 'adult_population', 'footnote', 'footnote_symbol']


def _download(url, params=None):
    """Fixed official endpoints, bounded decompressed bytes, no redirects."""
    reserve_request(url)
    with requests.get(url, params=params, timeout=(5, 30), stream=True,
                      allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('CDC did not return a complete resource')
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError('CDC page exceeds byte limit')
            chunks.append(chunk)
    return json.loads(b''.join(chunks)), size


def _get(url, params=None):
    for attempt in range(3):
        try:
            return _download(url, params)
        except requests.RequestException as error:
            status = error.response.status_code if error.response is not None else None
            transient = isinstance(error, (requests.Timeout, requests.ConnectionError)) or status in {429, 502, 503, 504}
            if not transient or attempt == 2:
                raise
            time.sleep(attempt + 1)


def _metadata():
    meta, _ = _get(METADATA_URL)
    if (not isinstance(meta, dict) or meta.get('id') != DATASET or meta.get('name') !=
            'PLACES: Local Data for Better Health, Census Tract Data, 2025 release'):
        raise ValueError('Unexpected CDC tract release')
    fields = {c['fieldName'] for c in meta['columns']}
    if not set(FIELDS.split(',')).issubset(fields):
        raise ValueError('CDC tract schema is missing required fields')
    if not isinstance(meta.get('rowsUpdatedAt'), int):
        raise ValueError('CDC source revision is unavailable')
    return meta


def fetch(*,region=OREGON):
    region=get_region(region)
    region.require("tract_health")
    meta = _metadata()
    codes = ','.join(f"'{code}'" for code in MEASURES)
    where = f"stateabbr='{region.abbr}' AND measureid in ({codes})"
    counts, size = _get(RESOURCE_URL, {'$select': 'count(*) as rows', '$where': where})
    if (not isinstance(counts, list) or len(counts) != 1 or not isinstance(counts[0], dict)
            or not re.fullmatch(r'[0-9]+', str(counts[0].get('rows', '')))):
        raise ValueError('Invalid CDC count response')
    expected = int(counts[0]['rows'])
    if not 0 < expected <= MAX_ROWS:
        raise ValueError('CDC tract row count outside import bounds')
    rows = []
    # The extra request at an exact page boundary verifies pagination terminates.
    for offset in range(0, expected + 1, PAGE_SIZE):
        page, used = _get(RESOURCE_URL, {'$select': FIELDS, '$where': where,
                                       '$order': 'locationid,measureid,year',
                                       '$limit': PAGE_SIZE, '$offset': offset})
        size += used
        if size > MAX_BYTES or not isinstance(page, list) or len(page) > PAGE_SIZE:
            raise ValueError('CDC response exceeds import bounds or has invalid shape')
        rows.extend(page)
        if len(rows) > expected:
            raise ValueError('CDC row count changed during pagination')
        if len(page) < PAGE_SIZE:
            break
    if len(rows) != expected or _metadata()['rowsUpdatedAt'] != meta['rowsUpdatedAt']:
        raise ValueError('CDC response incomplete or source changed during pagination')
    normalize(rows,region=region)
    return rows, dict(state=region.abbr,state_fips=region.state_fips,dataset=DATASET, release_year=RELEASE_YEAR, source_url=f'{BASE}/d/{DATASET}',
                      metadata_url=METADATA_URL, title=meta['name'],
                      source_rows_updated_at=meta['rowsUpdatedAt'],
                      fetched_at=datetime.now(timezone.utc).isoformat(),
                      catalog=list(MEASURES), rows=len(rows), response_bytes=size,
                      confidence_level=95, geography='census_tract', recommended_boundary_year=2023,
                      license_id=meta.get('licenseId'), attribution=meta.get('attribution'))


def _text(row, key, required=False):
    value = row.get(key)
    if value is None or value == '':
        if required:
            raise ValueError(f'Missing CDC field: {key}')
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Invalid CDC text field: {key}')
    return value


def _number(value, integer=False):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('Invalid CDC number')
    number = float(value)
    if not math.isfinite(number) or number < 0 or (integer and not number.is_integer()):
        raise ValueError('Invalid CDC number')
    return int(number) if integer else number


def normalize(rows,*,region=OREGON):
    region=get_region(region)
    names=county_names(region)
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
        raise ValueError('Invalid CDC tract row collection')
    records, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid CDC tract record')
        tract = _text(row, 'locationid', True)
        county = _text(row, 'countyfips', True)
        if (not re.fullmatch(region.state_fips+r'[0-9]{9}', tract) or county not in names
                or tract[:5] != county or row.get('stateabbr') != region.abbr):
            raise ValueError(f'Invalid {region.name} tract/county identifier')
        measure = row.get('measureid')
        if (measure not in MEASURES or row.get('data_value_type') != 'Crude prevalence'
                or row.get('data_value_unit') != '%'):
            raise ValueError('Unexpected CDC measure, unit or estimate type')
        year = _number(row.get('year'), integer=True)
        if year is None or not 2000 <= year <= RELEASE_YEAR:
            raise ValueError('Invalid CDC estimate year')
        key = (tract, measure, year)
        if key in seen:
            raise ValueError('Duplicate tract/measure/year')
        seen.add(key)
        value, low, high = [_number(row.get(k)) for k in
                            ('data_value', 'low_confidence_limit', 'high_confidence_limit')]
        if (any(x is not None and x > 100 for x in (value, low, high))
                or (low is not None and high is not None and low > high)
                or (value is not None and low is not None and low > value)
                or (value is not None and high is not None and high < value)):
            raise ValueError('Invalid prevalence or confidence interval')
        population = _number(row.get('totalpopulation'), integer=True)
        adults = _number(row.get('totalpop18plus'), integer=True)
        if population is not None and adults is not None and adults > population:
            raise ValueError('Adult population exceeds total population')
        records.append((tract, county, _text(row, 'countyname', True),
                        _text(row, 'locationname', True), measure, year, value, low, high,
                        _text(row, 'measure', True), '%', 'Crude prevalence',
                        _text(row, 'datasource', True), _text(row, 'category', True),
                        _text(row, 'categoryid'), _text(row, 'datavaluetypeid'),
                        _text(row, 'short_question_text'), population, adults,
                        _text(row, 'data_value_footnote'), _text(row, 'data_value_footnote_symbol')))
    return records


def tract_path(region=OREGON):
    region=get_region(region)
    return DB_PATH if region==OREGON else cache_dir(region,DB_PATH.parent)/'tracts.duckdb'


def check_region(db,region=OREGON):
    region=get_region(region)
    p=json.loads(db.execute('SELECT payload FROM tract_provenance').fetchone()[0])
    if p.get('state_fips','41')!=region.state_fips or p.get('state',region.abbr)!=region.abbr:
        raise ValueError('Tract snapshot belongs to another region')
    return p


def store(rows, provenance, path=None,*,region=OREGON):
    region=get_region(region)
    region.require('tract_health')
    records=normalize(rows,region=region)
    if (provenance['dataset'] != DATASET or provenance['release_year'] != RELEASE_YEAR
            or provenance['rows'] != len(records) or provenance['catalog'] != list(MEASURES)
            or provenance.get('state_fips',region.state_fips)!=region.state_fips
            or provenance.get('state',region.abbr)!=region.abbr):
        raise ValueError('Tract provenance does not match records')
    fetched = datetime.fromisoformat(provenance['fetched_at'])
    if fetched.tzinfo is None:
        raise ValueError('Tract fetch time must include timezone')
    payload=json.dumps(dict(provenance,state=region.abbr,state_fips=region.state_fips),allow_nan=False)
    path=Path(path if path is not None else tract_path(region))
    if path.resolve() in {COUNTY_PATH.resolve(),health_path(region,COUNTY_PATH.parent).resolve()}:
        raise ValueError('Tract snapshots cannot replace the county database')
    def validate(previous):
        with duckdb.connect(str(previous),read_only=True) as db:
            check_region(db,region)
    with snapshot(path,validate) as temp:
        with duckdb.connect(str(temp)) as db:
            db.execute('''CREATE TABLE tract_estimates (
                tract_fips VARCHAR, county_fips VARCHAR, county VARCHAR, tract VARCHAR,
                measure VARCHAR, year INTEGER, prevalence DOUBLE, low DOUBLE, high DOUBLE,
                definition VARCHAR, unit VARCHAR, estimate_type VARCHAR, datasource VARCHAR,
                category VARCHAR, category_id VARCHAR, estimate_type_id VARCHAR,
                short_question VARCHAR, population BIGINT, adult_population BIGINT,
                footnote VARCHAR, footnote_symbol VARCHAR,
                PRIMARY KEY (tract_fips, measure, year))''')
            db.executemany('''INSERT INTO tract_estimates VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', records)
            db.execute('CREATE TABLE tract_provenance (payload VARCHAR)')
            db.execute('INSERT INTO tract_provenance VALUES (?)', [payload])
    return len(records)


def estimates(county_fips, measure, year, path=None,*,region=OREGON):
    """Read one county's saved tract rows, including unavailable estimates."""
    region=get_region(region)
    path=path if path is not None else tract_path(region)
    if county_fips not in county_names(region) or measure not in MEASURES:
        raise ValueError(f'Unknown {region.name} county or measure')
    with duckdb.connect(str(path), read_only=True) as db:
        check_region(db,region)
        cursor = db.execute('''SELECT * FROM tract_estimates
            WHERE county_fips = ? AND measure = ? AND year = ? ORDER BY tract_fips''',
                            [county_fips, measure, year])
        return [dict(zip(COLUMNS, row)) for row in cursor.fetchall()]


def coverage(path=None,*,region=OREGON):
    """Read-only counts are saved estimates, not population or boundary coverage."""
    region=get_region(region)
    path=path if path is not None else tract_path(region)
    with duckdb.connect(str(path), read_only=True) as db:
        provenance=check_region(db,region)
        cursor = db.execute('''SELECT county_fips, measure, year, count(*) AS rows,
            count(DISTINCT tract_fips) AS tracts, count(prevalence) AS available,
            count(*) FILTER (WHERE prevalence IS NULL) AS unavailable,
            count(*) FILTER (WHERE prevalence IS NOT NULL AND (low IS NULL OR high IS NULL))
                AS without_uncertainty
            FROM tract_estimates GROUP BY county_fips, measure, year
            ORDER BY county_fips, measure, year''')
        counts = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
    return dict(provenance=provenance, coverage=counts)


def needs_refresh(path=None,*,region=OREGON):
    region=get_region(region)
    path=path if path is not None else tract_path(region)
    if not Path(path).exists():
        return True
    provenance = coverage(path,region=region)['provenance']
    return (provenance['catalog'] != list(MEASURES) or provenance['dataset'] != DATASET
            or datetime.now(timezone.utc) - datetime.fromisoformat(provenance['fetched_at']) >= CACHE_TTL)


def refresh(path=None, force=False,*,region=OREGON):
    region=get_region(region)
    region.require('tract_health')
    path=path if path is not None else tract_path(region)
    if Path(path).exists():
        with duckdb.connect(str(path),read_only=True) as db:
            check_region(db,region)
    if not force and not needs_refresh(path,region=region):
        return coverage(path,region=region)['provenance']['rows']
    _reserve_refresh(path)
    try:
        rows, provenance = fetch() if region==OREGON else fetch(region=region)
        count = store(rows, provenance, path,region=region)
    except Exception:
        record_refresh(path, DATASET, 'failed')
        raise
    record_refresh(path, DATASET, 'success')
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path)
    parser.add_argument('--region', choices=['OR','WA'], default='OR')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    try:
        refresh(args.path, force=args.refresh,region=args.region)
        report = coverage(args.path,region=args.region)
    except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
        parser.exit(1, 'CDC tract refresh failed; any previous snapshot was retained. Check source access/schema or retry later.\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
