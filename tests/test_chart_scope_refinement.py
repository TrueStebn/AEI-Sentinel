"""Sparse real cohorts, faithful charts, contextual exports and workspace isolation."""

import io
import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from src.advanced_analysis import AdvancedAnalysisEngine,CohortFilters
from src.resistance import ResistanceEngine,ResistanceFilters,ResistanceTimeSeries
from src.chart_states import availability,explanation,percentage
from src.charts import trend_chart
from src.explorer_charts import trend_chart as advanced_chart
from src.briefing import build_briefing,BriefingFilters
from src.briefing_pdf import render_pdf
from src.ux_state import investigation_context,enter_reports
from src.patient_aware_analysis import PatientAwareEngine
from src.investigation_pdf import render_investigation_pdf
from test_resistance import observation,seed
from test_ux_redesign import home,click
from test_view_refinement import mode,visible_text
from test_workspace_interface import reconnect
from ui_navigation import navigate,source
from src.simulator import HISTORICAL,SCENARIO


@pytest.fixture
def sparse(settings,seed,monkeypatch):
    seed([observation(f'p{i}',f'2026-09-{(4,11,18,25)[i//10]:02}',result='R' if i<16 else 'S') for i in range(40)])
    monkeypatch.setenv('AEI_DATABASE_PATH',str(settings.database_path))
    return settings


def advanced(settings):
    return AdvancedAnalysisEngine(settings).analyze(CohortFilters('ED','Escherichia coli','urine'),code='CIP',as_of_date='2026-09-25',expected_version=7)


def explore():
    return navigate(home(),'Explore','Investigation')


def weekly(app):
    app.radio(key='_explorer_frequency').set_value('Weekly').run(timeout=30)
    assert not app.exception and not app.error
    return app


def test_no_weekly_points_but_unchanged_valid_aggregate_and_monthly(sparse):
    r=advanced(sparse)
    assert r.current.n_observed==40 and r.current.percentage==40
    assert availability(r.weekly).valid==0 and availability(r.monthly).valid==1
    title,text=explanation(availability(r.weekly),'weekly',aggregate=r.current)
    assert title=='Insufficient data for weekly trends'
    assert 'minimum of 30' in text and '40.00%' in text
    assert 'overall 28-day result is still available' in text
    old=ResistanceEngine(sparse).last_28_days(ResistanceFilters('CIP','ED','Escherichia coli','urine'),as_of_date='2026-09-25')
    assert r.current.percentage==old.resistance_pct and r.current.n_observed==old.n_tested


@pytest.mark.parametrize('status,expected',[('no_data','No Data'),('insufficient_data','Insufficient data'),('unsupported','Unsupported combination')])
def test_known_empty_reasons_and_no_fabricated_aggregate(sparse,status,expected):
    point=replace(advanced(sparse).current,status=status,percentage=None,n_observed=0 if status=='no_data' else 5)
    state=availability([point])
    title,text=explanation(state,'weekly',aggregate=point)
    assert expected in title and state.valid==0
    assert 'still available' not in text and percentage(point) is None


def test_partial_weekly_points_keep_zero_hundred_and_real_gaps(settings,seed):
    rows=[]
    for day,count,resistant in [('04',30,0),('11',7,7),('25',30,30)]:
        rows += [observation(f'{day}-{i}','2026-09-'+day,result='R' if i<resistant else 'S') for i in range(count)]
    seed(rows)
    series=ResistanceEngine(settings).time_series(ResistanceFilters('CIP'),start_date='2026-09-01',end_date='2026-09-27',frequency='week')
    figure=trend_chart(series)
    assert list(figure.data[0].y)==[0.0,None,None,100.0]
    assert figure.data[0].connectgaps is False and figure.data[0].mode=='lines+markers'
    assert figure.layout.yaxis.range==(0,100)
    assert availability(series.points).status=='partial'
    assert availability(series.points).insufficient==1 and availability(series.points).empty==1


@pytest.mark.parametrize('value',[0.0,100.0,40.0])
def test_single_valid_value_is_marker_without_line(sparse,value):
    r=advanced(sparse)
    point=replace(r.current,percentage=value)
    missing=replace(point,percentage=None,status='insufficient_data')
    fig=advanced_chart(r,(missing,point,missing))
    scatter=fig.data[-1]
    assert list(scatter.y)==[None,value,None] and scatter.mode=='markers'
    assert scatter.connectgaps is False
    assert list(fig.data[0].y)==[None,40,None]
    state=availability((missing,point,missing))
    assert state.status=='single' and 'not a continuous trend' in explanation(state,'weekly')[1]


