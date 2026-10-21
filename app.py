import logging
import os
from pathlib import Path

import duckdb
import streamlit as st
import requests

from oregon_health.api_cache import DEFAULT_DATASET, RefreshLimited, needs_refresh, refresh

from oregon_health.data import DB_PATH, MEASURES, comparison, demo_rows, store
from oregon_health.map import county_deck

st.set_page_config(page_title="Health Atlas", page_icon="🌲", layout="wide")
st.caption("OREGON / COMMUNITY HEALTH")
st.title("Health Atlas")
st.write("A closer look at the health of Oregon’s communities.")
st.sidebar.header("Explore Oregon")
use_demo = st.sidebar.toggle("Use synthetic demo data", value=False)
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
    measure = st.sidebar.selectbox("Health measure", list(MEASURES), format_func=MEASURES.get)
    years = [row[0] for row in db.execute("SELECT DISTINCT year FROM estimates WHERE measure = ? ORDER BY year DESC", [measure]).fetchall()]
    year = st.sidebar.selectbox("Estimate year", years)
    frame = comparison(db, measure, year)
    st.subheader(f"{MEASURES[measure]} · {year}")
    st.caption("Modeled adult crude prevalence · Estimate year, not release year · County comparisons reflect age differences")
    stats = st.columns(3)
    stats[0].metric("Counties with estimates", f"{len(frame)} / 36", border=True)
    if not frame.empty:
        stats[1].metric("Lowest county estimate", f"{frame.prevalence.min():.1f}%", border=True)
        stats[2].metric("Highest county estimate", f"{frame.prevalence.max():.1f}%", border=True)
    selected = st.multiselect("Counties to compare", sorted(frame.county), default=sorted(frame.county)[:3])
    visible = frame[frame.county.isin(selected)]
    map_column, chart_column = st.columns([1.6, 1], gap="large")
    with map_column:
        st.markdown("### The county landscape")
        st.pydeck_chart(county_deck(frame, selected), height=480)
        st.caption("Light teal = lower prevalence · Dark teal = higher prevalence · Gray = no estimate · Copper outline = selected county. Scale adjusts to each measure and year.")
        st.caption("Pan, zoom, or hover over a county for its estimate and 95% confidence interval. Select counties above to compare.")
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
    source, imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
    st.caption(f"Source: {source} · Imported: {imported} UTC")
st.info("PLACES estimates are modeled population measures, not individual diagnoses. Crude estimates reflect differences in age composition; use caution comparing counties. Differences and associations do not establish causes. Consult confidence intervals and CDC methodology before interpreting results.")
st.link_button("CDC PLACES data and methodology", "https://www.cdc.gov/places/")
