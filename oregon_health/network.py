"""Bounded CDC/ACS responses and per-host budgets across local worker processes."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

import requests

from oregon_health.regions import DATA_ROOT

WINDOW_SECONDS = 60
HOST_BUDGETS = {"data.cdc.gov": 30, "api.census.gov": 30,
                "data.cms.gov": 30, "geocoding.geo.census.gov": 30,
                "gisportal.hrsa.gov": 120}


def writable_refreshes():
    mode = os.environ.get('HEALTH_ATLAS_REFRESH_MODE', 'single-writer')
    if mode != 'single-writer':
        raise ValueError('Source refreshes are disabled; use saved snapshots or the designated writer.')


def reserve_request(url):
    writable_refreshes()
    host = urlsplit(url).hostname
    if host not in HOST_BUDGETS or not url.startswith('https://'):
        raise ValueError('Unsupported source endpoint')
    root = Path(os.environ.get('HEALTH_ATLAS_COORDINATION_DIR', DATA_ROOT))
    try:
        root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(root / '.requests.sqlite3', timeout=1)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (host TEXT, attempted_at REAL)')
            db.execute('BEGIN IMMEDIATE')
            now = time.time()
            db.execute('DELETE FROM requests WHERE attempted_at <= ?', [now - WINDOW_SECONDS])
            count = db.execute('SELECT count(*) FROM requests WHERE host = ?', [host]).fetchone()[0]
            if count >= HOST_BUDGETS[host]:
                raise ValueError('Source request budget exhausted; retry after one minute.')
            db.execute('INSERT INTO requests VALUES (?, ?)', [host, now])
    except (sqlite3.Error, OSError):
        raise ValueError('Source request coordination is unavailable; retry later.') from None


def get_json(url, params=None, *, max_bytes=2_000_000):
    """Count attempts, reject redirects, cap decompressed bytes, close on every exit."""
    reserve_request(url)
    with requests.get(url, params=params, timeout=(5, 30), stream=True,
                      allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Source did not return a complete response')
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError('Source response exceeds the byte budget')
            chunks.append(chunk)
    return json.loads(b''.join(chunks))