@pytest.mark.parametrize('invalid',[None,float('nan'),float('inf')])
def test_missing_and_nonfinite_values_never_become_zero(sparse,invalid):
    r=advanced(sparse)
    assert percentage(replace(r.current,percentage=invalid)) is None
    assert percentage(replace(r.current,status='insufficient_data',percentage=99)) is None
    assert percentage(replace(r.current,percentage=0))==0


def test_guided_empty_panel_monthly_action_and_preserved_filters(sparse):
    app=weekly(explore())
    before=investigation_context(app.session_state)
    assert 'Insufficient data for weekly trends' in visible_text(app)
    assert '40.00%' in visible_text(app)
    assert not any(x.key=='explorer_trend' for x in app.get('plotly_chart'))
    assert 'Reporting periods:' not in visible_text(app)
    assert not any('quality' in x.label.lower() for x in app.expander)
    click(app,'View monthly trend')
    assert app.radio(key='_explorer_frequency').value=='Monthly'
    assert investigation_context(app.session_state)==before
    assert any(x.key=='explorer_trend' for x in app.get('plotly_chart'))
    assert 'Single valid observation' in visible_text(app)
    restored=reconnect(app.query_params['workspace'])
    assert restored.radio(key='_explorer_frequency').value=='Monthly'
    assert investigation_context(restored.session_state)==before


def test_expert_breakdown_uses_same_points_and_threshold(sparse):
    app=weekly(explore())
    before=investigation_context(app.session_state)
    mode(app,'Expert')
    text=visible_text(app)
    assert '0 valid;' in text and 'Insufficient Data;' in text and 'No Data;' in text and 'Configured minimum: 30' in text
    assert 'Insufficient data for weekly trends' in text
    click(app,'View monthly trend')
    assert investigation_context(app.session_state)==before
    assert any(m.value=='40.00%' for m in app.metric) and not app.exception


def test_no_ineffective_monthly_button(settings,seed,monkeypatch):
    seed([observation('one')])
    monkeypatch.setenv('AEI_DATABASE_PATH',str(settings.database_path))
    app=weekly(explore())
    assert not any(b.label=='View monthly trend' for b in app.button)
    assert 'Monthly aggregation also has no reportable percentages' in visible_text(app)
    assert not app.get('plotly_chart')


def test_investigation_defaults_reports_and_retains_patient_filters(sparse):
    app=mode(explore(),'Expert')
    app.selectbox(key='_ward_code').set_value('ED').run(timeout=30)
    app.selectbox(key='_explorer_sex').set_value('F').run(timeout=30)
    before=investigation_context(app.session_state)
    app.sidebar.radio[0].set_value('Reports').run(timeout=30)
    assert app.radio(key='_reports_view').value=='Current Investigation'
    assert investigation_context(app.session_state)==before
    assert 'Selected PDF scope: Current Investigation' in visible_text(app)
    assert 'sex: F' in visible_text(app)
    click(app,'Generate investigation PDF')
    stored=app.session_state['_investigation_pdf']
    manifest=json.loads(stored['manifest'])
    assert manifest['selected_analysis']['cohort']['sex']=='F'
    assert manifest['selected_analysis']['code']=='CIP'
    text=' '.join(' '.join(p.extract_text() for p in PdfReader(io.BytesIO(stored['pdf'])).pages).split())
    assert 'ED / Escherichia coli / CIP / urine' in text and 'sex: F' in text
    assert 'Insufficient data for weekly trends' in text
    assert app.query_params['workspace'] not in text
    restored=reconnect(app.query_params['workspace'])
    assert restored.radio(key='_reports_view').value=='Current Investigation'
    assert investigation_context(restored.session_state)==before


def test_fresh_reports_default_hospital_and_show_unambiguous_scope(sparse):
    app=home()
    navigate(app,'Reports')
    assert app.radio(key='_reports_view').value=='Surveillance Briefing'
    assert app.selectbox(key='_briefing_report_scope').value=='Hospital-wide summary'
    assert 'Selected PDF scope: Hospital-wide summary' in visible_text(app)
    click(app,'Generate PDF report')
    assert json.loads(app.session_state['_briefing_pdf']['manifest'])['scope']=='hospital'


