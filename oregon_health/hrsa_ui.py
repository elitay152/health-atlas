"""Separate, opt-in view of HRSA designation components and facility points."""
from datetime import datetime, timezone
from functools import partial
import logging

import duckdb
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

from oregon_health.regions import OREGON, get_region
from oregon_health import hrsa
from oregon_health.api_cache import RefreshLimited
from oregon_health.map import boundaries
from oregon_health.profile import county_names
from oregon_health.status import last_refresh

CATEGORIES = ["Geographic", "Population group", "Facility"]
COLORS = {"Geographic": [23,107,99,90], "Population group": [184,106,64,90], "Facility": [80,115,146,220]}


def filter_records(records, discipline, category, statuses, county=None, *, region=OREGON):
    region = get_region(region)
    if county not in (None,"__unresolved__") and county not in county_names(region):
        raise ValueError(f"Unknown {region.name} county")
    return [r for r in records if r["discipline"] == discipline and r["category"] == category
            and r["status"] in statuses and (county is None or (
                r["county_fips"] is None if county == "__unresolved__" else r["county_fips"] == county))]


def export_csv(records, provenance):
    rows=[{**{k:v for k,v in r.items() if k!="geometry"}, "has_geometry":r["geometry"] is not None,
           "state":r.get("state",provenance.get("state","OR")),
           "state_fips":r.get("state_fips",provenance.get("state_fips","41")),
           "source_url":provenance["service_url"], "fetched_at":provenance["fetched_at"],
           "generalization_degrees":provenance["generalization_degrees"]} for r in records]
    def safe(value):
        if isinstance(value,str) and (value.lstrip().startswith(("=","+","-","@")) or value.startswith(("\t","\r","\n"))):
            return "'"+value
        return value
    return pd.DataFrame(rows).map(safe).to_csv(index=False)


def selected_component(event, allowed):
    if not isinstance(event,dict):
        return None
    selection=event.get("selection")
    objects=selection.get("objects") if isinstance(selection,dict) else None
    features=objects.get("hrsa-designations") if isinstance(objects,dict) else None
    if not isinstance(features,list) or not features or not isinstance(features[0],dict):
        return None
    props=features[0].get("properties")
    key=props.get("feature_key") if isinstance(props,dict) else None
    return key if isinstance(key,str) and key in allowed else None


def _open_component(allowed):
    key=selected_component(st.session_state.get("hrsa_map"),allowed)
    if key:
        st.session_state["hrsa_detail"]=key


def designation_deck(records, category, selected=None, *, region=OREGON):
    region = get_region(region)
    features=[dict(type="Feature", geometry=r["geometry"], properties={
        "name":r["name"], "feature_key":r["feature_key"],
        "outline":[35,59,53,255] if r["feature_key"]==selected else COLORS[category][:3]+[200],
        "width":3 if r["feature_key"]==selected else 1, "detail":f"{r['discipline']} · {r['designation_type']} · {r['status']}\nHPSA ID: {r['designation_id']}"})
              for r in records if r["geometry"] is not None]
    return pdk.Deck(layers=[
        pdk.Layer("GeoJsonLayer",boundaries(region),id="hrsa-counties",pickable=False,filled=False,
                  get_line_color=[92,120,96,170],line_width_min_pixels=1),
        pdk.Layer("GeoJsonLayer",dict(type="FeatureCollection",features=features),id="hrsa-designations",
                  pickable=True,filled=True,stroked=True,get_fill_color=COLORS[category],
                  get_line_color="properties.outline",get_line_width="properties.width",
                  line_width_units="'pixels'",line_width_min_pixels=1,line_width_max_pixels=3,
                  get_point_radius=500,point_radius_min_pixels=5,point_radius_max_pixels=10),
    ],initial_view_state=pdk.ViewState(latitude=region.center[1],longitude=region.center[0],zoom=min(region.zoom, 4.5),min_zoom=4,max_zoom=12),
        map_provider=None,map_style=None,tooltip={"text":"{name}\n{detail}"})


