import logging
import os
from pathlib import Path

import duckdb
import streamlit as st
import requests

from oregon_health.api_cache import DEFAULT_DATASET, RefreshLimited, catalog_is_current, needs_refresh, refresh

from oregon_health.data import DB_PATH, MEASURES, comparison, county_profile, demo_rows, store
from oregon_health.map import county_deck
from oregon_health.profile import county_names, selected_county
from oregon_health.census import CensusKeyRequired, context as census_context
from oregon_health.compare import comparison_rows, display_table
from oregon_health.links import parse_counties, link_params
from oregon_health.status_ui import render_data_status
from oregon_health.charts import intervals, distribution, condition_profile, heatmap
from oregon_health.hospital_ui import render_hospitals
from oregon_health.hrsa_ui import render_hrsa
from oregon_health.tract_ui import render_tracts
from oregon_health.interpretation import render_interpretation
from oregon_health.service_area_ui import render_service_area
from oregon_health.regions import OREGON, get_region

def open_map_profile():
    fips = selected_county(st.session_state.get("county_map", {}))
    if fips:
        st.session_state["profile_county"] = fips


def add_comparison(county):
    current = st.session_state.get("compare_counties", [])
    if county not in current:
        st.session_state["compare_counties"] = [*current, county]


def census_api_key():
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("CENSUS_API_KEY")
    except FileNotFoundError:
        return None


st.set_page_config(page_title="Health Atlas", page_icon="🌲", layout="wide")
st.caption("OREGON / COMMUNITY HEALTH")
st.title("Health Atlas")
st.write("Explore a county. Build a comparison. Share what you find.")
st.markdown("[Map](#county-map) · [County profile](#county-profile) · [Tracts](#tract-explorer) · [Comparison](#county-comparison) · [Service area](#service-area) · [Hospitals](#hospital-directory) · [Shortage designations](#shortage-designations) · [Data status](#data-status)")
try:
    requested_region=get_region(st.query_params.get("state","OR"))
except ValueError:
    st.info("That region is not supported. This deployment currently serves Oregon.")
    st.link_button("Open Oregon explorer", "?")
    st.stop()
if not requested_region.deployed:
    st.info(f"{requested_region.name} is prepared for development, but this deployment currently serves Oregon.")
    st.link_button("Open Oregon explorer", "?")
    st.stop()
st.sidebar.header("Data settings")
st.sidebar.markdown("[Data status & coverage](#data-status)")
if "demo_mode" not in st.session_state:
    st.session_state["demo_mode"] = st.query_params.get("demo") == "1"
use_demo = st.sidebar.toggle("Use synthetic demo data", key="demo_mode")
with st.sidebar.expander("Source & refresh"):
    dataset = os.environ.get("CDC_PLACES_DATASET", DEFAULT_DATASET)
    st.text(f"Dataset: {dataset}")
    st.caption("The site maintainer chooses the CDC release. The downloaded release title appears below the chart.")
    refresh_clicked = st.button("Refresh data", disabled=use_demo)
    st.caption("Cached for 24 hours. Shared refresh limit: once every five minutes.")
if not use_demo:
    # One automatic attempt per source per session prevents retrying on every
    # widget interaction if CDC is unavailable. The button always retries.
    attempt_key = f"cdc_attempt:{dataset}"
    if refresh_clicked or (not st.session_state.get(attempt_key) and needs_refresh(dataset)):
        st.session_state[attempt_key] = True
        try:
            with st.spinner("Fetching Oregon estimates from CDC…"):
                count = refresh(dataset)
            st.session_state.pop(attempt_key, None)
            st.success(f"Downloaded {count} Oregon estimates from CDC.")
        except RefreshLimited as exc:
            st.info(str(exc))
        except (requests.RequestException, ValueError, KeyError, TypeError, duckdb.Error):
            logging.getLogger(__name__).exception("CDC refresh failed")
            st.error("Could not refresh CDC data. Please try again later. Technical details are available in the server logs.")
            if DB_PATH.exists():
                st.warning("Showing previously downloaded data. Check its source and import date below; it may be older or from another release.")

if use_demo:
    st.warning("DEMO — synthetic values for three counties. These are not CDC estimates and must not be used for health decisions.")
    demo_path = Path(__file__).resolve().parent / "data" / "demo.duckdb"
    if not catalog_is_current(demo_path):
        store(demo_rows(), "Synthetic demo fixture — not CDC data", demo_path)
    path = demo_path