@pytest.mark.parametrize('changed',['source','code','date','cohort','mode'])
def test_stale_investigation_context_cannot_select_current_report(changed):
    state={'data_source':'Laboratory Dataset','analysis_date':date(2026,9,27),'antibiotic_code':'CIP','ward_code':'ED'}
    state['_active_investigation']=investigation_context(state)
    field={'source':'data_source','code':'antibiotic_code','date':'analysis_date','cohort':'ward_code','mode':'explorer_mode'}[changed]
    state[field]={'source':SCENARIO,'code':'AMP','date':date(2026,9,26),'cohort':'URO','mode':'phenotype'}[changed]
    enter_reports(state)
    assert state['reports_view']=='Surveillance Briefing' and state['briefing_report_scope']=='Hospital-wide summary'


@pytest.mark.parametrize('replay',[HISTORICAL,SCENARIO])
def test_empty_replay_no_charts_no_current_default_and_source_frequency_isolation(sparse,replay):
    app=weekly(explore())
    lab=investigation_context(app.session_state)
    source(app,replay)
    assert not app.exception and not app.get('plotly_chart')
    assert 'No Data' in visible_text(app)
    navigate(app,'Reports')
    assert app.radio(key='_reports_view').value=='Surveillance Briefing'
    source(app,'Laboratory Dataset')
    navigate(app,'Explore','Investigation')
    assert app.radio(key='_explorer_frequency').value=='Weekly'
    assert investigation_context(app.session_state)==lab


def test_pdf_without_reportable_comparison_omits_vector_axes(settings,seed):
    seed([observation('PRIVATE-132')])
    report=build_briefing(settings,filters=BriefingFilters('ED','Escherichia coli','urine'),code='CIP',as_of_date='2026-09-27')
    data=render_pdf(report,settings,scope='selected')
    text=' '.join(' '.join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages).split())
    assert 'Insufficient data for comparison trends' in text
    assert 'Fixed chart scale' not in text and 'PRIVATE-132' not in text
    assert 'Insufficient Data' in text and 'minimum N=30' in text


def test_phenotype_zero_is_reportable_and_unsupported_markers_are_distinct(sparse):
    engine=AdvancedAnalysisEngine(sparse)
    cohort=CohortFilters('ED','Escherichia coli','urine')
    r=engine.analyze(cohort,mode='phenotype',code='ESBL',as_of_date='2026-09-25',expected_version=7)
    assert r.current.percentage==0 and availability(r.weekly).valid==0
    _,note=explanation(availability(r.weekly),'weekly',denominator='eligible first isolates')
    assert '30 eligible first isolates' in note and 'tested' not in note
    figure=advanced_chart(r,r.monthly)
    assert 0.0 in figure.data[-1].y and figure.data[-1].mode=='markers'
    unsupported=engine.analyze(cohort,mode='phenotype',code='MRSA',as_of_date='2026-09-25',expected_version=7)
    assert availability(unsupported.weekly,supported=unsupported.supported).status=='unsupported'


def test_empty_renderer_never_builds_a_figure_or_queries_the_engine():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('''
from src.chart_states import render_chart
def forbidden():
    raise AssertionError("An empty chart factory must not run")
render_chart((),forbidden,key='empty',frequency='weekly',minimum=47,monthly=())
''').run(timeout=30)
    assert not app.exception and not app.get('plotly_chart')
    assert 'No Data for weekly values' in visible_text(app)
    assert 'minimum of 47' in visible_text(app)
    assert not app.button


def test_basic_dashboard_and_detailed_period_share_sparse_chart_behavior(sparse):
    app=navigate(home(),'Explore','Resistance Trends')
    assert 'Insufficient data for weekly trends' in visible_text(app)
    assert any(m.value=='40.00%' for m in app.metric)
    assert not any(x.key=='weekly_trend' for x in app.get('plotly_chart'))
    click(app,'View monthly trend')
    assert any(x.key=='weekly_trend_monthly_chart' for x in app.get('plotly_chart'))
    navigate(app,'Explore','Detailed Period')
    assert not any(x.key=='detail_Week' for x in app.get('plotly_chart'))
    assert 'Insufficient data for weekly trends' in visible_text(app)
    assert any(x.key=='detail_Month' for x in app.get('plotly_chart'))


def test_basic_single_observation_has_no_line(sparse):
    series=ResistanceEngine(sparse).time_series(ResistanceFilters('CIP'),start_date='2026-09-01',end_date='2026-09-25',frequency='month')
    figure=trend_chart(series)
    assert figure.data[0].mode=='markers' and list(figure.data[0].y)==[40.0]
