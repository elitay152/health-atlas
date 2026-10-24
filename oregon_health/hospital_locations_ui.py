"""Opt-in approximate address map, independent of health and directory imports."""
from collections import Counter
from functools import partial
from datetime import datetime, timezone
import logging

import duckdb
import pandas as pd
import requests
import streamlit as st

from oregon_health.regions import OREGON, get_region
from oregon_health import geocoding
from oregon_health.api_cache import RefreshLimited
from oregon_health.map import hospital_deck, selected_hospital
from oregon_health.status import last_refresh

LABELS = {
    "matched_exact": "Exact address match — approximate point",
    "unmatched": "No address match", "ambiguous": "Ambiguous address match",
    "non_exact": "Non-exact match withheld", "invalid_coordinates": "Invalid coordinates withheld",
    "county_mismatch": "County check failed — needs review", "county_unresolved": "Source county unresolved",
    "missing_match_address": "Matched address missing", "missing_address": "Incomplete source address",
    "address_changed": "Source address changed — new match needed", "not_geocoded": "Not geocoded",
}


def _open_hospital(ids):
    facility_id = selected_hospital(st.session_state.get("hospital_location_map"), ids)
    if facility_id:
        st.session_state["hospital_detail"] = facility_id


def render_locations(records, filtered, *, region=OREGON):
    region = get_region(region)
    region.require("hospital_locations")
    region_options = {} if region == OREGON else {"region": region}
    if not st.checkbox("Show approximate hospital locations", key="show_hospital_locations",
                       help="Sends the directory's published hospital addresses to the Census Geocoder. No search text or patient data is sent."):
        return {}, None
    st.caption("Address matches are approximate street locations, not verified buildings, entrances or travel-time estimates. Only exact, county-consistent matches are mapped; other records remain in the directory.")
    path = geocoding.location_path(region)
    retry = st.button("Refresh address matches", key="geocode_refresh")
    attempt_key = f"geocode_attempt:{path}:{geocoding.fingerprint(records)}"
    try:
        if retry or (not st.session_state.get(attempt_key) and geocoding.needs_refresh(records, path, **region_options)):
            st.session_state[attempt_key] = True
            try:
                with st.spinner("Matching public hospital addresses with Census…"):
                    geocoding.refresh(records, path, force=retry, **region_options)
                st.session_state.pop(attempt_key, None)
            except RefreshLimited as exc:
                st.info(str(exc))
            except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
                logging.getLogger(__name__).exception("Hospital geocoding failed")
                st.warning("Address matching could not be refreshed. The hospital directory and health estimates remain available.")
        locations, provenance = geocoding.snapshot(records, path, **region_options)
    except (ValueError, KeyError, TypeError, OSError, duckdb.Error):
        logging.getLogger(__name__).exception("Hospital location snapshot unavailable")
        st.info("Hospital locations are unavailable. The directory remains usable.")
        return {}, None
    if provenance:
        event = last_refresh(path, "xubh-q36u")
        failed = event and event["outcome"] == "failed" and datetime.fromisoformat(event["checked_at"]) >= provenance["fetched_at"]
        stale = datetime.now(timezone.utc) - provenance["fetched_at"] >= geocoding.CACHE_TTL
        if failed or stale:
            st.warning("Showing previously matched addresses. The match cache is due for refresh or its latest refresh failed. Only unchanged source addresses retain points.")
        st.caption(f"[Census Geocoder]({geocoding.SOURCE_URL}) · Benchmark {provenance['benchmark']} "
                   f"(ID {provenance['benchmark_id'] or 'not requested'}) · Matched {provenance['fetched_at']:%Y-%m-%d %H:%M UTC} · Cached for 30 days.")
    visible = [{**r, **locations[r["facility_id"]]} for r in filtered]
    points = [r for r in visible if r["location_status"] == "matched_exact"]
    st.caption(f"{len(points)} of {len(visible)} filtered hospitals have mapped address matches. Copper: address match · Teal: selected hospital.")
    if points:
        st.pydeck_chart(hospital_deck(points, st.session_state.get("hospital_detail"), **region_options), height=420,
                        key="hospital_location_map",
                        on_select=partial(_open_hospital, tuple(r["facility_id"] for r in filtered)),
                        selection_mode="single-object",
                        alt=f"Approximate Census address matches over {region.name} county boundaries. Use the hospital details selector as a keyboard-accessible alternative.")
        st.caption("Click a point to open its hospital details below. County boundaries are generalized; markers do not define service areas.")
    else:
        st.info("No accepted address matches for these filters. Unmatched records remain available in the table and details selector.")
    with st.expander("Address-match quality"):
        counts = Counter(r["location_status"] for r in locations.values())
        st.dataframe(pd.DataFrame([{"Match status": LABELS[status], "Listed hospitals": count}
                                   for status, count in sorted(counts.items())]), hide_index=True)
        st.caption("Quality counts cover the full loaded directory, not just the filters. Census street-address matching may interpolate along a road. An Exact match is not a verified campus location. Non-exact, tied, out-of-range, county-inconsistent and changed-address matches are withheld. Border matches may need review because county geometry is generalized. No ZIP/county centroids or fuzzy name matches are used.")
    return locations, provenance
