"""Guided/Expert visibility, unchanged context, exports and recovery regressions."""

import hashlib,json
from copy import deepcopy
from datetime import date

import pytest

from src.advanced_analysis import AdvancedAnalysisEngine
from src.anomalies import AnomalyEngine
from src.patient_aware_analysis import PatientAwareEngine
from src.database import connect,transaction
from src.presentation import plain_limitation
from src.ux_state import selected_cohort
from src.workspaces import FILTERS,WorkspaceStore
from src.simulator import SCENARIO,HISTORICAL
from test_app import ui_data,metrics
from test_patient_aware_interface import patient_ui
from test_resistance import observation,seed
from test_ux_redesign import home,click
from test_workspace_interface import reconnect
from ui_navigation import navigate,source,replay_click

TECHNICAL={'Patient and healthcare filters','Group comparison options','Period counts and full-population method',
    'Original surveillance signal — original cohort','How to read this investigation','Patient membership and full-population counts',
    'Populations, assumptions and export','Evidence quality and terminology','Patient and map filters',
    'Counts, quality and aggregate exports','Source-wide context and quality counts','Selected analysis and trend',
    'Detailed quality explanations and actual sample sizes','Period counts and reporting rules','Scan scope, data quality and export'}


def mode(app,value):
    app.radio(key='_presentation_mode').set_value(value).run(timeout=30)
    assert not app.exception and not app.error
    return app


def investigation(app):
    return navigate(app,'Explore','Investigation')


def downloads(app):
    return [item.label for item in app.get('download_button')]


def visible_text(app):
    return ' '.join(item.value for kind in ('caption','markdown','info','warning') for item in app.get(kind))


def assert_guided(app):
    assert not TECHNICAL.intersection(item.label for item in app.expander)
    assert not any('JSON' in name or 'CSV' in name for name in downloads(app))
    assert any(b.label=='Switch to Expert Mode' for b in app.button)


def test_guided_default_compact_brand_and_new_label(ui_data):
    app=home()
    assert app.radio(key='_presentation_mode').label=='View mode'
    assert app.radio(key='_presentation_mode').value=='Guided'
    assert_guided(app)
    assert not any(b.label=='Autosave status' for b in app.button)
    assert not any('Last Saved:' in x.value for x in app.caption)
    assert not any('<svg' in x.proto.body for x in app.get('html'))


def test_guided_investigation_has_working_trends_groups_and_limits(patient_ui):
    app=investigation(home())
    assert_guided(app)
    assert metrics(app)['Current resistance']=='94.29%'
    assert {'Resistance Trends','Patient Groups','Interpretation'}<=set(x.label for x in app.tabs)
    assert len(app.get('plotly_chart'))==2
    assert '5 patients were tested in both periods' in visible_text(app)
    assert 'cannot support a statistical conclusion' in visible_text(app)
    assert not any(word in visible_text(app) for word in ('Wilson','McNemar','Unadjusted p='))
    assert not any('Baseline → Current' in frame.value.columns for frame in app.dataframe)
    assert any('Tested' in frame.value.columns and 'Wilson lower (%)' not in frame.value.columns for frame in app.table)
    assert any('Difference vs reference (pp)' in frame.value.columns for frame in app.table)
    assert not app.dataframe # No native technical CSV toolbar is instantiated in Guided.
    assert not any(x.key=='_explorer_sex' for x in app.selectbox)


