"""Task routing and durable analytical state, independent of widget lifetimes."""
from copy import deepcopy

import streamlit as st

VIEWS = {'explore': 'Explore counties', 'compare': 'Compare counties',
         'areas': 'Service areas', 'access': 'Healthcare access', 'sources': 'Sources & methods'}
HEALTH_KEYS = {'measure', 'year', 'compare', 'county', 'census'}
LOCAL_KEYS = {'local', 'tract', 'tract_year'}
AREA_KEYS = {'area', 'area_rev', 'area_compare', 'area_b', 'area_b_rev', 'area_b_year'}
LINK_KEYS = HEALTH_KEYS | LOCAL_KEYS | AREA_KEYS | {'state', 'demo', 'view'}
PERSIST = {'health_measure', 'estimate_year', 'compare_counties', 'profile_county', 'include_census',
           'show_tracts', 'tract_year', 'tract_selection', 'area_counties', 'show_area_summary',
           'compare_areas', 'area_b_counties', 'area_b_year', 'area_health_condition', 'area_compare_measure',
           'show_hospitals', 'hospital_query', 'hospital_county', 'hospital_types', 'hospital_detail',
           'show_hospital_locations', 'show_hrsa', 'hrsa_discipline', 'hrsa_category', 'hrsa_statuses',
           'hrsa_county', 'hrsa_detail'}
LINKED_STATE = {'health_measure', 'estimate_year', 'compare_counties', 'profile_county', 'include_census',
                'show_tracts', 'tract_year', 'tract_selection', 'tract_link_initialized', 'tract_scope',
                'area_counties', 'area_active', 'compare_areas', 'area_b_counties', 'area_b_year',
                'comparison_source', 'demo_mode'}


ACTIVE = {
    'explore': {'health_measure', 'estimate_year', 'profile_county', 'show_tracts', 'tract_year', 'tract_selection'},
    'compare': {'health_measure', 'estimate_year', 'compare_counties', 'include_census'},
    'areas': {'estimate_year', 'area_counties', 'show_area_summary', 'compare_areas', 'area_b_counties',
              'area_b_year', 'area_health_condition', 'area_compare_measure'},
    'access': {key for key in PERSIST if key.startswith(('hospital_', 'hrsa_', 'show_hospital', 'show_hrsa'))},
    'sources': set(),
}

def resolve(params):
    if 'view' in params:
        return (params['view'], False) if params['view'] in VIEWS else ('explore', True)
    if any(k in params for k in AREA_KEYS):
        return 'areas', False
    if any(k in params for k in ('county', 'local', 'tract')):
        return 'explore', False
    if 'compare' in params or params.get('census') == '1':
        return 'compare', False
    return 'explore', False


def bounded_params(params):
    return {k: v for k, v in params.items() if k in LINK_KEYS and isinstance(v, str) and len(v) <= 250}


def remember():
    saved = st.session_state.setdefault('_saved_widgets', {})
    for key in ACTIVE.get(st.session_state.get('_active_view'), set()):
        if key in st.session_state:
            saved[key] = deepcopy(st.session_state[key])


def begin(params, region):
    if st.session_state.get('_active_region') != region.abbr:
        st.session_state.clear()
        st.session_state['_active_region'] = region.abbr
    elif st.session_state.get('_last_query') != params:
        # Browser back/forward or a new shared URL must hydrate URL-backed state.
        saved = st.session_state.get('_saved_widgets', {})
        for key in LINKED_STATE:
            st.session_state.pop(key, None)
            saved.pop(key, None)
        st.session_state['_task_links'] = bounded_params(params)
    view, invalid = resolve(params)
    previous_view = st.session_state.get('_active_view')
    for key, value in st.session_state.get('_saved_widgets', {}).items():
        if key not in st.session_state or key not in ACTIVE[view] or view != previous_view:
            st.session_state[key] = deepcopy(value)
    st.session_state.setdefault('_task_links', bounded_params(params))
    st.session_state['_active_view'] = view
    st.session_state['view_selector'] = view
    return view, invalid


def go(view, **updates):
    remember()
    if 'county' in updates:
        st.session_state.setdefault('_saved_widgets', {})['profile_county'] = updates['county']
    params = bounded_params(dict(st.query_params)) | updates | {'view': view}
    st.query_params.from_dict(params)
    st.session_state['_last_query'] = params
    st.session_state['view_selector'] = view


def choose_view():
    go(st.session_state['view_selector'])


def finish(view, region, demo, updates=None, replace=()):
    params = dict(st.session_state.get('_task_links', {}))
    for key in replace:
        params.pop(key, None)
    params.update(updates or {})
    params.update(view=view, demo='1' if demo else '0')
    if region.abbr == 'OR':
        params.pop('state', None)
    else:
        params['state'] = region.abbr
    # Sanitize inactive task links too; they must never carry foreign geography.
    from oregon_health.links import parse_counties
    if 'compare' in params:
        codes, _ = parse_counties(params['compare'], region=region)
        params['compare'] = ','.join(codes or [])
    if params.get('county') not in region.counties():
        params.pop('county', None)
    from oregon_health.links import parse_tract
    selected,invalid=parse_tract(params.get('tract'),params.get('county'),region=region)
    if invalid:
        params.pop('tract',None)
    if params.get('county') not in region.counties():
        for key in LOCAL_KEYS:
            params.pop(key,None)
    from oregon_health.service_areas import parse_link
    for key, revision in [('area', 'area_rev'), ('area_b', 'area_b_rev')]:
        if key in params:
            try:
                parse_link(params[key], params.get(revision), region=region)
            except ValueError:
                params.pop(key, None)
                params.pop(revision, None)
    if demo:
        for key in LOCAL_KEYS | AREA_KEYS:
            params.pop(key, None)
        params['census'] = '0'
    st.session_state['_task_links'] = params
    remember()
    if dict(st.query_params) != params:
        st.query_params.from_dict(params)
    st.session_state['_last_query'] = params
