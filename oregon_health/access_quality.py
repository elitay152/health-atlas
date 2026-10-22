"""Read-only snapshot audits. Run: python -m oregon_health.access_quality.

No source requests, refresh reservations, directory creation or snapshot writes.
Counts describe loaded source records, never true facility supply or demand.
"""
from collections import Counter
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path

import duckdb

from oregon_health import geocoding, hospitals, hrsa
from oregon_health.profile import county_names
from oregon_health.status import last_refresh


MATCH_LABELS={
    'matched_exact':'Exact address response', 'unmatched':'No address match', 'non_exact':'Non-exact match withheld',
    'ambiguous':'Ambiguous match', 'missing_address':'Incomplete source address', 'address_changed':'Address changed',
    'not_geocoded':'Not geocoded', 'invalid_coordinates':'Invalid coordinates withheld',
    'county_mismatch':'County check failed', 'county_unresolved':'Source county unresolved',
    'missing_match_address':'Matched address missing',
}


def _rows(db, sql):
    cursor=db.execute(sql)
    return [dict(zip([c[0] for c in cursor.description],row)) for row in cursor.fetchall()]


def _read(path, kind):
    with duckdb.connect(str(path),read_only=True) as db:
        if kind=='CMS hospitals':
            rows=_rows(db,'SELECT facility_id,name,address,city,state,zip_code,county,county_fips,hospital_type,ownership,emergency_services FROM hospitals')
            provenance=_rows(db,'SELECT released,modified,fetched_at FROM provenance')[0]
        elif kind=='Address matches':
            rows=_rows(db,'SELECT facility_id,address_hash,location_status,longitude,latitude,matched_address FROM locations')
            provenance=_rows(db,'SELECT benchmark,benchmark_id,fetched_at,fingerprint FROM provenance')[0]
        else:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM features').fetchall()]
            provenance=json.loads(db.execute('SELECT payload FROM provenance').fetchone()[0])
            required={'feature_key','designation_id','discipline','category','county_fips','geometry','source_date','status'}
            if any(not isinstance(r,dict) or not required.issubset(r) for r in rows):
                raise ValueError('Unsupported HRSA snapshot schema')
            for r in rows:
                if (not all(isinstance(r[k],str) for k in ('feature_key','designation_id','discipline','category','source_date','status'))
                        or r['county_fips'] is not None and not isinstance(r['county_fips'],str)):
                    raise ValueError('Unsupported HRSA snapshot values')
                date.fromisoformat(r['source_date'])
    return rows,provenance


def _utc(value):
    if not isinstance(value,datetime):
        value=datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _source(kind,path,ttl,identity,now):
    event=last_refresh(path,identity)
    result=dict(source=kind,state='Not loaded',rows=None,provenance=None,event=event,
                fetched_at=None,age_days=None,freshness='Not evaluated',retained=False)
    if not path.exists():
        return result
    try:
        rows,provenance=_read(path,kind)
        result.update(state='Loaded' if rows else 'Loaded empty snapshot',rows=rows,provenance=provenance)
        try:
            fetched=_utc(provenance['fetched_at'])
            result.update(fetched_at=fetched.isoformat(),age_days=round((now-fetched).total_seconds()/86400,2),
                          freshness='Fetch time in future' if fetched>now else 'Refresh due' if now-fetched>=ttl else 'Within refresh window',
                          retained=bool(event and event['outcome']=='failed' and _utc(event['checked_at'])>=fetched))
        except (ValueError,TypeError,KeyError):
            result['freshness']='Fetch time unavailable or invalid'
    except (duckdb.Error,OSError,ValueError,KeyError,TypeError,IndexError):
        result.update(state='Unreadable snapshot',rows=None,provenance=None)
    return result


def _duplicates(rows,key):
    return sum(n-1 for n in Counter(r[key] for r in rows).values() if n>1)


