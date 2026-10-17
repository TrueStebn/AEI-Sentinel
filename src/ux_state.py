"""Presentation routing and legacy aliases over one source-scoped cohort state."""

from src.workspaces import PAGES

LEGACY_ROUTES = {"Dashboard":("Overview",None), "Detailed Analysis":("Explore","Detailed Period"),
    "Advanced Anomaly Explorer":("Explore","Investigation"), "Surveillance Map":("Explore","Surveillance Map"),
    "Surveillance Briefing":("Reports","Surveillance Briefing"), "Data Management":("Data & Settings",None)}
ALIASES = {"briefing_ward":"ward_code", "briefing_organism":"organism", "briefing_specimen":"specimen",
    "briefing_antibiotic":"antibiotic_code", **{f"map_{name}":f"explorer_{name}" for name in
    ("sex","age_group","age_min","age_max","ward_type","institution")}}


def normalize_route(state):
    old=state.get("navigation","Overview")
    if old in LEGACY_ROUTES:
        destination,view=LEGACY_ROUTES[old]
        if old=="Surveillance Briefing":
            for alias,canonical in ALIASES.items():
                if alias.startswith("briefing_") and alias in state:
                    state[canonical]=state[alias]
        if old=="Surveillance Map":
            for alias,canonical in ALIASES.items():
                if alias.startswith("map_") and alias in state:
                    state[canonical]=state[alias]
        for saved in state.get('source_filters',{}).values():
            for alias,canonical in ALIASES.items():
                if ((old=='Surveillance Map' and alias.startswith('map_')) or
                    (old=='Surveillance Briefing' and alias.startswith('briefing_'))) and alias in saved:
                    saved[canonical]=saved[alias]
        state['navigation']=destination
        if view:
            state['explore_view' if destination=='Explore' else 'reports_view']=view
    elif old not in PAGES:
        state['navigation']='Overview'
    state.setdefault('navigation','Overview')
    state.setdefault('presentation_mode','Guided')
    state.setdefault('explore_view','Surveillance Map')
    state.setdefault('reports_view','Surveillance Briefing')


def sync_aliases(state):
    for alias,canonical in ALIASES.items():
        if canonical in state:
            state[alias]=state[canonical]


def validate_context(state,catalog):
    for name,values,default in (("ward_code",catalog.wards,None), ("organism",catalog.organisms,catalog.default_organism),
        ("specimen",catalog.specimens,catalog.default_specimen)):
        if name not in state:
            state[name]=default
        elif state[name] is not None and state[name] not in values:
            state[name]=default
    if state.get('antibiotic_code') not in catalog.antibiotics:
        state['antibiotic_code']=catalog.default_antibiotic
    sync_aliases(state)


def selected_cohort(state):
    from src.advanced_analysis import AGE_GROUPS,CohortFilters
    ages=AGE_GROUPS.get(state.get('explorer_age_group','all'),(None,None))
    if state.get('explorer_age_group')=='custom':
        ages=state.get('explorer_age_min',0),state.get('explorer_age_max',130)
    return CohortFilters(state.get('ward_code'),state.get('organism'),state.get('specimen'),state.get('explorer_sex'),
        *ages,state.get('explorer_home_area'),state.get('explorer_ward_type'),state.get('explorer_institution'),state.get('explorer_admission_timing'))


def investigation_context(state):
    from dataclasses import asdict
    return dict(source=state.get('data_source','Laboratory Dataset'),date=str(state.get('analysis_date')),
        cohort=asdict(selected_cohort(state)),code=state.get('antibiotic_code'),mode=state.get('explorer_mode','antibiotic'))


def enter_reports(state):
    """Only navigation into Reports chooses a default; restored Reports choices remain untouched."""
    active=state.get('_active_investigation')
    if active and active==investigation_context(state):
        state['reports_view']='Current Investigation'
    else:
        state['reports_view']='Surveillance Briefing'
        state['briefing_report_scope']='Hospital-wide summary'


def go(destination,view=None):
    import streamlit as st
    from src.workspace_interface import autosave_workspace
    if destination=='Reports' and view is None:
        enter_reports(st.session_state)
    st.session_state['navigation']=destination
    if view:
        st.session_state['explore_view' if destination=='Explore' else 'reports_view']=view
    autosave_workspace()


def select_department(unit):
    import streamlit as st
    from src.workspace_interface import autosave_workspace
    st.session_state['ward_code']=unit.code
    st.session_state['explorer_origin']=None
    for key,value in (('explorer_ward_type',unit.ward_type),('explorer_institution',unit.institution)):
        if st.session_state.get(key) not in (None,value):
            st.session_state[key]=None
    sync_aliases(st.session_state)
    autosave_workspace()


def department_report():
    import streamlit as st
    sync_aliases(st.session_state)
    st.session_state['briefing_report_scope']='Selected investigation'
    st.session_state['_reports_export_requested']=True
    go('Reports','Surveillance Briefing')


def explore_department():
    import streamlit as st
    st.session_state['map_selected_ward']=st.session_state.get('ward_code')
    st.session_state['map_selected_antibiotic']=st.session_state.get('antibiotic_code')
    st.session_state['map_antibiotic_class']=None
    go('Explore','Surveillance Map')


def map_report(report,cell):
    from src.investigations import open_map_investigation
    open_map_investigation(report,cell)
    go('Reports','Current Investigation')
