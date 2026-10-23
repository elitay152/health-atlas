"""Read-only data coverage plus safe, persistent refresh outcomes."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging
import re
import sqlite3

import duckdb
import pandas as pd

from oregon_health.data import MEASURES
from oregon_health.profile import county_names
from oregon_health.regions import OREGON, get_region, check_identity


def record_refresh(path, identity, outcome):
    """Never store exception strings, URLs, keys, or credential values."""
    if outcome not in {"success", "failed", "key_required"}:
        raise ValueError("Unknown refresh outcome")
    identity = str(identity)
    if not re.fullmatch(r"(?:[a-z0-9]{4}-[a-z0-9]{4}|[0-9]{4})", identity):
        identity = "invalid-source"
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(path.parent / ".source-status.sqlite3"), timeout=1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS refreshes (cache TEXT PRIMARY KEY, identity TEXT, outcome TEXT, checked_at TEXT)")
            db.execute("INSERT OR REPLACE INTO refreshes VALUES (?, ?, ?, ?)", [path.name, identity, outcome, datetime.now(timezone.utc).isoformat()])
    except (sqlite3.Error, OSError):
        logging.getLogger(__name__).warning("Could not save data-status metadata")


def last_refresh(path, identity):
    path = Path(path)
    log = path.parent / ".source-status.sqlite3"
    if not log.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{log.as_posix()}?mode=ro", uri=True, timeout=1)) as db:
            row = db.execute("SELECT outcome, checked_at FROM refreshes WHERE cache = ? AND identity = ?", [path.name, str(identity)]).fetchone()
        return dict(outcome=row[0], checked_at=row[1]) if row else None
    except sqlite3.Error:
        return None


def _freshness(imported, ttl, now):
    imported = imported.replace(tzinfo=timezone.utc) if imported.tzinfo is None else imported
    return "Refresh due" if now - imported >= ttl else "Within refresh window"


def health_report(path, year, dataset, demo=False, now=None, *, region=OREGON):
    region=get_region(region)
    path = Path(path)
    event = None if demo else last_refresh(path, dataset)
    result = dict(loaded=False, event=event, coverage=pd.DataFrame(), missing=pd.DataFrame(), demo=demo)
    if not path.exists():
        return result
    with duckdb.connect(str(path), read_only=True) as db:
        check_identity(db,region)
        source, imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
        years = [r[0] for r in db.execute("SELECT DISTINCT year FROM estimates ORDER BY year DESC").fetchall()]
        rows = db.execute("SELECT fips, measure, low, high FROM estimates WHERE year = ? AND prevalence IS NOT NULL", [year]).fetchall()
    expected = county_names(region)
    coverage, missing = [], []
    for code, label in MEASURES.items():
        available = {fips for fips, measure, low, high in rows if measure == code and fips in expected}
        uncertainty_missing = sum(1 for fips, measure, low, high in rows if measure == code and fips in expected and (low is None or high is None))
        coverage.append({"Measure": label, "Available counties": len(available), "Expected counties": len(expected), "Missing estimates": len(expected) - len(available), "Without uncertainty": uncertainty_missing})
        missing.extend({"County": expected[fips], "FIPS": fips, "Measure": label, "Estimate year": year} for fips in expected if fips not in available)
    result.update(loaded=True, source=source, imported=imported, years=years,
                  freshness="Synthetic demo" if demo else _freshness(imported, timedelta(hours=24), now or datetime.now(timezone.utc)),
                  source_matches=demo or f"https://data.cdc.gov/d/{dataset}" in source,
                  coverage=pd.DataFrame(coverage), missing=pd.DataFrame(missing))
    return result


def census_report(cache_dir, year, now=None, *, region=OREGON):
    region=get_region(region)
    expected=county_names(region)
    path = Path(cache_dir) / f"census_{year}.duckdb"
    result = dict(loaded=False, event=last_refresh(path, year), coverage=pd.DataFrame(), missing=pd.DataFrame())
    if not path.exists():
        return result
    with duckdb.connect(str(path), read_only=True) as db:
        check_identity(db,region)
        stored_year, source, imported = db.execute("SELECT year, source, imported_at FROM provenance").fetchone()
        records = {r[0]: r[1:] for r in db.execute("SELECT fips, population, population_moe, age65_pct, age65_moe, poverty_pct, poverty_moe FROM demographics").fetchall()}
    if stored_year != year:
        raise ValueError("Census cache year differs from requested release")
    coverage, missing = [], []
    for index, label in enumerate(["Population", "Residents age 65+", "Below poverty level"]):
        available = {fips for fips, values in records.items() if fips in expected and values[index*2] is not None}
        coverage.append({"Measure": label, "Available counties": len(available), "Expected counties": len(expected), "Missing estimates": len(expected)-len(available), "Without uncertainty": sum(1 for fips in available if records[fips][index*2+1] is None)})
        missing.extend({"County": name, "FIPS": fips, "Measure": label, "Period": f"{year-4}–{year}"} for fips, name in expected.items() if fips not in available)
    result.update(loaded=True, source=source, imported=imported,
                  freshness=_freshness(imported, timedelta(days=30), now or datetime.now(timezone.utc)),
                  coverage=pd.DataFrame(coverage), missing=pd.DataFrame(missing))
    return result
