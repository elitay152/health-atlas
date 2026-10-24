"""Shared shell: render one analytical task per rerun."""
import logging
import os
from pathlib import Path

import duckdb
import requests
import streamlit as st

from oregon_health import navigation
from oregon_health.api_cache import DEFAULT_DATASET, RefreshLimited, catalog_is_current, needs_refresh, refresh
from oregon_health.data import DB_PATH, demo_rows, store
from oregon_health.county_ui import render_health
from oregon_health.hospital_ui import render_hospitals
from oregon_health.hrsa_ui import render_hrsa
from oregon_health.interpretation import render_interpretation
from oregon_health.service_area_ui import render_service_area
from oregon_health.status_ui import render_data_status
from oregon_health.regions import OREGON, REGIONS, get_region, health_path, check_identity

st.set_page_config(page_title='Health Atlas', page_icon='🌲', layout='wide')
try:
    region = get_region(st.query_params.get('state', 'OR'))
except ValueError:
    st.info('That region is not supported. Choose Oregon or Washington.')
    st.link_button('Open Oregon explorer', '?')
    st.stop()
if not region.deployed:
    st.info(f'{region.name} is not available in this deployment.')
    st.stop()
view, invalid_view = navigation.begin(dict(st.query_params), region)


def switch_region():
    code = st.session_state['region_selector']
    params = {'view': st.session_state['view_selector']}
    if code != 'OR':
        params['state'] = code
    st.query_params.from_dict(params)
    st.session_state.clear()


st.sidebar.title('Health Atlas')
st.sidebar.radio('State', [r.abbr for r in REGIONS.values() if r.deployed],
    index=[r.abbr for r in REGIONS.values() if r.deployed].index(region.abbr),
    format_func=lambda code: get_region(code).name, key='region_selector', on_change=switch_region, horizontal=True)
st.sidebar.radio('View', list(navigation.VIEWS), format_func=navigation.VIEWS.get,
                 key='view_selector', on_change=navigation.choose_view)
st.sidebar.divider()
filter_panel = st.sidebar.container()
if 'demo_mode' not in st.session_state:
    st.session_state['demo_mode'] = st.query_params.get('demo') == '1' and region == OREGON
    if st.query_params.get('demo') == '1' and region != OREGON:
        st.warning('The synthetic demo is available for Oregon only. This Washington view uses real data.')
use_demo = st.sidebar.toggle('Use synthetic demo data', key='demo_mode', disabled=region != OREGON)
st.caption(f'HEALTH ATLAS / {region.name.upper()} / COMMUNITY HEALTH' + (' / SYNTHETIC DEMO' if use_demo else ''))
st.title(navigation.VIEWS[view])
if invalid_view:
    st.warning('That view is not supported. Showing Explore counties.')

region_options = {} if region == OREGON else {'region': region}
if region != OREGON:
    DB_PATH = health_path(region, DB_PATH.parent)
dataset = os.environ.get('CDC_PLACES_DATASET', DEFAULT_DATASET)
health_view = view in {'explore', 'compare'}
refresh_clicked = False
if health_view or view == 'sources':
    st.sidebar.divider()
    refresh_clicked = st.sidebar.button('Refresh data', disabled=use_demo,
        help='Refresh the configured CDC release. Cached for 24 hours; attempts share a five-minute limit.')
    if view == 'sources':
        st.text(f'Dataset: {dataset}')
        st.caption('The maintainer chooses the CDC release. Cached for 24 hours; refresh attempts share a five-minute limit.')

unreadable = False
if DB_PATH.exists():
    try:
        with duckdb.connect(str(DB_PATH), read_only=True) as cached:
            check_identity(cached, region)
            cached.execute('SELECT fips, county, measure, year, prevalence, low, high FROM estimates LIMIT 0')
            cached.execute('SELECT source, imported_at FROM provenance').fetchone()
    except (duckdb.Error, ValueError, OSError):
        unreadable = True
        if health_view or view == 'sources':
            st.error('The saved county health snapshot could not be read. Ask the maintainer to repair or replace it. Independent data panels remain available.')

if not use_demo and not unreadable and (health_view or refresh_clicked):
    attempt_key = f'cdc_attempt:{region.abbr}:{dataset}'
    try:
        health_options = {} if region == OREGON else {'path': DB_PATH, **region_options}
        if refresh_clicked or (not st.session_state.get(attempt_key) and needs_refresh(dataset, **health_options)):
            st.session_state[attempt_key] = True
            with st.spinner(f'Fetching {region.name} estimates from CDC…'):
                count = refresh(dataset, **health_options)
            st.session_state.pop(attempt_key, None)
            st.success(f'Downloaded {count} {region.name} estimates from CDC.')
    except RefreshLimited as exc:
        st.info(str(exc))
    except (requests.RequestException, ValueError, KeyError, TypeError, OSError, duckdb.Error):
        logging.getLogger(__name__).exception('CDC refresh failed')
        st.error('Could not refresh CDC data. Please try again later. Technical details are available in the server logs.')
        if DB_PATH.exists():
            st.warning('Showing previously downloaded data. Check its source and import date; it may be older or from another release.')

path = DB_PATH
if use_demo:
    st.warning('DEMO — synthetic values for three counties. These are not CDC estimates and must not be used for health decisions.')
    if health_view or view == 'sources':
        path = Path(__file__).resolve().parent / 'data' / 'demo.duckdb'
        if not catalog_is_current(path):
            store(demo_rows(), 'Synthetic demo fixture — not CDC data', path)

updates = {}
replace = set()
if health_view:
    if use_demo or (path.exists() and not unreadable):
        updates = render_health(path, region, view, use_demo, dataset,controls=filter_panel)
        replace = navigation.HEALTH_KEYS | navigation.LOCAL_KEYS
    else:
        st.info('No CDC data is available for this view. Try Refresh data after five minutes, or ask the maintainer to check the saved snapshot and data source.')
        st.button('Open healthcare access', on_click=navigation.go, args=('access',))
        st.button('Define a service area', on_click=navigation.go, args=('areas',))
elif view == 'areas':
    st.caption('Define whole-county membership, then explore saved-source summaries and comparisons.')
    years = list(range(2024, 2014, -1))
    if 'estimate_year' not in st.session_state:
        raw_year = st.query_params.get('year')
        st.session_state['estimate_year'] = next((y for y in years if str(y) == raw_year), 2023)
    if st.session_state['estimate_year'] not in years:
        st.session_state['estimate_year'] = 2023
    filter_panel.subheader('Area settings')
    year = filter_panel.selectbox('Estimate year', years, key='estimate_year')
    updates = render_service_area(use_demo, health_path=DB_PATH, year=year, controls=filter_panel, **region_options) | {'year': str(year)}
    replace = navigation.AREA_KEYS | {'year'}
elif view == 'access':
    st.caption('Explore facility listings and workforce shortage designations. These sources describe different aspects of access; they do not measure capacity or demand.')
    render_hospitals(use_demo, context_available=DB_PATH.exists() and not unreadable, controls=filter_panel, **region_options)
    render_hrsa(use_demo, controls=filter_panel, **region_options)
else:
    st.caption('Inspect saved coverage and source dates. Status inspection makes no source requests; use explicit refresh controls when needed.')
    year = st.session_state.get('estimate_year')
    if year is None:
        raw_year = st.query_params.get('year')
        year = next((y for y in range(2015, 2025) if str(y) == raw_year), 2023)
    render_data_status(path, year, dataset, use_demo, **region_options)
    render_interpretation()

navigation.finish(view, region, use_demo, updates, replace)
st.caption('Population estimates, not individual diagnoses. County differences do not establish causes.')
