"""Persistent CDC API cache and deployment-wide refresh throttling."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time

import duckdb

from oregon_health.network import writable_refreshes
from oregon_health.data import CATALOG, DB_PATH, store
from oregon_health.import_data import fetch
from oregon_health.status import record_refresh
from oregon_health.regions import OREGON, get_region, health_path, check_identity

DEFAULT_DATASET = "swc5-untb"
CACHE_TTL = timedelta(hours=24)
REFRESH_COOLDOWN_SECONDS = 300


class RefreshLimited(ValueError):
    """Another visitor recently reserved a refresh for this cache."""


def catalog_is_current(path):
    if not Path(path).exists():
        return False
    with duckdb.connect(str(path), read_only=True) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info('provenance')").fetchall()}
        return "catalog" in columns and db.execute("SELECT catalog FROM provenance").fetchone()[0] == CATALOG


def needs_refresh(dataset, path=None, now=None, *, region=OREGON):
    region=get_region(region)
    path=path if path is not None else (DB_PATH if region==OREGON else health_path(region))
    if not catalog_is_current(path):
        return True
    with duckdb.connect(str(path), read_only=True) as db:
        check_identity(db,region)
        source, imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
    return f"https://data.cdc.gov/d/{dataset}" not in source or (now or datetime.now(timezone.utc)) - imported.replace(tzinfo=timezone.utc) >= CACHE_TTL


def _reserve_refresh(path):
    """Reserve before network I/O, including failures; share state across processes."""
    writable_refreshes()
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(str(path.parent / ".refresh.sqlite3"), timeout=1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (cache TEXT PRIMARY KEY, attempted_at REAL NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT attempted_at FROM attempts WHERE cache = ?", [path.name]).fetchone()
            now = time.time()
            if previous and now - previous[0] < REFRESH_COOLDOWN_SECONDS:
                raise RefreshLimited("A refresh was recently requested. Please wait up to five minutes before trying again.")
            db.execute("INSERT OR REPLACE INTO attempts VALUES (?, ?)", [path.name, now])
    except sqlite3.OperationalError as exc:
        raise RefreshLimited("Refresh is busy. Please try again shortly.") from exc


def refresh(dataset, path=None, *, region=OREGON):
    """Limit this cache; a failed download leaves the snapshot untouched."""
    region=get_region(region)
    region.require("county_health")
    path=path if path is not None else (DB_PATH if region==OREGON else health_path(region))
    # Reject a foreign cache before reserving or making a request.
    if Path(path).exists():
        with duckdb.connect(str(path),read_only=True) as previous:
            check_identity(previous,region)
    _reserve_refresh(path)
    try:
        rows, source = fetch(dataset) if region==OREGON else fetch(dataset,region=region)
        count = store(rows, source, path,region=region)
    except Exception:
        record_refresh(path, dataset, "failed")
        raise
    record_refresh(path, dataset, "success")
    return count
