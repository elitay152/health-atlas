"""Reproducible, read-only area comparison; no differences, ranks or rate totals."""
import csv
import io
import json

from oregon_health import service_areas
from oregon_health.service_area_summary import summarize


def comparison(a_codes,b_codes,a_year,b_year,**kwargs):
    a=summarize(a_codes,a_year,**kwargs)
    b=summarize(b_codes,b_year,**kwargs)
    return assemble(a,b)


def assemble(a,b):
    for r in (a,b):
        service_areas.loads(json.dumps(r['area']).encode())
    loaded_a={s['source']:s['state']=='Loaded' for s in a['sources']}
    loaded_b={s['source']:s['state']=='Loaded' for s in b['sources']}
    shared=sorted(set(a['area']['health_atlas']['county_fips']) & set(b['area']['health_atlas']['county_fips']))
    cms=sorted(set(a['hospitals']['facility_ids']) & set(b['hospitals']['facility_ids']))
    points=sorted(set(a['hospitals']['accepted_location_ids']) & set(b['hospitals']['accepted_location_ids']))
    def designation_ids(r):
        return {(x['discipline'],i) for x in r['shortages'] for i in x['designation_ids']}
    hrsa=sorted(designation_ids(a) & designation_ids(b))
    if not (loaded_a['CMS hospitals'] and loaded_b['CMS hospitals']):
        cms=None
    if not all(loaded_a[k] and loaded_b[k] for k in ('CMS hospitals','Address matches')):
        points=None
    if not (loaded_a['HRSA designations'] and loaded_b['HRSA designations']):
        hrsa=None
    warnings=[]
    if a['estimate_year']!=b['estimate_year']:
        warnings.append('Estimate years and ACS periods differ. No change, difference, ranking or trend is calculated.')
    if shared:
        warnings.append('Areas share counties. Their populations and facility counts are not independent and must not be added as separate totals.')
    if hrsa:
        warnings.append('Some HPSA IDs occur in both areas; designations can span counties even when county sets are disjoint.')
    for label,r in [('Area A',a),('Area B',b)]:
        if any(s['state']!='Loaded' for s in r['sources']):
            warnings.append(f'{label} has unavailable or incompatible sources; missing counts are not zero.')
        if (r['population']['estimate'] is None or any(h['status']!='Available' for h in r['health'])
                or any(h['prevalence'] is not None and h['uncertainty']=='Interval unavailable' for h in r['health'])
                or any(d[k] is None for d in r['demographics'] for k in ('population','age65_pct','poverty_pct'))):
            warnings.append(f'{label} has incomplete health/demographic coverage; compare the available county rows, not inferred area-wide rates.')
    by_a={s['source']:s for s in a['sources']};by_b={s['source']:s for s in b['sources']}
    for key in by_a.keys() & by_b.keys():
        x,y=by_a[key],by_b[key]
        if x['state']=='Loaded' and y['state']=='Loaded' and x['provenance']!=y['provenance']:
            warnings.append(f'{key} source snapshots/periods differ between areas. Review source dates before comparing.')
    return dict(schema_version=1,areas={'Area A':a,'Area B':b},shared_counties=shared,
        shared_cms_ids=cms,shared_accepted_point_ids=points,
        shared_hrsa_designations=[dict(discipline=d,designation_id=i) for d,i in hrsa] if hrsa is not None else None,
        periods_match=a['estimate_year']==b['estimate_year'],warnings=sorted(warnings),
        interpretation='Source record counts are supply indicators, not verified capacity or utilization. Community health need is separate from commercial demand. No opportunity score or statistical comparison is calculated.')


