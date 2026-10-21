import logging
import os
from pathlib import Path

import duckdb
import streamlit as st
import requests

from oregon_health.api_cache import DEFAULT_DATASET, RefreshLimited, needs_refresh, refresh

from oregon_health.data import DB_PATH, MEASURES, comparison, county_profile, demo_rows, store
from oregon_health.map import county_deck
from oregon_health.profile import county_names, selected_county
from oregon_health.census import context as census_context

def open_map_profile():
    fips = selected_county(st.session_state.get("county_map", {}))
    if fips:
        st.session_state["profile_county"] = fips


def add_comparison(county):
    current = st.session_state.get("compare_counties", [])
    if county not in current:
        st.session_state["compare_counties"] = [*current, county]


st.set_page_config(page_title="Health Atlas", page_icon="🌲", layout="wide")
st.caption("OREGON / COMMUNITY HEALTH")
st.title("Health Atlas")
st.write("A closer look at the health of Oregon’s communities.")
st.sidebar.header("Explore Oregon")
use_demo = st.sidebar.toggle("Use synthetic demo data", value=st.query_params.get("demo") == "1")
with st.sidebar.expander("CDC data source"):
    dataset = os.environ.get("CDC_PLACES_DATASET", DEFAULT_DATASET)
    st.text(f"Dataset: {dataset}")
    st.caption("The site maintainer chooses the CDC release. The downloaded release title appears below the chart.")
refresh_clicked = st.sidebar.button("Refresh data", disabled=use_demo)
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
    st.sidebar.caption("CDC data is cached locally for 24 hours. Refresh attempts are limited to one per five minutes for all visitors combined. No API key required.")
if use_demo:
    st.warning("DEMO — synthetic values for three counties. These are not CDC estimates and must not be used for health decisions.")
    demo_path = Path(__file__).resolve().parent / "data" / "demo.duckdb"
    if not demo_path.exists():
        store(demo_rows(), "Synthetic demo fixture — not CDC data", demo_path)
    path = demo_path
else:
    path = DB_PATH
    if not path.exists():
        st.info("No CDC data has been downloaded yet. Try Refresh data after five minutes, or ask the maintainer to check the data source. You can also enable the clearly labeled synthetic demo.")
        st.stop()

