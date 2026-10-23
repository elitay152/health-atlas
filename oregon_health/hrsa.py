"""Bounded Oregon HRSA HPSA imports, preserving component and designation grain.

Run: python -m oregon_health.hrsa. Source polygons are generalized by the HRSA
query service for display; they are not eligibility or address-level determinations.
"""
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile

import duckdb
import requests

from oregon_health.network import reserve_request
from oregon_health.api_cache import _reserve_refresh
from oregon_health.data import DB_PATH as HEALTH_PATH
from oregon_health.profile import county_names
from oregon_health.status import record_refresh

BASE = "https://gisportal.hrsa.gov/server/rest/services/Shortage/HealthProfessionalShortageAreas_FS/MapServer"
SOURCE_URL = "https://data.hrsa.gov/topics/health-workforce/shortage-areas"
DB_PATH = HEALTH_PATH.parent / "hrsa.duckdb"
CACHE_TTL = timedelta(hours=24)
DISCIPLINES = {"Primary Care": (9, 11), "Dental Health": (1, 3), "Mental Health": (5, 7)}
STATUSES = {"D": "Designated", "P": "Proposed For Withdrawal", "W": "Withdrawn"}
COMMON = ["OBJECTID", "HPSA_SOURCE_ID", "HPSA_NM", "HPSA_TYP_CD", "HPSA_TYP_DESC",
          "HPSA_STATUS_CD", "HPSA_STATUS_DESC", "DISCIPLINE_CLASS_DESC", "HPSA_DESIGNATION_DT_TXT",
          "HPSA_DESIG_LAST_UPD_DT_TXT", "HPSA_WITHDRAWAL_DT_TXT", "DW_RECORD_CREATE_DT_TXT",
          "CMN_STATE_COUNTY_FIPS_CD", "HPSA_POPULATION_TYP_DESC"]
COMPONENT = ["GEO_ID", "COMP_TYP_CD", "COMP_NM", "STATE_COUNTY_FIPS_CD", "STATE_FIPS_CD"]
POINT = ["PRIMARY_STATE_FIPS_CD", "CMN_STATE_FIPS_CD"]
MAX_FEATURES = 5000


def _get(path, params=None):
    """Only fixed HRSA service paths; reject redirects and bound decoded bytes."""
    reserve_request(BASE + path)
    with requests.get(BASE + path, params={"f": "json", **(params or {})}, timeout=(5, 60),
                      allow_redirects=False, stream=True) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("HRSA did not return a complete response")
        parts, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 20_000_000:
                raise ValueError("HRSA response exceeds the download limit")
            parts.append(chunk)
    result = json.loads(b"".join(parts))
    if not isinstance(result, dict) or "error" in result:
        raise ValueError("HRSA returned an unexpected service response")
    return result


def _date(value):
    if value in (None, "", "NA"):
        return None
    return datetime.strptime(value, "%Y/%m/%d").date().isoformat()


def _geometry(value, kind):
    if value is None:
        return None
    allowed = {"Point"} if kind == "Facility" else {"Polygon", "MultiPolygon"}
    if not isinstance(value, dict) or value.get("type") not in allowed:
        raise ValueError("Unexpected HRSA geometry type")
    vertices = 0
    def coordinate(coords):
        nonlocal vertices
        vertices += 1
        if (not isinstance(coords,list) or len(coords)!=2
                or not all(type(v) in (int,float) and math.isfinite(v) for v in coords)
                or not -180<=coords[0]<=180 or not -90<=coords[1]<=90):
            raise ValueError("Invalid HRSA coordinates")
    def polygon(rings):
        if not isinstance(rings,list) or not rings:
            raise ValueError("Malformed HRSA polygon")
        for ring in rings:
            if not isinstance(ring,list) or len(ring)<4 or ring[0]!=ring[-1]:
                raise ValueError("Malformed HRSA polygon ring")
            for point in ring:
                coordinate(point)
    coords=value.get("coordinates")
    if value["type"]=="Point":
        coordinate(coords)
    elif value["type"]=="Polygon":
        polygon(coords)
    else:
        if not isinstance(coords,list) or not coords:
            raise ValueError("Malformed HRSA multipolygon")
        for item in coords:
            polygon(item)
    if vertices > 200000:
        raise ValueError("HRSA geometry exceeds the vertex limit")
    return value


