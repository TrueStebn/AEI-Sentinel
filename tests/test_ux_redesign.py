"""New home, dynamic units, source-consistent counts, shared context and modes."""

import hashlib,json
from contextlib import contextmanager
from copy import deepcopy
from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from src.anomalies import AnomalyEngine
from src.analysis_service import DatasetChangedError
from src.database import connect,transaction
from src.overview import build_overview
from src.ui_data import load_catalog
from src.ux_state import normalize_route,validate_context,selected_cohort,LEGACY_ROUTES
from src.workspaces import FILTERS,PAGES,WorkspaceStore,validate_state,WorkspaceError
from src.simulator import SCENARIO,HISTORICAL
from test_resistance import seed,observation
from test_app import APP,metrics,ui_data
from test_workspace_interface import reconnect
from test_briefing import populations
from ui_navigation import navigate,source,replay_click


def home():
    app=AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception and not app.error
    return app


def click(app,label):
    next(b for b in app.button if b.label==label).click().run(timeout=30)
    assert not app.exception and not app.error
    return app


@pytest.fixture
def activity(settings,seed):
    seed([observation('outside','2026-08-30'),observation('start','2026-08-31',tests={'CIP':'S','AMP':'R'}),
          observation('two','2026-09-27',culture_id='multi',isolate_no=1),
          observation('two','2026-09-27',culture_id='multi',isolate_no=2,organism='Klebsiella pneumoniae',tests={'CIP':'R','AMP':'R'}),
          observation('future','2026-09-28'),observation('uro','2026-09-15',ward_code='URO')])
    with connect(settings) as con,transaction(con):
        con.execute("INSERT INTO wards VALUES ('CENTRE-X','Centre','New community centre','out','primary_care')")
        con.execute("INSERT INTO cultures SELECT 'negative',patient_id,age_years,'2026-09-10',2026,37,'ED',NULL,'urine','virtsa','negative',0 FROM cultures LIMIT 1")


def test_distinct_culture_dates_negative_results_and_dynamic_unit_types(settings,activity):
    r=build_overview(settings,as_of_date='2026-09-27')
    units={u.code:u for u in r.units}
    assert set(units)=={'ED','URO','CENTRE-X'}
    assert units['ED'].cultures_28d==3 # start + multi-isolate culture + negative culture, no AST multiplication
    assert units['ED'].latest_sample==date(2026,9,27)
    assert units['URO'].cultures_28d==1 and units['URO'].latest_sample==date(2026,9,15)
    assert units['CENTRE-X'].cultures_28d==0 and units['CENTRE-X'].latest_sample is None
    assert units['ED'].type_label=='Hospital department' and units['ED'].ward_type=='out'
    assert units['CENTRE-X'].type_label=='Primary care centre'
    assert r.latest_sample==date(2026,9,28) # source availability differs from reporting-date activity


def test_no_per_card_scan_or_query_and_primary_read_only(settings,activity,monkeypatch):
    import src.overview as module
    catalog=load_catalog(settings)
    before=settings.database_path.read_bytes()
    statements=[];scans=[]
    actual=module.connect;scan=AnomalyEngine.scan
    @contextmanager
    def traced(*args,**kwargs):
        with actual(*args,**kwargs) as con:
            con.set_trace_callback(statements.append)
            yield con
    def counted(self,**kwargs):
        scans.append(kwargs)
        return scan(self,**kwargs)
    monkeypatch.setattr(module,'connect',traced)
    monkeypatch.setattr(AnomalyEngine,'scan',counted)
    r=build_overview(settings,catalog=catalog,as_of_date='2026-09-27')
    assert len(r.units)==3 and len(scans)==1
    assert len([s for s in statements if s.lstrip().upper().startswith('SELECT')])==3
    assert len([s for s in statements if 'GROUP BY w.ward_code' in s])==1
    assert settings.database_path.read_bytes()==before


def test_overview_rejects_revision_before_and_after_scan(settings,activity,monkeypatch):
    catalog=load_catalog(settings)
    with pytest.raises(DatasetChangedError):
        build_overview(settings,catalog=catalog,expected_version=99)
    original=AnomalyEngine.scan
    def change(self,**kwargs):
        r=original(self,**kwargs)
        with connect(settings) as con,transaction(con):
            con.execute('UPDATE data_version SET version=8')
        return r
    monkeypatch.setattr(AnomalyEngine,'scan',change)
    with pytest.raises(DatasetChangedError):
        build_overview(settings,catalog=catalog)


def test_original_global_and_unit_alert_counts_overlap_correctly(settings,populations):
    original=AnomalyEngine(settings).scan()
    report=build_overview(settings)
    assert report.scan==original
    assert len(report.preliminary)==2 and len(report.scan.alerts)==1 and len(report.findings)==2
    units={u.code:u for u in report.units}
    assert (units['ED'].preliminary_count,units['ED'].supported_count)==(1,1)
    assert (units['URO'].preliminary_count,units['URO'].supported_count)==(1,0)
    assert all(item.comparison in original.results for item in report.findings)
    higher=build_overview(settings,signal_threshold=100)
    assert not higher.preliminary and len(higher.findings)==1 and higher.findings[0].kind=='BY-alert'
    assert higher.scan.alerts==original.alerts


