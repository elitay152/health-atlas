"""Explicit supported-region contracts; no global environment switch or fallback."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA_ROOT=ROOT/'data'


@dataclass(frozen=True)
class Region:
    abbr: str
    name: str
    state_fips: str
    county_count: int
    boundary_file: str
    center: tuple
    bounds: tuple  # west, south, east, north
    zoom: float
    supported_sources: tuple
    deployed: bool = False

    @property
    def layer(self):
        return self.name.lower()+'-counties'

    def require(self,source):
        if source not in self.supported_sources:
            raise ValueError(f'{source} is not prepared for {self.name}')

    def counties(self):
        return dict(sorted(((f['properties']['fips'],f['properties']['name'])
                            for f in boundary_collection(self)['features']),key=lambda r:r[1]))


OREGON=Region('OR','Oregon','41',36,'oregon-counties.geojson',(-120.55,44.15),(-125,41,-116,47),4.8,
              ('county_health','census','tract_health','tract_boundaries','hospitals','hospital_locations','hrsa','service_areas'),True)
WASHINGTON=Region('WA','Washington','53',39,'washington-counties.geojson',(-120.8,47.4),(-125,45,-116,50),5.2,
                  ('county_health','census','hospitals','hospital_locations'), True)
REGIONS={'OR':OREGON,'WA':WASHINGTON}


def get_region(value=OREGON):
    if isinstance(value,Region):
        if REGIONS.get(value.abbr)!=value:
            raise ValueError('Unsupported region configuration')
        return value
    if not isinstance(value,str) or value.upper() not in REGIONS:
        raise ValueError('Supported region codes are OR and WA')
    return REGIONS[value.upper()]


@lru_cache(maxsize=2)
def boundary_collection(region=OREGON):
    region=get_region(region)
    collection=json.loads((ROOT/'assets'/region.boundary_file).read_text())
    features=collection['features']
    ids=[f['properties']['fips'] for f in features]
    if (len(features)!=region.county_count or len(set(ids))!=len(ids)
            or any(not isinstance(i,str) or len(i)!=5 or not i.isdigit() or not i.startswith(region.state_fips) for i in ids)):
        raise ValueError('Boundary identifiers differ from the region contract')
    west,south,east,north=region.bounds
    for f in features:
        g=f['geometry']
        if g['type'] not in {'Polygon','MultiPolygon'}:
            raise ValueError('County polygon required')
        for poly in [g['coordinates']] if g['type']=='Polygon' else g['coordinates']:
            for ring in poly:
                if len(ring)<4 or ring[0]!=ring[-1] or any(not(west<=x<=east and south<=y<=north) for x,y in ring):
                    raise ValueError('County geometry differs from region bounds')
    return collection


def cache_dir(region=OREGON,root=DATA_ROOT):
    region=get_region(region)
    return Path(root) if region==OREGON else Path(root)/'regions'/region.abbr.lower()


def health_path(region=OREGON,root=DATA_ROOT):
    region=get_region(region)
    return cache_dir(region,root)/('oregon.duckdb' if region==OREGON else 'counties.duckdb')


def check_identity(db,region=OREGON):
    """Legacy provenance without state identity is Oregon only, never inferred."""
    region=get_region(region)
    columns={r[1] for r in db.execute("PRAGMA table_info('provenance')").fetchall()}
    identity=db.execute('SELECT state_fips FROM provenance').fetchone()[0] if 'state_fips' in columns else '41'
    if identity!=region.state_fips:
        raise ValueError('Snapshot belongs to another region; choose a separate cache path')


def main():
    import argparse
    from dataclasses import asdict
    parser=argparse.ArgumentParser(description='Inspect prepared region configuration without source requests')
    parser.add_argument('--region',choices=REGIONS,default='OR')
    args=parser.parse_args()
    region=get_region(args.region)
    result=asdict(region)
    result.update(county_fips=sorted(region.counties()),health_cache=str(health_path(region)),cache_directory=str(cache_dir(region)))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
