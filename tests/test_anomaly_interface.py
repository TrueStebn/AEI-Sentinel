from ui_navigation import navigate, source
"""UI verification on explicitly controlled test cohorts, never the source dataset."""

from dataclasses import replace

import pytest
from streamlit.testing.v1 import AppTest

from test_anomalies import cohort_rows, seed
from test_app import APP, metrics, open_app, ui_data
from src.anomalies import AnomalyEngine
from src.database import connect
from src.resistance import ResistanceFilters


def test_alert_card_scan_adjusted_values_and_detailed_export(settings, seed, monkeypatch):
    seed(cohort_rows(30, 15, 30, 0))
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    app = open_app()
    assert metrics(app)["Potential Deviations"] == "1"
    assert metrics(app)["Eligible Comparisons"] == "1"
    assert any("Potential Increase" in item.value for item in app.markdown)
    assert any("Flagged for review" in item.value for item in app.markdown)
    alerts = next(frame.value for frame in app.dataframe if "Adjusted p (BY)" in frame.value.columns)
    assert alerts.iloc[0]["Current Tested"] == alerts.iloc[0]["Baseline Tested"] == 30
    assert alerts.iloc[0]["Adjusted p (BY)"] < 0.05
    assert len(app.get("plotly_chart")) == 4
    navigate(app,"Detailed Analysis")
    assert not app.exception and not app.error
    labels = [button.proto.label for button in app.get("download_button")]
    assert "Download anomaly assessment (CSV)" in labels
    periods = app.dataframe[-1].value
    assert list(periods["Period"]) == ["Current 28 Days", "Historical Baseline"]
    assert list(periods["R"]) == [15, 0]


def test_patient_overlap_limits_ui_without_fabricated_warning(settings, seed, monkeypatch):
    seed(cohort_rows(30, 15, 30, 0, shared=1))
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    app = open_app()
    assert metrics(app)["Potential Deviations"] == "0" and metrics(app)["Eligible Comparisons"] == "0"
    assert any("Analysis Limited" in item.value for item in app.markdown)
    assert any("not independent" in item.value for item in app.warning)
    assert any("not evidence of no change" in item.value for item in app.info)
    assert any("Fisher test not performed" in item.value for item in app.caption)


def test_independent_sessions_keep_distinct_selected_assessments(settings, seed, monkeypatch):
    seed(cohort_rows(30, 15, 30, 0) + cohort_rows(30, 3, 30, 3, organism="Other", prefix="other-"))
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    first, second = open_app(), open_app()
    first.selectbox(key="_organism").set_value("Other").run(timeout=30)
    second.run(timeout=30)
    assert metrics(first)["Change from Historical Baseline"] == "+0.00 pp"
    assert metrics(second)["Change from Historical Baseline"] == "+50.00 pp"
    assert metrics(first)["Potential Deviations"] == metrics(second)["Potential Deviations"] == "1"


def test_ui_rejects_mixed_anomaly_revision(ui_data, monkeypatch):
    original = AnomalyEngine.scan
    def changed(self, **kwargs):
        return replace(original(self, **kwargs), data_version=999)
    monkeypatch.setattr(AnomalyEngine, "scan", changed)
    app = open_app()
    assert any("dataset changed" in item.value for item in app.warning)
    assert not app.metric and not app.get("plotly_chart")


def test_anomaly_results_refresh_after_committed_changes(settings, seed, monkeypatch):
    rows = cohort_rows(30, 0, 30, 0)
    seed(rows)
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    app = open_app()
    assert metrics(app)["Potential Deviations"] == "0"
    # A separately committed test fixture version adds independent current R outcomes.
    additional = [dict(row, patient=f"new-{n}", culture_id=f"new-c{n}", isolate_id=f"new-i{n}", result="R")
                  for n, row in enumerate(rows[:30])]
    seed(additional, version=8)
    next(button for button in app.button if button.label == "Refresh data").click().run(timeout=30)
    assert not app.exception and not app.error
    assert metrics(app)["Potential Deviations"] == "1"
    assert metrics(app)["Current Data Version"] == "8"


def test_selected_and_scan_views_do_not_write_records(settings, seed, monkeypatch):
    seed(cohort_rows(30, 15, 30, 0))
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    with connect(settings, read_only=True) as con:
        before = list(con.iterdump())
    app = open_app()
    navigate(app,"Detailed Analysis")
    with connect(settings, read_only=True) as con:
        assert list(con.iterdump()) == before
