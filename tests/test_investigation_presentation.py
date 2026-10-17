from ui_navigation import navigate, source
"""Phase 12 presentation, aggregate export and source/recovery regression checks."""

import hashlib
import io
import json
from dataclasses import replace
from datetime import date

import pytest
from pypdf import PdfReader

from src.advanced_analysis import AdvancedAnalysisEngine, CohortFilters
from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine
from src.briefing import BriefingFilters,build_briefing
from src.briefing_pdf import render_pdf,report_manifest
from src.database import connect,transaction
from src.investigation_pdf import render_investigation_pdf,investigation_manifest
from src.investigation_presentation import summary_data,transition_chart,transition_rows,comparison_rows
from src.patient_aware_analysis import PatientAwareEngine
from src.simulator import HISTORICAL,SCENARIO
from src.workspaces import WorkspaceStore
from test_resistance import observation,seed
from test_patient_aware_analysis import paired_rows
from test_patient_aware_interface import patient_ui
from test_app import open_app,metrics
from test_explorer_interface import explorer
from test_replay_interface import click
from test_workspace_interface import reconnect

EC="Escherichia coli"
STAMP="2026-10-17T00:00:00+00:00"


@pytest.fixture
def evidence(settings,seed):
    seed(paired_rows([('S','S'),('S','R'),('S','R'),('R','S'),('R','R')])+
         [observation(f'private-old{i}','2026-08-01') for i in range(30)]+
         [observation(f'private-new{i}',result='R') for i in range(30)])
    cohort=CohortFilters(organism=EC,specimen='urine')
    advanced=AdvancedAnalysisEngine(settings).analyze(cohort,mode='antibiotic',code='CIP',as_of_date='2026-09-27')
    patient=PatientAwareEngine(settings).analyze(cohort,code='CIP',as_of_date=advanced.analysis_date)
    return advanced,patient


def pdf_text(data):
    return '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_summary_uses_exact_original_evidence(evidence):
    a,p=evidence
    s=summary_data(a,p)
    assert (s['current_n'],s['baseline_n'],s['shared_observed'],s['shared_tested'])==(35,35,5,5)
    assert s['current_pct']==pytest.approx(3300/35)
    assert s['baseline_pct']==pytest.approx(200/35)
    assert s['delta_pp']==a.assessment.difference_pp
    assert s['preliminary_signal'] and s['paired_available'] and s['sensitivity_available']
    assert 'No supported statistical conclusion' in s['conclusion']
    assert 'BY' not in s['status']


def test_transitions_chart_labels_counts_hover(evidence):
    p=evidence[1].paired
    chart=transition_chart(p)
    assert list(chart.data[0].x)==['S → S','S → R','R → S','R → R']
    assert list(chart.data[0].y)==[1,2,1,1]
    assert 'Paired patients' in chart.data[0].hovertemplate
    assert [r['Patients'] for r in transition_rows(p)]==[1,2,1,1]


@pytest.mark.parametrize('n',[0,None])
def test_chart_empty_is_not_manufactured(evidence,n):
    paired=replace(evidence[1].paired,n_complete_pairs=n)
    assert transition_chart(paired) is None
    if n is None:
        assert transition_rows(paired)==[]


def test_full_restricted_distinct_denominators_and_p_values(evidence):
    a,p=evidence
    full,restricted,paired=comparison_rows(p)
    assert full['denominator']=='Current N=35; baseline N=35' and full['p_value'] is None
    assert restricted['denominator']=='Current N=30; baseline N=30'
    assert restricted['p_value']==p.sensitivity.p_value and restricted['delta_pp']==100
    assert paired['denominator']=='Complete pairs=5; discordant=3'
    assert paired['p_value']==p.paired.p_value and paired['delta_pp'] is None
    assert all('unadjusted' in r['test'] for r in (full,restricted,paired))


