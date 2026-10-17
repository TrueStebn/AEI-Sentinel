from ui_navigation import replay_click
from ui_navigation import navigate, source
"""Full application rerenders after append-only replay imports."""

from datetime import date

import pytest

from src.database import database_summary
from src.replay_interface import LABORATORY
from src.simulator import SCENARIO, HISTORICAL
from test_app import metrics, open_app, ui_data
from test_anomalies import cohort_rows, seed


def click(app, label):
    if label in ("Next Batch","Start Replay","Pause Replay","Reset Replay"):
        return replay_click(app,label)
    next(button for button in app.button if button.label == label).click().run(timeout=30)
    assert not app.exception and not app.error
    return app


def choose_scenario(app):
    source(app,SCENARIO)
    assert not app.exception and not app.error
    return app


def test_scenario_dashboard_refreshes_after_each_batch(ui_data, settings):
    app = choose_scenario(open_app())
    assert any("Simulated Surveillance Scenario" in item.value for item in app.info)
    assert app.warning and not app.metric
    click(app, "Next Batch")
    assert metrics(app)["Current Data Version"] == "1"
    assert app.date_input(key="_analysis_date").value == date(2026, 8, 1)
    click(app, "Next Batch")
    assert metrics(app)["Resistance Rate — Last 28 Days"] == "10.00%"
    assert metrics(app)["Resistance Rate — Last 365 Days"] == "10.00%"
    assert metrics(app)["Preliminary Signals"] == "0"
    assert metrics(app)["Statistically Supported Alerts"] == "0"
    assert metrics(app)["Current Data Version"] == "2"
    assert any("Last Successful Import:" in item.value for item in app.caption)
    click(app, "Next Batch")
    assert metrics(app)["Resistance Rate — Last 28 Days"] == "40.00%"
    assert metrics(app)["Resistance Rate — Last 365 Days"] == "25.00%"
    assert metrics(app)["Preliminary Signals"] == metrics(app)["Statistically Supported Alerts"] == "1"
    assert metrics(app)["Number of Tested Isolates"] == "100"
    assert metrics(app)["Current Data Version"] == "3"
    assert app.date_input(key="_analysis_date").value == date(2026, 9, 2)
    assert any("Dataset revision 3" in item.proto.body for item in app.get('html'))  # Revision remains visible in the compact header.
    assert len(app.get("plotly_chart")) == 4
    assert database_summary(settings)["version"] == 1
    app.number_input(key="_signal_threshold").set_value(30).run(timeout=30)
    assert metrics(app)["Preliminary Signals"] == "0"
    assert metrics(app)["Statistically Supported Alerts"] == "1"  # Thresholds are independent.
    click(app, "Reset Replay")
    assert not app.metric and app.warning
    click(app, "Next Batch")
    assert metrics(app)["Current Data Version"] == "1"


def test_modes_preserve_filters_pause_and_keep_shared_imports_separate(ui_data):
    app = open_app()
    app.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    choose_scenario(app)
    click(app, "Start Replay")
    controller = app.session_state["replay_controllers"][SCENARIO]
    assert controller.running
    click(app, "Pause Replay")
    assert not controller.running
    click(app, "Start Replay")
    source(app,LABORATORY)
    assert not controller.running
    assert app.selectbox(key="_organism").value == "Klebsiella pneumoniae"
    choose_scenario(app)
    navigate(app,"Data Management")
    assert not app.exception and not app.error
    assert not app.get("file_uploader")
    assert any("planned replay batches" in item.value for item in app.info)


def test_sessions_reset_and_playback_speed_are_independent(ui_data):
    first, second = choose_scenario(open_app()), choose_scenario(open_app())
    click(first, "Next Batch")
    a = first.session_state["replay_controllers"][SCENARIO]
    b = second.session_state["replay_controllers"][SCENARIO]
    assert a.settings.database_path != b.settings.database_path
    assert a.cursor == 1 and b.cursor == 0
    navigate(first,"Data & Settings")
    first.selectbox(key=f"speed_{SCENARIO}").set_value(5).run()
    assert a.interval == 5 and b.interval == 2
    click(second, "Reset Replay")
    assert a.cursor == 1 and database_summary(a.settings)["version"] == 1


def test_historical_mode_uses_weekly_original_records(ui_data, settings):
    app = open_app()
    source(app,HISTORICAL)
    click(app, "Next Batch")
    # The first week contains only a negative culture: no fabricated susceptibility.
    assert app.warning and not app.metric
    click(app, "Next Batch")
    assert app.date_input(key="_analysis_date").value == date(2026, 9, 27)
    assert metrics(app)["Current Data Version"] == "2"
    assert app.session_state["replay_controllers"][HISTORICAL].complete
    assert database_summary(settings)["version"] == 1


def test_native_timer_advances_and_refreshes_without_manual_button(ui_data):
    app = choose_scenario(open_app())
    click(app, "Start Replay")
    controller = app.session_state["replay_controllers"][SCENARIO]
    controller.next_due = 0
    app.run(timeout=30)
    assert not app.exception and not app.error
    assert controller.cursor == 1
    assert metrics(app)["Current Data Version"] == "1"
    click(app, "Pause Replay")
    controller.next_due = 0
    app.run()
    assert controller.cursor == 1


def test_overlap_signal_is_visible_without_statistical_alert(settings, seed, monkeypatch):
    seed(cohort_rows(30, 15, 30, 0, shared=1))
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    app = open_app()
    assert metrics(app)["Preliminary Signals"] == "1"
    assert metrics(app)["Statistically Supported Alerts"] == "0"
    table = app.get("table")[0].value
    assert table.iloc[0]["Baseline S/R/N"] == "30/0/30"
    assert table.iloc[0]["Current S/R/N"] == "15/15/30"
    assert table.iloc[0]["Statistical status"] == "Preliminary Signal only"
    assert "not independent" in table.iloc[0]["Limitations"]


def test_native_watcher_detects_shared_imports_without_local_ingestion(settings, bundle, monkeypatch):
    import streamlit as st
    from src.data_loader import import_csv_bundle
    from src.replay_interface import refresh_watcher
    from src.ui_data import load_catalog
    reruns = []
    monkeypatch.setattr(st, "rerun", lambda **kwargs: reruns.append(kwargs))
    initial = load_catalog(settings)
    import_csv_bundle(bundle, settings)
    refresh_watcher.__wrapped__(settings, initial.version, initial.last_successful_import, None)
    assert reruns == [{"scope": "app"}]
    reruns.clear()
    updated = load_catalog(settings)
    refresh_watcher.__wrapped__(settings, updated.version, updated.last_successful_import, None)
    assert not reruns