def render_hrsa(demo=False, *, region=OREGON, controls=None):
    controls=controls if controls is not None else st.sidebar
    region = get_region(region)
    region.require("hrsa")
    region_options = {} if region == OREGON else {"region": region}
    st.divider()
    st.subheader("Health workforce shortage designations",anchor="shortage-designations")
    st.caption("Explore HRSA designations for primary care, dental health and mental health. Designations are administrative classifications, not health prevalence or a forecast of service demand.")
    if demo:
        st.session_state["show_hrsa"]=False
    controls.subheader("Shortage designations")
    enabled=controls.checkbox("Show HRSA shortage designations",key="show_hrsa",disabled=demo)
    if demo:
        st.info("Real HRSA designations are not loaded or mixed into the synthetic demo.")
        return
    if not enabled:
        return
    path=hrsa.hrsa_path(region)
    retry=st.button("Refresh HRSA designations",key="hrsa_refresh")
    attempt=f"hrsa_attempt:{path}"
    try:
        if retry or (not st.session_state.get(attempt) and hrsa.needs_refresh(path, **region_options)):
            st.session_state[attempt]=True
            try:
                with st.spinner(f"Loading {region.name} HRSA designations…"):
                    hrsa.refresh(path,force=retry, **region_options)
                st.session_state.pop(attempt,None)
            except RefreshLimited as exc:
                st.info(str(exc))
            except (requests.RequestException,ValueError,KeyError,TypeError,OSError,duckdb.Error):
                logging.getLogger(__name__).exception("HRSA refresh failed")
                st.warning("HRSA could not be refreshed. Any previous snapshot is retained; hospital and health data remain available.")
        if not path.exists():
            st.info("HRSA designations are unavailable. Retry after five minutes. Other sources remain usable.")
            return
        records,provenance=hrsa.snapshot(path, **region_options)
    except (ValueError,KeyError,TypeError,OSError,duckdb.Error):
        logging.getLogger(__name__).exception("HRSA snapshot unavailable")
        st.info("HRSA designations are unavailable. Other sources remain usable.")
        return
    fetched=datetime.fromisoformat(provenance["fetched_at"])
    if st.session_state.get("hrsa_snapshot")!=provenance["fetched_at"]:
        st.session_state["hrsa_detail"]=None
        st.session_state["hrsa_snapshot"]=provenance["fetched_at"]
    event=last_refresh(path,"hrsa-hpsa")
    if (datetime.now(timezone.utc)-fetched>=hrsa.CACHE_TTL or
            (event and event["outcome"]=="failed" and datetime.fromisoformat(event["checked_at"])>=fetched)):
        st.warning("Showing previously downloaded HRSA designations. Refresh is due or the latest refresh failed; check source dates.")
    dates=sorted({r["source_date"] for r in records})
    st.caption(f"{region.name} snapshot · [HRSA source]({hrsa.SOURCE_URL}) · Warehouse dates: {', '.join(dates)} · Fetched {fetched:%Y-%m-%d %H:%M UTC} · 24-hour cache.")
    st.caption(f"{len(records)} source components / facility points · "
               f"{sum(r['geometry'] is None for r in records)} without geometry · "
               f"{sum(r['county_fips'] is None for r in records)} unresolved county links across the full snapshot. "
               "These are source-record counts, not people or clinicians.")
    st.caption("The public GIS view is not a full historical inventory. In the verified snapshot it returns Designated and Proposed For Withdrawal records; withdrawn records also exist in HRSA's separate CSV downloads. Proposed withdrawal is a distinct source status, not a completed withdrawal.")
    discipline=controls.selectbox("Shortage discipline",list(hrsa.DISCIPLINES),key="hrsa_discipline")
    category=controls.selectbox("Designation category",CATEGORIES,key="hrsa_category")
    statuses=controls.multiselect("Designation statuses",list(hrsa.STATUSES.values()),default=["Designated"],key="hrsa_statuses")
    names=county_names(region)
    county=controls.selectbox("Designation component or facility county",[None,*names,"__unresolved__"],key="hrsa_county",
        format_func=lambda v:f"All {region.name} counties" if v is None else "Unresolved county" if v=="__unresolved__" else names[v])
    selected=filter_records(records,discipline,category,statuses,county, region=region)
    if category=="Population group":
        st.info("These boundaries identify designated population groups, not every resident in the shaded area. Read the reported population type in the details.")
    elif category=="Facility":
        st.info("These are HRSA facility designation points, not the CMS hospital directory. A designation does not establish current service capacity or eligibility for a particular program.")
    cards=st.columns(3)
    cards[0].metric("Distinct designations",len({r["designation_id"] for r in selected}))
    cards[1].metric("Components / facility points",len(selected))
    cards[2].metric("Records without geometry",sum(r["geometry"] is None for r in selected))
    if not selected:
        st.info("No source records match these filters. This does not establish an absence of workforce shortages.")
        return
    if any(r["geometry"] for r in selected):
        st.pydeck_chart(designation_deck(selected,category,st.session_state.get("hrsa_detail"), region=region),height=420,key="hrsa_map",
            on_select=partial(_open_component,tuple(r["feature_key"] for r in selected)),selection_mode="single-object",
            alt="HRSA designation components or facility points. The table and details selector provide a non-map alternative.")
        st.caption(f"{'Teal' if category=='Geographic' else 'Copper' if category=='Population group' else 'Blue'}: {category.lower()} designations matching the selected status. Hover for source details; click to open the record below. Dark outline: selected record. Overlapping components are not an intensity scale or composite score.")
    st.caption("HRSA geometry is generalized at 0.005° for display. Missing components remain in the table; do not infer full designation coverage from an incomplete map. County filters refer to source components/points, not whole-county shortage status. This map does not determine address-level eligibility.")
    frame=pd.DataFrame(selected)
    display=frame[["designation_id","name","designation_type","status","component_name","population","county_fips","updated"]].rename(columns={
        "designation_id":"HPSA ID","name":"Designation","designation_type":"Type","status":"Status",
        "component_name":"Component","population":"Population type","county_fips":"County FIPS","updated":"Last designation update"})
    st.dataframe(display.fillna("Not reported"),hide_index=True,width="stretch")
    options={r["feature_key"]:r for r in selected}
    if st.session_state.get("hrsa_detail") not in options:
        st.session_state["hrsa_detail"]=None
    choice=st.selectbox("Designation component details",list(options),index=None,key="hrsa_detail",
        format_func=lambda v:f"{options[v]['name']} · {options[v]['designation_id']} · {options[v]['component_name'] or 'Facility'}")
    if choice:
        r=options[choice]
        st.text("\n".join(f"{label}: {r[key] or 'Not reported'}" for label,key in [
            ("HPSA ID","designation_id"),("Designation","name"),("Type","designation_type"),("Discipline","discipline"),
            ("Status","status"),("Population","population"),("Component","component_name"),("Geography ID","geography_id"),
            ("Source county FIPS","source_county_fips"),("Designated","designated"),("Last updated","updated"),("Withdrawn","withdrawn")]))
    st.download_button("Download filtered designation records",export_csv(selected,provenance),
        "health-atlas-hrsa-designations.csv","text/csv",key="hrsa_download")
    with st.expander("Source coverage & interpretation"):
        st.dataframe(pd.DataFrame(provenance["layers"]),hide_index=True)
        st.caption("One designation may include multiple components and overlap other designations. Counts are source records or distinct HPSA IDs within a discipline, not people, clinicians or unmet demand. HRSA IDs are strings and may contain letters; they are not CMS facility IDs or NPIs. Historical geography may differ from current census areas; no tract-vintage join is inferred. Source layer/object IDs identify this snapshot's features, not a durable cross-release component identity. These public HRSA GIS services are documented for custom applications; attribution is retained and no government endorsement is implied.")