with duckdb.connect(str(path), read_only=True) as db:
    requested_measure = st.query_params.get("measure")
    measures = list(MEASURES)
    measure = st.sidebar.selectbox("Health measure", measures, index=measures.index(requested_measure) if requested_measure in measures else 0, format_func=MEASURES.get)
    years = [row[0] for row in db.execute("SELECT DISTINCT year FROM estimates WHERE measure = ? ORDER BY year DESC", [measure]).fetchall()]
    if not years:
        st.info("No estimates are available for this measure. Try another measure.")
        st.stop()
    requested_year = st.query_params.get("year", "")
    default_year = next((i for i, value in enumerate(years) if str(value) == requested_year), 0)
    year = st.sidebar.selectbox("Estimate year", years, index=default_year)
    frame = comparison(db, measure, year)
    st.subheader(f"{MEASURES[measure]} · {year}")
    st.caption("Modeled adult crude prevalence · Estimate year, not release year · County comparisons reflect age differences")
    stats = st.columns(3)
    stats[0].metric("Counties with estimates", f"{len(frame)} / 36", border=True)
    if not frame.empty:
        stats[1].metric("Lowest county estimate", f"{frame.prevalence.min():.1f}%", border=True)
        stats[2].metric("Highest county estimate", f"{frame.prevalence.max():.1f}%", border=True)
    options = sorted(frame.county)
    comparison_source = (use_demo, dataset)
    if "compare_counties" not in st.session_state or st.session_state.get("comparison_source") != comparison_source:
        st.session_state["compare_counties"] = options[:3]
    else:
        st.session_state["compare_counties"] = [c for c in st.session_state["compare_counties"] if c in options]
    st.session_state["comparison_source"] = comparison_source
    selected = st.multiselect("Counties to compare", options, key="compare_counties")
    visible = frame[frame.county.isin(selected)]
    map_column, chart_column = st.columns([1.6, 1], gap="large")
    with map_column:
        st.markdown("### The county landscape")
        st.pydeck_chart(county_deck(frame, selected), height=480, key="county_map", on_select=open_map_profile, selection_mode="single-object", alt="Oregon counties colored by estimated prevalence. Use the county profile selector below as a keyboard-accessible alternative.")
        st.caption("Light teal = lower prevalence · Dark teal = higher prevalence · Gray = no estimate · Copper outline = selected county. Scale adjusts to each measure and year.")
        st.caption("Click a county to open its profile below. Pan, zoom, or hover for its estimate and 95% confidence interval.")
        st.caption("Boundaries: [Plotly county GeoJSON](https://github.com/plotly/datasets/blob/0c447c47b757ad74edecab31f0d72f849d2e67c2/geojson-counties-fips.json), MIT license. Generalized boundaries for visualization.")
    with chart_column:
        st.markdown("### Side by side")
        if visible.empty:
            st.info("Select at least one county to display a comparison.")
        else:
            st.bar_chart(visible.set_index("county")[["prevalence"]], y_label="Estimated prevalence (%)", color="#176B63", height=360)
            st.caption("Estimates are shown with confidence intervals in the table below. Differences do not necessarily indicate statistically significant differences.")
    if not visible.empty:
        with st.expander("Compare estimates & download", expanded=True):
            st.dataframe(visible.drop(columns="fips").rename(columns={"county": "County", "prevalence": "Estimate (%)", "low": "95% CI lower (%)", "high": "95% CI upper (%)"}), hide_index=True)
            st.download_button("Download comparison CSV", visible.to_csv(index=False), "health-atlas-oregon.csv", "text/csv")
    st.divider()
    st.markdown("### County spotlight")
    st.caption("Click the map or choose a county to explore all three health measures.")
    names = county_names()
    requested_county = st.query_params.get("county")
    if "profile_county" not in st.session_state:
        st.session_state["profile_county"] = requested_county if requested_county in names else None
        if requested_county and requested_county not in names:
            st.warning("That county link is not a recognized Oregon county. Choose a county below.")
    fips = st.selectbox("County profile", list(names), format_func=lambda value: f"{names[value]} County", index=None, placeholder="Choose an Oregon county", key="profile_county")
    if fips:
        st.query_params.update(county=fips, year=str(year), measure=measure, demo="1" if use_demo else "0")
        st.subheader(f"{names[fips]} County")
        st.caption(f"{year} estimates · Modeled adult crude prevalence")
        profile = county_profile(db, fips, year)
        records = {row.measure: row for row in profile.itertuples()}
        cards = st.columns(3)
        for card, (code, label) in zip(cards, MEASURES.items()):
            with card.container(border=True):
                row = records.get(code)
                st.metric(label, f"{row.prevalence:.1f}%" if row is not None else "Unavailable")
                st.caption(f"95% CI: {row.low:.1f}%–{row.high:.1f}%" if row is not None else "No estimate for this county and year.")
        if profile.empty:
            st.info("No estimates are available for this county in the selected year. Missing values are not zero.")
        st.markdown("#### Community context")
        if use_demo:
            st.info("Census demographics are available in real-data mode. No real Census values are mixed into this synthetic demo.")
        else:
            try:
                with st.spinner("Loading Census context…"):
                    context = census_context(fips, year)
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
                st.caption(f"Source: ACS {year - 4}–{year} five-year estimates · [U.S. Census Bureau]({context['source']}) · Imported {context['imported_at']} UTC")
                st.caption("Census figures describe a five-year period and include residents of all ages. Poverty uses the population for whom poverty status is determined. CDC health measures describe adults. Census margins use 90% confidence; CDC intervals use 95%. These figures provide context and do not establish causes.")
            except (requests.RequestException, ValueError, KeyError, TypeError, duckdb.Error):
                logging.getLogger(__name__).exception("Census context unavailable")
                st.info("Census context is currently unavailable for this year. Health estimates and county comparisons remain available.")
        st.button("Add county to comparison", on_click=add_comparison, args=(names[fips],), disabled=names[fips] not in options or names[fips] in selected)
        if names[fips] not in options:
            st.caption("This county has no estimate for the current map measure, so it cannot be added to that comparison.")
        st.caption("Copy the address in your browser to share this county profile. Links preserve the measure, estimate year, and demo mode; they use the deployment’s current CDC release.")
    elif "county" in st.query_params:
        del st.query_params["county"]
    source, imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
    st.caption(f"Source: {source} · Imported: {imported} UTC")
st.info("PLACES estimates are modeled population measures, not individual diagnoses. Crude estimates reflect differences in age composition; use caution comparing counties. Differences and associations do not establish causes. Consult confidence intervals and CDC methodology before interpreting results.")
st.link_button("CDC PLACES data and methodology", "https://www.cdc.gov/places/")
