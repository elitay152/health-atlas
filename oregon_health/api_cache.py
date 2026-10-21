"""Persistent CDC API cache shared across Streamlit reruns."""
from datetime import datetime, timedelta, timezone
from threading import RLock

import duckdb

from oregon_health.data import DB_PATH, store
from oregon_health.import_data import fetch

DEFAULT_DATASET = "swc5-untb"
CACHE_TTL = timedelta(hours=24)
_lock = RLock()


def needs_refresh(dataset, path=DB_PATH, now=None):
    if not path.exists():
        return True
    with duckdb.connect(str(path), read_only=True) as db:
        source, imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
    return f"https://data.cdc.gov/d/{dataset}" not in source or (now or datetime.now(timezone.utc)) - imported.replace(tzinfo=timezone.utc) >= CACHE_TTL


def refresh(dataset, path=DB_PATH):
    """Download before touching the cache; failed fetches retain old data."""
    with _lock:
        rows, source = fetch(dataset)
        return store(rows, source, path)