@pytest.mark.parametrize('old',tuple(LEGACY_ROUTES))
def test_legacy_navigation_restores_without_schema_migration(old):
    original={'format':1,'preferences':{'navigation':old},'source_filters':{},'replays':{},'laboratory_version':0}
    assert validate_state(original)==original
    state=dict(original['preferences']);normalize_route(state)
    assert state['navigation']==LEGACY_ROUTES[old][0] and state['presentation_mode']=='Guided'
    if LEGACY_ROUTES[old][1]:
        assert state['explore_view' if state['navigation']=='Explore' else 'reports_view']==LEGACY_ROUTES[old][1]


def test_old_briefing_context_and_strict_new_preferences():
    state={'navigation':'Surveillance Briefing','ward_code':'URO','briefing_ward':'ED','briefing_antibiotic':'CIP'}
    normalize_route(state)
    assert state['ward_code']=='ED' and state['antibiotic_code']=='CIP'
    legacy_map={'navigation':'Surveillance Map','map_sex':'F','source_filters':{SCENARIO:{'map_sex':'M'}}}
    normalize_route(legacy_map)
    assert legacy_map['explorer_sex']=='F' and legacy_map['source_filters'][SCENARIO]['explorer_sex']=='M'
    with pytest.raises(WorkspaceError):
        validate_state({'format':1,'preferences':{'presentation_mode':'Different algorithm'}})


@pytest.mark.parametrize('old',tuple(LEGACY_ROUTES))
def test_real_legacy_workspace_reconnect_normalizes_route(ui_data,settings,old):
    store=WorkspaceStore(settings)
    token,record=store.create()
    state={'format':1,'preferences':{'navigation':old,'ward_code':'ED','organism':'Escherichia coli',
        'antibiotic_code':'CIP','specimen':'urine'},'source_filters':{},'replays':{},'laboratory_version':1}
    store.save(token,state,record.revision)
    app=reconnect(token)
    assert app.sidebar.radio[0].value==LEGACY_ROUTES[old][0]
    assert app.session_state['ward_code']=='ED'
    assert app.radio(key='_presentation_mode').value=='Guided'


def test_source_catalog_invalid_values_and_subgroup_shared_state(settings,activity):
    state={'ward_code':'missing','organism':'missing','antibiotic_code':'missing','specimen':'blood',
        'explorer_sex':'F','explorer_age_group':'custom','explorer_age_min':40,'explorer_age_max':65,'explorer_home_area':'Centre'}
    validate_context(state,load_catalog(settings))
    assert state['ward_code'] is None and state['organism']=='Escherichia coli' and state['antibiotic_code'] in ('AMP','CIP')
    assert state['specimen']=='urine' and state['map_sex']=='F'
    cohort=selected_cohort(state)
    assert (cohort.sex,cohort.age_min,cohort.age_max,cohort.home_area)==('F',40,65,'Centre')


def test_new_workspace_guided_overview_four_routes_and_dynamic_cards(ui_data):
    app=home()
    assert app.sidebar.radio[0].options==list(PAGES) and app.sidebar.radio[0].value=='Overview'
    assert app.radio(key='_presentation_mode').value=='Guided'
    assert metrics(app)['Healthcare units']=='2'
    assert {'select_unit_ED','select_unit_NEW-WARD'}<=set(b.key for b in app.button)
    assert not any(item.key=='_organism' for item in app.selectbox)
    assert not any(item.key=='data_source' for item in app.selectbox)
    assert not any(item.key=='manual_restore_token' for item in app.text_input)
    assert not any('0% resistance' in item.value or 'Clinically safe' in item.value for item in app.markdown)


def test_expert_only_changes_presentation_and_restores(ui_data,settings):
    app=home();token=app.query_params['workspace']
    before={k:deepcopy(app.session_state[k]) for k in FILTERS if k in app.session_state}
    values=metrics(app)
    scan=AnomalyEngine(settings).scan()
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    assert metrics(app)==values
    assert {k:app.session_state[k] for k in before}==before
    assert AnomalyEngine(settings).scan()==scan
    restored=reconnect(token)
    assert restored.radio(key='_presentation_mode').value=='Expert' and restored.sidebar.radio[0].value=='Overview'
    assert metrics(restored)==values


