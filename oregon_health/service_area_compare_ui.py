"""Accessible two-area comparison of saved data and explicit overlaps."""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from oregon_health import service_areas, service_area_compare
from oregon_health.data import MEASURES
from oregon_health.profile import county_names


def health_chart(rows):
    frame=pd.DataFrame([r for r in rows if r['status']=='Available'])
    if frame.empty:
        return None
    base=alt.Chart(frame).encode(y=alt.Y('county:N',title=None),tooltip=[
        alt.Tooltip('county:N'),alt.Tooltip('year:O'),alt.Tooltip('prevalence:Q',format='.1f'),
        alt.Tooltip('low:Q',format='.1f'),alt.Tooltip('high:Q',format='.1f')])
    limits=base.mark_rule(color='#176B63').encode(
        x=alt.X('low:Q',title='Adult crude prevalence (%)',scale=alt.Scale(domain=[0,100])),x2='high:Q')
    points=base.mark_point(filled=True,color='#B86A40',size=80).encode(
        x=alt.X('prevalence:Q',scale=alt.Scale(domain=[0,100])))
    return (limits+points).properties(height=max(160,len(frame)*28))


def render_comparison(codes,year,health_path=None):
    if 'compare_areas' not in st.session_state:
        st.session_state['compare_areas']=st.query_params.get('area_compare')=='1'
    enabled=st.checkbox('Compare with a second service area',key='compare_areas')
    if not enabled:
        return {}
    st.markdown('### Compare service areas')
    st.caption('Area A is the primary county selection above. Area B is an independent whole-county set. This view only reads saved snapshots.')
    if 'area_b_counties' not in st.session_state:
        try:
            st.session_state['area_b_counties']=service_areas.parse_link(st.query_params.get('area_b'),st.query_params.get('area_b_rev')) or []
        except ValueError:
            st.session_state['area_b_counties']=[]
            st.warning('The Area B link has invalid county IDs or an incompatible boundary revision. No Area B was loaded.')
    with st.expander('Load saved Area B'):
        upload=st.file_uploader('Saved Area B definition',type=['json','geojson'],max_upload_size=1,key='area_b_upload')
        if st.button('Load Area B definition',disabled=upload is None,key='area_b_load'):
            try:
                st.session_state['area_b_counties']=service_areas.loads(upload.getvalue())
                st.success('Saved Area B loaded.')
            except ValueError:
                st.warning('That file is incompatible with the current county boundary revision. Area B was retained.')
    names=county_names()
    b=st.multiselect('Area B counties',sorted(names,key=names.get),key='area_b_counties',
        format_func=lambda c:f'{names[c]} County · {c}')
    years=sorted({year,*range(2015,2025)},reverse=True)
    if 'area_b_year' not in st.session_state:
        requested=st.query_params.get('area_b_year')
        desired=int(requested) if requested and requested.isdigit() and len(requested)==4 else year
        st.session_state['area_b_year']=desired if desired in years else year
        if requested and (str(desired)!=requested or desired not in years):
            st.warning('The linked Area B year is unsupported; showing the selected year below.')
    if st.session_state['area_b_year'] not in years:
        st.session_state['area_b_year']=year
    b_year=st.selectbox('Area B estimate year',years,key='area_b_year')
    params=service_area_compare.link_params(b,b_year)
    if not codes or not b:
        st.info('Select at least one county in both Area A and Area B to compare. Empty selections are preserved in links.')
        return params
    report=service_area_compare.comparison(codes,b,year,b_year,
        cache_dir=Path(health_path).parent if health_path else None,health_path=health_path)
    for warning in report['warnings']:
        st.warning(warning)
    for col,(label,r) in zip(st.columns(2),report['areas'].items()):
        with col:
            st.markdown(f'**{label} · {r["estimate_year"]}**')
            st.write(', '.join(names[c] for c in r['area']['health_atlas']['county_fips']))
            st.caption(f'ACS {r["estimate_year"]-4}–{r["estimate_year"]} · {len(r["area"]["health_atlas"]["county_fips"])} whole counties')
    shared=', '.join(names[c] for c in report['shared_counties']) or 'None'
    def count(key):
        return len(report[key]) if report[key] is not None else 'Not evaluated'
    st.info(f'Shared counties: {shared}. Shared CMS hospital IDs: {count("shared_cms_ids")}. Shared accepted point IDs: {count("shared_accepted_point_ids")}. Shared discipline/HPSA IDs: {count("shared_hrsa_designations")}.')
    metrics=[]
    for label,unit,field in [('ACS population','people','population'),('CMS-listed hospitals','hospital records','listed'),
                             ('Accepted approximate hospital points','approximate points','approximate_locations')]:
        row={'Metric':label,'Unit':unit}
        for area,r in report['areas'].items():
            value=r['population']['estimate'] if field=='population' else r['hospitals'][field]
            row[area]='Unavailable / not evaluated' if value is None else f'{value:,.0f}'
        metrics.append(row)
    st.dataframe(pd.DataFrame(metrics),hide_index=True)
    st.caption('Source record counts are supply indicators, not verified capacity, utilization, unmet need or commercial demand. No differences, combined area totals, rate averages, significance tests or opportunity scores are calculated. Multi-county population MOEs remain unavailable.')
    measure=st.selectbox('Comparison health condition',list(MEASURES),format_func=MEASURES.get,key='area_compare_measure')
    for col,(label,r) in zip(st.columns(2),report['areas'].items()):
        with col:
            st.markdown(f'**{label} · {MEASURES[measure]} · {r["estimate_year"]}**')
            chart=health_chart([h for h in r['health'] if h['measure']==measure])
            if chart is None:
                st.info('No available county estimates for this condition/year.')
            else:
                st.altair_chart(chart,width='stretch')
    st.caption('Both charts use the same 0–100% scale. Dots: county estimates; lines: 95% intervals where available. Missing intervals do not mean zero uncertainty. Shared counties appear in both charts; unlike years do not establish a trend.')
    with st.expander('HRSA designation groups'):
        groups=[dict(area=area,**{k:v for k,v in h.items() if k!='designation_ids'})
                for area,r in report['areas'].items() for h in r['shortages']]
        if groups:
            st.dataframe(pd.DataFrame(groups),hide_index=True)
        else:
            st.info('No assigned HRSA groups are available. Review source states to distinguish missing data from no saved records in these areas.')
        st.caption('Discipline, category and status remain separate. Component records are not people or clinicians; a county assignment is not a whole-county designation. Shared IDs can occur across areas. This GIS view is not complete withdrawn history.')
    rows=service_area_compare.export_rows(report)
    with st.expander('Values, units, periods & coverage'):
        st.dataframe(pd.DataFrame(rows)[['area','metric','definition','county_fips','value','unit','period_start','period_end','low','high','margin_of_error','confidence','status','source_state']],hide_index=True)
        for area,r in report['areas'].items():
            st.markdown(f'**{area} source dates and coverage**')
            st.dataframe(pd.DataFrame([{k:s[k] for k in ('source','state','fetched_at','freshness','retained_after_failure')} for s in r['sources']]),hide_index=True)
    st.download_button('Download area comparison CSV',service_area_compare.export_csv(report),
        'health-atlas-area-comparison.csv','text/csv',on_click='ignore')
    st.download_button('Download area comparison JSON',json.dumps(report,indent=2,allow_nan=False),
        'health-atlas-area-comparison.json','application/json',on_click='ignore')
    st.caption('Load missing sources through County spotlight and the hospital/HRSA panels, then rerun the comparison. This comparison does not fetch sources.')
    st.caption('CSV preserves units, periods, intervals/MOEs, missing states, source provenance and overlap warnings. JSON also retains both full area definitions and shared IDs. Import FIPS/identifier columns as text. Copy the browser address to share both county sets and years; links use current saved source data rather than historical snapshots.')
    return params
