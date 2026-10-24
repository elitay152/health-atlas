"""Opt-in CMS directory, literal search, public details and safe CSV exports."""
from datetime import datetime, timezone
import logging

import duckdb
import pandas as pd
import requests
import streamlit as st

from oregon_health.regions import OREGON, get_region
from oregon_health import hospitals
from oregon_health.api_cache import RefreshLimited
from oregon_health.profile import county_names
from oregon_health.status import last_refresh
from oregon_health.hospital_locations_ui import LABELS, render_locations

UNKNOWN = "__unresolved__"


def emergency_label(value):
    return "Not reported" if value is None else "Yes" if value else "No"


def filter_records(records, query="", county=None, types=(), *, region=OREGON):
    """Literal, case-insensitive search; values never become SQL or regex."""
    region = get_region(region)
    if county not in (None, UNKNOWN) and county not in county_names(region):
        raise ValueError(f"Unknown {region.name} county")
    query = query.strip().casefold()
    return [r for r in records
            if (county is None or (r["county_fips"] is None if county == UNKNOWN else r["county_fips"] == county))
            and (not types or (r["hospital_type"] or "Not reported") in types)
            and (not query or query in " ".join(str(r.get(k) or "") for k in (
                "facility_id", "name", "address", "city", "zip_code", "county", "hospital_type")).casefold())]


def export_csv(records, provenance, locations=None, location_provenance=None):
    """Retain source fields/provenance and neutralize spreadsheet formulas."""
    columns = [*hospitals.COLUMNS, "emergency_status", "coordinate_status",
               "dataset", "source_url", "released", "modified", "fetched_at", "sha256",
               "longitude", "latitude", "location_status", "matched_address",
               "location_source", "location_benchmark", "location_benchmark_id", "location_fetched_at", "location_address_hash", "location_state_fips", "location_boundary_sha256", "location_fingerprint"]
    def location_fields(record):
        location = (locations or {}).get(record["facility_id"], {})
        return {**{k: location.get(k) for k in ("longitude", "latitude", "matched_address")},
                "location_status": location.get("location_status", "not_geocoded"),
                "location_address_hash": location.get("address_hash"),
                **{f"location_{name}": (location_provenance or {}).get(key) for name, key in (
                    ("source", "source_url"), ("benchmark", "benchmark"), ("benchmark_id", "benchmark_id"), ("fetched_at", "fetched_at"), ("state_fips", "state_fips"),
                    ("boundary_sha256", "boundary_sha256"), ("fingerprint", "fingerprint"))}}
    rows = [{**r, **location_fields(r), "emergency_status": emergency_label(r["emergency_services"]),
             "coordinate_status": "Not supplied by CMS",
             **{key: provenance[key] for key in columns if key in provenance}} for r in records]
    frame = pd.DataFrame(rows, columns=columns)
    def safe(value):
        if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@"))
                                       or value.startswith(("\t", "\r", "\n"))):
            return "'" + value
        return value
    return frame.map(safe).to_csv(index=False)


def _open_profile(fips):
    from oregon_health.navigation import go
    st.session_state["profile_county"] = fips
    go("explore", county=fips)


def _detail(record, context_available, location=None, *, region=OREGON):
    st.markdown("#### Facility details")
    st.text(record["name"])
    st.caption(f"CMS Facility ID: {record['facility_id']} · This identifier is not an NPI.")
    st.text("\n".join([record["address"] or "Street address not reported",
                         f"{record['city'] or 'City not reported'}, {record['state']} {record['zip_code'] or 'ZIP not reported'}"]))
    st.text(f"County: {record['county'] or 'Not reported'}")
    st.text(f"Type: {record['hospital_type'] or 'Not reported'}")
    st.text(f"Ownership: {record['ownership'] or 'Not reported'}")
    st.text(f"Emergency services: {emergency_label(record['emergency_services'])}")
    if location:
        st.caption(f"Address location: {LABELS[location['location_status']]}")
        if location["location_status"] == "matched_exact":
            st.text(f"Matched address: {location['matched_address']}")
            st.caption(f"Approximate point: {location['latitude']:.5f}, {location['longitude']:.5f}. "
                       "Census address interpolation, not a verified building or entrance.")
    elif "hospital_locations" in region.supported_sources:
        st.caption("CMS supplies no coordinates. Enable approximate hospital locations to request Census address matches.")
    else:
        st.caption(f"CMS supplies no coordinates. Approximate address locations are unavailable for {region.name}; no points are mapped.")
    fips = record["county_fips"]
    if fips in county_names(region) and context_available:
        st.button(f"View {county_names(region)[fips]} County profile", key="hospital_open_county",
                  on_click=_open_profile, args=(fips,))
        st.caption("County estimates describe residents, not patients of this hospital. The hospital release and health estimate year may differ.")
    elif fips in county_names(region):
        st.info("County profiles need CDC health data. Refresh the health source to explore community context.")
    else:
        st.info("This facility's county could not be linked to the selected state’s community context.")


