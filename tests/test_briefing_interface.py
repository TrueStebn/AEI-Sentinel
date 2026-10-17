from ui_navigation import navigate, source
"""End-to-end preferences, investigation actions, refresh invalidation and source isolation."""

from copy import deepcopy

import pytest

from src.database import connect, transaction
from src.workspaces import WorkspaceError, WorkspaceStore, validate_state
from src.simulator import SCENARIO, HISTORICAL
from src.map_charts import matrix_chart, visible_antibiotics
from src.surveillance_map import SurveillanceMapEngine
from src.advanced_analysis import CohortFilters
from test_app import open_app, ui_data, metrics
from test_replay_interface import click
from test_workspace_interface import reconnect
from test_resistance import observation, seed


def open_brief(app):
    navigate(app,"Surveillance Briefing")
    assert not app.exception and not app.error
    return app


def test_hospital_ward_quality_and_selected_pdf(ui_data):
    app=open_brief(open_app())
    assert metrics(app)["No Data comparisons"]=="4"
    assert metrics(app)["Statistically supported alerts"]=="0"
    app.selectbox(key="_briefing_ward").set_value("ED").run(timeout=30)
    app.selectbox(key="_briefing_organism").set_value("Escherichia coli").run(timeout=30)
    app.selectbox(key="_briefing_specimen").set_value("urine").run(timeout=30)
    app.selectbox(key="_briefing_antibiotic").set_value("CIP").run(timeout=30)
    assert metrics(app)["Current 28 days"]=="23.33%"
    assert metrics(app)["No Data comparisons"]=="4" # global remains global
    assert any("Current N" in frame.value.columns for frame in app.dataframe)
    app.selectbox(key="_briefing_report_scope").set_value("Selected investigation").run()
    click(app,"Generate PDF report")
    assert app.session_state["_briefing_pdf"]["pdf"].startswith(b"%PDF")
    assert len(app.get("download_button"))==2
    app.selectbox(key="_briefing_ward").set_value("NEW-WARD").run()
    assert "_briefing_pdf" not in app.session_state
    assert metrics(app)["Current 28 days"]=="No Data"


def test_scenario_queue_action_preserves_original_scope(ui_data):
    app=open_brief(open_app())
    source(app,SCENARIO)
    for _ in range(3):
        click(app,"Next Batch")
    assert metrics(app)["Statistically supported alerts"]=="1"
    app.selectbox(key="_briefing_sort").set_value("Ward").run()
    app.selectbox(key="_briefing_status").set_value("Statistically supported").run()
    click(app,"Investigate")
    assert app.sidebar.radio[0].value=='Explore'
    origin=app.session_state["explorer_origin"]
    assert origin["kind"]=="BY-alert" and origin["source"]==SCENARIO
    assert origin["ward_code"]=="SIM" and origin["data_version"]==3
    assert origin["analysis_date"]=="2026-09-02"
    assert metrics(app)["Current 28 days"]=="40.00%"
    assert app.selectbox(key="_explorer_sex").value=="__all__"


def test_briefing_autosave_reconnect_and_legacy_workspace(ui_data,settings):
    app=open_brief(open_app())
    for key,value in (("ward","ED"),("organism","Escherichia coli"),("specimen","urine"),("antibiotic","CIP"),("sort","Ward"),("report_scope","Selected investigation")):
        app.selectbox(key="_briefing_"+key).set_value(value).run(timeout=30)
    token=app.query_params["workspace"]
    restored=reconnect(token)
    assert restored.sidebar.radio[0].value=='Reports'
    assert restored.selectbox(key="_briefing_ward").value=="ED"
    assert restored.selectbox(key="_briefing_report_scope").value=="Selected investigation"
    assert not restored.exception and not restored.error
    state=WorkspaceStore(settings).load(token).state
    old=deepcopy(state)
    old["preferences"]={"navigation":"Surveillance Map","map_view":"level"}
    old["source_filters"]={}
    assert validate_state(old)==old
    invalid=deepcopy(old)
    invalid["preferences"]["briefing_specimen"]="invented"
    with pytest.raises(WorkspaceError):
        validate_state(invalid)


@pytest.mark.parametrize("mode",[HISTORICAL,SCENARIO])
def test_replay_source_preferences_labels_updates_and_pdf_invalidation(ui_data,settings,mode):
    app=open_brief(open_app())
    app.selectbox(key="_briefing_ward").set_value("ED").run()
    source(app,mode)
    assert metrics(app)["No Data comparisons"]=="0"
    assert app.selectbox(key="_briefing_ward").value=="__all__"
    click(app,"Generate PDF report")
    assert app.session_state["_briefing_pdf"]["signature"][1]==0
    click(app,"Next Batch")
    assert "_briefing_pdf" not in app.session_state
    assert any("New import revision: 0" in item.value for item in app.info)
    click(app,"Generate PDF report")
    assert mode in app.session_state["_briefing_pdf"]["manifest"]
    source(app,"Laboratory Dataset")
    assert app.selectbox(key="_briefing_ward").value=="ED"
    with connect(settings,read_only=True) as con:
        assert con.execute("SELECT version FROM data_version").fetchone()[0]==1


def test_dataset_change_invalidates_report_and_updates_summary(ui_data,settings):
    app=open_brief(open_app())
    click(app,"Generate PDF report")
    assert "_briefing_pdf" in app.session_state
    with connect(settings) as con,transaction(con):
        con.execute("UPDATE data_version SET version=2")
    click(app,"Refresh data")
    assert "_briefing_pdf" not in app.session_state
    assert any("New import revision: 1" in item.value for item in app.info)
    assert not app.exception and not app.error


def test_map_readability_keeps_numbers_statuses_and_ordinary_navigation(settings,seed):
    seed([observation(str(i),result="R" if i<12 else "S") for i in range(30)])
    r=SurveillanceMapEngine(settings).analyze(CohortFilters(organism="Escherichia coli",specimen="urine"))
    full=matrix_chart(r)
    focused=matrix_chart(r,scale_max=50)
    assert sorted(text for trace in full.data for text in trace.text)==sorted(text for trace in focused.data for text in trace.text)
    assert any(trace.marker.cmax==50 for trace in focused.data)
    visible=visible_antibiotics(r,hide_empty=True)
    assert [code for code,_ in visible]==["CIP"]
    hidden=matrix_chart(r,hide_empty=True)
    assert all(x=="CIP" for trace in hidden.data for x in trace.x)
    assert any("No Data" in text for trace in full.data for text in trace.hovertext)
    assert r.cells==SurveillanceMapEngine(settings).analyze(r.cohort).cells


def test_map_new_preferences_restore(ui_data):
    app=open_app()
    navigate(app,"Surveillance Map")
    app.selectbox(key="_map_scale").set_value("focused").run()
    app.selectbox(key="_map_columns").set_value("hide_empty").run()
    restored=reconnect(app.query_params["workspace"])
    assert restored.selectbox(key="_map_scale").value=="focused"
    assert restored.selectbox(key="_map_columns").value=="hide_empty"
    assert not restored.exception and not restored.error