def _coordinates(row):
    lon,lat=row['longitude'],row['latitude']
    if lon is None or lat is None:
        return 'missing'
    if (type(lon) not in (int,float) or type(lat) not in (int,float) or not math.isfinite(lon)
            or not math.isfinite(lat) or not -124.8<=lon<=-116.3 or not 41.9<=lat<=46.4):
        return 'invalid'
    return 'valid'


def audit(cache_dir=None,now=None):
    """Return summaries, checks, controlled findings and all-county record coverage."""
    root=Path(cache_dir) if cache_dir is not None else hospitals.DB_PATH.parent
    now=_utc(now or datetime.now(timezone.utc))
    specs=[('CMS hospitals','hospitals.duckdb',hospitals.CACHE_TTL,hospitals.DATASET),
           ('Address matches','hospital_locations.duckdb',geocoding.CACHE_TTL,hospitals.DATASET),
           ('HRSA designations','hrsa.duckdb',hrsa.CACHE_TTL,'hrsa-hpsa')]
    sources={name:_source(name,root/file,ttl,identity,now) for name,file,ttl,identity in specs}
    checks,findings=[],[]
    def check(source,label,count,state='Evaluated',note=''):
        checks.append(dict(Source=source,Check=label,Count=count,State=state,Meaning=note))
    def finding(source,record,label,detail=''):
        findings.append({'Source':source,'Record ID':str(record),'Check':label,'Detail':detail})
    for name,source in sources.items():
        if source['rows'] is None:
            check(name,'Snapshot records',None,'Not evaluated',source['state']+'; no counts or coverage inferred.')
            continue
        check(name,'Snapshot records',len(source['rows']),note='Records in this loaded snapshot, not true supply.')
    def duplicate_check(name,rows,key,label):
        check(name,label,_duplicates(rows,key),note='Loaded snapshot only; rejected refresh payloads are not retained.')
        for value,count in Counter(r[key] for r in rows).items():
            if count>1:finding(name,value,label,f'{count} snapshot rows share this key.')
    cms=sources['CMS hospitals']['rows']
    if cms is not None:
        duplicate_check('CMS hospitals',cms,'facility_id','Duplicate facility IDs')
        for label,predicate in [
            ('Unresolved county joins',lambda r:r['county_fips'] not in county_names()),
            ('Conflicting county-name joins',lambda r:r['county_fips'] in county_names() and r['county'] and r['county'].strip().casefold()!=county_names()[r['county_fips']].casefold()),
            ('Incomplete addresses',lambda r:not all(r[k] for k in ('address','city','zip_code'))),
            ('Emergency services not reported',lambda r:r['emergency_services'] is None),
        ]:
            selected=[r for r in cms if predicate(r)]
            check('CMS hospitals',label,len(selected))
            for r in selected:finding('CMS hospitals',r['facility_id'],label)
        check('CMS hospitals','Source coordinate fields',None,'Unsupported','CMS does not supply coordinates. Separate Census matches are audited below.')
    matches=sources['Address matches']['rows']
    if matches is not None:
        duplicate_check('Address matches',matches,'facility_id','Duplicate cached facility IDs')
        for label,predicate in [
            ('Accepted matches missing coordinates',lambda r:r['location_status']=='matched_exact' and _coordinates(r)=='missing'),
            ('Invalid or contradictory coordinates',lambda r:(_coordinates(r)=='invalid' if r['location_status']=='matched_exact'
                else r['longitude'] is not None or r['latitude'] is not None)),
        ]:
            selected=[r for r in matches if predicate(r)]
            check('Address matches',label,len(selected))
            for r in selected:finding('Address matches',r['facility_id'],label)
        if cms is None:
            check('Address matches','Current-address join',None,'Not evaluated','CMS snapshot unavailable; cached points are not assumed to match current hospitals.')
        else:
            by_id={r['facility_id']:r for r in matches}
            statuses=Counter()
            failed_county=0
            for r in cms:
                cached=by_id.get(r['facility_id'])
                status='not_geocoded' if cached is None else 'address_changed' if cached['address_hash']!=geocoding.address_hash(r) else cached['location_status']
                statuses[status]+=1
                if status!='matched_exact':finding('Address matches',r['facility_id'],'Not mapped',MATCH_LABELS.get(status,'Unrecognized match status'))
                elif _coordinates(cached)=='valid' and not geocoding.in_county(cached['longitude'],cached['latitude'],r['county_fips']):
                    failed_county+=1
                    finding('Address matches',r['facility_id'],'Accepted point fails current county check')
            for status,count in sorted(statuses.items()):check('Address matches','Current hospital match status: '+MATCH_LABELS.get(status,'Unrecognized status'),count,
                note='Current address-fingerprint join. Exact is an approximate address response; coordinate validity is checked separately.')
            check('Address matches','Accepted points failing current county check',failed_county)
            current_ids={r['facility_id'] for r in cms}
            orphaned=[r for r in matches if r['facility_id'] not in current_ids]
            check('Address matches','Cached IDs absent from current CMS snapshot',len(orphaned),
                  note='Not assumed to be closed facilities; these cached rows are outside the current directory.')
            for r in orphaned:finding('Address matches',r['facility_id'],'Cached ID absent from current CMS snapshot',
                                      'Outside the current directory; this does not establish closure.')
    elif cms is not None:
        check('Address matches','Current-address join',None,'Not evaluated','No match snapshot. Coordinates are not assumed to be missing source measurements or zero locations.')
    hpsa=sources['HRSA designations']['rows']
    if hpsa is not None:
        duplicate_check('HRSA designations',hpsa,'feature_key','Duplicate source feature keys')
        check('HRSA designations','Distinct discipline/designation pairs',len({(r['discipline'],r['designation_id']) for r in hpsa}),
              note='Repeated designation IDs across components are expected, not duplicate features.')
        missing,invalid,unresolved=[],[],[]
        for r in hpsa:
            if r['geometry'] is None:missing.append(r)
            else:
                try:hrsa._geometry(r['geometry'],r['category'])
                except (ValueError,TypeError,KeyError):invalid.append(r)
            if r['county_fips'] not in county_names():unresolved.append(r)
        for label,selected in [('Records without source geometry',missing),('Records with invalid geometry',invalid),('Unresolved county joins',unresolved)]:
            check('HRSA designations',label,len(selected))
            for r in selected:finding('HRSA designations',r['feature_key'],label,'HPSA ID: '+r['designation_id'])
        check('HRSA designations','Complete withdrawn history',None,'Unsupported','This GIS view is not a full historical inventory; CSV downloads also contain withdrawn history.')
    county=[]
    for fips,name in county_names().items():
        local=None if hpsa is None else [r for r in hpsa if r['county_fips']==fips]
        county.append({'County':name,'County FIPS':fips,
            'CMS-listed hospitals':None if cms is None else sum(r['county_fips']==fips for r in cms),
            'HRSA components / facility points':None if local is None else len(local),
            'HRSA distinct discipline/designation pairs':None if local is None else len({(r['discipline'],r['designation_id']) for r in local})})
    summary=[]
    for name,source in sources.items():
        p=source['provenance'] or {}
        period=(str(p.get('released','Not reported')) if name=='CMS hospitals' else p.get('benchmark','Not reported') if name=='Address matches'
                else ', '.join(sorted({r['source_date'] for r in source['rows']})) if source['rows'] else 'Not evaluated')
        summary.append({'Source':name,'Snapshot':source['state'],'Cached records':None if source['rows'] is None else len(source['rows']),
            'Successful fetch (UTC)':source['fetched_at'],'Days since fetch':source['age_days'],'Fetch freshness':source['freshness'],
            'Source release / warehouse date / benchmark':period,'Retained after failed refresh':source['retained'],
            'Last recorded refresh':source['event']['outcome'] if source['event'] else 'Not recorded'})
    return dict(summary=summary,checks=checks,findings=findings,counties=county)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=hospitals.DB_PATH.parent)
    args=parser.parse_args()
    print(json.dumps(audit(args.directory),indent=2,allow_nan=False))
