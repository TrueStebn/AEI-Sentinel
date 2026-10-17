from ui_navigation import navigate, source
"""Recreate Streamlit sessions with the private URL token, not Python state."""

from datetime import date

from streamlit.testing.v1 import AppTest

from src.database import connect, database_summary
from src.simulator import SCENARIO
from src.workspaces import WorkspaceStore
from test_app import APP, metrics, open_app, ui_data
from test_replay_interface import click, choose_scenario


def reconnect(token):
    app = AppTest.from_file(str(APP))
    app.query_params["workspace"] = token
    app.run(timeout=30)
    assert not app.exception and not app.error
    return app


def test_autosave_restores_filters_dates_threshold_page_and_period(ui_data, settings):
    app = open_app()
    token = app.query_params["workspace"]
    app.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    app.selectbox(key="_antibiotic_code").set_value("CIP").run()
    app.date_input(key="_analysis_date").set_value(date(2026, 9, 26)).run()
    app.number_input(key="_signal_threshold").set_value(18).run()
    navigate(app,"Detailed Analysis")
    app.date_input(key="_custom_period").set_value((date(2026, 9, 1), date(2026, 9, 26))).run()
    saved = WorkspaceStore(settings).load(token)
    assert saved.state["preferences"]["organism"] == "Klebsiella pneumoniae"
    restored = reconnect(token)
    assert restored.sidebar.radio[0].value == 'Explore'
    assert restored.selectbox(key="_organism").value == "Klebsiella pneumoniae"
    assert restored.selectbox(key="_antibiotic_code").value == "CIP"
    assert restored.date_input(key="_analysis_date").value == date(2026, 9, 26)
    assert restored.date_input(key="_custom_period").value == (date(2026, 9, 1), date(2026, 9, 26))
    assert restored.session_state["signal_threshold"] == 18
    assert metrics(restored)["Resistance Rate — Selected Period"] == "No Data"


def test_workspace_reruns_do_not_write_when_state_unchanged(ui_data, settings):
    app = open_app()
    token = app.query_params["workspace"]
    before = WorkspaceStore(settings).load(token)
    app.run()
    app.run()
    assert WorkspaceStore(settings).load(token) == before


def test_last_saved_caption_matches_final_committed_state_after_source_switch(ui_data):
    app = choose_scenario(open_app())
    navigate(app,'Data & Settings')
    stamp = app.session_state["_workspace_record"].last_saved_at
    assert any(item.value == f"Last Saved: {stamp} UTC" for item in app.caption)


def test_two_ui_identities_keep_independent_saved_filters(ui_data):
    first, second = open_app(), open_app()
    a, b = first.query_params["workspace"], second.query_params["workspace"]
    assert a != b
    first.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    ra, rb = reconnect(a), reconnect(b)
    assert ra.selectbox(key="_organism").value == "Klebsiella pneumoniae"
    assert rb.selectbox(key="_organism").value == "Escherichia coli"


def test_replay_restores_paused_after_new_session_and_can_finish(ui_data):
    app = choose_scenario(open_app())
    token = app.query_params["workspace"]
    click(app, "Next Batch")
    click(app, "Next Batch")
    click(app, "Start Replay")
    original = app.session_state["replay_controllers"][SCENARIO]
    restored = reconnect(token)
    recovered = restored.session_state["replay_controllers"][SCENARIO]
    assert not recovered.running and recovered.cursor == 2
    assert recovered.settings.database_path == original.settings.database_path
    assert metrics(restored)["Resistance Rate — Last 28 Days"] == "10.00%"
    click(restored, "Next Batch")
    assert metrics(restored)["Resistance Rate — Last 28 Days"] == "40.00%"
    assert metrics(restored)["Current Data Version"] == "3"