else:
    path = DB_PATH
    if not path.exists():
        area_view = render_service_area(health_path=DB_PATH)
        if dict(st.query_params) != area_view:
            st.query_params.from_dict(area_view)
        render_hospitals(context_available=False)
        render_hrsa()
        render_data_status(DB_PATH, None, dataset)
        st.info("No CDC data has been downloaded yet. Try Refresh data after five minutes, or ask the maintainer to check the data source. You can also enable the clearly labeled synthetic demo.")
        st.stop()

with duckdb.connect(str(path), read_only=True) as db:
    measure_column, year_column = st.columns([2, 1])
    requested_measure = st.query_params.get("measure")
    available_measures = {row[0] for row in db.execute("SELECT DISTINCT measure FROM estimates").fetchall()}
    measures = [code for code in MEASURES if code in available_measures]
    if not measures:
        st.info("No supported health measures are loaded. Check Data status or refresh the source.")
        st.stop()
    if "health_measure" not in st.session_state:
        st.session_state["health_measure"] = requested_measure if requested_measure in measures else measures[0]
        if requested_measure and requested_measure not in measures:
            st.warning("The linked health measure is unavailable. Showing the measure selected below.")
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
            st.warning("The linked estimate year is unavailable. Showing the year selected below.")
    elif st.session_state["estimate_year"] not in years:
        st.session_state["estimate_year"] = years[0]
        st.warning("The previous year is unavailable for this measure. Showing the latest available year.")
    year = year_column.selectbox("Estimate year", years, key="estimate_year")
    frame = comparison(db, measure, year)
    st.subheader(f"{MEASURES[measure]} · {year}")
    st.caption("Modeled estimates for adults · Choose a measure and year, then a county.")
    st.caption("Crude rates reflect age composition. Read 95% intervals alongside the estimates; a higher rate alone does not establish greater healthcare demand.")
    if not frame.empty:
        st.caption(f"{len(frame)} Oregon counties · County estimates range from {frame.prevalence.min():.1f}% to {frame.prevalence.max():.1f}%.")
    options = sorted(frame.county)
    comparison_source = (use_demo, dataset)
    if "compare_counties" not in st.session_state:
        requested_codes, invalid = parse_counties(st.query_params.get("compare"))
        available_names = dict(zip(frame.fips, frame.county))
        st.session_state["compare_counties"] = options[:3] if requested_codes is None else [available_names[code] for code in requested_codes if code in available_names]
        if invalid or (requested_codes is not None and any(code not in available_names for code in requested_codes)):
            st.warning("Some linked counties are invalid or have no estimate for this measure and year; they were omitted.")
    elif st.session_state.get("comparison_source") != comparison_source:
        st.session_state["compare_counties"] = options[:3]
    else:
        st.session_state["compare_counties"] = [c for c in st.session_state["compare_counties"] if c in options]
    st.session_state["comparison_source"] = comparison_source
    selected = st.multiselect("Counties to compare", options, key="compare_counties")
    visible = frame[frame.county.isin(selected)]
    map_column, chart_column = st.columns([1.6, 1], gap="large")
    with map_column:
        st.subheader("The county landscape", anchor="county-map")
        st.pydeck_chart(county_deck(frame, selected), height=480, key="county_map", on_select=open_map_profile, selection_mode="single-object", alt="Oregon counties colored by estimated prevalence. Use the county profile selector below as a keyboard-accessible alternative.")
        st.caption("Light → dark teal: lower → higher estimates · Gray: no estimate · Copper outline: in your comparison.")
        current_profile = st.session_state.get("profile_county")
        if current_profile in county_names():
            st.markdown(f"**{county_names()[current_profile]} County selected.** [View county profile ↓](#county-profile)")
        else:
            st.caption("Hover for details. Click a county, then open its profile below.")
        with st.expander("Map legend & source"):
            st.caption("Colors adjust to each measure and year. Crude prevalence reflects county age differences; colors are not directly comparable between measures. Pan or zoom to explore; hover for 95% confidence intervals.")
            st.caption("Boundaries: [Plotly county GeoJSON](https://github.com/plotly/datasets/blob/0c447c47b757ad74edecab31f0d72f849d2e67c2/geojson-counties-fips.json), MIT license. Generalized boundaries for visualization.")
    with chart_column:
        st.markdown("### Side by side")
        if visible.empty:
            st.info("Select at least one county to display a comparison.")
        else:
            st.altair_chart(intervals(visible), width="stretch")
            st.caption("Dots are estimates; lines are 95% confidence intervals. Overlap is not a significance test.")
        st.markdown("#### Across Oregon")
        st.altair_chart(distribution(frame), width="stretch")
        st.caption("One observation per county. Dashed line: county median, not population-weighted statewide prevalence.")
    if not visible.empty:
        with st.expander("Compare estimates & download", expanded=False):
            st.dataframe(visible.drop(columns="fips").rename(columns={"county": "County", "prevalence": "Estimate (%)", "low": "95% CI lower (%)", "high": "95% CI upper (%)"}), hide_index=True)
            st.download_button("Download comparison CSV", visible.to_csv(index=False), "health-atlas-oregon.csv", "text/csv")
    st.divider()
    st.subheader("County spotlight", anchor="county-profile")
    st.caption("Choose a county to see its health measures, then add it to your comparison.")
    names = county_names()
    requested_county = st.query_params.get("county")
    if "profile_county" not in st.session_state:
        st.session_state["profile_county"] = requested_county if requested_county in names else None
        if requested_county and requested_county not in names:
            st.warning("That county link is not a recognized Oregon county. Choose a county below.")
    fips = st.selectbox("County profile", list(names), format_func=lambda value: f"{names[value]} County", index=None, placeholder="Choose an Oregon county", key="profile_county")
    if fips:
        st.subheader(f"{names[fips]} County")
        st.caption(f"{year} estimates · Modeled adult crude prevalence")
        st.caption("Three familiar measures at a glance; the chart below includes every available condition.")
        profile = county_profile(db, fips, year)
        records = {row.measure: row for row in profile.itertuples()}
        cards = st.columns(3)
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
                    context = census_context(fips, year, api_key=census_api_key())
                if context["warning"]:
                    st.warning(context["warning"])
                context_cards = st.columns(3)
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
            except (requests.RequestException, ValueError, KeyError, TypeError, duckdb.Error):
                logging.getLogger(__name__).exception("Census context unavailable")
                st.info("Census context is currently unavailable for this year. Health estimates and county comparisons remain available.")
        st.button("Add county to comparison", on_click=add_comparison, args=(names[fips],), disabled=names[fips] not in options or names[fips] in selected)
        if names[fips] not in options:
            st.caption("This county has no estimate for the current map measure, so it cannot be added to that comparison.")
        if names[fips] in selected:
            st.caption("Already in your comparison.")
        st.markdown("[View comparison ↓](#county-comparison) · [Back to map ↑](#county-map)")
    local_view = render_tracts(fips, measure, year, use_demo)
    st.divider()
    st.subheader("Compare the whole picture", anchor="county-comparison")
    st.caption("The counties selected above, across nine health conditions and community demographics.")
    if "include_census" not in st.session_state:
        st.session_state["include_census"] = st.query_params.get("census") == "1" and not use_demo
    if use_demo:
        st.session_state["include_census"] = False
    include_census = st.checkbox("Include Census demographics", key="include_census", disabled=use_demo, help="Uses the cached ACS release when available. The first load may need a Census request.")
    if not selected:
        st.info("Choose counties above to build a side-by-side comparison.")
    else:
        names_to_fips = dict(zip(frame.county, frame.fips))
        codes = [names_to_fips[name] for name in selected]
        contexts = None
        if include_census and not use_demo:
            contexts = {}
            try:
                with st.spinner("Loading comparison demographics…"):
                    for code in codes:
                        result = census_context(code, year, api_key=census_api_key())
                        if result["year"] != year:
                            raise ValueError("Census release year differs from the comparison year")
                        contexts[code] = result
                if any(value["warning"] for value in contexts.values()):
                    st.warning("Census could not be refreshed. Some demographics use previously downloaded data; import dates are included in the CSV.")
            except CensusKeyRequired:
                st.info("Census demographics need a key in deployment secrets. Health comparisons remain available.")
            except (requests.RequestException, ValueError, KeyError, TypeError, duckdb.Error):
                logging.getLogger(__name__).exception("Comparison demographics unavailable")
                st.info("Some Census demographics are unavailable. Health comparisons remain available.")
        comparison_source, comparison_imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
        rows = comparison_rows(db, codes, year, comparison_source, comparison_imported, contexts, demo=use_demo)
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
    st.caption("Copy your browser’s address to share these counties and view settings. Links use the site’s current data release.")
    selected_fips = dict(zip(frame.county, frame.fips))
    shared_view = link_params([selected_fips[name] for name in selected], year, measure, use_demo, include_census, fips, **local_view)
    st.markdown("[Back to map ↑](#county-map) · [Data status & sources](#data-status)")
area_view = render_service_area(use_demo, health_path=path, year=year)
shared_view.update(area_view)
if dict(st.query_params) != shared_view:
    st.query_params.from_dict(shared_view)
render_hospitals(use_demo)
render_hrsa(use_demo)
render_data_status(path, year, dataset, use_demo)
st.caption("Population estimates, not individual diagnoses. County differences do not establish causes.")
render_interpretation()
