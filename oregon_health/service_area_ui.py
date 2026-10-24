"""Local-only county area selection, revision-pinned links and bounded imports."""
from copy import deepcopy

import pydeck as pdk
import streamlit as st

from oregon_health.regions import OREGON, get_region
from oregon_health import service_areas
from oregon_health.map import boundaries
from oregon_health.profile import county_names
from oregon_health.service_area_summary_ui import render_summary
from oregon_health.service_area_compare_ui import render_comparison


def _activate():
    st.session_state['area_active'] = True


def _clear():
    st.session_state['area_counties'] = []
    _activate()


def area_deck(codes, *, region=OREGON):
    region = get_region(region)
    collection = deepcopy(boundaries(region))
    for f in collection['features']:
        p = f['properties']
        selected = p['fips'] in codes
        p.update(color=[23,107,99,220] if selected else [220,222,216,130],
                 outline=[184,106,64,255] if selected else [250,249,244,220],
                 membership='Included whole county' if selected else 'Not included')
    return pdk.Deck(layers=[pdk.Layer('GeoJsonLayer',collection,id='service-area-counties',
        pickable=True,get_fill_color='properties.color',get_line_color='properties.outline',
        line_width_min_pixels=1)],map_provider=None,map_style='__MAP_STYLE__',
        initial_view_state=pdk.ViewState(latitude=region.center[1],longitude=region.center[0],zoom=min(region.zoom, 4.5),min_zoom=4,max_zoom=8),
        tooltip={'text':'{name} County · {fips}\n{membership}'})


def render_service_area(demo=False, *, health_path=None, year=2023, region=OREGON, controls=None):
    controls=controls if controls is not None else st.sidebar
    region = get_region(region)
    region.require("service_areas")
    st.divider()
    st.subheader('Define a service area',anchor='service-area')
    st.caption(f'Choose whole {region.name} counties. This is an area you define, not a measured patient catchment or travel-time zone.')
    if demo:
        st.info('Service-area definitions are available in real-data mode, separate from synthetic health comparisons.')
        return {}
    names = county_names(region)
    if 'area_counties' not in st.session_state:
        try:
            linked = service_areas.parse_link(st.query_params.get('area'),st.query_params.get('area_rev'), region=region)
            st.session_state['area_counties'] = linked or []
            st.session_state['area_active'] = linked is not None
        except ValueError:
            st.session_state['area_counties'] = []
            st.session_state['area_active'] = False
            st.warning('The area link has invalid county IDs or an incompatible boundary revision. No area was loaded. Select counties to define a current area.')
    with st.expander('Load a saved area'):
        upload = st.file_uploader('Health Atlas area definition',type=['geojson','json'],
                                  max_upload_size=1,key='area_upload',
                                  help='Loads only a current Health Atlas whole-county definition, up to 250 KB. No addresses are needed.')
        if st.button('Load saved area',disabled=upload is None,key='area_load'):
            try:
                st.session_state['area_counties'] = service_areas.loads(upload.getvalue(), region=region)
                _activate()
                st.success('Saved county selection loaded.')
            except ValueError:
                st.warning('That file is not a compatible Health Atlas area definition. Boundary revisions, county IDs and polygons must match the current bundle. Your selection was retained.')
    codes = controls.multiselect('Counties in the service area',sorted(names,key=names.get),
        format_func=lambda c:f'{names[c]} County · {c}',key='area_counties',on_change=_activate)
    controls.button('Clear service area',key='area_clear',on_click=_clear,disabled=not codes)
    st.write(f'**{len(codes)} whole counties selected**')
    if not codes:
        st.info('The area is empty. Select counties in the sidebar; empty definitions can also be saved and shared.')
    st.pydeck_chart(area_deck(codes, region=region),height=380,key='service_area_map',
        alt=f'{region.name} county membership preview. Teal and copper identify included counties; use the Counties in the service area selector to change membership.')
    st.caption('Teal fill / copper outline: included whole counties · Gray: excluded. This map shows membership, not prevalence, supply or eligibility.')
    st.caption('Boundaries: pinned Plotly county dataset · Boundary vintage unverified · Generalized display polygons.')
    st.download_button('Download area definition (GeoJSON)',service_areas.dumps(codes, region=region),
        'health-atlas-service-area.geojson' if region == OREGON else f'health-atlas-{region.name.lower()}-service-area.geojson','application/geo+json',on_click='ignore')
    st.caption('Copy the browser address to share membership pinned to this boundary revision. The downloaded definition also embeds the selected display polygons and source metadata. It does not save health estimates.')
    with st.expander('Inclusion rules & boundary limits'):
        st.write('Each selected county is included once in full, even if its health data is unavailable. Duplicates are removed. Disconnected county sets are allowed. Comparison counties and the county spotlight do not change this area.')
        st.write('If two areas include the same county, that county belongs to both; their results must not be added without accounting for overlap. People can cross area borders for care, and a whole county can contain places outside an intended service reach. County membership alone does not establish where clients live or where services are delivered.')
        st.write('These generalized display polygons come from the pinned Plotly county dataset under MIT terms. Their boundary vintage is unverified. A revised bundle requires review and redefinition; links and imported files do not silently switch boundaries. No area/perimeter, point-in-polygon eligibility or travel-time analysis is performed.')
        st.caption('No patient/client addresses, uploaded address lists, geocoding or drive-time service are used. This definition starts with county IDs; tract-based analytical areas require separately suitable geography.')
        st.link_button('Boundary source & MIT attribution',service_areas.SOURCE)
    if 'service_area_summary' in region.supported_sources:
        render_summary(codes,year,health_path,region=region,controls=controls)
    else:
        st.info(f'Service-area summaries are not available for {region.name}.')
    if 'service_area_compare' not in region.supported_sources:
        st.info(f'Area comparisons are not available for {region.name}.')
    compared = render_comparison(codes,year,health_path,region=region,controls=controls) if 'service_area_compare' in region.supported_sources else {}
    params=service_areas.link_params(codes, region=region) if st.session_state.get('area_active') else {}
    # Include an explicit empty A when sharing an unfinished comparison.
    if compared:
        params=service_areas.link_params(codes, region=region)
    return params | compared