def test_department_explore_reports_keep_selected_full_cohort(ui_data):
    app=home();click(app,'Emergency department')
    assert app.session_state['ward_code']=='ED'
    click(app,'Explore Emergency department')
    assert app.sidebar.radio[0].value=='Explore' and app.radio(key='_explore_view').value=='Surveillance Map'
    assert app.selectbox(key='_map_selected_ward').value=='ED'
    click(app,'Investigate this cell')
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    app.selectbox(key='_antibiotic_code').set_value('CIP').run(timeout=30)
    app.selectbox(key='_explorer_sex').set_value('F').run(timeout=30)
    current=metrics(app)['Current resistance']
    before={k:deepcopy(app.session_state[k]) for k in FILTERS if k in app.session_state}
    values=metrics(app)
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    assert metrics(app)==values and {k:app.session_state[k] for k in before}==before
    click(app,'Create report')
    assert app.sidebar.radio[0].value=='Reports' and app.radio(key='_reports_view').value=='Current Investigation'
    assert metrics(app)['Current resistance']==current
    click(app,'Generate investigation PDF')
    payload=json.loads(app.session_state['_investigation_pdf']['manifest'])
    assert payload['patient_aware']['cohort']['ward_code']=='ED' and payload['patient_aware']['cohort']['sex']=='F'
    assert payload['patient_aware']['antibiotic_code']=='CIP'
    assert all(secret not in json.dumps(payload) for secret in ('patient_id','workspace=','restore_token'))


def test_original_finding_explore_reports_preserves_exact_locator(settings,populations,monkeypatch):
    monkeypatch.setenv('AEI_DATABASE_PATH',str(settings.database_path))
    original=AnomalyEngine(settings).scan()
    app=home()
    app.button(key='overview_finding_1_open').click().run(timeout=30)
    origin=dict(app.session_state['explorer_origin'])
    assert origin['kind']=='preliminary' and origin['ward_code']=='URO' and origin['antibiotic_code']=='CIP'
    assert origin['source']=='Laboratory Dataset' and origin['data_version']==7 and origin['analysis_date']=='2026-09-27'
    assert app.sidebar.radio[0].value=='Explore'
    click(app,'Create report')
    assert app.session_state['explorer_origin']==origin
    click(app,'Generate investigation PDF')
    payload=json.loads(app.session_state['_investigation_pdf']['manifest'])
    assert payload['data_version']==7 and payload['patient_aware']['cohort']['ward_code']=='URO'
    assert AnomalyEngine(settings).scan()==original


def test_department_report_is_naturally_selected(ui_data):
    app=home();click(app,'Emergency department');click(app,'Department-focused report')
    assert app.sidebar.radio[0].value=='Reports'
    assert app.selectbox(key='_briefing_ward').value=='ED'
    assert app.selectbox(key='_briefing_report_scope').value=='Selected investigation'


def test_map_selected_cell_reports_exact_context_without_alert(ui_data):
    app=home();navigate(app,'Explore','Surveillance Map')
    assert next(b for b in app.button if b.label=='Create report').disabled
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    app.selectbox(key='_map_selected_ward').set_value('ED').run(timeout=30)
    app.selectbox(key='_map_selected_antibiotic').set_value('CIP').run(timeout=30)
    app.selectbox(key='_map_sex').set_value('F').run(timeout=30)
    click(app,'Create report')
    assert app.sidebar.radio[0].value=='Reports'
    assert app.session_state['ward_code']=='ED' and app.session_state['antibiotic_code']=='CIP'
    assert app.session_state['explorer_origin'] is None
    click(app,'Generate investigation PDF')
    cohort=json.loads(app.session_state['_investigation_pdf']['manifest'])['patient_aware']['cohort']
    assert (cohort['ward_code'],cohort['sex'])==('ED','F')


@pytest.mark.parametrize('mode',[SCENARIO,HISTORICAL])
def test_replay_overview_empty_partial_mode_and_primary_isolation(ui_data,settings,mode):
    before=hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
    app=home()
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    source(app,mode)
    assert metrics(app)['Healthcare units']=='0' and metrics(app)['Latest available sample']=='No Data'
    assert app.radio(key='_presentation_mode').value=='Expert'
    replay_click(app,'Next Batch')
    assert int(metrics(app)['Healthcare units'])>0
    assert app.session_state['ward_code'] is None
    if mode==SCENARIO:
        replay_click(app,'Next Batch');replay_click(app,'Next Batch')
        assert metrics(app)['Supported alerts']=='1' and metrics(app)['Preliminary signals']=='1'
    source(app,'Laboratory Dataset')
    assert metrics(app)['Healthcare units']=='2'
    assert hashlib.sha256(settings.database_path.read_bytes()).hexdigest()==before


def test_overview_new_revision_recalculates_activity(ui_data,settings):
    app=home()
    with connect(settings) as con,transaction(con):
        con.execute("UPDATE cultures SET ward_code='NEW-WARD' WHERE culture_id='C0'")
        con.execute('UPDATE data_version SET version=2')
    click(app,'Refresh data')
    assert any('Dataset revision 2' in x.proto.body for x in app.get('html'))
    report=build_overview(settings)
    assert next(u for u in report.units if u.code=='NEW-WARD').cultures_28d==1
    assert not app.exception and not app.error
