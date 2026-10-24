"""County exploration and comparison views; source and statistical contracts unchanged."""
from functools import partial
import logging
import os

import duckdb
import streamlit as st
import requests


from oregon_health.data import MEASURES, comparison, county_profile
from oregon_health.map import county_deck
from oregon_health.profile import county_names, selected_county
from oregon_health.census import CensusKeyRequired
from oregon_health import census, navigation
from oregon_health.compare import comparison_rows, display_table
from oregon_health.links import parse_counties, link_params
from oregon_health.charts import intervals, distribution, condition_profile, heatmap
from oregon_health.tract_ui import render_tracts
from oregon_health.regions import OREGON

def add_comparison(county):
    current = st.session_state.get("compare_counties", [])
    if county not in current:
        st.session_state["compare_counties"] = [*current, county]
        st.session_state.setdefault("_saved_widgets", {})["compare_counties"] = [*current, county]


def census_api_key():
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("CENSUS_API_KEY")
    except FileNotFoundError:
        return None


def open_map_profile(region):
    fips = selected_county(st.session_state.get("county_map", {}), region)
    if fips:
        st.session_state['profile_county'] = fips

def render_health(path, requested_region, view, use_demo, dataset,*,controls=None):
    controls=controls if controls is not None else st.sidebar
    region_options = {} if requested_region == OREGON else {"region": requested_region}
    census_options = {} if requested_region == OREGON else {"cache_dir": path.parent, **region_options}
    with duckdb.connect(str(path), read_only=True) as db:
        return _render_health(db, requested_region, view, use_demo, dataset, region_options, census_options, controls)

