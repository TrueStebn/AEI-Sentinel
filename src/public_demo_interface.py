"""Ephemeral visitor preferences over shared immutable synthetic snapshots."""

import streamlit as st
from src.demo_assets import public_settings
from src.workspaces import FILTERS

LABORATORY = 'Laboratory Dataset'
SCENARIO = 'Simulated Surveillance Scenario'


def _advance_scenario():
    step = st.session_state.get('_public_scenario_step', 0)
    st.session_state['_public_scenario_step'] = min(3, max(0, step if type(step) is int else 0) + 1)


def _reset_scenario():
    st.session_state['_public_scenario_step'] = 0


def bootstrap_public_session():
    if not st.session_state.get('_public_session_ready'):
        st.session_state.clear()
        st.session_state['_public_session_ready'] = True
    # URL values are neither identity nor authority; no restore token is consumed.
    if st.query_params:
        st.query_params.clear()
        st.session_state['_public_url_notice'] = True
    return True


def source_selection(primary, *, target=None, controls=True):
    ui = target or st.sidebar
    old = st.session_state.get('_public_active_source')
    mode = st.session_state.get('data_source', LABORATORY)
    if mode not in (LABORATORY, SCENARIO):
        mode = LABORATORY
    st.session_state['data_source'] = mode
    if controls:
        mode = ui.selectbox('Data Source', [LABORATORY, SCENARIO], key='data_source')
    step = st.session_state.get('_public_scenario_step', 0)
    if type(step) is not int or not 0 <= step <= 3:
        step = 0
    if mode == SCENARIO:
        ui.info('Simulated Surveillance Scenario · Predefined synthetic snapshots. No visitor database is created.')
        if controls:
            ui.caption('Three fixed steps: baseline 10/100 R, current 4/40 R, then current 40/100 R with one original supported alert. This is an artificial demonstration.')
            left, right = ui.columns(2)
            left.button('Next Batch', disabled=step == 3, key='_public_next', on_click=_advance_scenario)
            right.button('Reset Replay', key='_public_reset', on_click=_reset_scenario)
            ui.progress(step / 3)
            ui.caption(f'Predefined scenario: {step}/3 steps. Reset changes only your temporary selection.')
    st.session_state['_public_scenario_step'] = step
    identity = (mode, step if mode == SCENARIO else None)
    if old != identity:
        for name in list(st.session_state):
            if name in FILTERS or (name.startswith('_smart_') and name != '_smart_last_ai') or name in ('_investigation_pdf', 'briefing_pdf', '_active_investigation'):
                st.session_state.pop(name, None)
        st.session_state['follow_latest_date'] = True
        st.session_state['_public_active_source'] = identity
    return public_settings(primary, step=step if mode == SCENARIO else None), None


def public_workspace_notice(target=None, controls=True):
    ui = target or st.sidebar
    if st.session_state.pop('_public_url_notice', False):
        st.info('Public Demo Mode ignores restore links and URL preferences. Each browser session starts with temporary state.')
    if controls:
        ui.info('Public Demo Mode · Read-only synthetic data. Workspaces, restore links, imports and historical Replay are disabled. Search drafts and results are temporary and disappear after refresh/restart or session expiry.')
    return None, None
