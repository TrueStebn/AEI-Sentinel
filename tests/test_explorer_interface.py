from ui_navigation import navigate, source
"""UI integration, signal provenance and Phase 7 recovery compatibility."""

from copy import deepcopy

import pytest

from src.database import connect, transaction
from src.investigations import origin_locator, resolve_origin
from src.anomalies import AnomalyEngine
from src.simulator import HISTORICAL, SCENARIO
from src.workspaces import WorkspaceError, WorkspaceStore, validate_state
from test_app import metrics, open_app, ui_data
from test_replay_interface import click
from test_workspace_interface import reconnect


def explorer(app):
    navigate(app,"Advanced Anomaly Explorer")
    assert not app.exception and not app.error
    return app


def test_subgroup_filter_and_no_misleading_zero(ui_data):
    app = explorer(open_app())
    app.selectbox(key="_antibiotic_code").set_value("CIP").run()
    assert metrics(app)["Current 28 days"] == "23.33%"
    app.selectbox(key="_explorer_sex").set_value("M").run()
    assert metrics(app)["Current 28 days"] == "No Data"
    app.selectbox(key="_explorer_sex").set_value("__all__").run()
    app.selectbox(key="_explorer_age_group").set_value("65+").run()
    assert metrics(app)["Current 28 days"] == "No Data"
    app.selectbox(key="_explorer_age_group").set_value("custom").run()
    app.number_input(key="_explorer_age_min").set_value(40).run()
    app.number_input(key="_explorer_age_max").set_value(50).run()
    assert metrics(app)["Current 28 days"] == "23.33%"
    assert not app.exception and not app.error


def test_phenotype_formula_labels_unsupported_and_groups(ui_data):
    app = explorer(open_app())
    app.selectbox(key="_explorer_mode").set_value("phenotype").run()
    assert metrics(app)["Current 28 days"] == "0.00%"
    assert any("Positive: 0" in item.value and "Eligible: 30" in item.value for item in app.caption)
    assert "_antibiotic_code" not in [item.key for item in app.selectbox]
    app.selectbox(key="_explorer_group_dimension").set_value("sex").run()
    rows = app.dataframe[0].value
    assert list(rows["Period"]) == ["F", "M"] and "Marker positive" in rows
    app.selectbox(key="_explorer_marker").set_value("MRSA").run()
    assert metrics(app)["Current 28 days"] == "Unsupported combination"
    assert any("not a negative" in item.value for item in app.warning)
    assert not app.exception and not app.error


def test_advanced_preferences_autosave_and_reconnect(ui_data, settings):
    app = explorer(open_app())
    token = app.query_params["workspace"]
    for key, value in [("mode", "phenotype"), ("marker", "ESBL"), ("sex", "F"), ("age_group", "custom"),
                       ("home_area", "Centre"), ("ward_type", "out"), ("institution", "hospital"), ("group_dimension", "home_area"), ("group_period", "baseline")]:
        app.selectbox(key="_explorer_"+key).set_value(value).run()
    app.number_input(key="_explorer_age_min").set_value(20).run()
    app.number_input(key="_explorer_age_max").set_value(80).run()
    saved = WorkspaceStore(settings).load(token)
    restored = reconnect(token)
    assert restored.sidebar.radio[0].value == 'Explore'
    for key in ("mode", "marker", "sex", "age_group", "home_area", "ward_type", "institution", "group_dimension", "group_period"):
        assert restored.session_state["explorer_"+key] == saved.state["preferences"]["explorer_"+key]
    assert restored.number_input(key="_explorer_age_min").value == 20
    assert restored.number_input(key="_explorer_age_max").value == 80
    assert metrics(restored)["Current 28 days"] == "0.00%"
    snapshot = WorkspaceStore(settings).save_snapshot(token, "Advanced view", saved.state)
    assert WorkspaceStore(settings).snapshot(token, snapshot) == saved.state