def test_expert_action_retains_finding_and_exposes_all_details(patient_ui):
    app=home();app.button(key='overview_finding_0_open').click().run(timeout=30)
    origin=deepcopy(app.session_state['explorer_origin'])
    context=selected_cohort(app.session_state);current=metrics(app)['Current resistance']
    click(app,'Switch to Expert Mode')
    assert app.radio(key='_presentation_mode').value=='Expert'
    assert selected_cohort(app.session_state)==context and app.session_state['explorer_origin']==origin
    assert metrics(app)['Current resistance']==current
    assert TECHNICAL.intersection(x.label for x in app.expander)
    assert all(not x.proto.expanded for x in app.expander)
    assert any('Baseline → Current' in frame.value.columns for frame in app.dataframe)
    assert 'Exact McNemar' in visible_text(app) and 'Wilson' in visible_text(app)
    assert 'Download patient-aware investigation (JSON)' in downloads(app)
    assert 'Download investigation periods (CSV)' in downloads(app)
    mode(app,'Guided');assert_guided(app)
    assert selected_cohort(app.session_state)==context and app.session_state['explorer_origin']==origin
    restored=reconnect(app.query_params['workspace'])
    assert restored.radio(key='_presentation_mode').value=='Guided'
    assert selected_cohort(restored.session_state)==context and restored.session_state['explorer_origin']==origin


def test_hidden_custom_filters_survive_cleanup_navigation_and_reconnect(ui_data,settings):
    with connect(settings) as con,transaction(con):
        con.execute("UPDATE wards SET ward_type='in' WHERE ward_code='ED'")
        con.execute('UPDATE data_version SET version=2')
    app=investigation(mode(home(),'Expert'))
    for key,value in (('_ward_code','ED'),('_antibiotic_code','CIP'),('_explorer_sex','F'),
        ('_explorer_age_group','custom'),('_explorer_ward_type','in'),('_explorer_institution','hospital')):
        app.selectbox(key=key).set_value(value).run(timeout=30)
    app.number_input(key='_explorer_age_min').set_value(40).run(timeout=30)
    app.number_input(key='_explorer_age_max').set_value(50).run(timeout=30)
    home_area=next(x for x in app.selectbox(key='_explorer_home_area').options if x!='All')
    # Use the actual canonical option, independent of its formatted display label.
    app.selectbox(key='_explorer_home_area').set_value(home_area).run(timeout=30)
    app.selectbox(key='_explorer_admission_timing').set_value('days_1_2').run(timeout=30)
    app.selectbox(key='_explorer_group_period').set_value('baseline').run(timeout=30)
    app.date_input(key='_analysis_date').set_value(date(2026,9,26)).run(timeout=30)
    context=selected_cohort(app.session_state)
    before={key:deepcopy(app.session_state[key]) for key in FILTERS if key in app.session_state}
    a=AdvancedAnalysisEngine(settings).analyze(context,code='CIP',as_of_date='2026-09-26',group_period='baseline')
    p=PatientAwareEngine(settings).analyze(context,code='CIP',as_of_date='2026-09-26')
    mode(app,'Guided');app.run(timeout=30) # A second run exercises Streamlit hidden-widget cleanup.
    assert not any(x.key.startswith('_explorer_age_') for x in app.number_input)
    assert selected_cohort(app.session_state)==context
    assert {key:app.session_state[key] for key in before}==before
    assert 'Active patient filters:' in visible_text(app)
    click(app,'Create report')
    click(app,'Generate investigation PDF')
    cohort=json.loads(app.session_state['_investigation_pdf']['manifest'])['patient_aware']['cohort']
    assert cohort['age_min']==40 and cohort['age_max']==50 and cohort['admission_timing']=='days_1_2'
    restored=reconnect(app.query_params['workspace'])
    assert selected_cohort(restored.session_state)==context and restored.radio(key='_presentation_mode').value=='Guided'
    assert AdvancedAnalysisEngine(settings).analyze(context,code='CIP',as_of_date='2026-09-26',group_period='baseline')==a
    assert PatientAwareEngine(settings).analyze(context,code='CIP',as_of_date='2026-09-26')==p


@pytest.mark.parametrize('page,view',[('Overview',None),('Explore','Investigation'),('Reports','Current Investigation')])
def test_primary_metric_components_and_styles_identical_between_modes(ui_data,page,view):
    app=home();navigate(app,page,view)
    labels=('Healthcare units','Preliminary signals','Supported alerts','Latest available sample') if page=='Overview' else ('Current resistance','Historical resistance','Observed change')
    before={m.label:m.proto.SerializeToString() for m in app.metric if m.label in labels}
    css=next(x.proto.body for x in app.get('html') if '<style>' in x.proto.body)
    assert '.st-key-expert' not in css and '.st-key-guided' not in css
    mode(app,'Expert')
    assert {m.label:m.proto.SerializeToString() for m in app.metric if m.label in labels}==before
    assert next(x.proto.body for x in app.get('html') if '<style>' in x.proto.body)==css


