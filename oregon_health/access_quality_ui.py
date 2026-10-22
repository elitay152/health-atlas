"""UI for local access-source audits; no network calls or refresh controls."""
import pandas as pd
import streamlit as st

from oregon_health.access_quality import audit


def _csv(rows,columns=None):
    frame=pd.DataFrame(rows,columns=columns)
    def safe(value):
        if isinstance(value,str) and (value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n'))):
            return "'"+value
        return value
    if 'Count' in frame.columns:
        frame['Count']=pd.array(frame['Count'],dtype='Int64')
    return frame.map(safe).to_csv(index=False)


def _frame(rows):
    frame=pd.DataFrame(rows)
    for column in ['Count','Cached records','CMS-listed hospitals','HRSA components / facility points',
                   'HRSA distinct discipline/designation pairs']:
        if column in frame.columns:
            frame[column]=[('Not applicable' if r.get('State')=='Unsupported' else 'Not evaluated')
                           if pd.isna(value) else f'{int(value):,}' for r,value in zip(rows,frame[column])]
    return frame.fillna('Not evaluated')


def render_access_quality(cache_dir,demo=False):
    st.markdown('#### Healthcare access sources')
    if demo:
        st.caption('Facility and shortage snapshots are not evaluated in synthetic demo mode. Real records are not mixed into this view.')
        return
    report=audit(cache_dir)
    st.caption('Read-only audit of independently cached CMS, Census address-match and HRSA snapshots. Not loaded or unreadable snapshots have unevaluated counts, not zero supply. This panel never requests or refreshes source data.')
    st.dataframe(_frame(report['summary']),hide_index=True,width='stretch')
    if any(r['Snapshot']=='Unreadable snapshot' for r in report['summary']):
        st.warning('At least one local snapshot could not be read. Other sources are audited independently; this does not establish a live service outage.')
    if any(r['Snapshot']=='Loaded empty snapshot' for r in report['summary']):
        st.warning('At least one loaded snapshot contains no records. Its zero record count is not evidence of no healthcare supply or shortages.')
    if any(r['Retained after failed refresh'] for r in report['summary']):
        st.warning('A retained snapshot follows a failed refresh. The audit describes its saved records, not the current source service.')
    st.caption('Fetch age and the official source release/warehouse date are separate. A recent download may contain older source records. Address benchmarks are not release dates. Duplicate checks describe saved snapshots; importers reject duplicates before publication, and rejected payload counts are not retained.')
    st.dataframe(_frame(report['checks']),hide_index=True,width='stretch')
    st.caption('Unsupported means the source does not supply the field or scope. Missing/invalid values concern supported fields in loaded records. HRSA components may repeat a designation ID legitimately; only duplicate feature keys are counted as duplicates. Match statuses and coordinate validity are audited separately. These checks do not form a quality score.')
    with st.expander('County coverage in loaded access snapshots'):
        st.dataframe(_frame(report['counties']),hide_index=True,width='stretch')
        st.caption('Zero means no matching records in that loaded snapshot, never no healthcare access or no shortages. HRSA counts include source components/points and the statuses present in the snapshot; they are not whole-county shortage determinations. Unresolved county joins are reported separately above.')
    if report['findings']:
        with st.expander('Inspect access-data findings'):
            st.dataframe(pd.DataFrame(report['findings']),hide_index=True,width='stretch')
    else:
        st.caption('No record-level findings were identified among the evaluated checks. Unevaluated sources and unsupported fields are not certified complete.')
    st.download_button('Download access-quality checks',_csv(report['checks']),
                       'health-atlas-access-quality.csv','text/csv',key='access_quality_checks')
    st.download_button('Download access-quality findings',_csv(report['findings'],['Source','Record ID','Check','Detail']),
                       'health-atlas-access-findings.csv','text/csv',key='access_quality_findings')
