"""Opt-in county-to-tract exploration; source failures leave county views usable."""
from datetime import datetime, timezone
from functools import partial
import logging
import math

import duckdb
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

from oregon_health import tracts, tract_boundaries
from oregon_health.api_cache import RefreshLimited
from oregon_health.data import MEASURES
from oregon_health.links import parse_tract
from oregon_health.interpretation import render_interpretation
from oregon_health.map import LOW, HIGH, MISSING
from oregon_health.profile import county_names
from oregon_health.status import last_refresh
from oregon_health.regions import OREGON, get_region

LAYER = 'oregon-tracts'
# At Oregon/Washington latitudes, zoom <= 8 (512-pixel Web Mercator world) is
# more conservative than 1:500,000 at nominal 96 CSS pixels per inch.
# The controller is disabled: users select features, not street-level zoom.
MAX_ZOOM = 8.0


def _points(collection):
    for feature in collection['features']:
        geo = feature['geometry']
        for polygon in [geo['coordinates']] if geo['type'] == 'Polygon' else geo['coordinates']:
            for ring in polygon:
                yield from ring


def overview(collection):
    points = list(_points(collection))
    if not points:
        raise ValueError('No tract geometry for this county')
    xs = [(p[0] + 180) / 360 for p in points]
    ys = [(1 - math.asinh(math.tan(math.radians(p[1]))) / math.pi) / 2 for p in points]
    x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    # Fit a narrow mobile viewport as well as desktop; larger screens get margin.
    zoom = min(MAX_ZOOM, math.log2(280 / (512 * max(max(xs)-min(xs), 1e-8))),
               math.log2(340 / (512 * max(max(ys)-min(ys), 1e-8))))
    return pdk.ViewState(longitude=x * 360 - 180,
                         latitude=math.degrees(math.atan(math.sinh(math.pi * (1-2*y)))),
                         zoom=zoom, min_zoom=zoom, max_zoom=zoom)


def tract_deck(collection, selected=None,*,region=OREGON):
    region=get_region(region)
    values = [f['properties']['prevalence'] for f in collection['features']
              if f['properties']['prevalence'] is not None]
    lower, upper = (min(values), max(values)) if values else (0, 0)
    for feature in collection['features']:
        p = feature['properties']
        value = p['prevalence']
        if value is None:
            color = MISSING
        else:
            ratio = (value-lower)/(upper-lower) if upper > lower else .5
            color = [round(a+ratio*(b-a)) for a,b in zip(LOW,HIGH)] + [255]
        p.update(color=color, outline=[184,106,64,255] if p['geoid'] == selected else [250,249,244,220],
                 line_width=3 if p['geoid'] == selected else 1,
                 estimate='No estimate available' if value is None else f'{value:.1f}%',
                 interval=_interval(p['low'],p['high']))
    # None lets Streamlit inject a themed base map with independent zoom buttons.
    # The Streamlit no-style sentinel suppresses both controls and external tiles.
    return pdk.Deck(layers=[pdk.Layer('GeoJsonLayer',collection,id=LAYER if region==OREGON else 'washington-tracts',pickable=True,
        get_fill_color='properties.color',get_line_color='properties.outline',
        get_line_width='properties.line_width',line_width_units="'pixels'",
        line_width_min_pixels=1,line_width_max_pixels=3,auto_highlight=True)],
        views=[pdk.View(type='MapView',controller=False)], initial_view_state=overview(collection),
        map_provider=None,map_style="__MAP_STYLE__",
        tooltip={'text':'{name} · {geoid}\nPrevalence: {estimate}\n95% confidence interval: {interval}'})


def _interval(low, high):
    return 'Unavailable' if low is None or high is None else f'{low:.1f}%–{high:.1f}%'