def render_hospitals(demo=False, context_available=True, *, region=OREGON, controls=None):
    controls=controls if controls is not None else st.sidebar
    region = get_region(region)
    region.require("hospitals")
    region_options = {} if region == OREGON else {"region": region}
    st.divider()
    st.subheader("Find a hospital", anchor="hospital-directory")
    st.caption(f"Explore CMS-listed {region.name} hospitals and connect their counties to community health context.")
    if demo:
        # Do not mix a previously enabled real directory into synthetic sessions.
        st.session_state["show_hospitals"] = False
    controls.subheader("Hospitals")
    enabled = controls.checkbox("Show hospital directory", key="show_hospitals", disabled=demo,
                          help="Loads CMS public hospital records separately from health estimates. No API key is needed.")
    if demo:
        st.info("Hospital listings are available in real-data mode. No real facility records are mixed into the synthetic demo.")
        return
    if not enabled:
        st.caption("Load the directory when you need it. The health map does not wait for CMS.")
        return
    path = hospitals.hospital_path(region)
    retry = st.button("Refresh hospital directory", key="hospital_refresh")
    attempt_key = f"hospital_attempt:{path}"
    try:
        if retry or (not st.session_state.get(attempt_key) and hospitals.needs_refresh(path, **region_options)):
            st.session_state[attempt_key] = True
            try:
                with st.spinner(f"Loading {region.name} hospitals from CMS…"):
                    hospitals.refresh(path, force=retry, **region_options)
                st.session_state.pop(attempt_key, None)
            except RefreshLimited as exc:
                st.info(str(exc))
            except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
                logging.getLogger(__name__).exception("CMS hospital refresh failed")
                st.warning("CMS could not be refreshed. Any previously downloaded hospital records are retained.")
        if not path.exists():
            st.info("Hospital listings are currently unavailable. Try refreshing after five minutes. Health estimates remain available.")
            return
        report = hospitals.coverage(path, **region_options)
        records = hospitals.directory(path=path, **region_options)
    except (ValueError, KeyError, TypeError, OSError, duckdb.Error):
        logging.getLogger(__name__).exception("CMS hospital snapshot unavailable")
        st.info("Hospital listings are currently unavailable. Health estimates remain available.")
        return

    provenance = report["provenance"]
    event = last_refresh(path, hospitals.DATASET)
    if (event and event["outcome"] == "failed"
            and datetime.fromisoformat(event["checked_at"]) >= provenance["fetched_at"]):
        st.warning("Showing the previous hospital snapshot after a failed refresh. Check the source dates below.")
    age = datetime.now(timezone.utc) - provenance["fetched_at"]
    if age >= hospitals.CACHE_TTL:
        st.warning("Hospital refresh is due. These are previously downloaded records, not a live facility inventory.")
    represented = sum(c["listed_hospitals"] > 0 for c in report["counties"])
    cards = st.columns(3)
    cards[0].metric(f"{region.name} hospitals listed", report["records"])
    cards[1].metric("Counties represented", f"{represented} / {region.county_count}")
    cards[2].metric("Reported emergency services", sum(r["emergency_services"] is True for r in records))
    st.caption(f"[CMS Hospital General Information]({hospitals.SOURCE_URL}) · Released {provenance['released']} · "
               f"Modified {provenance['modified']} · Fetched {provenance['fetched_at']:%Y-%m-%d %H:%M UTC}")
    with st.expander("Coverage & interpretation"):
        st.write("This directory covers hospitals listed by CMS, not every clinic or facility campus. No listed hospitals in a county does not mean no healthcare services. Listings and emergency-services status do not establish current capacity, specialty availability, unmet demand or purchase intent.")
        st.caption(f"Unresolved county links: {report['unresolved_counties']} · Incomplete addresses: {report['missing_address']} · "
                   f"Emergency services not reported: {report['unknown_emergency_services']}. "
                   "CMS supplies no coordinates; optional Census address matches are separate. Facility IDs are source strings, not NPIs.")
        st.dataframe(pd.DataFrame(report["counties"]).rename(columns={
            "fips": "County FIPS", "county": "County", "listed_hospitals": "CMS-listed hospitals"}), hide_index=True)
        st.caption("Source refreshes are typically quarterly and may change. The app reuses downloads for 24 hours; refresh attempts share a five-minute limit. CMS public reporting data are in the public domain; no government endorsement is implied.")

    names = county_names(region)
    county_options = [None, *names, UNKNOWN]
    county_label = lambda value: f"All {region.name} counties" if value is None else "Unresolved county" if value == UNKNOWN else f"{names[value]} County"
    search_col = county_col = controls
    query = search_col.text_input("Search hospitals", key="hospital_query", max_chars=120,
                                  placeholder="Name, city, ZIP, address or facility ID")
    county = county_col.selectbox("Hospital county", county_options, format_func=county_label, key="hospital_county")
    available_types = sorted({r["hospital_type"] or "Not reported" for r in records})
    if "hospital_types" in st.session_state:
        st.session_state["hospital_types"] = [t for t in st.session_state["hospital_types"] if t in available_types]
    types = controls.multiselect("Hospital types", available_types,
                           key="hospital_types", placeholder="All hospital types")
    filtered = filter_records(records, query, county, types, region=region)
    st.caption(f"{len(filtered)} of {len(records)} listed hospitals match your filters.")
    if not filtered:
        st.info("No listed hospitals match these filters. Try another county or clear the search and type filters. This does not establish an absence of healthcare services.")
        return
    if "hospital_locations" in region.supported_sources:
        locations, location_provenance = render_locations(records, filtered, controls=controls, **region_options)
    else:
        locations, location_provenance = {}, None
        st.info(f"CMS supplies no coordinates. Address locations are unavailable for {region.name}. Listings remain searchable below.")
    table_col, detail_col = st.columns([1.5, 1], gap="large")
    with table_col:
        frame = pd.DataFrame([{
            "Facility ID": r["facility_id"], "Hospital": r["name"], "City": r["city"] or "Not reported",
            "County": r["county"] or "Not reported", "Type": r["hospital_type"] or "Not reported",
            "Emergency services": emergency_label(r["emergency_services"]),
        } for r in filtered])
        st.dataframe(frame, hide_index=True, width="stretch", key="hospital_results",
                     column_config={"Facility ID": st.column_config.TextColumn("Facility ID")})
        st.download_button("Download filtered hospital CSV", export_csv(filtered, provenance, locations, location_provenance),
                           f"health-atlas-{region.name.lower()}-hospitals.csv", "text/csv", key="hospital_download")
        st.caption("Download includes source fields, release dates and fetch provenance. Spreadsheet formulas are escaped; import ID and ZIP columns as text to retain leading zeros.")
    with detail_col:
        by_id = {r["facility_id"]: r for r in filtered}
        if st.session_state.get("hospital_detail") not in by_id:
            st.session_state["hospital_detail"] = None
        selected = st.selectbox("Choose a hospital for details", list(by_id), index=None,
                                format_func=lambda value: f"{by_id[value]['name']} · {value}",
                                key="hospital_detail", placeholder="Select a listed hospital")
        if selected:
            with st.container(border=True):
                _detail(by_id[selected], context_available, locations.get(selected), region=region)
        else:
            st.info("Choose a hospital to see its reported attributes and county context.")