def test_selected_department_does_not_insert_controls_into_card_grid(ui_data):
    app=home()
    card_keys=[b.key for b in app.button if str(b.key).startswith('select_unit_')]
    click(app,'Emergency department')
    assert [b.key for b in app.button if str(b.key).startswith('select_unit_')]==card_keys
    assert not any(b.label=='Explore unit' for b in app.button)
    assert app.button(key='select_unit_ED').proto.type=='primary'
    assert any('Selected department' in x.proto.body for x in app.get('html'))
    assert any(b.label=='Explore Emergency department' for b in app.button)
    click(app,'New ward')
    assert not any(b.label=='Explore unit' for b in app.button)
    assert app.button(key='select_unit_NEW-WARD').proto.type=='primary'


def test_guided_pdf_expert_manifest_and_cache_share_identical_context(patient_ui):
    app=investigation(home());click(app,'Create report');click(app,'Generate investigation PDF')
    stored=deepcopy(app.session_state['_investigation_pdf'])
    assert downloads(app)==['Download investigation PDF']
    payload=json.loads(stored['manifest'])
    assert payload['patient_aware']['membership']['shared_observed']==5
    assert all(secret not in stored['manifest'] for secret in ('patient_id','restore_token','workspace='))
    click(app,'Switch to Expert Mode')
    assert 'Download investigation PDF manifest (JSON)' in downloads(app)
    assert app.session_state['_investigation_pdf']==stored
    mode(app,'Guided');assert downloads(app)==['Download investigation PDF']
    assert app.session_state['_investigation_pdf']==stored


@pytest.mark.parametrize('scope',['Hospital-wide summary','Selected investigation'])
def test_guided_briefing_normal_pdf_and_expert_json(ui_data,scope):
    app=home();navigate(app,'Reports','Surveillance Briefing')
    app.selectbox(key='_briefing_report_scope').set_value(scope).run(timeout=30)
    assert_guided(app)
    click(app,'Generate PDF report');stored=deepcopy(app.session_state['_briefing_pdf'])
    assert downloads(app)==['Download PDF report']
    mode(app,'Expert')
    assert 'Download aggregate report manifest (JSON)' in downloads(app)
    assert app.session_state['_briefing_pdf']==stored


def test_expert_optional_patient_pdf_choice_survives_guided(patient_ui):
    app=mode(home(),'Expert');navigate(app,'Reports','Surveillance Briefing')
    app.selectbox(key='_briefing_report_scope').set_value('Selected investigation').run(timeout=30)
    app.checkbox(key='_briefing_include_patient').check().run(timeout=30)
    click(app,'Generate PDF report');stored=deepcopy(app.session_state['_briefing_pdf'])
    assert 'patient_aware' in json.loads(stored['manifest'])
    mode(app,'Guided');app.run(timeout=30)
    assert not app.checkbox and app.session_state['_briefing_pdf']==stored
    mode(app,'Expert')
    assert app.checkbox(key='_briefing_include_patient').value is True
    assert app.session_state['_briefing_pdf']==stored


@pytest.mark.parametrize('view',['Surveillance Map','Resistance Trends','Detailed Period'])
def test_guided_other_explore_tools_omit_technical_panels(ui_data,view):
    app=home();navigate(app,'Explore',view)
    assert_guided(app)
    assert not any(x.key=='_signal_threshold' for x in app.number_input)
    mode(app,'Expert')
    assert any(name in TECHNICAL for name in (x.label for x in app.expander)) or bool(downloads(app))