def selected_tract(event, allowed,*,region=OREGON):
    region=get_region(region)
    if not isinstance(event, dict):
        return None
    selection = event.get('selection')
    objects = selection.get('objects') if isinstance(selection, dict) else None
    features = objects.get(LAYER if region==OREGON else 'washington-tracts') if isinstance(objects, dict) else None
    if not isinstance(features, list) or not features or not isinstance(features[0], dict):
        return None
    props = features[0].get('properties', features[0])
    geoid = props.get('geoid') if isinstance(props,dict) else None
    return geoid if isinstance(geoid,str) and geoid in allowed else None


def _select(allowed,region=OREGON):
    event = st.session_state.get('tract_map',{})
    geoid = selected_tract(event, allowed,region=region)
    selection = event.get('selection') if isinstance(event,dict) else None
    empty = isinstance(selection,dict) and selection.get('objects') == {}
    if geoid or empty:
        st.session_state['tract_selection'] = geoid


def _clear():
    """Exit tract mode without changing the county, condition or county year."""
    st.session_state['show_tracts'] = False
    st.session_state['tract_selection'] = None


def render_tracts(county, measure, county_year, demo=False,*,region=OREGON,controls=None):
    region=get_region(region)
    region.require('tract_health')
    controls=controls if controls is not None else st.sidebar
    options={} if region==OREGON else {'region':region}
    st.divider()
    st.subheader('A closer look: census tracts', anchor='tract-explorer')
    st.caption(f'{region.name} → County profile → Census tract · Smaller statistical areas, not postal ZIP codes.')
    if 'show_tracts' not in st.session_state:
        st.session_state['show_tracts'] = st.query_params.get('local') == '1' and county in county_names(region) and not demo
    if demo or county not in county_names(region):
        st.session_state['show_tracts'] = False
    enabled = controls.toggle('Explore census tracts in this county', key='show_tracts', disabled=demo or not county)
    result = dict(local=enabled, tract=None, tract_year=None)
    if demo:
        st.info('Tract exploration is available in real-data mode. No real tract records are loaded in this synthetic demo.')
        return result
    if not county:
        st.info(f'Choose a County profile in the sidebar, or click the {region.name} county map, to explore its tracts.')
        return result
    if not enabled:
        st.caption('Load local estimates when you need them. County exploration does not wait for tract data.')
        return result
    st.markdown(f'**{county_names(region)[county]} County / {MEASURES[measure]}**')
    source_panel = st.expander('Tract source & refresh')
    with source_panel:
        retry = st.button('Refresh tract estimates', key='tract_refresh')
        st.caption('Shared daily cache; one refresh attempt per session when due. Manual refresh uses the five-minute cooldown.')
    path = tracts.tract_path(region)
    attempt_key = f'tract_attempt:{path}'
    try:
        if retry or (not st.session_state.get(attempt_key) and tracts.needs_refresh(path,**options)):
            st.session_state[attempt_key] = True
            try:
                with st.spinner(f'Loading {region.name} tract estimates from CDC…'):
                    tracts.refresh(path,force=retry,**options)
                st.session_state.pop(attempt_key,None)
            except RefreshLimited as exc:
                st.info(str(exc))
            except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
                st.warning('CDC tract data could not be refreshed. Any previous snapshot is retained; county views remain available.')
        if not path.exists():
            st.info('Tract estimates are unavailable. Try Refresh tract estimates after five minutes. County views remain available.')
            return result
        report = tracts.coverage(path,**options)
        source = report['provenance']
        fetched = datetime.fromisoformat(source['fetched_at'])
        event = last_refresh(path,tracts.DATASET)
        if event and event['outcome']=='failed' and datetime.fromisoformat(event['checked_at']) >= fetched:
            st.warning('Showing the previous tract snapshot after a failed refresh. Check the fetch date below.')
        if datetime.now(timezone.utc)-fetched >= tracts.CACHE_TTL:
            st.warning('Tract refresh is due. Showing previously downloaded estimates.')
        with source_panel:
            st.caption(f"[CDC PLACES tract release {source['release_year']}]({source['source_url']}) · Fetched {fetched:%Y-%m-%d %H:%M UTC}")
        years = sorted({r['year'] for r in report['coverage'] if r['county_fips']==county and r['measure']==measure},reverse=True)
        scope = (county,measure)
        if st.session_state.get('tract_scope') != scope:
            first = 'tract_scope' not in st.session_state
            prior_scope = st.session_state.get('tract_scope')
            st.session_state['tract_scope'] = scope
            if prior_scope is None or prior_scope[0] != county:
                st.session_state['tract_selection'] = None
            requested = st.query_params.get('tract_year') if first else None
            desired = next((y for y in years if str(y)==requested),county_year)
            st.session_state['tract_year'] = desired if desired in years else years[0] if years else None
            if requested and requested not in {str(y) for y in years}:
                st.warning('The linked tract estimate year is unavailable. Choose an available year below.')
        if years:
            if st.session_state.get('tract_year') not in years:
                st.session_state['tract_year'] = years[0]
            year = controls.selectbox('Tract estimate year',years,key='tract_year')
            result['tract_year'] = year
            rows = tracts.estimates(county,measure,year,path,**options)
        else:
            year, rows = None, []
            st.info('No saved tract estimates for this county and measure. Boundaries remain visible with unavailable values.')
        if year != county_year and year is not None:
            st.info(f'Tract estimates use {year}; the county view uses {county_year}. These periods are separate.')
        collection, joined = tract_boundaries.join_estimates(rows,county,
            estimate_boundary_year=source['recommended_boundary_year'],**options)
    except (ValueError, KeyError, TypeError, OSError, duckdb.Error):
        logging.getLogger(__name__).warning('Tract snapshot or boundaries could not be read')
        st.info('Tract exploration is currently unavailable. County views remain available; try refreshing the tract source.')
        return result
    features = {f['id']:f for f in collection['features']}
    if 'tract_link_initialized' not in st.session_state:
        st.session_state['tract_link_initialized'] = True
        requested, invalid = parse_tract(st.query_params.get('tract'), county,region=region)
        if invalid or (requested and requested not in features):
            st.warning('The linked tract is invalid, belongs to another county or has no compatible boundary. Choose a tract below.')
        elif requested:
            st.session_state['tract_selection'] = requested
    if st.session_state.get('tract_selection') not in features:
        st.session_state['tract_selection'] = None
    available = sum(f['properties']['prevalence'] is not None for f in features.values())
    st.write(f'**{available} of {len(features)} tracts with estimates** · {len(features)-available} unavailable')
    st.caption(f"Estimate year: {year or 'unavailable'} · Adult crude prevalence · 95% confidence intervals · Census boundaries: 2023")
    st.caption("These crude rates reflect age differences. Read the intervals; a tract estimate does not describe individual patients or establish healthcare demand.")
    if joined['estimates_without_boundary']:
        st.warning(f"{len(joined['estimates_without_boundary'])} saved tract records have no compatible boundary and are excluded from this map.")
    select = controls.selectbox('Census tract',list(features),index=None,key='tract_selection',
        placeholder='Choose a tract or click the map',format_func=lambda code:f"{features[code]['properties']['name']} · {code}")
    st.button('Back to county overview',on_click=_clear,key='tract_back')
    map_column, detail_column = st.columns([1.6, 1], gap='large')
    with map_column:
        st.pydeck_chart(tract_deck(collection,select,region=region),height=420,key='tract_map',
            on_select=partial(_select,frozenset(features),region),selection_mode='single-object',
            alt=f"Overview of {county_names(region)[county]} County census tracts. Use the Census tract selector and table for keyboard access.")
        st.caption('Teal: lower → higher estimates within this county and year · Gray: unavailable · Copper outline: selected tract. Colors are not comparable across counties or years.')
        st.caption('Fixed overview: click or hover to explore. U.S. Census Bureau cartographic boundaries, 1:500,000; zoom is disabled to respect the source display scale. Small tracts can be selected by name in the sidebar.')
    with detail_column:
        if select:
            p = features[select]['properties']
            st.markdown('#### Tract details')
            st.text(f"{p['name']} · GEOID {select}")
            st.metric(MEASURES[measure], 'Unavailable' if p['prevalence'] is None else f"{p['prevalence']:.1f}%")
            st.caption(f"95% confidence interval: {_interval(p['low'],p['high'])}")
            if p['estimate_status']=='no_record':
                st.info('No CDC row is saved for this boundary, measure and year. Missing is not zero; the source does not establish why this tract is absent.')
            if p['estimate_status']=='unavailable':
                st.info('A CDC row is saved, but its estimate is unavailable. Read any source footnote; only call it suppressed if the source says so. Missing is not zero.')
            elif p['prevalence'] is not None and (p['low'] is None or p['high'] is None):
                st.info('The estimate is available, but a complete 95% confidence interval is unavailable. This does not mean zero uncertainty.')
            record = next((r for r in rows if r['tract_fips']==select),None)
            if record:
                st.text(record['definition'])
                if record['footnote']:
                    st.text(f"Source footnote: {record['footnote']}")
            st.caption(f"{county_names(region)[county]} County · Estimate year {year or 'unavailable'} · Census tract geography, not an individual neighborhood or facility catchment.")
        else:
            st.info('Select a tract on the map or in the sidebar selector to open its estimate and confidence interval.')
    render_interpretation(small_area=True)
    with st.expander('All tracts & interpretation',expanded=False):
        st.dataframe(pd.DataFrame([{'Tract':f['properties']['name'],'GEOID':f['id'],
            'Estimate (%)':f['properties']['prevalence'],'95% CI lower (%)':f['properties']['low'],
            '95% CI upper (%)':f['properties']['high'],
            'Status':{'available':'Available','unavailable':'Unavailable (source row)','no_record':'No source row'}[f['properties']['estimate_status']]}
            for f in features.values()]),hide_index=True,key='tract_table')
        by_id={r['tract_fips']:r for r in rows}
        export=pd.DataFrame([dict(state=region.abbr,state_fips=region.state_fips,tract_fips=f['id'],
            county_fips=county,measure=measure,estimate_year=year,prevalence=f['properties']['prevalence'],
            low=f['properties']['low'],high=f['properties']['high'],status=f['properties']['estimate_status'],
            confidence=95,unit='percent',population_universe='Adults',estimate_type='Crude prevalence',
            source_url=source['source_url'],source_release=source['release_year'],fetched_at=source['fetched_at'],
            boundary_year=2023,boundary_sha256=tract_boundaries.provenance(region=region)['geojson_sha256'],
            footnote=by_id.get(f['id'],{}).get('footnote'),
            footnote_symbol=by_id.get(f['id'],{}).get('footnote_symbol'),
            definition=by_id.get(f['id'],{}).get('definition'),datasource=by_id.get(f['id'],{}).get('datasource')) for f in features.values()])
        def safe(value):
            return "'"+value if isinstance(value,str) and (value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n'))) else value
        st.download_button('Download tract estimates CSV',export.map(safe).to_csv(index=False),
            f'health-atlas-{region.name.lower()}-tracts.csv','text/csv',key='tract_download',on_click='ignore')
        st.caption('Modeled estimates describe adult residents, not individual patients, healthcare demand or intervention effects. Intervals are not a significance test. Do not average tract percentages to recreate a county estimate. Tracts, ZCTAs and postal ZIP codes are different geographies.')
        st.caption('Generalized Census geometry is for small-scale display, not address geocoding, area calculations, precise spatial relationships or service-area eligibility. Source: U.S. Census Bureau, 2023 cartographic boundaries.')
    st.caption('Copy the browser address to share this county, tract, measure and tract estimate year. Links use the current saved source release.')
    result['tract'] = select
    return result