def normalize(features, discipline, layer, point):
    rows, seen = [], set()
    for feature in features:
        p = feature["properties"]
        oid = p["OBJECTID"]
        designation_id = p["HPSA_SOURCE_ID"]
        if (type(oid) is not int or oid in seen or not isinstance(designation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9]{10}", designation_id) or not p.get("HPSA_NM")):
            raise ValueError("Invalid or duplicate HRSA feature identifier")
        seen.add(oid)
        if p["DISCIPLINE_CLASS_DESC"] != discipline or p["HPSA_STATUS_CD"] not in STATUSES:
            raise ValueError("HRSA discipline or status differs from the source contract")
        if p["HPSA_STATUS_DESC"] != STATUSES[p["HPSA_STATUS_CD"]]:
            raise ValueError("HRSA status description changed")
        state = (p.get("PRIMARY_STATE_FIPS_CD") == "41" or p.get("CMN_STATE_FIPS_CD") == "41") if point else p.get("STATE_FIPS_CD") == "41"
        if not state:
            raise ValueError("HRSA returned a feature outside the Oregon query")
        if point:
            if p["HPSA_TYP_CD"] in {"Hpsa Geo", "Hpsa Geo HN", "Hpsa Pop"}:
                raise ValueError("Non-facility designation appeared in the facility point layer")
            category = "Facility"
        elif p["HPSA_TYP_CD"] in {"Hpsa Geo", "Hpsa Geo HN"}:
            category = "Geographic"
        elif p["HPSA_TYP_CD"] == "Hpsa Pop":
            category = "Population group"
        else:
            raise ValueError("Unexpected HRSA polygon designation type")
        county = p.get("CMN_STATE_COUNTY_FIPS_CD") if point else p.get("STATE_COUNTY_FIPS_CD")
        row = dict(feature_key=f"{layer}:{oid}", designation_id=designation_id,
                   name=p["HPSA_NM"], discipline=discipline, category=category,
                   designation_type=p["HPSA_TYP_DESC"], status=STATUSES[p["HPSA_STATUS_CD"]],
                   county_fips=county if county in county_names() else None, source_county_fips=county,
                   geography_id=p.get("GEO_ID"), component_type=p.get("COMP_TYP_CD"),
                   component_name=p.get("COMP_NM"), population=p.get("HPSA_POPULATION_TYP_DESC"),
                   designated=_date(p.get("HPSA_DESIGNATION_DT_TXT")),
                   updated=_date(p.get("HPSA_DESIG_LAST_UPD_DT_TXT")),
                   withdrawn=_date(p.get("HPSA_WITHDRAWAL_DT_TXT")),
                   source_date=_date(p["DW_RECORD_CREATE_DT_TXT"]), source_layer=layer,
                   geometry=_geometry(feature.get("geometry"), category))
        if row["source_date"] is None:
            raise ValueError("HRSA warehouse date is unavailable")
        rows.append(row)
    return rows


def fetch():
    records, layers = [], []
    for discipline, ids in DISCIPLINES.items():
        for index, layer in enumerate(ids):
            point = index == 0
            required = COMMON + (POINT if point else COMPONENT)
            metadata = _get(f"/{layer}")
            expected_name = f"{discipline} HPSA - {'points' if point else 'component polygons'}"
            if (metadata.get("name") != expected_name or not set(required).issubset(
                    {f["name"] for f in metadata.get("fields", [])})):
                raise ValueError("HRSA layer schema differs from the expected source")
            where = "(PRIMARY_STATE_FIPS_CD = '41' OR CMN_STATE_FIPS_CD = '41')" if point else "STATE_FIPS_CD = '41'"
            ids = _get(f"/{layer}/query", {"where": where, "returnIdsOnly": "true"}).get("objectIds")
            if not isinstance(ids, list) or len(ids) > MAX_FEATURES or any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
                raise ValueError("HRSA returned an invalid or oversized feature index")
            features = []
            for offset in range(0, len(ids), 100):
                chunk = ids[offset:offset+100]
                payload = _get(f"/{layer}/query", {"f": "geojson", "objectIds": ",".join(map(str, chunk)),
                    "outFields": ",".join(required), "outSR": "4326", "returnGeometry": "true",
                    "maxAllowableOffset": "0.005"})
                batch = payload.get("features")
                if (payload.get("exceededTransferLimit") or not isinstance(batch, list)
                        or len(batch) != len(chunk) or {f["properties"]["OBJECTID"] for f in batch} != set(chunk)):
                    raise ValueError("HRSA returned a truncated or inconsistent feature batch")
                features.extend(batch)
            records.extend(normalize(features, discipline, layer, point))
            if len(records)>MAX_FEATURES:
                raise ValueError("HRSA Oregon snapshot exceeds the feature limit")
            layers.append(dict(layer=layer, name=expected_name, features=len(features)))
    if not records:
        raise ValueError("HRSA returned no Oregon designations")
    return records, dict(source_url=SOURCE_URL, service_url=BASE, fetched_at=datetime.now(timezone.utc).isoformat(),
                         layers=layers, generalization_degrees=0.005)


def store(records, provenance, path=DB_PATH):
    """Atomic, separate snapshot; one row per source feature, not per designation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, filename = tempfile.mkstemp(dir=path.parent, suffix=".duckdb")
    os.close(handle)
    temp = Path(filename)
    temp.unlink()
    try:
        with duckdb.connect(str(temp)) as db:
            db.execute("CREATE TABLE features (feature_key VARCHAR PRIMARY KEY, designation_id VARCHAR, discipline VARCHAR, county_fips VARCHAR, payload VARCHAR)")
            db.executemany("INSERT INTO features VALUES (?, ?, ?, ?, ?)", [
                (r["feature_key"],r["designation_id"],r["discipline"],r["county_fips"],json.dumps(r)) for r in records])
            db.execute("CREATE TABLE provenance (payload VARCHAR)")
            db.execute("INSERT INTO provenance VALUES (?)",[json.dumps(provenance)])
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def snapshot(path=DB_PATH):
    with duckdb.connect(str(path),read_only=True) as db:
        records=[json.loads(row[0]) for row in db.execute("SELECT payload FROM features ORDER BY feature_key").fetchall()]
        provenance=json.loads(db.execute("SELECT payload FROM provenance").fetchone()[0])
    return records,provenance


def needs_refresh(path=DB_PATH):
    if not Path(path).exists():
        return True
    _,provenance=snapshot(path)
    return datetime.now(timezone.utc)-datetime.fromisoformat(provenance["fetched_at"])>=CACHE_TTL


def refresh(path=DB_PATH,force=False):
    if not force and not needs_refresh(path):
        return
    _reserve_refresh(path)
    try:
        store(*fetch(),path=path)
    except Exception:
        record_refresh(path,"hrsa-hpsa","failed")
        raise
    record_refresh(path,"hrsa-hpsa","success")


if __name__ == "__main__":
    refresh()
    records,provenance=snapshot()
    print(json.dumps({"features":len(records),"designations":len({(r['discipline'],r['designation_id']) for r in records}),
                      "provenance":provenance},indent=2))
