"""Presentation-only mode actions and wording over existing calculated evidence."""

import re
import streamlit as st

from src.design import expert


def set_view_mode(mode):
    from src.workspace_interface import autosave_workspace
    st.session_state['presentation_mode']=mode
    autosave_workspace()


def expert_action(key):
    if not expert():
        st.caption('Switch to Expert Mode to examine detailed statistical evidence, advanced filters and technical exports.')
        st.button('Switch to Expert Mode',key='expert_action_'+key,on_click=set_view_mode,args=('Expert',))


def plain_limitation(message):
    match=re.fullmatch(r'(\d+) (?:tested|observed) patients occur in both periods; the two samples are not independent\.',message)
    if match:
        return (f'{match[1]} patients were tested in both periods. This limits how confidently we can interpret the observed change. '
                'A supported statistical conclusion is unavailable for these overlapping groups.')
    if message.startswith('Pooled groups include repeated patients within a period'):
        return 'Some patients contribute more than one observation in a period. This limits confidence in the comparison; a supported statistical conclusion is unavailable.'
    return message # Never discard an unfamiliar limitation.


def limitations(messages):
    for message in messages:
        st.warning(message if expert() else plain_limitation(message))


def period_note(period):
    if period.status=='no_data':
        return 'No laboratory results are available for this period. This is not a measured zero.'
    if period.status=='insufficient_data':
        return 'Not enough laboratory observations to calculate a reliable percentage. An unavailable value is not zero.'
    if period.status=='unsupported':
        return 'This organism and marker combination is not supported. This is not a negative result.'
    return None


def guided_quality(report):
    """Keep every period's availability and assessment limitations visible."""
    for label in ('current','baseline','annual'):
        period=getattr(report,label)
        note=period_note(period)
        if note:
            st.info(f'{"Historical baseline" if label=="baseline" else "Rolling 365 days" if label=="annual" else "Current 28 days"}: {note}')
    limitations(report.assessment.limitations)
    st.caption('Observed patterns do not establish their cause, clinical infection or the suitability of treatment. Missing tests are never treated as susceptible.')


def cohort_caption(cohort):
    from dataclasses import asdict
    active={key:value for key,value in asdict(cohort).items() if key not in ('ward_code','organism','specimen') and value is not None}
    if active:
        st.caption('Active patient filters: '+ '; '.join(f'{key.replace("_"," ")}: {value}' for key,value in active.items()) + '. These remain active in both view modes.')


def saved_choice(name,values,default):
    """Normalize a source-scoped choice identically whether its widget is visible."""
    if name not in st.session_state or st.session_state[name] not in values:
        st.session_state[name]=default
    return st.session_state[name]
