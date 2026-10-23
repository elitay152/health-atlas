"""Read-only, county-membership summaries; no network calls or rate aggregation."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import re

import duckdb

from oregon_health import census, geocoding, hospitals, hrsa, service_areas
from oregon_health.data import DB_PATH, MEASURES
from oregon_health.profile import county_names
from oregon_health.status import last_refresh


def _utc(value):
    if not isinstance(value,datetime):
        value=datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _number(value, percent=False, integer=False):
    if value is None:
        return None
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid saved number')
    if percent and value>100 or integer and int(value)!=value:
        raise ValueError('Invalid saved estimate')
    return value


def _rows(db,sql,params=None):
    c=db.execute(sql,params or [])
    return [dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]


def _read(kind,path,year):
    with duckdb.connect(str(path),read_only=True) as db:
        if kind=='CDC health':
            rows=_rows(db,'SELECT * FROM estimates WHERE year = ?',[year])
            p=_rows(db,'SELECT source, imported_at FROM provenance')[0]
            p['fetched_at']=p.pop('imported_at')
            keys=set()
            for r in rows:
                key=(r['fips'],r['measure'])
                if r['fips'] not in county_names() or key in keys:
                    raise ValueError('Duplicate or invalid health geography')
                keys.add(key)
                v,l,h=[_number(r[k],percent=True) for k in ('prevalence','low','high')]
                if (l is not None and h is not None and l>h or
                    v is not None and (l is not None and l>v or h is not None and h<v)):
                    raise ValueError('Invalid saved interval')
        elif kind=='ACS context':
            rows=_rows(db,'SELECT * FROM demographics')
            p=_rows(db,'SELECT * FROM provenance')[0]
            if p['year']!=year or p['source']!=census.release_url(year):
                raise ValueError('ACS release mismatch')
            p['fetched_at']=p.pop('imported_at')
            keys=set()
            for r in rows:
                if r['fips'] not in county_names() or r['fips'] in keys:
                    raise ValueError('Duplicate or invalid ACS geography')
                keys.add(r['fips'])
                for k in ('population','population_moe'):
                    _number(r[k],integer=True)
                for k in ('age65_pct','age65_moe','poverty_pct','poverty_moe'):
                    _number(r[k],percent=True)
        elif kind=='CMS hospitals':
            rows=_rows(db,'SELECT * FROM hospitals')
            p=_rows(db,'SELECT * FROM provenance')[0]
            if p['dataset']!=hospitals.DATASET:
                raise ValueError('CMS source mismatch')
            for r in rows:
                mapped={name.upper():code for code,name in county_names().items()}
                if (not re.fullmatch('[A-Z0-9]{6}',r['facility_id']) or r['state']!='OR'
                        or r['county_fips'] is not None and mapped.get((r['county'] or '').upper())!=r['county_fips']):
                    raise ValueError('Invalid CMS identity')
        elif kind=='Address matches':
            rows=_rows(db,'SELECT * FROM locations')
            p=_rows(db,'SELECT * FROM provenance')[0]
            if p['benchmark']!=geocoding.BENCHMARK or p['source_url']!=geocoding.SOURCE_URL:
                raise ValueError('Address benchmark/source mismatch')
        else:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM features').fetchall()]
            p=json.loads(db.execute('SELECT payload FROM provenance').fetchone()[0])
            for r in rows:
                if (not re.fullmatch('[A-Za-z0-9]{10}',r['designation_id'])
                        or r['discipline'] not in hrsa.DISCIPLINES or r['status'] not in hrsa.STATUSES.values()
                        or r['category'] not in {'Geographic','Population group','Facility'}):
                    raise ValueError('Invalid HRSA record')
                datetime.fromisoformat(r['source_date'])
                hrsa._geometry(r['geometry'],r['category'])
            p['warehouse_dates']=sorted({r['source_date'] for r in rows})
        # Identical rows can be deduplicated; conflicting identities invalidate a source.
        key={'CDC health':None,'ACS context':None,'CMS hospitals':'facility_id',
             'Address matches':'facility_id','HRSA designations':'feature_key'}[kind]
        if key:
            unique={}
            for r in rows:
                if r[key] in unique and r!=unique[r[key]]:
                    raise ValueError('Conflicting saved source identity')
                unique[r[key]]=r
            rows=list(unique.values())
    return rows,p


def _load(kind,path,year,now,ttl,identity):
    source=dict(source=kind,state='Not loaded',records=None,fetched_at=None,freshness='Not evaluated',
                retained_after_failure=False,provenance=None,unresolved_counties=None)
    if not path.exists():
        return [],source
    try:
        rows,p=_read(kind,path,year)
        fetched=_utc(p['fetched_at'])
        event=last_refresh(path,identity)
        source.update(state='Loaded',records=len(rows),fetched_at=fetched.isoformat(),
            freshness='Refresh due' if now-fetched>=ttl else 'Within refresh window',
            retained_after_failure=bool(event and event['outcome']=='failed' and _utc(event['checked_at'])>=fetched),
            provenance={k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in p.items()})
        if kind in {'CMS hospitals','HRSA designations'}:
            source['unresolved_counties']=sum(r.get('county_fips') not in county_names() for r in rows)
        return rows,source
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,OSError,duckdb.Error):
        source['state']='Unreadable / incompatible'
        return [],source


def summarize(codes,year,*,cache_dir=None,health_path=None,now=None):
    codes=service_areas.normalize(codes)
    if not codes:
        raise ValueError('Select at least one whole county')
    if type(year) is not int or not 2000<=year<=2100:
        raise ValueError('Invalid summary estimate year')
    root=Path(cache_dir) if cache_dir is not None else DB_PATH.parent
    now=_utc(now or datetime.now(timezone.utc))
    specs=[('CDC health',Path(health_path) if health_path else root/'oregon.duckdb',timedelta(days=1),'swc5-untb'),
           ('ACS context',root/f'census_{year}.duckdb',census.CACHE_TTL,year),
           ('CMS hospitals',root/'hospitals.duckdb',hospitals.CACHE_TTL,hospitals.DATASET),
           ('Address matches',root/'hospital_locations.duckdb',geocoding.CACHE_TTL,hospitals.DATASET),
           ('HRSA designations',root/'hrsa.duckdb',hrsa.CACHE_TTL,'invalid-source')]
    saved={};sources=[]
    for kind,path,ttl,identity in specs:
        saved[kind],source=_load(kind,path,year,now,ttl,identity)
        sources.append(source)
    loaded={s['source']:s['state']=='Loaded' for s in sources}
    names=county_names()
    h={(r['fips'],r['measure']):r for r in saved['CDC health']}
    health=[]
    for code in codes:
        for measure,label in MEASURES.items():
            row=h.get((code,measure),{})
            health.append(dict(county_fips=code,county=names[code],measure=measure,definition=label,year=year,unit='percent',confidence=95,
                population_universe='Adults',estimate_type='Crude prevalence',
                prevalence=row.get('prevalence'),low=row.get('low'),high=row.get('high'),
                uncertainty='95% interval' if row.get('low') is not None and row.get('high') is not None else 'Interval unavailable',
                status='Source unavailable' if not loaded['CDC health'] else
                    'No saved estimate' if row.get('prevalence') is None else 'Available'))
    d={r['fips']:r for r in saved['ACS context']}
    demographics=[dict(county_fips=code,county=names[code],period=f'{year-4}–{year}',
        period_start=year-4,period_end=year,moe_confidence=90,
        **{k:d.get(code,{}).get(k) for k in census.COLUMNS}) for code in codes]
    complete=loaded['ACS context'] and all(r['population'] is not None for r in demographics)
    population=sum(r['population'] for r in demographics) if complete else None
    population_moe=demographics[0]['population_moe'] if complete and len(codes)==1 else None
    all_hospitals=saved['CMS hospitals']
    selected=[r for r in all_hospitals if r['county_fips'] in codes]
    locations={r['facility_id']:r for r in saved['Address matches']}
    accepted=[]
    if loaded['CMS hospitals'] and loaded['Address matches']:
        for r in selected:
            p=locations.get(r['facility_id'],{})
            lon,lat=p.get('longitude'),p.get('latitude')
            if (p.get('location_status')=='matched_exact' and p.get('address_hash')==geocoding.address_hash(r)
                    and p.get('matched_address') and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (lon,lat))
                    and -124.8<=lon<=-116.3 and 41.9<=lat<=46.4 and geocoding.in_county(lon,lat,r['county_fips'])):
                accepted.append(r['facility_id'])
    groups=defaultdict(list)
    for r in saved['HRSA designations']:
        if r['county_fips'] in codes:
            groups[(r['discipline'],r['category'],r['status'])].append(r)
    shortages=[dict(discipline=k[0],category=k[1],status=k[2],
        designations=len({r['designation_id'] for r in rows}),
        designation_ids=sorted({r['designation_id'] for r in rows}),components=len(rows),
        without_geometry=sum(r['geometry'] is None for r in rows)) for k,rows in sorted(groups.items())]
    return dict(area=service_areas.definition(codes),estimate_year=year,summary_as_of=now.isoformat(),sources=sources,
        health=health,demographics=demographics,
        population=dict(estimate=population,moe90=population_moe,covered_counties=sum(r['population'] is not None for r in demographics),
                        selected_counties=len(codes),status='Complete selected-county sum' if complete else 'Withheld: compatible population estimates missing',
                        uncertainty='Population estimate unavailable' if not complete else
                            'Source county MOE' if len(codes)==1 else 'Combined MOE unavailable; covariance is not supplied'),
        hospitals=dict(listed=len(selected) if loaded['CMS hospitals'] else None,
                       approximate_locations=len(accepted) if loaded['CMS hospitals'] and loaded['Address matches'] else None,
                       without_accepted_location=len(selected)-len(accepted) if loaded['CMS hospitals'] and loaded['Address matches'] else None,
                       facility_ids=sorted(r['facility_id'] for r in selected),accepted_location_ids=sorted(accepted)),
        shortages=shortages,
        unsupported_aggregations=['Health prevalence total/average','Combined health confidence interval',
                                  'Area age-65 percentage','Area poverty percentage'],
        rules='Whole-county membership; distinct IDs within each source. No cross-source facility matching or geometric intersection.')
