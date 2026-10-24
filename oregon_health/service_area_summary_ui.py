"""Present defensible, saved-source summaries without initiating downloads."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from oregon_health import service_area_summary
from oregon_health.charts import intervals
from oregon_health.data import MEASURES
from oregon_health.regions import OREGON, get_region


def render_summary(codes,year,health_path=None,*,region=OREGON,controls=None):
    controls=controls if controls is not None else st.sidebar
    region=get_region(region)
    region.require("service_area_summary")
    if not codes:
        st.session_state['show_area_summary']=False
    enabled=controls.checkbox('Show service-area summary',key='show_area_summary',disabled=not codes,
        help='Reads saved source snapshots only. No additional API requests are made by this summary.')
    if not enabled:
        return
    st.markdown('### Service-area summary')
    st.caption(f'{region.name} · Health estimate year: {year} · ACS period: {year-4}–{year} · Whole-county membership')
    st.button('Update summary from saved data',key='area_summary_update')
    report=service_area_summary.summarize(codes,year,
        cache_dir=Path(health_path).parent if health_path else None,health_path=health_path,region=region)
    population=report['population'];facilities=report['hospitals']
    a,b,c=st.columns(3)
    a.metric('ACS population',f"{population['estimate']:,.0f}" if population['estimate'] is not None else 'Unavailable')
    b.metric('CMS-listed hospitals',facilities['listed'] if facilities['listed'] is not None else 'Not loaded')
    c.metric('Accepted approximate hospital points',facilities['approximate_locations'] if facilities['approximate_locations'] is not None else 'Not evaluated')
    st.caption(f"Population coverage: {population['covered_counties']} / {population['selected_counties']} selected counties. {population['status']}.")
    if population['moe90'] is not None:
        st.caption(f"Population 90% margin of error: ±{population['moe90']:,.0f} people.")
    elif population['selected_counties']==1:
        st.caption('Population uncertainty: the source county margin of error is unavailable, not zero.')
    else:
        st.caption('Population uncertainty: no combined margin of error is reported. Missing covariance prevents a supported multi-county interval; missing source uncertainty is not zero.')
    st.caption('Hospital listings are deduplicated CMS IDs assigned by source county. Points additionally require a current address fingerprint, an Exact Census response and a passing coordinate/county check. Approximate address points are not verified buildings, capacity or demand. Missing/unreadable sources are not zero.')
    st.markdown('#### County health estimates')
    measure=controls.selectbox('Area health condition',list(MEASURES),format_func=MEASURES.get,key='area_health_condition')
    health=pd.DataFrame(report['health'])
    selected=health[(health.measure==measure)&(health.status=='Available')].rename(columns={'county_fips':'fips'})
    if selected.empty:
        st.info('No saved county estimates are available for this condition and year. Missing values are not zero.')
    else:
        st.altair_chart(intervals(selected),width='stretch')
        if selected[['low','high']].isna().any(axis=1).any():
            st.info('Some estimates lack complete confidence intervals. Their uncertainty is unavailable, not zero.')
        st.caption('County estimates with 95% confidence intervals. No area-wide prevalence, rate average or combined health interval is calculated. Missing counties are omitted from the chart and retained in the table.')
    with st.expander('All county health estimates'):
        st.dataframe(health.rename(columns={'county':'County','definition':'Condition','prevalence':'Estimate (%)','low':'95% CI lower (%)','high':'95% CI upper (%)','status':'Status'}),hide_index=True)
    with st.expander('Demographic context by county'):
        st.dataframe(pd.DataFrame(report['demographics']).rename(columns={
            'county':'County','period':'ACS period','population':'Population','population_moe':'Population 90% MOE',
            'age65_pct':'Age 65+ (%)','age65_moe':'Age 65+ 90% MOE (pp)',
            'poverty_pct':'Poverty (%)','poverty_moe':'Poverty 90% MOE (pp)'}),hide_index=True)
        st.caption('Only population counts from a single compatible ACS release are summed, and only with complete selected-county coverage. Age and poverty percentages remain county-specific; the required compatible counts/denominators are not stored. County MOEs are not added or treated as independent.')
    st.markdown('#### HRSA designation records')
    hrsa_source=next(s for s in report['sources'] if s['source']=='HRSA designations')
    if hrsa_source['state']!='Loaded':
        st.info('HRSA records are not loaded or readable. No shortage count is inferred.')
    elif report['shortages']:
        st.dataframe(pd.DataFrame(report['shortages']).drop(columns=['designation_ids']).rename(columns={
            'discipline':'Discipline','category':'Category','status':'Status',
            'designations':'Distinct HPSA IDs','components':'Component/point records','without_geometry':'Without geometry'}),hide_index=True)
    else:
        st.info('No HRSA records in the saved snapshot have a county assignment in this area. This is not evidence of no shortages.')
    st.caption('HPSA IDs are deduplicated within discipline/category/status across selected counties. A designation spanning counties is counted once per group. Components are not people or clinicians; a county assignment does not mean the whole county is designated. Proposed withdrawal remains separate from Designated. The public GIS source is not complete withdrawn history. Facility designations are not CMS hospitals and are never added to hospital counts.')
    with st.expander('Sources, dates & coverage',expanded=True):
        st.dataframe(pd.DataFrame([{k:s[k] for k in ('source','state','records','fetched_at','freshness','retained_after_failure','unresolved_counties')} for s in report['sources']]),hide_index=True)
        for source in report['sources']:
            if source['state']=='Loaded':
                st.text(source['source'])
                st.json(source['provenance'],expanded=False)
        st.caption('Record and unresolved-county counts above describe statewide saved snapshots. Unknown counties cannot be assigned to this area and are excluded. A fresh fetch does not establish a current source release. Load Census context in Explore counties and hospital/address-match/HRSA data in Healthcare access, then return to update this summary. This panel only reads snapshots; unavailable sources do not block the rest.')
    st.download_button('Download service-area summary (JSON)',json.dumps(report,indent=2,allow_nan=False),
        'health-atlas-area-summary.json' if region==OREGON else f'health-atlas-{region.name.lower()}-area-summary.json','application/json',on_click='ignore')
    st.caption('The download retains the area definition, county values, intervals, coverage states, source dates and unsupported aggregations. Population is not a patient count or market-size estimate. Cross-area totals must account for overlapping counties.')
