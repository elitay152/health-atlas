"""Reproduce the pinned Census artifact; build-only pyshp and pyproj required.

UV_CACHE_DIR=/tmp/uv-cache uv run --with pyshp==3.1.4 --with pyproj==3.7.2 \
    python scripts/prepare_tract_boundaries.py /path/to/cb_2023_41_tract_500k.zip
Download the official ZIP separately; this script makes no network requests.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pyproj
import shapefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oregon_health.tract_boundaries import validate  # noqa: E402
from oregon_health.regions import OREGON, get_region

SOURCE = 'https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_41_tract_500k.zip'
SHA256 = '366d526c6d2cbb6f11c53048cc1ed3f99ec227f029e9c1f8f3f64cd6996e7a34'
STEM = 'cb_2023_41_tract_500k'


def prepare(archive, output,*,region=OREGON):
    region=get_region(region)
    stem=f'cb_2023_{region.state_fips}_tract_500k'
    source=f'https://www2.census.gov/geo/tiger/GENZ2023/shp/{stem}.zip'
    digest=SHA256 if region==OREGON else '704608592dda0964bcecd2381752325c9ce18095bc046d2269536fd33d49452f'
    raw = Path(archive).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Census ZIP differs from the researched source; review a new revision explicitly')
    transformer = pyproj.Transformer.from_crs('EPSG:4269', 'EPSG:4326', always_xy=True, allow_ballpark=False)
    def ring_points(ring, exterior):
        points = [[round(x, 6), round(y, 6)] for x, y in
                  (transformer.transform(*point, errcheck=True) for point in ring)]
        signed = sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(points, points[1:]))
        if (signed > 0) != exterior:
            points.reverse()
        return points
    features = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        def part(extension):
            return io.BytesIO(z.read(f'{stem}.{extension}'))
        source_crs = pyproj.CRS.from_wkt(part('prj').read().decode())
        if not source_crs.equals(pyproj.CRS.from_epsg(4269), ignore_axis_order=True):
            raise ValueError('Unexpected source coordinate reference system')
        reader = shapefile.Reader(shp=part('shp'), shx=part('shx'), dbf=part('dbf'))
        for sr in reader.iterShapeRecords():
            record = sr.record.as_dict()
            geoid = record['GEOID']
            if geoid != record['STATEFP'] + record['COUNTYFP'] + record['TRACTCE']:
                raise ValueError('Inconsistent Census source identifier')
            geo = sr.shape.__geo_interface__
            polys = [geo['coordinates']] if geo['type'] == 'Polygon' else geo['coordinates']
            converted = [[ring_points(ring, i == 0) for i, ring in enumerate(poly)] for poly in polys]
            features.append(dict(type='Feature', id=geoid,
                                 properties=dict(geoid=geoid, county_fips=geoid[:5], name=record['NAMELSAD']),
                                 geometry=dict(type=geo['type'], coordinates=converted[0] if geo['type'] == 'Polygon' else converted)))
    collection = validate(dict(type='FeatureCollection', features=sorted(features, key=lambda f: f['id'])),region=region)
    encoded = (json.dumps(collection, separators=(',', ':'), allow_nan=False) + '\n').encode()
    manifest = dict(state=region.abbr,state_fips=region.state_fips,source_url=source, source_sha256=digest, source_bytes=len(raw),
                    source_retrieved_at='2026-10-22' if region==OREGON else '2026-10-24', prepared_at=datetime.now(timezone.utc).isoformat(),
                    boundary_year=2023, scale='1:500,000', source_crs='EPSG:4269', output_crs='EPSG:4326',
                    axis_order='longitude,latitude', coordinate_decimals=6,
                    transformation='pyproj Transformer.from_crs, always_xy=True, allow_ballpark=False; no additional simplification',
                    pyshp_version=shapefile.__version__, pyproj_version=pyproj.__version__, proj_version=pyproj.proj_version_str,
                    features=len(features), geojson_bytes=len(encoded), geojson_sha256=hashlib.sha256(encoded).hexdigest(),
                    attribution='U.S. Census Bureau',
                    reuse='Free to use in a product or publication with U.S. Census Bureau acknowledgement.',
                    constraints='Small-scale visual display only, no larger than 1:500,000. Not for geographic analysis, area/perimeter calculations, geocoding or precise geographic relationships.')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / f'{region.name.lower()}-tracts-2023.geojson').write_bytes(encoded)
    (output / f'{region.name.lower()}-tracts-2023.provenance.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'assets')
    parser.add_argument('--region',choices=['OR','WA'],default='OR')
    args = parser.parse_args()
    prepare(args.archive, args.output,region=args.region)