def test_replay_recovery_failure_offers_reset_without_showing_laboratory_metrics(ui_data):
    app = choose_scenario(open_app())
    token = app.query_params["workspace"]
    click(app, "Next Batch")
    replay = app.session_state["replay_controllers"][SCENARIO]
    replay.lease.close()
    replay.settings.database_path.unlink()
    restored = reconnect(token)
    assert any("missing" in item.value for item in restored.warning)
    assert not restored.metric
    click(restored, "Reset Replay")
    assert not restored.error and not restored.exception
    click(restored, "Next Batch")
    assert metrics(restored)["Current Data Version"] == "1"


def test_invalid_url_token_does_not_create_an_identity(ui_data, settings):
    app = AppTest.from_file(str(APP))
    app.query_params["workspace"] = "invalid-token"
    app.run()
    assert not app.exception and app.error
    assert any("restore token" in item.value for item in app.error)
    assert not app.metric
    store = WorkspaceStore(settings)
    with connect(store.settings, read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0


def test_failed_autosave_is_visible_and_can_retry_without_losing_filters(ui_data, settings):
    app = open_app()
    token = app.query_params["workspace"]
    store = WorkspaceStore(settings)
    with connect(store.settings) as con:
        con.execute("CREATE TRIGGER fail_save AFTER UPDATE ON user_sessions BEGIN SELECT RAISE(ABORT,'save unavailable'); END")
    app.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    assert not app.exception
    assert any("Unable to save" in item.value for item in app.warning)
    assert store.load(token).state["preferences"]["organism"] == "Escherichia coli"
    with connect(store.settings) as con:
        con.execute("DROP TRIGGER fail_save")
    click(app, "Retry autosave")
    assert store.load(token).state["preferences"]["organism"] == "Klebsiella pneumoniae"


def test_changed_dataset_recalculates_on_restore_but_keeps_pinned_preferences(ui_data, settings):
    app = open_app()
    token = app.query_params["workspace"]
    app.date_input(key="_analysis_date").set_value(date(2026, 9, 26)).run()
    with connect(settings) as con:
        con.execute("UPDATE data_version SET version=9")
    restored = reconnect(token)
    assert restored.date_input(key="_analysis_date").value == date(2026, 9, 26)
    assert metrics(restored)["Current Data Version"] == "9"
    assert metrics(restored)["Resistance Rate — Last 28 Days"] == "No Data"
    assert WorkspaceStore(settings).load(token).state["laboratory_version"] == 9


def test_manual_snapshot_save_rename_restore_and_confirmed_delete(ui_data, settings):
    app = open_app()
    token = app.query_params["workspace"]
    app.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    navigate(app,"Data & Settings")
    app.text_input(key="snapshot_title").set_value("Saved review")
    click(app, "Save Workspace")
    store = WorkspaceStore(settings)
    key = store.list_snapshots(token)[0]["workspace_id"]
    next(field for field in app.text_input if field.label == "New workspace title").set_value("Renamed review")
    click(app, "Rename Workspace")
    assert store.list_snapshots(token)[0]["title"] == "Renamed review"
    navigate(app,"Dashboard")
    app.selectbox(key="_organism").set_value("Escherichia coli").run()
    navigate(app,"Data & Settings")
    click(app, "Restore Saved Workspace")
    navigate(app,"Dashboard")
    assert app.selectbox(key="_organism").value == "Klebsiella pneumoniae"
    navigate(app,"Data & Settings")
    assert next(button for button in app.button if button.label == "Delete Workspace").disabled
    app.checkbox(key=f"delete_confirm_{key}").check().run()
    click(app, "Delete Workspace")
    assert not store.list_snapshots(token)


def test_manual_identity_restore_reloads_latest_after_a_write_conflict(ui_data, settings):
    app = open_app()
    token = app.query_params["workspace"]
    other = reconnect(token)
    other.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    app.selectbox(key="_antibiotic_code").set_value("CIP").run()
    assert app.session_state["workspace_save_error"]
    navigate(app,"Data & Settings")
    app.text_input(key="manual_restore_token").set_value(token)
    click(app, "Restore Workspace")
    assert app.selectbox(key="_organism").value == "Klebsiella pneumoniae"
    assert app.selectbox(key="_antibiotic_code").value == "AMP"