def test_old_phase7_state_loads_and_new_keys_are_strict(settings):
    old = {"format": 1, "preferences": {"navigation": "Dashboard", "organism": "Escherichia coli"},
           "source_filters": {"Laboratory Dataset": {"ward_code": None}}, "replays": {}, "laboratory_version": 1}
    assert validate_state(old) == old
    store = WorkspaceStore(settings)
    token, record = store.create()
    store.save(token, old, record.revision)
    assert store.load(token).state == old
    for name, value in [("explorer_marker", "OTHER"), ("explorer_sex", "X"), ("explorer_age_min", -1),
                        ("explorer_age_max", True), ("explorer_group_dimension", "patient_id"), ("patient_ids", ["PRIVATE"]),
                        ("explorer_origin", {"kind": "preliminary", "counts": [1, 2, 3]})]:
        state = deepcopy(old)
        state["preferences"][name] = value
        with pytest.raises(WorkspaceError):
            validate_state(state)


@pytest.mark.parametrize("mode", [HISTORICAL, SCENARIO])
def test_replay_empty_partial_source_filters_and_version_refresh(ui_data, settings, mode):
    app = explorer(open_app())
    app.selectbox(key="_explorer_sex").set_value("F").run()
    source(app,mode)
    assert not app.exception and not app.error
    assert metrics(app)["Current 28 days"] == "No Data"
    assert app.selectbox(key="_explorer_sex").value == "__all__"
    click(app, "Next Batch")
    assert any("Data version: 1" in item.value for item in app.caption)
    app.selectbox(key="_explorer_mode").set_value("phenotype").run()
    assert not app.exception and not app.error
    source(app,"Laboratory Dataset")
    assert app.selectbox(key="_explorer_sex").value == "F"
    assert app.selectbox(key="_explorer_mode").value == "antibiotic"
    with connect(settings, read_only=True) as con:
        assert con.execute("SELECT version FROM data_version").fetchone()[0] == 1


def test_signal_drilldown_original_vs_new_subgroup_and_recovery(ui_data):
    app = open_app()
    source(app,SCENARIO)
    for _ in range(3):
        click(app, "Next Batch")
    click(app, "Open Investigation")
    assert app.sidebar.radio[0].value == 'Explore'
    assert app.selectbox(key="_ward_code").value == "SIM"
    assert app.selectbox(key="_antibiotic_code").value == "CIP"
    assert metrics(app)["Current 28 days"] == "40.00%"
    assert any("Original current S/R/N: 60/40/100" in item.value for item in app.markdown)
    app.selectbox(key="_explorer_sex").set_value("M").run()
    assert any("does not classify" in item.value for item in app.info)
    assert metrics(app)["Current 28 days"] == "No Data"
    assert any("Fisher test withheld" in item.value for item in app.caption)
    token = app.query_params["workspace"]
    restored = reconnect(token)
    assert restored.session_state["explorer_origin"]["source"] == SCENARIO
    assert restored.selectbox(key="_explorer_sex").value == "M"
    # A supported scan alert has its own route and retains BY evidence.
    navigate(app,"Dashboard")
    click(app, "Open Alert Investigation")
    assert app.session_state["explorer_origin"]["kind"] == "BY-alert"
    assert any("Original BY-adjusted p=" in item.value for item in app.caption)


def test_origin_version_or_database_mismatch_never_reuses_counts(ui_data, settings):
    result = AnomalyEngine(settings).scan().results[0]
    origin = origin_locator(result, "preliminary", settings, "Laboratory Dataset")
    assert resolve_origin(origin, settings, "Laboratory Dataset", 1) == result
    assert resolve_origin(origin, settings, SCENARIO, 1) is None
    assert resolve_origin(origin, settings, "Laboratory Dataset", 2) is None
    assert resolve_origin({**origin, "database_fingerprint": "0"*64}, settings, "Laboratory Dataset", 1) is None


def test_empty_primary_and_phenotype_without_susceptibility_reference(settings, monkeypatch):
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    app = explorer(open_app())
    app.selectbox(key="_explorer_mode").set_value("phenotype").run()
    assert metrics(app)["Current 28 days"] == "No Data" and not app.exception and not app.error
