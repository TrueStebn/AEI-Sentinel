from ui_navigation import navigate, source
"""Native selection integration, ordinary navigation and compatible autosave."""

from copy import deepcopy

import pytest

from src.database import connect
from src.map_interface import accept_selection
from src.simulator import HISTORICAL, SCENARIO
from src.surveillance_map import SurveillanceMapEngine
from src.advanced_analysis import CohortFilters
from src.workspaces import WorkspaceError, WorkspaceStore, validate_state
from test_app import open_app, ui_data, metrics
from test_replay_interface import click
from test_workspace_interface import reconnect


def open_map(app):
    navigate(app,"Surveillance Map")
    assert not app.exception and not app.error
    return app


def test_matrix_current_annual_change_and_sparse_details(ui_data):
    app = open_map(open_app())
    app.selectbox(key="_map_selected_ward").set_value("ED").run()
    app.selectbox(key="_map_selected_antibiotic").set_value("CIP").run()
    assert any("23.33%" in item.value for item in app.markdown)
    app.selectbox(key="_map_period").set_value("current").run()
    assert any("Tested=30" in item.value for item in app.caption)
    app.selectbox(key="_map_view").set_value("change").run()
    assert any("No Data" in item.value for item in app.markdown)
    app.selectbox(key="_map_view").set_value("level").run()
    app.selectbox(key="_map_selected_ward").set_value("NEW-WARD").run()
    assert any("No Data" in item.value for item in app.markdown)
    app.selectbox(key="_map_sex").set_value("M").run()
    assert not app.exception and not app.error


def test_map_navigation_transfers_filters_not_alert(ui_data):
    app = open_map(open_app())
    app.selectbox(key="_map_sex").set_value("F").run()
    app.selectbox(key="_map_age_group").set_value("custom").run()
    app.number_input(key="_map_age_min").set_value(40).run()
    app.number_input(key="_map_age_max").set_value(50).run()
    app.selectbox(key="_map_ward_type").set_value("out").run()
    app.selectbox(key="_map_institution").set_value("hospital").run()
    app.selectbox(key="_map_selected_ward").set_value("ED").run()
    app.selectbox(key="_map_selected_antibiotic").set_value("CIP").run()
    click(app, "Investigate this cell")
    assert app.sidebar.radio[0].value == 'Explore'
    assert app.selectbox(key="_ward_code").value == "ED"
    assert app.selectbox(key="_antibiotic_code").value == "CIP"
    assert app.selectbox(key="_explorer_sex").value == "F"
    assert app.number_input(key="_explorer_age_min").value == 40
    assert app.number_input(key="_explorer_age_max").value == 50
    assert app.session_state["explorer_origin"] is None
    assert metrics(app)["Current 28 days"] == "23.33%"
    assert app.session_state["data_source"] == "Laboratory Dataset"


def test_native_selection_reaches_valid_cell_and_old_event_does_not_override_manual(ui_data, settings, monkeypatch):
    import src.map_interface as interface
    report = SurveillanceMapEngine(settings).analyze(CohortFilters())
    state = {}
    monkeypatch.setattr(interface.st, "session_state", state)
    event = {"selection": {"points": [{"x": "CIP", "y": "ED"}]}}
    accept_selection(report, event, "plot1")
    assert (state["map_selected_ward"], state["map_selected_antibiotic"]) == ("ED", "CIP")
    state["map_selected_ward"] = "NEW-WARD"
    accept_selection(report, event, "plot1")
    assert state["map_selected_ward"] == "NEW-WARD"
    accept_selection(report, {"selection": {"points": [{"x": "invalid", "y": "ED"}]}}, "plot2")
    assert state["map_selected_ward"] == "NEW-WARD"


def test_map_autosave_reconnect_and_old_workspace_compatibility(ui_data, settings):
    app = open_map(open_app())
    for key, value in [("view", "change"), ("period", "current"), ("sex", "F"), ("age_group", "65+"),
                       ("ward_type", "out"), ("institution", "hospital"), ("sort", "volume"), ("selected_ward", "ED"), ("selected_antibiotic", "CIP")]:
        app.selectbox(key="_map_"+key).set_value(value).run()
    token = app.query_params["workspace"]
    restored = reconnect(token)
    assert restored.sidebar.radio[0].value == 'Explore'
    for key in ("view", "period", "sex", "age_group", "ward_type", "institution", "sort", "selected_ward", "selected_antibiotic"):
        assert restored.session_state["map_"+key] == app.session_state["map_"+key]
    state = WorkspaceStore(settings).load(token).state
    old = deepcopy(state)
    old["preferences"] = {"navigation": "Dashboard", "organism": "Escherichia coli"}
    old["source_filters"] = {}
    assert validate_state(old) == old
    for key, value in [("map_view", "unsafe"), ("map_period", "lifetime"), ("map_age_min", True), ("map_sex", "X"), ("map_results", [1])]:
        invalid = deepcopy(old)
        invalid["preferences"][key] = value
        with pytest.raises(WorkspaceError):
            validate_state(invalid)


@pytest.mark.parametrize("mode", [HISTORICAL, SCENARIO])
def test_map_empty_partial_replay_source_isolation_and_preferences(ui_data, settings, mode):
    app = open_map(open_app())
    app.selectbox(key="_map_sex").set_value("F").run()
    source(app,mode)
    assert any("No Data" in item.value for item in app.info)
    click(app, "Next Batch")
    assert not app.exception and not app.error
    assert any("Version: 1" in item.value for item in app.caption)
    assert app.selectbox(key="_map_sex").value == "__all__"
    source(app,"Laboratory Dataset")
    assert app.selectbox(key="_map_sex").value == "F"
    with connect(settings, read_only=True) as con:
        assert con.execute("SELECT version FROM data_version").fetchone()[0] == 1


def test_scenario_map_markers_and_ordinary_cell_navigation_remain_separate(ui_data):
    app = open_map(open_app())
    source(app,SCENARIO)
    for _ in range(3):
        click(app, "Next Batch")
    app.selectbox(key="_map_view").set_value("change").run()
    assert "A" not in list(app.dataframe[0].value["Indicator"])  # Initial empty-source All context remains pooled.
    app.selectbox(key="_organism").set_value("Escherichia coli").run()
    app.selectbox(key="_specimen").set_value("urine").run()
    assert "A" in list(app.dataframe[0].value["Indicator"])
    app.selectbox(key="_map_selected_ward").set_value("SIM").run()
    app.selectbox(key="_map_selected_antibiotic").set_value("CIP").run()
    click(app, "Investigate this cell")
    assert app.session_state["data_source"] == SCENARIO
    assert app.session_state["explorer_origin"] is None
    assert metrics(app)["Current 28 days"] == "40.00%"
