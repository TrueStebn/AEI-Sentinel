"""Four product destinations over existing analytical and export services."""

import streamlit as st
from src.design import brand,expert
from src.interface import _save_filter,_latest_date,render_analysis_date,render_dashboard,render_detailed,render_filters,render_management
from src.ux_state import go,selected_cohort,map_report
from src.workspaces import EXPLORE_VIEWS,REPORT_VIEWS
from src.presentation import expert_action


def header(catalog):
    with st.container(key='app_header',horizontal=True,wrap=True,vertical_alignment='center',gap='medium'):
        with st.container(width=400,key='header_identity'):
            brand()
            st.html('<div class="aei-source"><span class="aei-badge">Synthetic Demo Data</span> '+
                f"{st.session_state.get('data_source','Laboratory Dataset')} · Dataset revision {catalog.version}</div>")
        with st.container(width=170):
            st.session_state['_presentation_mode']=st.session_state['presentation_mode']
            st.radio('View mode',['Guided','Expert'],horizontal=True,key='_presentation_mode',
                help='Changes presentation only. Data, filters, reporting periods and statistical rules stay the same.',
                on_change=_save_filter,args=('presentation_mode','_presentation_mode'))
        with st.container(width=200):
            day=catalog.latest_sample if st.session_state.get('follow_latest_date',True) else st.session_state.get('analysis_date')
            with st.popover(f"Analysis date · {day or 'No Data'}",width='stretch'):
                render_analysis_date(catalog,target=st)
                if catalog.latest_sample:
                    st.button('Use latest sample',on_click=_latest_date,args=(catalog.latest_sample,),kwargs={'persist':True})
                else:
                    st.caption('Analysis date: No Data')
            st.session_state['_shared_date_rendered']=True


def view_choice(label,options,name):
    if st.session_state.get(name) not in options:
        st.session_state[name]=options[0]
    key='_'+name
    st.session_state[key]=st.session_state[name]
    return st.radio(label,options,horizontal=True,key=key,on_change=_save_filter,args=(name,key))


def render_explore(settings,catalog):
    st.html('<div class="aei-kicker">Explore</div>')
    st.caption('Select Context → Examine Patterns → Investigate → Review Evidence')
    view=view_choice('Explore view',EXPLORE_VIEWS,'explore_view')
    actions=st.columns([4,1])
    actions[0].caption('Start with the map, compare resistance trends, or open a focused investigation. All tools share the active source and date.')
    selected_map=None
    cohort=selected_cohort(st.session_state)
    restricted=any(getattr(cohort,name) is not None for name in ('sex','age_min','age_max','home_area','ward_type','institution','admission_timing'))
    if restricted and view in ('Resistance Trends','Detailed Period'):
        st.info('This retained basic tool uses ward / organism / antibiotic / specimen only. Your patient filters are retained in Investigation; use that view for patient-filtered evidence and reports.')
    if view=='Surveillance Map':
        from src.map_interface import render_surveillance_map
        selected_map=render_surveillance_map(settings,catalog)
    elif view=='Investigation':
        from src.explorer_interface import render_explorer
        render_explorer(settings,catalog)
    elif not catalog.available:
        st.warning('No Data. Use Next Batch in Data & Settings to receive observations.' if st.session_state.get('data_source')!='Laboratory Dataset'
            else 'No Data. Import a laboratory dataset from Data & Settings to begin.')
    else:
        filters,as_of=render_filters(catalog)
        (render_dashboard if view=='Resistance Trends' else render_detailed)(settings,catalog,filters,as_of)
    if selected_map is not None:
        actions[1].button('Create report',on_click=map_report,args=selected_map,disabled=selected_map[1] is None,width='stretch')
    elif restricted and view in ('Resistance Trends','Detailed Period'):
        actions[1].button('Use investigation',on_click=go,args=('Explore','Investigation'),width='stretch')
    else:
        actions[1].button('Create report',on_click=go,args=('Reports','Current Investigation'),width='stretch')
    if view in ('Resistance Trends','Detailed Period'):
        expert_action('basic_tools')


def render_reports(settings,catalog):
    st.html('<div class="aei-kicker">Reports</div>')
    st.subheader('Turn evidence into a clear report')
    view=view_choice('Report workspace',REPORT_VIEWS,'reports_view')
    st.caption('Current Investigation: the active organism, antibiotic, specimen and patient filters. Surveillance Briefing: choose a hospital-wide summary or a department-focused report.')
    if view=='Surveillance Briefing':
        from src.briefing_interface import render_briefing
        cohort=selected_cohort(st.session_state)
        if any(getattr(cohort,name) is not None for name in ('sex','age_min','age_max','home_area','ward_type','institution','admission_timing')):
            st.info('This briefing uses ward / organism / specimen scope. Your patient filters are retained for Current Investigation; the original global scan is unchanged.')
        render_briefing(settings,catalog)
        return
    from src.advanced_analysis import AdvancedAnalysisEngine
    from src.patient_aware_analysis import PatientAwareEngine
    from src.patient_aware_interface import validate_matches_advanced
    from src.investigation_presentation import render_summary
    from src.investigation_pdf import render_investigation_export
    cohort=selected_cohort(st.session_state)
    code=st.session_state.get('antibiotic_code')
    st.write('The current Explore context is offered below, including its patient filters. Choose Surveillance Briefing for hospital-wide or department-focused alternatives.')
    if st.session_state.get('explorer_mode','antibiotic')!='antibiotic' or not code:
        st.info('A selected antibiotic is required for the patient-aware PDF. Phenotype aggregate exports remain in Explore → Investigation → Review Evidence.')
        st.button('Return to investigation',on_click=go,args=('Explore','Investigation'))
        return
    advanced=AdvancedAnalysisEngine(settings).analyze(cohort,code=code,as_of_date=st.session_state.get('analysis_date'),
        expected_version=catalog.version,group_by=st.session_state.get('explorer_group_dimension','age'),
        group_period=st.session_state.get('explorer_group_period','annual'),
        signal_threshold=st.session_state.get('signal_threshold',settings.signal_minimum_change_pp))
    patient=PatientAwareEngine(settings).analyze(cohort,code=code,as_of_date=advanced.analysis_date,
        expected_version=advanced.data_version,source=st.session_state.get('data_source','Laboratory Dataset'))
    validate_matches_advanced(patient,advanced)
    render_summary(advanced,patient,catalog.wards.get(cohort.ward_code,'All wards'))
    render_investigation_export(advanced,patient,settings)
    expert_action('reports_current')
    st.button('Back to detailed evidence',on_click=go,args=('Explore','Investigation'))


def render_settings(settings,catalog,controller):
    if controller:
        st.info('Replay databases accept only their planned replay batches. Switch Data Source to Laboratory Dataset for shared CSV imports.')
    elif expert():
        with st.expander('Laboratory data and CSV imports'):
            render_management(settings,catalog)
    else:
        st.caption('Laboratory imports, integrity diagnostics and technical controls are available in Expert Mode. Source selection, Replay and workspace recovery remain available here.')
        expert_action('settings')
    with st.expander('Interface preferences and help'):
        st.write('Guided Mode offers summaries, trends, patient groups and PDF reports. Expert Mode adds advanced filters, statistical evidence and technical exports. Both modes use the same typography and analytical context.')
        st.write('Restore URLs are private bearer secrets. Keep the app bound to 127.0.0.1; do not expose unrestricted CSV imports publicly.')
