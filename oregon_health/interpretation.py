"""Shared reading guidance for modeled CDC population estimates."""
import streamlit as st

METHODOLOGY_URL = 'https://www.cdc.gov/places/methodology/index.html'


def render_interpretation(*, small_area=False):
    label = 'How to read tract estimates' if small_area else 'How to interpret these estimates'
    with st.expander(label):
        st.markdown('**Modeled residents, not individual patients.** CDC PLACES combines survey and population information to estimate adult prevalence. A tract value does not diagnose its residents or describe patients at a nearby facility. Local patterns can suggest questions for further research; they do not establish causes or measure the effect of a local intervention.')
        st.markdown('**Read the interval with the estimate.** CDC health estimate limits form a 95% confidence interval and communicate model uncertainty; Census context uses 90% margins of error. A higher point estimate alone does not establish statistical significance. Interval overlap is not a significance test; a missing interval does not mean zero uncertainty.')
        st.markdown('**Check age, period and geography.** These are crude rates, so differences can reflect age composition. Compare the same measure definition and estimate year, and check denominators. Release year, estimate year, ACS five-year period and boundary vintage mean different things. Tract boundaries can change; a repeated name or GEOID alone does not prove unchanged geography across releases. Do not infer a trend from unlike periods or incompatible boundaries.')
        st.markdown('**Keep percentages separate.** Conditions can overlap, so do not add their percentages. Do not sum or take a simple average of tract rates to recreate a county estimate. County and tract products describe different areas; postal ZIP codes and ZCTAs are not interchangeable with tracts.')
        st.markdown('**Prevalence is not demand.** A high estimate does not establish unmet need, service capacity, willingness to buy or the number of potential customers. Assess the eligible population, existing services, access barriers and local evidence separately. Multiplying a prevalence rate by an unrelated population or a facility patient count does not establish a market size.')
        st.markdown('**Unavailable is not zero.**\n\n- **No source row:** a boundary exists but no estimate row is saved for this measure/year. The reason is unverified.\n- **Unavailable (source row):** the row exists but its estimate is blank. Read any source footnote; only describe it as suppressed if the source says so.\n- **Estimate present, interval unavailable:** the percentage can be shown, but its uncertainty is incomplete. Do not substitute a zero-width interval.')
        st.link_button('CDC PLACES methodology', METHODOLOGY_URL)
