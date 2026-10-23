"""Verify official county source coverage and joins for Oregon/Washington.

Run: uv run python scripts/verify_regions.py > /tmp/region-verification.json
Uses configured Census key; never prints credentials or request URLs with keys.
Does not publish or modify application snapshots.
"""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oregon_health import api_cache, census, import_data, network, regions, verification


def main():
    dataset = api_cache.DEFAULT_DATASET
    metadata_url = f'https://data.cdc.gov/api/views/{dataset}.json'
    results = []
    try:
        for region in regions.REGIONS.values():
            before = network.get_json(metadata_url)
            if before.get('id') != dataset or not isinstance(before.get('rowsUpdatedAt'), int):
                raise ValueError('CDC metadata identity/revision is unavailable')
            rows, source = import_data.fetch(dataset, region=region)
            census_url = census.release_url(2023)
            # Validate variable definitions as in the application, then retain the
            # official NAME column for an independent county catalog/name join.
            metadata = census._get_json(census_url + '/variables.json')
            census.validate_variables(metadata['variables'])
            key = os.environ.get('CENSUS_API_KEY')
            if not key:
                raise census.CensusKeyRequired('Census key is required for live verification')
            payload = census._get_json(census_url, dict(get=','.join(['NAME', *census.FIELDS]),
                                       **{'for': 'county:*', 'in': f'state:{region.state_fips}', 'key': key}))
            after = network.get_json(metadata_url)
            if before['rowsUpdatedAt'] != after.get('rowsUpdatedAt'):
                raise ValueError('CDC revision changed during verification')
            report = verification.audit(region, rows, payload)
            report.update(cdc_dataset=dataset, cdc_title=before['name'],
                          cdc_source=source, cdc_source_revision=before['rowsUpdatedAt'],
                          census_source=census_url)
            results.append(report)
    except Exception as error:
        # Neither the error message nor its request-bearing chain is output.
        print(f'Region verification failed ({type(error).__name__}); check official source access/schema and key configuration.', file=sys.stderr)
        return 1
    print(json.dumps(dict(verified_at=datetime.now(timezone.utc).isoformat(), regions=results), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