def test_insufficient_summary_suppresses_conclusion(settings,seed):
    seed([observation('one')])
    a=AdvancedAnalysisEngine(settings).analyze(CohortFilters(organism=EC,specimen='urine'),mode='antibiotic',code='CIP')
    p=PatientAwareEngine(settings).analyze(a.cohort,code='CIP')
    s=summary_data(a,p)
    assert s['current_pct'] is None and s['delta_pp'] is None and not s['preliminary_signal']
    assert not s['paired_available'] and not s['sensitivity_available']
    assert 'No supported statistical conclusion' in s['conclusion']


def test_presentation_pdf_preserve_scan_and_sqlite(settings,evidence):
    a,p=evidence
    before=settings.database_path.read_bytes()
    scan=AnomalyEngine(settings).scan()
    summary_data(a,p);transition_chart(p.paired);comparison_rows(p)
    render_investigation_pdf(a,p,settings,generated_at=STAMP)
    assert AnomalyEngine(settings).scan()==scan
    assert settings.database_path.read_bytes()==before


def test_investigation_pdf_counts_privacy_determinism(settings,evidence):
    a,p=evidence
    data,manifest=render_investigation_pdf(a,p,settings,generated_at=STAMP)
    assert data==render_investigation_pdf(a,p,settings,generated_at=STAMP)[0]
    text=' '.join(pdf_text(data).split())
    for label in ('Full / current','Restricted / baseline','Exact McNemar p (unadjusted)','S to R','global BY','selection bias','Version: 7'):
        assert label in text
    encoded=json.dumps(manifest)
    for secret in ('private-old','private-new','shared-','patient_id','workspace','restore_token',str(settings.database_path)):
        assert secret not in text and secret not in encoded
    assert manifest['patient_aware']['paired']['s_to_r']==2
    assert manifest['signal_threshold_pp']==settings.signal_minimum_change_pp


def test_briefing_optional_patient_pdf_old_api_and_manifest(settings,evidence):
    a,p=evidence
    report=build_briefing(settings,filters=BriefingFilters(None,EC,'urine'),code='CIP',generated_at=STAMP)
    old=render_pdf(report,settings,scope='selected')
    assert old==render_pdf(report,settings,scope='selected',patient_aware=None)
    assert 'Patient-aware investigation' not in pdf_text(old)
    new=render_pdf(report,settings,scope='selected',patient_aware=p)
    assert 'Patient-aware investigation' in pdf_text(new)
    assert report_manifest(report,'selected',p)['patient_aware']==p.to_dict()
    assert report_manifest(report,'selected')==report.to_dict('selected')
    assert render_pdf(report,settings).startswith(b'%PDF')
    with pytest.raises(ValueError):
        render_pdf(report,settings,patient_aware=p)


@pytest.mark.parametrize('field,value',[('source',SCENARIO),('source_identity','wrong'),('data_version',8),('analysis_date',date(2026,9,26)),('antibiotic_code','AMP'),('cohort',CohortFilters(specimen='blood'))])
def test_mixed_patient_briefing_context_rejected(settings,evidence,field,value):
    report=build_briefing(settings,filters=BriefingFilters(None,EC,'urine'),code='CIP')
    with pytest.raises(DatasetChangedError):
        render_pdf(report,settings,scope='selected',patient_aware=replace(evidence[1],**{field:value}))


def test_stale_export_rejected(settings,evidence):
    with connect(settings) as con,transaction(con):
        con.execute('UPDATE data_version SET version=8')
    with pytest.raises(DatasetChangedError):
        render_investigation_pdf(*evidence,settings)


def test_revision_during_pdf_rejected(settings,evidence,monkeypatch):
    from reportlab.platypus import SimpleDocTemplate
    original=SimpleDocTemplate.build
    def changed(*args,**kwargs):
        original(*args,**kwargs)
        with connect(settings) as con,transaction(con):
            con.execute('UPDATE data_version SET version=8')
    monkeypatch.setattr(SimpleDocTemplate,'build',changed)
    with pytest.raises(DatasetChangedError):
        render_investigation_pdf(*evidence,settings)


