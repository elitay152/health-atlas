"""Measure isolated OR/WA county+ACS snapshots; live downloads require Census key.

Run: uv run python scripts/benchmark_regions.py
No production cache is changed. Output contains measurements, never credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

# Direct script execution should work without installing the application package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def worker(code):
    from oregon_health import api_cache, census, data, network, regions
    region = regions.get_region(code)
    requests = []
    reserve = network.reserve_request
    def counted(url):
        reserve(url)
        requests.append(1)
    network.reserve_request = counted
    with tempfile.TemporaryDirectory(prefix='health-atlas-benchmark-') as directory:
        root = Path(directory)
        path = regions.health_path(region, root)
        acs_path = regions.cache_dir(region, root) / 'census_2023.duckdb'
        started = time.perf_counter()
        rows = api_cache.refresh(api_cache.DEFAULT_DATASET, path, region=region)
        context = census.context(next(iter(region.counties())), 2023, acs_path.parent, region=region)
        cold = time.perf_counter() - started
        cold_requests = len(requests)
        started = time.perf_counter()
        assert not api_cache.needs_refresh(api_cache.DEFAULT_DATASET, path, region=region)
        import duckdb
        with duckdb.connect(str(path), read_only=True) as db:
            assert len(data.comparison(db, 'DIABETES', 2023, region=region)) == region.county_count
        assert census.context(next(iter(region.counties())), 2023, acs_path.parent, region=region) == context
        warm = time.perf_counter() - started
        warm_requests = len(requests) - cold_requests
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        # Force a due attempt without waiting five minutes; fail after reservation.
        real_time, real_fetch = api_cache.time.time, api_cache.fetch
        def offline(*args, **kwargs):
            raise ValueError('Synthetic upstream interruption')
        try:
            api_cache.time.time = lambda: real_time() + 301
            api_cache.fetch = offline
            try:
                api_cache.refresh(api_cache.DEFAULT_DATASET, path, region=region)
            except ValueError:
                pass
            else:
                raise AssertionError('Injected failure unexpectedly succeeded')
        finally:
            api_cache.time.time, api_cache.fetch = real_time, real_fetch
        preserved = hashlib.sha256(path.read_bytes()).hexdigest() == before
        result = dict(region=code, counties=region.county_count, health_rows=rows,
                      cold_seconds=round(cold, 3), warm_seconds=round(warm, 3),
                      cold_requests=cold_requests, warm_requests=warm_requests,
                      county_snapshot_bytes=path.stat().st_size,
                      acs_snapshot_bytes=acs_path.stat().st_size,
                      peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                      failed_refresh_preserved_snapshot=preserved)
        result['within_budget'] = (cold < 120 and warm < 2 and warm_requests == 0
                                  and result['peak_rss_mib'] < 512
                                  and path.stat().st_size + acs_path.stat().st_size < 10_000_000
                                  and preserved)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=['OR', 'WA'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            print(json.dumps(worker(args.worker)))
        except Exception as error:
            # Requests/Census exceptions can contain credential-bearing URLs.
            print(json.dumps(dict(region=args.worker, error_type=type(error).__name__)))
            return 1
        return 0
    results = []
    for code in ['OR', 'WA']:
        process = subprocess.run([sys.executable, __file__, '--worker', code],
                                 capture_output=True, text=True, timeout=180)
        if process.returncode:
            print(f'{code} benchmark failed; check API access/key and request budget.', file=sys.stderr)
            return 1
        results.append(json.loads(process.stdout))
    print(json.dumps(dict(platform=sys.platform, results=results), indent=2))
    return 0 if all(r['within_budget'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