def export_rows(report):
    rows=[]
    for label,r in report['areas'].items():
        sources={s['source']:s for s in r['sources']}
        base=dict(area=label,area_counties=','.join(r['area']['health_atlas']['county_fips']),
                  boundary_revision=r['area']['health_atlas']['boundary_source']['sha256'],
                  shared_counties=','.join(report['shared_counties']),periods_match=report['periods_match'],
                  shared_cms_ids=','.join(report['shared_cms_ids']) if report['shared_cms_ids'] is not None else 'Not evaluated',
                  shared_accepted_point_ids=','.join(report['shared_accepted_point_ids']) if report['shared_accepted_point_ids'] is not None else 'Not evaluated',
                  shared_hrsa_ids=json.dumps(report['shared_hrsa_designations'],sort_keys=True),
                  comparison_warnings=json.dumps(report['warnings']))
        def add(metric,value,unit,status,source,*,county=None,period_start=None,period_end=None,
                low=None,high=None,moe=None,confidence=None,detail=None):
            s=sources[source]
            rows.append(dict(base,metric=metric,value=value,unit=unit,status=status,county_fips=county,
                shared_county=county in report['shared_counties'] if county else None,
                period_start=period_start,period_end=period_end,low=low,high=high,margin_of_error=moe,
                confidence=confidence,definition=detail,source=source,source_state=s['state'],
                source_fetched_at=s['fetched_at'],freshness=s['freshness'],
                retained_after_failure=s['retained_after_failure'],
                source_provenance=json.dumps(s['provenance'],sort_keys=True),
                interpretation=report['interpretation']))
        for source,s in sources.items():
            add('Source snapshot records',s['records'],'statewide saved records',s['state'],source,
                detail='Coverage metadata; not a selected-area facility or population total',
                period_start=r['estimate_year'] if source=='CDC health' else r['estimate_year']-4 if source=='ACS context' else None,
                period_end=r['estimate_year'] if source in {'CDC health','ACS context'} else None)
        p=r['population']
        add('Area population',p['estimate'],'people',p['status'],'ACS context',
            period_start=r['estimate_year']-4,period_end=r['estimate_year'],moe=p['moe90'],confidence=90,detail=p['uncertainty'])
        for metric,key,source,unit in [('CMS-listed hospitals','listed','CMS hospitals','hospital records'),
            ('Accepted approximate hospital points','approximate_locations','Address matches','approximate address points')]:
            value=r['hospitals'][key]
            add(metric,value,unit,'Available source count' if value is not None else 'Not evaluated',source,
                detail='Distinct source IDs within selected counties; not capacity, utilization or demand')
        for h in r['health']:
            add(h['measure'],h['prevalence'],'percent',h['status'],'CDC health',county=h['county_fips'],
                period_start=h['year'],period_end=h['year'],low=h['low'],high=h['high'],confidence=95,
                detail=h['definition']+'; adults; crude prevalence; '+h['uncertainty'])
        for d in r['demographics']:
            for metric,key,moe,unit in [('County population','population','population_moe','people'),
                ('Residents age 65+','age65_pct','age65_moe','percent'),('Below poverty level','poverty_pct','poverty_moe','percent')]:
                value=d[key]
                add(metric,value,unit,'Available' if value is not None else 'Unavailable','ACS context',
                    county=d['county_fips'],period_start=d['period_start'],period_end=d['period_end'],
                    moe=d[moe],confidence=90,
                    detail='All residents' if key!='poverty_pct' else 'People for whom poverty status is determined')
        for h in r['shortages']:
            for metric,key,unit in [('HRSA designations','designations','distinct HPSA IDs'),('HRSA components','components','component/point records')]:
                add(metric,h[key],unit,'Available source count','HRSA designations',
                    detail=f"{h['discipline']}; {h['category']}; {h['status']}; not a whole-county eligibility finding")
    return rows


def export_csv(report):
    rows=export_rows(report)
    out=io.StringIO(newline='')
    writer=csv.DictWriter(out,fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        writer.writerow({k:("'"+v if isinstance(v,str) and (v.lstrip().startswith(('=','+','-','@'))
            or v.startswith(('\t','\r','\n'))) else v) for k,v in row.items()})
    return out.getvalue()


def link_params(codes,year):
    p=service_areas.link_params(codes)
    return dict(area_compare='1',area_b=p['area'],area_b_rev=p['area_rev'],area_b_year=str(year))