def test_summary_pdf_recovery_and_filter_invalidation(patient_ui,settings):
    app=explorer(open_app())
    assert metrics(app)['Current resistance']=='94.29%'
    assert any('No supported statistical conclusion' in i.value for i in app.info)
    click(app,'Generate investigation PDF')
    assert app.session_state['_investigation_pdf']['pdf'].startswith(b'%PDF')
    app.session_state['signal_threshold']=11.0
    app.run(timeout=30)
    assert '_investigation_pdf' not in app.session_state
    click(app,'Generate investigation PDF')
    assert json.loads(app.session_state['_investigation_pdf']['manifest'])['signal_threshold_pp']==11.0
    token=app.query_params['workspace']
    restored=reconnect(token)
    assert metrics(restored)['Current resistance']=='94.29%'
    state=WorkspaceStore(settings).load(token).state
    assert '_investigation_pdf' not in json.dumps(state)
    app.selectbox(key='_explorer_sex').set_value('M').run(timeout=30)
    assert '_investigation_pdf' not in app.session_state
    assert metrics(app)['Current resistance']=='Unavailable'
    assert not app.error and not app.exception


@pytest.mark.parametrize('mode',[SCENARIO,HISTORICAL])
def test_pdf_cache_does_not_cross_replay_source(patient_ui,settings,mode):
    before=hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
    app=explorer(open_app())
    click(app,'Generate investigation PDF')
    source(app,mode)
    assert '_investigation_pdf' not in app.session_state
    assert metrics(app)['Current resistance']=='Unavailable'
    click(app,'Next Batch')
    click(app,'Generate investigation PDF')
    payload=json.loads(app.session_state['_investigation_pdf']['manifest'])
    assert payload['source']==mode and payload['data_version']==1
    assert payload['patient_aware']['paired']['n_complete_pairs']==0
    assert hashlib.sha256(settings.database_path.read_bytes()).hexdigest()==before
    assert not app.exception and not app.error


def test_briefing_optional_patient_ui_and_toggle_invalidation(patient_ui):
    app=open_app()
    navigate(app,'Surveillance Briefing')
    app.selectbox(key='_briefing_organism').set_value(EC).run(timeout=30)
    app.selectbox(key='_briefing_specimen').set_value('urine').run(timeout=30)
    app.selectbox(key='_briefing_report_scope').set_value('Selected investigation').run(timeout=30)
    app.checkbox(key='_briefing_include_patient').check().run(timeout=30)
    click(app,'Generate PDF report')
    stored=app.session_state['_briefing_pdf']
    assert 'patient_aware' in json.loads(stored['manifest'])
    assert 'Exact McNemar p (unadjusted)' in pdf_text(stored['pdf'])
    app.checkbox(key='_briefing_include_patient').uncheck().run(timeout=30)
    assert '_briefing_pdf' not in app.session_state
    source(app,SCENARIO)
    app.session_state['_briefing_include_patient']=True
    app.selectbox(key='_briefing_report_scope').set_value('Selected investigation').run(timeout=30)
    assert app.checkbox(key='_briefing_include_patient').disabled
    click(app,'Generate PDF report')
    assert 'patient_aware' not in json.loads(app.session_state['_briefing_pdf']['manifest'])
    assert not app.exception and not app.error


def test_pure_summary_chart_do_not_query_or_reclassify(evidence,monkeypatch):
    a,p=evidence
    def fail(*args,**kwargs):
        raise AssertionError('Presentation must reuse computed objects')
    monkeypatch.setattr(AnomalyEngine,'scan',fail)
    monkeypatch.setattr(PatientAwareEngine,'analyze',fail)
    monkeypatch.setattr(AdvancedAnalysisEngine,'analyze',fail)
    assert summary_data(a,p)['status']==a.assessment.status
    assert transition_chart(p.paired) is not None
    assert comparison_rows(p)[1]['p_value']==p.sensitivity.p_value
