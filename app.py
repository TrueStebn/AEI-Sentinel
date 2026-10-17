"""AEI Sentinel product shell; one source and cohort across four destinations."""

import sqlite3
import streamlit as st

from src.analysis_service import DatasetChangedError
from src.config import get_settings
from src.database import connect,initialize_database
from src.interface import apply_style
from src.design import MARK,expert
from src.overview_interface import render_overview
from src.product_interface import header,render_explore,render_reports,render_settings
from src.ui_data import load_catalog
from src.replay_interface import select_data_source,show_update,refresh_watcher
from src.workspace_interface import autosave_workspace,bootstrap_workspace,render_workspace
from src.ux_state import normalize_route,validate_context,go,enter_reports
from src.workspaces import PAGES


def _destination_changed():
    if st.session_state['_destination']=='Reports':
        enter_reports(st.session_state)
    st.session_state['navigation']=st.session_state['_destination']
    autosave_workspace()


def main():
    st.set_page_config(page_title='AEI Sentinel',page_icon=str(MARK),layout='wide',initial_sidebar_state='expanded')
    apply_style()
    st.session_state['_shared_date_rendered']=False
    with st.sidebar.container(horizontal=True,wrap=False,vertical_alignment='center'):
        st.image(str(MARK),width=30)
        st.markdown('**AEI Sentinel**')
    try:
        primary=get_settings()
        initialize_database(primary)
        if not bootstrap_workspace(primary):
            return
        with connect(primary,read_only=True) as con:
            st.session_state['_laboratory_version']=con.execute('SELECT version FROM data_version WHERE singleton_id=1').fetchone()[0]
    except (sqlite3.Error,OSError,RuntimeError,ValueError) as exc:
        st.error(f'Unable to initialize the local workspace. {exc}')
        return
    normalize_route(st.session_state)
    st.session_state['_destination']=st.session_state['navigation']
    page=st.sidebar.radio('Navigation',PAGES,key='_destination',on_change=_destination_changed,
        captions=['Healthcare units & changes','Patterns & investigation','Briefings & exports','Sources, Replay & workspaces'])
    st.sidebar.button('Refresh data',icon=':material/refresh:')
    st.sidebar.markdown('[Back to top](#aei-top)')
    header_slot=st.container()
    admin_slot=st.container() if page=='Data & Settings' else None
    if admin_slot is not None:
        admin_slot.subheader('Data & Settings')
        admin_slot.caption('Choose a source, control Replay, or manage a private workspace. These are unauthenticated local demonstration controls.')
    save_status=render_workspace(primary,target=admin_slot,controls=page=='Data & Settings')
    try:
        settings,controller=select_data_source(primary,target=admin_slot,controls=page=='Data & Settings')
        if settings is None:
            autosave_workspace(save_status)
            return
        catalog=load_catalog(settings)
        validate_context(st.session_state,catalog)
    except (sqlite3.Error,OSError,RuntimeError,ValueError) as exc:
        st.error(f'Unable to load the laboratory database. Check the configuration and retry. {exc}')
        return
    with header_slot:
        header(catalog)
    st.sidebar.caption(f"Source: {st.session_state.get('data_source','Laboratory Dataset')} · v{catalog.version}")
    if page!='Data & Settings':
        st.sidebar.button('Source & workspace settings',on_click=go,args=('Data & Settings',),type='tertiary')
    if 'workspace_restore_notice' in st.session_state:
        st.info(st.session_state.pop('workspace_restore_notice'))
    notice=st.empty()
    try:
        with st.container(key='expert' if expert() else 'guided'):
            if page=='Overview':
                render_overview(settings,catalog)
            elif page=='Explore':
                render_explore(settings,catalog)
            elif page=='Reports':
                render_reports(settings,catalog)
            else:
                render_settings(settings,catalog,controller)
        with notice.container():
            show_update(settings,catalog)
    except DatasetChangedError as exc:
        st.warning(str(exc))
    except (sqlite3.Error,OSError,RuntimeError,ValueError) as exc:
        st.error(f'Unable to complete this request. {exc}')
    st.sidebar.divider()
    st.sidebar.caption('Synthetic data · Local demonstration · No authentication')
    autosave_workspace(save_status)
    refresh_watcher(settings,catalog.version,catalog.last_successful_import,controller)


if __name__=='__main__':
    main()