def test_map_hidden_filters_preserved_and_reports_receive_them(ui_data):
    app=mode(home(),'Expert');navigate(app,'Explore','Surveillance Map')
    app.selectbox(key='_map_selected_ward').set_value('ED').run(timeout=30)
    app.selectbox(key='_map_selected_antibiotic').set_value('CIP').run(timeout=30)
    app.selectbox(key='_map_sex').set_value('F').run(timeout=30)
    app.selectbox(key='_map_scale').set_value('focused').run(timeout=30)
    mode(app,'Guided');app.run(timeout=30)
    assert not any(x.key=='_map_sex' for x in app.selectbox)
    assert app.session_state['map_sex']=='F' and app.session_state['map_scale']=='focused'
    assert_guided(app)
    click(app,'Create report');click(app,'Generate investigation PDF')
    assert json.loads(app.session_state['_investigation_pdf']['manifest'])['patient_aware']['cohort']['sex']=='F'


@pytest.mark.parametrize('mode_name',[SCENARIO,HISTORICAL])
def test_empty_partial_replay_and_modes_keep_source_isolated(ui_data,settings,mode_name):
    before=hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
    app=home();source(app,mode_name)
    empty=metrics(app);mode(app,'Expert');assert metrics(app)==empty
    mode(app,'Guided');replay_click(app,'Next Batch');partial=metrics(app)
    mode(app,'Expert');assert metrics(app)==partial
    source(app,'Laboratory Dataset')
    assert metrics(app)['Healthcare units']=='2'
    assert hashlib.sha256(settings.database_path.read_bytes()).hexdigest()==before


def test_supported_scenario_exact_classification_same_both_modes(ui_data):
    app=home();source(app,SCENARIO)
    for _ in range(3):replay_click(app,'Next Batch')
    values=metrics(app)
    assert values['Supported alerts']=='1' and values['Preliminary signals']=='1'
    mode(app,'Expert');assert metrics(app)==values
    assert '1.243340557' in visible_text(app)
    mode(app,'Guided');assert metrics(app)==values
    assert not TECHNICAL.intersection(x.label for x in app.expander)


@pytest.mark.parametrize('n',[0,1])
def test_empty_and_insufficient_guided_are_not_zero(settings,seed,monkeypatch,n):
    seed([observation('old','2026-08-01'),observation('new')] if n else [])
    monkeypatch.setenv('AEI_DATABASE_PATH',str(settings.database_path))
    app=investigation(home())
    assert metrics(app)['Current resistance']=='Unavailable'
    assert 'not zero' in visible_text(app) or 'not a measured zero' in visible_text(app)
    assert ('Not enough laboratory observations' if n else 'No laboratory results') in visible_text(app)
    assert not any('patient overlap' in item.value.lower() for item in app.info) # Do not invent overlap when only data availability limits the result.
    assert_guided(app)


def test_guided_phenotype_remains_interactive_with_same_result(ui_data):
    app=investigation(home())
    app.selectbox(key='_explorer_mode').set_value('phenotype').run(timeout=30)
    before={key:metrics(app)[key] for key in ('Current 28 days','Historical baseline — preceding 365 days','Rolling 365 days (overlapping)')}
    assert_guided(app)
    mode(app,'Expert')
    assert {key:metrics(app)[key] for key in before}==before


def test_statistics_threshold_retained_when_expert_widget_disappears(ui_data):
    app=mode(home(),'Expert');navigate(app,'Explore','Resistance Trends')
    app.number_input(key='_signal_threshold').set_value(18).run(timeout=30)
    counts={key:metrics(app)[key] for key in ('Preliminary Signals','Statistically Supported Alerts')}
    mode(app,'Guided');app.run(timeout=30)
    assert app.session_state['signal_threshold']==18
    assert {key:metrics(app)[key] for key in counts}==counts
    mode(app,'Expert');assert app.number_input(key='_signal_threshold').value==18


def test_plain_language_preserves_overlap_number_and_unknown_limits():
    assert plain_limitation('33 tested patients occur in both periods; the two samples are not independent.').startswith('33 patients were tested in both periods.')
    assert plain_limitation('Future methodological limitation.')=='Future methodological limitation.'