def _render_health(db, requested_region, view, use_demo, dataset, region_options, census_options, controls):
    controls.subheader('County filters' if view=='explore' else 'Comparison filters')
    measure_column = year_column = controls
    requested_measure = st.query_params.get("measure")
    available_measures = {row[0] for row in db.execute("SELECT DISTINCT measure FROM estimates").fetchall()}
    measures = [code for code in MEASURES if code in available_measures]
    if not measures:
        st.info("No supported health measures are loaded. Check Data status or refresh the source.")
        st.stop()
    if "health_measure" not in st.session_state:
        st.session_state["health_measure"] = requested_measure if requested_measure in measures else measures[0]
        if requested_measure and requested_measure not in measures:
            st.warning("The linked health measure is unavailable. Showing the measure selected in the sidebar.")
    elif st.session_state["health_measure"] not in measures:
        st.session_state["health_measure"] = measures[0]
        st.warning("The selected measure is unavailable in this snapshot. Showing an available measure.")
    measure = measure_column.selectbox("Health measure", measures, format_func=MEASURES.get, key="health_measure", help="Modeled adult prevalence from CDC PLACES. COPD means chronic obstructive pulmonary disease.")
    years = [row[0] for row in db.execute("SELECT DISTINCT year FROM estimates WHERE measure = ? ORDER BY year DESC", [measure]).fetchall()]
    if not years:
        st.info("No estimates are available for this measure. Try another measure.")
        st.stop()
    requested_year = st.query_params.get("year", "")
    default_year = next((i for i, value in enumerate(years) if str(value) == requested_year), 0)
    if "estimate_year" not in st.session_state:
        st.session_state["estimate_year"] = years[default_year]
        if requested_year and requested_year not in {str(value) for value in years}:
            st.warning("The linked estimate year is unavailable. Showing the year selected in the sidebar.")
    elif st.session_state["estimate_year"] not in years:
        st.session_state["estimate_year"] = years[0]
        st.warning("The previous year is unavailable for this measure. Showing the latest available year.")
    year = year_column.selectbox("Estimate year", years, key="estimate_year")
    frame = comparison(db, measure, year, **region_options)
    st.caption(f"{MEASURES[measure]} · {year} · Adult crude prevalence · 95% confidence intervals")


    options = sorted(frame.county)
    comparison_source = (requested_region.abbr, use_demo, dataset)
    if "compare_counties" not in st.session_state:
        requested_codes, invalid = parse_counties(st.query_params.get("compare"), **region_options)
        available_names = dict(zip(frame.fips, frame.county))
        st.session_state["compare_counties"] = options[:3] if requested_codes is None else [available_names[code] for code in requested_codes if code in available_names]
        if invalid or (requested_codes is not None and any(code not in available_names for code in requested_codes)):
            st.warning("Some linked counties are invalid or have no estimate for this measure and year; they were omitted.")
    elif st.session_state.get("comparison_source") != comparison_source:
        st.session_state["compare_counties"] = options[:3]
    else:
        st.session_state["compare_counties"] = [c for c in st.session_state["compare_counties"] if c in options]
    st.session_state["comparison_source"] = comparison_source
    selected = st.session_state['compare_counties']
    options = sorted(frame.county)
    names = county_names(requested_region)
    if 'profile_county' not in st.session_state:
        requested = st.query_params.get('county')
        st.session_state['profile_county'] = requested if requested in names else None
        if requested and requested not in names:
            st.warning(f'That county link is not a recognized {requested_region.name} county. Choose a county in the sidebar.')
    fips = st.session_state.get('profile_county')
    if view == 'explore':
        fips = controls.selectbox('County profile', list(names), format_func=lambda value: f'{names[value]} County',
                            index=None, placeholder=f'Choose a {requested_region.name} county', key='profile_county')
        map_column, profile_column = st.columns([1.3, 1], gap='large')
        with map_column:
            st.subheader("The county landscape", anchor="county-map")
            st.pydeck_chart(county_deck(frame, selected, **region_options), height=380, key="county_map", on_select=partial(open_map_profile, requested_region), selection_mode="single-object", alt=f"{requested_region.name} counties colored by estimated prevalence. Use the county profile selector in the sidebar as a keyboard-accessible alternative.")
            st.caption("Light → dark teal: lower → higher estimates · Gray: no estimate · Copper outline: in your comparison.")
            if not frame.empty:
                st.caption(f"{len(frame)} {requested_region.name} counties · County estimates range from {frame.prevalence.min():.1f}% to {frame.prevalence.max():.1f}%.")
            current_profile = st.session_state.get("profile_county")
            if current_profile in county_names(requested_region):
                st.markdown(f"**{county_names(requested_region)[current_profile]} County selected.**")
            else:
                st.caption("Hover for details. Click a county, or choose a county in the sidebar.")
            with st.expander("Map legend & source"):
                st.caption("Colors adjust to each measure and year. Crude prevalence reflects county age differences; colors are not directly comparable between measures. Pan or zoom to explore; hover for 95% confidence intervals.")
                st.caption("Boundaries: [Plotly county GeoJSON](https://github.com/plotly/datasets/blob/0c447c47b757ad74edecab31f0d72f849d2e67c2/geojson-counties-fips.json), MIT license. Generalized boundaries for visualization; boundary vintage is unverified, so they are not evidence of precise geography or historical boundary alignment.")
        with profile_column:
            if fips:
                st.subheader(f"{names[fips]} County", anchor="county-profile")
                st.caption(f"{year} estimates · Modeled adult crude prevalence")
                profile = county_profile(db, fips, year, **region_options)
                records = {row.measure: row for row in profile.itertuples()}
                with st.expander("Headline estimates"):
                    cards = [st.container() for _ in range(3)]
                    for card, (code, label) in zip(cards, list(MEASURES.items())[:3]):
                        with card.container(border=True):
                            row = records.get(code)
                            st.metric(label, f"{row.prevalence:.1f}%" if row is not None else "Unavailable")
                            st.caption(f"95% CI: {row.low:.1f}%–{row.high:.1f}%" if row is not None else "No estimate for this county and year.")
                if profile.empty:
                    st.info("No estimates are available for this county in the selected year. Missing values are not zero.")
                if not profile.empty:
                    st.markdown("#### Conditions at a glance")
                    st.altair_chart(condition_profile(profile), width="stretch")
                    st.caption("Dots: estimates · Lines: 95% confidence intervals. Conditions can overlap; percentages do not add to 100%. Missing conditions are omitted, not treated as zero.")
                st.markdown("#### Community context")
                if use_demo:
                    st.info("Census demographics are available in real-data mode. No real Census values are mixed into this synthetic demo.")
                else:
                    try:
                        with st.spinner("Loading Census context…"):
                            context = census.context(fips, year, api_key=census_api_key(), **census_options)
                        if context["warning"]:
                            st.warning(context["warning"])
                        context_cards = [st.container(), *st.columns(2)]
                        labels = [("Population", "population", "population_moe", False),
                                  ("Residents age 65+", "age65_pct", "age65_moe", True),
                                  ("Below poverty level", "poverty_pct", "poverty_moe", True)]
                        for card, (label, field, moe_field, percent) in zip(context_cards, labels):
                            with card.container(border=True):
                                value, moe = context[field], context[moe_field]
                                formatted = "Unavailable" if value is None else (f"{value:.1f}%" if percent else f"{value:,.0f}")
                                st.metric(label, formatted)
                                if moe is not None and value is not None:
                                    st.caption(f"90% margin of error: ±{moe:.1f} percentage points" if percent else f"90% margin of error: ±{moe:,.0f} people")
                                else:
                                    st.caption("Margin of error unavailable.")
                        st.caption(f"ACS {year - 4}–{year} five-year estimates · [Census source]({context['source']})")
                        with st.expander("About Census estimates"):
                            st.caption("Census figures describe a five-year period and include residents of all ages. Poverty uses the population for whom poverty status is determined. CDC health measures describe adults. Census margins use 90% confidence; CDC intervals use 95%. These figures provide context and do not establish causes.")
                    except CensusKeyRequired:
                        st.info("Census context is not configured yet. The site maintainer needs to add a Census API key in deployment secrets. Health estimates remain available.")
                    except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
                        logging.getLogger(__name__).exception("Census context unavailable")
                        st.info("Census context is currently unavailable for this year. Health estimates and county comparisons remain available.")
                st.button("Add county to comparison", on_click=add_comparison, args=(names[fips],), disabled=names[fips] not in options or names[fips] in selected)
                if names[fips] not in options:
                    st.caption("This county has no estimate for the current map measure, so it cannot be added to that comparison.")
                if names[fips] in selected:
                    st.caption("Already in your comparison.")
                st.button("Open county comparison", on_click=navigation.go, args=("compare",))
            if not fips:
                st.subheader('County spotlight', anchor='county-profile')
                st.info('Choose a county in the sidebar or on the map to explore its conditions and community context.')
                st.altair_chart(distribution(frame), width='stretch')
                st.caption('One observation per county. County median is not population-weighted statewide prevalence.')
        st.caption('Crude rates reflect age composition. Higher prevalence alone does not establish greater healthcare demand.')
        with st.expander('County estimates & download'):
            visible = frame
            if not visible.empty:
                st.dataframe(visible.drop(columns="fips").rename(columns={"county": "County", "prevalence": "Estimate (%)", "low": "95% CI lower (%)", "high": "95% CI upper (%)"}), hide_index=True)
                export_source, export_imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
                county_export = visible.assign(state=requested_region.abbr, state_fips=requested_region.state_fips,
                                              measure=measure, estimate_year=year, confidence=95, unit="percent",
                                              source=export_source, imported_at=str(export_imported))
                st.download_button("Download comparison CSV", county_export.to_csv(index=False), f"health-atlas-{requested_region.name.lower()}.csv", "text/csv")
        local_view = render_tracts(fips, measure, year, use_demo) if requested_region == OREGON else {}
    else:
        selected = controls.multiselect('Counties to compare', options, key='compare_counties')
        visible = frame[frame.county.isin(selected)]
        if not visible.empty:
            st.altair_chart(intervals(visible), width='stretch')
        if not visible.empty:
            with st.expander("Compare estimates & download", expanded=False):
                st.dataframe(visible.drop(columns="fips").rename(columns={"county": "County", "prevalence": "Estimate (%)", "low": "95% CI lower (%)", "high": "95% CI upper (%)"}), hide_index=True)
                export_source, export_imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
                county_export = visible.assign(state=requested_region.abbr, state_fips=requested_region.state_fips,
                                              measure=measure, estimate_year=year, confidence=95, unit="percent",
                                              source=export_source, imported_at=str(export_imported))
                st.download_button("Download comparison CSV", county_export.to_csv(index=False), f"health-atlas-{requested_region.name.lower()}.csv", "text/csv")
        st.subheader("Compare the whole picture", anchor="county-comparison")
        st.caption("Nine health conditions and community demographics for your selected counties.")
        if "include_census" not in st.session_state:
            st.session_state["include_census"] = st.query_params.get("census") == "1" and not use_demo
        if use_demo:
            st.session_state["include_census"] = False
        include_census = controls.checkbox("Include Census demographics", key="include_census", disabled=use_demo, help="Uses the cached ACS release when available. The first load may need a Census request.")
        if not selected:
            st.info("Choose counties in the sidebar to build a side-by-side comparison.")
        else:
            names_to_fips = dict(zip(frame.county, frame.fips))
            codes = [names_to_fips[name] for name in selected]
            contexts = None
            if include_census and not use_demo:
                contexts = {}
                try:
                    with st.spinner("Loading comparison demographics…"):
                        for code in codes:
                            result = census.context(code, year, api_key=census_api_key(), **census_options)
                            if result["year"] != year:
                                raise ValueError("Census release year differs from the comparison year")
                            contexts[code] = result
                    if any(value["warning"] for value in contexts.values()):
                        st.warning("Census could not be refreshed. Some demographics use previously downloaded data; import dates are included in the CSV.")
                except CensusKeyRequired:
                    st.info("Census demographics need a key in deployment secrets. Health comparisons remain available.")
                except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
                    logging.getLogger(__name__).exception("Comparison demographics unavailable")
                    st.info("Some Census demographics are unavailable. Health comparisons remain available.")
            comparison_source, comparison_imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
            rows = comparison_rows(db, codes, year, comparison_source, comparison_imported, contexts, demo=use_demo, **region_options)
            st.markdown("#### Condition patterns")
            st.altair_chart(heatmap(rows), width="stretch")
            st.caption("Absolute prevalence, not a health grade or combined risk score. Conditions can overlap. Gray/— means no estimate. Hover for 95% intervals. The heatmap shows the first three selected counties; the table and CSV include all selected counties.")
            st.dataframe(display_table(rows), hide_index=True, key="full_comparison")
            with st.expander("How to read the comparison"):
                st.caption("Health: adult crude prevalence with 95% confidence intervals in parentheses. Census: five-year estimates with 90% margins of error (±); pp means percentage points. Different periods and population definitions are not directly interchangeable. These comparisons are descriptive and do not establish causes or statistical significance.")
            if use_demo:
                st.caption("Synthetic health data only; real Census demographics are not loaded in demo mode.")
            elif not include_census:
                st.caption("Select Include Census demographics to load population, age and poverty estimates.")
            if contexts:
                census_source = next(iter(contexts.values()))
                st.caption(f"Demographics source: [ACS {year - 4}–{year} five-year estimates]({census_source['source']}). Download includes each county’s import date.")
            st.download_button("Download full comparison CSV", rows.to_csv(index=False), "health-atlas-county-comparison.csv", "text/csv")
        local_view = dict(local=st.session_state.get('show_tracts', False), tract=st.session_state.get('tract_selection'), tract_year=st.session_state.get('tract_year'))
    selected_fips = dict(zip(frame.county, frame.fips))
    return link_params([selected_fips[name] for name in selected], year, measure, use_demo,
                       st.session_state.get('include_census', False), fips, **local_view, **region_options)

