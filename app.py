import os
from pathlib import Path

import duckdb
import streamlit as st
import requests

from oregon_health.api_cache import DEFAULT_DATASET, needs_refresh, refresh

from oregon_health.data import DB_PATH, MEASURES, comparison, demo_rows, store

st.set_page_config(page_title="Health Atlas", page_icon="🌲", layout="wide")
st.title("Health Atlas")
st.write("Explore the health of Oregon’s communities.")
use_demo = st.sidebar.toggle("Use synthetic demo data", value=False)
with st.sidebar.expander("CDC data source"):
    dataset = st.text_input("County GIS-friendly dataset ID", value=os.environ.get("CDC_PLACES_DATASET", DEFAULT_DATASET))
    st.caption("Use a CDC PLACES county GIS-friendly release ID. The downloaded release title appears below the chart.")
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
        except (requests.RequestException, ValueError, KeyError, TypeError, duckdb.Error) as exc:
            st.error(f"Could not refresh CDC data: {exc}")
            if DB_PATH.exists():
                st.warning("Showing previously downloaded data. Check its source and import date below; it may be older or from another release.")
    st.sidebar.caption("CDC data is cached locally for 24 hours. Refresh data checks CDC immediately. No API key required.")
if use_demo:
    st.warning("DEMO — synthetic values for three counties. These are not CDC estimates and must not be used for health decisions.")
    demo_path = Path(__file__).resolve().parent / "data" / "demo.duckdb"
    if not demo_path.exists():
        store(demo_rows(), "Synthetic demo fixture — not CDC data", demo_path)
    path = demo_path
else:
    path = DB_PATH
    if not path.exists():
        st.info("No CDC data has been downloaded yet. Check network access and the dataset ID, then click Refresh data. You can also enable the clearly labeled synthetic demo.")
        st.stop()

with duckdb.connect(str(path), read_only=True) as db:
    measure = st.sidebar.selectbox("Health measure", list(MEASURES), format_func=MEASURES.get)
    years = [row[0] for row in db.execute("SELECT DISTINCT year FROM estimates WHERE measure = ? ORDER BY year DESC", [measure]).fetchall()]
    year = st.sidebar.selectbox("Estimate year", years)
    frame = comparison(db, measure, year)
    selected = st.multiselect("Counties to compare", sorted(frame.county), default=sorted(frame.county)[:3])
    visible = frame[frame.county.isin(selected)]
    st.subheader(f"{MEASURES[measure]} · {year}")
    st.caption(f"{len(frame)} counties available. Crude prevalence: estimated percentage of adults. The estimate year can differ from the release year.")
    if visible.empty:
        st.info("Select at least one county to display a comparison.")
    else:
        st.bar_chart(visible.set_index("county")[["prevalence"]], y_label="Estimated prevalence (%)")
        st.dataframe(visible.rename(columns={"county": "County", "prevalence": "Estimate (%)", "low": "95% CI lower (%)", "high": "95% CI upper (%)"}), hide_index=True)
        st.download_button("Download comparison CSV", visible.to_csv(index=False), "oregon-comparison.csv", "text/csv")
    source, imported = db.execute("SELECT source, imported_at FROM provenance").fetchone()
    st.caption(f"Source: {source} · Imported: {imported} UTC")
st.info("PLACES estimates are modeled population measures, not individual diagnoses. Crude estimates reflect differences in age composition; use caution comparing counties. Differences and associations do not establish causes. Consult confidence intervals and CDC methodology before interpreting results.")
st.link_button("CDC PLACES data and methodology", "https://www.cdc.gov/places/")
