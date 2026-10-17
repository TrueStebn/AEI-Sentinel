from ui_navigation import navigate, source
"""Real Streamlit routes, selected population separation and old workspace recovery."""

import json

import pytest

from src.anomalies import AnomalyEngine
from src.database import connect,transaction
from src.simulator import HISTORICAL,SCENARIO
from src.workspaces import WorkspaceStore,validate_state
from test_app import open_app,metrics
from test_explorer_interface import explorer
from test_replay_interface import click
from test_workspace_interface import reconnect
from test_resistance import observation,seed
from test_patient_aware_analysis import paired_rows


@pytest.fixture
def patient_ui(settings,seed,monkeypatch):
    seed(paired_rows([("S","S"),("S","R"),("S","R"),("R","S"),("R","R")])+
         [observation(f"old{i}","2026-08-01") for i in range(30)]+[observation(f"new{i}",result="R") for i in range(30)])
    monkeypatch.setenv("AEI_DATABASE_PATH",str(settings.database_path))


def test_explorer_three_populations_and_aggregate_export(patient_ui):
    app=explorer(open_app())
    assert metrics(app)["Observed only in baseline"]=="30"
    assert metrics(app)["Observed only in current"]=="30"
    assert metrics(app)["Observed in both periods"]=="5"
    transitions=next(frame.value for frame in app.dataframe if "Baseline → Current" in frame.value.columns)
    assert list(transitions["Patients"])==[1,2,1,1]
    assert any("Exact McNemar p (unadjusted): 1" in item.value for item in app.markdown)
    assert any("Sensitivity Fisher p (unadjusted)" in item.value for item in app.markdown)
    assert any("selection bias" in item.value.lower() for item in app.warning)
    assert len([item for item in app.get("download_button") if item.label=="Download patient-aware investigation (JSON)"])==1
    all_tables=" ".join(frame.value.to_json() for frame in app.dataframe)
    assert "shared-" not in all_tables and "old0" not in all_tables
    app.selectbox(key="_explorer_mode").set_value("phenotype").run(timeout=30)
    assert any("uses antibiotic S/R" in item.value for item in app.info)
    assert not app.exception and not app.error


def test_dashboard_finding_route_retains_origin_and_global_counts(patient_ui,settings):
    app=open_app()
    before=AnomalyEngine(settings).scan()
    assert metrics(app)["Potential Deviations"]=="0" and metrics(app)["Eligible Comparisons"]=="0"
    click(app,"Patient-Aware Investigation")
    assert app.sidebar.radio[0].value=='Explore'
    assert app.session_state["explorer_section"]=="patient_aware"
    origin=app.session_state["explorer_origin"]
    assert origin["kind"]=="preliminary" and origin["data_version"]==7
    assert origin["organism"]=="Escherichia coli" and origin["specimen"]=="urine"
    assert any("Original current S/R/N: 2/33/35" in item.value for item in app.markdown)
    assert AnomalyEngine(settings).scan()==before


def test_selected_ordinary_dashboard_route_has_no_manufactured_origin(patient_ui):
    app=open_app()
    click(app,"Review selected patient overlap")
    assert app.session_state["explorer_origin"] is None
    assert app.session_state["explorer_section"]=="patient_aware"
    assert metrics(app)["Observed in both periods"]=="5"


def test_briefing_patient_route_recovery_and_subgroup_separation(patient_ui,settings):
    app=open_app()
    navigate(app,"Surveillance Briefing")
    assert metrics(app)["Statistically supported alerts"]=="0"
    click(app,"Patient-aware")
    assert app.session_state["explorer_section"]=="patient_aware"
    origin=dict(app.session_state["explorer_origin"])
    app.selectbox(key="_explorer_sex").set_value("M").run(timeout=30)
    assert metrics(app)["Observed in both periods"]=="0"
    assert app.session_state["explorer_origin"]==origin
    assert any("Original current S/R/N: 2/33/35" in item.value for item in app.markdown)
    token=app.query_params["workspace"]
    restored=reconnect(token)
    assert restored.session_state["explorer_section"]=="patient_aware"
    assert restored.session_state["explorer_origin"]==origin
    assert metrics(restored)["Observed in both periods"]=="0"
    state=WorkspaceStore(settings).load(token).state
    assert "patient_aware" in json.dumps(state)
    assert all(word not in json.dumps(state) for word in ("s_to_r","n_complete_pairs","shared-0"))
    legacy={"format":1,"preferences":{"navigation":"Advanced Anomaly Explorer"},"source_filters":{},"replays":{},"laboratory_version":7}
    assert validate_state(legacy)==legacy


@pytest.mark.parametrize("mode",[HISTORICAL,SCENARIO])
def test_replay_source_isolation_empty_partial_and_settings(patient_ui,settings,mode):
    app=explorer(open_app())
    source(app,mode)
    assert metrics(app)["Observed in both periods"]=="0"
    click(app,"Next Batch")
    assert any(f"Source: {mode} · Version: 1" in item.value for item in app.caption)
    if mode==SCENARIO:
        for _ in range(2):
            click(app,"Next Batch")
        assert metrics(app)["Observed in both periods"]=="0"
        assert any("Sensitivity Fisher p (unadjusted)" in item.value for item in app.markdown)
    source(app,"Laboratory Dataset")
    assert metrics(app)["Observed in both periods"]=="5"
    with connect(settings,read_only=True) as con:
        assert con.execute("SELECT version FROM data_version").fetchone()[0]==7
    assert not app.exception and not app.error


def test_new_revision_recalculates_patient_counts(patient_ui,settings):
    app=explorer(open_app())
    assert metrics(app)["Observed in both periods"]=="5"
    with connect(settings) as con,transaction(con):
        con.execute("UPDATE susceptibility SET result='S' WHERE result='R'")
        con.execute("UPDATE data_version SET version=8")
    click(app,"Refresh data")
    assert any("Version: 8" in item.value for item in app.caption)
    assert any("Exact McNemar p (unadjusted): 1" in item.value for item in app.markdown)
    transitions=next(frame.value for frame in app.dataframe if "Baseline → Current" in frame.value.columns)
    assert list(transitions["Patients"])==[5,0,0,0]
    assert not app.exception and not app.error
