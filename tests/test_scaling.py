"""Concurrency and failure tests use synthetic data and local coordination only."""
import multiprocessing
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import duckdb
import pytest

from oregon_health import api_cache, census, data, network, storage


def _attempt_request(limit):
    network.HOST_BUDGETS["data.cdc.gov"] = limit
    try:
        network.reserve_request('https://data.cdc.gov/resource/synthetic.json')
        return True
    except ValueError:
        return False


def test_processes_share_host_budget_across_region_cache_paths(monkeypatch):
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context('spawn')) as workers:
        assert sum(workers.map(_attempt_request, [3] * 12)) == 3
    # Another host has an independent budget; expiry permits future CDC attempts.
    network.reserve_request('https://api.census.gov/data/synthetic')
    now = network.time.time()
    monkeypatch.setattr(network.time, 'time', lambda: now + 61)
    network.reserve_request('https://data.cdc.gov/resource/synthetic.json')


def test_coordination_failure_and_replica_mode_fail_before_network(tmp_path, monkeypatch):
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('blocked')
    monkeypatch.setenv('HEALTH_ATLAS_COORDINATION_DIR', str(blocker))
    with pytest.raises((OSError, ValueError)):
        network.reserve_request('https://data.cdc.gov/resource/synthetic.json')
    monkeypatch.setenv('HEALTH_ATLAS_REFRESH_MODE', 'read-only')
    def forbidden(*args, **kwargs):
        pytest.fail('A read-only replica must not send requests')
    monkeypatch.setattr(network.requests, 'get', forbidden)
    with pytest.raises(ValueError, match='disabled'):
        network.get_json('https://api.census.gov/data/synthetic')
    with pytest.raises(ValueError, match='disabled'):
        api_cache._reserve_refresh(tmp_path / 'cache.duckdb')
    assert not (tmp_path / '.refresh.sqlite3').exists()


def test_stream_byte_cap_redirects_and_close(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        closed = False
        def __enter__(self): return self
        def __exit__(self, *args): self.closed = True
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield b'['; yield b'123456789]'
    response = Response()
    def get(url, **kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(network.requests, 'get', get)
    with pytest.raises(ValueError, match='byte budget'):
        network.get_json('https://data.cdc.gov/resource/synthetic.json', max_bytes=4)
    assert response.closed and calls[0]['stream'] and calls[0]['allow_redirects'] is False
    response.closed = False
    response.status_code = 302
    with pytest.raises(ValueError, match='complete response'):
        network.get_json('https://data.cdc.gov/resource/synthetic.json')
    assert response.closed


@pytest.mark.parametrize('kind', ['county', 'acs'])
def test_failed_publication_keeps_snapshot_and_cleans_temp(tmp_path, monkeypatch, kind):
    path = tmp_path / 'snapshot.duckdb'
    def publish():
        if kind == 'county':
            data.store(data.demo_rows(), 'Synthetic release', path)
        else:
            census.store([('41051', 100, 10, 20, 2, 10, 1)], 2023, path)
    publish()
    before = path.read_bytes()
    def fail(*args): raise OSError('Synthetic disk failure')
    monkeypatch.setattr(storage.os, 'replace', fail)
    with pytest.raises(OSError): publish()
    assert path.read_bytes() == before
    assert list(tmp_path.glob('*.duckdb')) == [path]
    assert not list(tmp_path.glob('*.wal'))


def test_reader_retains_old_snapshot_during_other_process_publication(tmp_path):
    path = tmp_path / 'snapshot.duckdb'
    data.store(data.demo_rows(), 'Old synthetic release', path)
    with duckdb.connect(str(path), read_only=True) as reader:
        script = '''
import sys
from oregon_health import data
data.store(data.demo_rows(), 'New synthetic release', sys.argv[1])
'''
        subprocess.run([sys.executable, '-c', script, str(path)], check=True, timeout=20)
        assert reader.execute('SELECT source FROM provenance').fetchone()[0] == 'Old synthetic release'
    with duckdb.connect(str(path), read_only=True) as reader:
        assert reader.execute('SELECT source FROM provenance').fetchone()[0] == 'New synthetic release'
        assert reader.execute('SELECT count(*) FROM estimates').fetchone()[0] == 27


def test_overlapping_publishers_are_rejected_without_partial_snapshot(tmp_path):
    path = tmp_path / 'snapshot.duckdb'
    data.store(data.demo_rows(), 'Old synthetic release', path)
    before = path.read_bytes()
    with storage.snapshot(path) as pending:
        script = '''
import sys
from oregon_health import data
try:
    data.store(data.demo_rows(), 'Conflicting release', sys.argv[1])
except ValueError:
    raise SystemExit(0)
raise SystemExit(1)
'''
        subprocess.run([sys.executable, '-c', script, str(path)], check=True, timeout=20)
        assert path.read_bytes() == before
        pending.write_bytes(before)
    assert path.read_bytes() == before


def test_aborted_build_preserves_old_snapshot(tmp_path):
    path = tmp_path / 'snapshot.duckdb'
    data.store(data.demo_rows(), 'Old synthetic release', path)
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        with storage.snapshot(path) as pending:
            pending.write_bytes(b'incomplete synthetic snapshot')
            raise RuntimeError('Synthetic interruption')
    assert path.read_bytes() == before
    assert list(tmp_path.glob('*.duckdb')) == [path]
