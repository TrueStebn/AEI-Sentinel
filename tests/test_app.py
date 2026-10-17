from ui_navigation import navigate, source
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import decode_rows, encode_rows
from src.data_loader import import_csv_bundle
from src.database import connect

APP = Path(__file__).resolve().parents[1] / "app.py"


def metrics(app):
    return {item.label: item.value for item in app.metric}


def open_app():
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception and not app.error
    # Legacy analytical regressions exercise the preserved Expert controls.
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    navigate(app,"Dashboard")
    return app


def choose_cip(app):
    app.selectbox(key="_antibiotic_code").set_value("CIP").run(timeout=30)
    return app


@pytest.fixture
def ui_data(settings, bundle, monkeypatch):
    monkeypatch.setenv("AEI_DATABASE_PATH", str(settings.database_path))
    patients, cultures, isolates, tests = [], [], [], []
    p = decode_rows(bundle, "patients")[0]
    c = decode_rows(bundle, "cultures")[0]
    i = decode_rows(bundle, "isolates")[0]
    s = decode_rows(bundle, "susceptibility")[0]
    for number in range(31):
        patient, culture, isolate = f"P{number}", f"C{number}", f"I{number}"
        organism = "Escherichia coli" if number < 30 else "Klebsiella pneumoniae"
        patients.append({**p, "patient_id": patient})
        cultures.append({**c, "culture_id": culture, "patient_id": patient})
        isolates.append({**i, "isolate_id": isolate, "culture_id": culture, "organism": organism})
        resistant = number < 7
        tests += [{**s, "isolate_id": isolate, "culture_id": culture, "organism": organism,
                   "antibiotic_code": "CIP", "antibiotic": "ciprofloxacin", "antibiotic_fi": "siprofloksasiini",
                   "result": "R" if resistant else "S"},
                  {**s, "isolate_id": isolate, "culture_id": culture, "organism": organism,
                   "result": "S" if resistant else "R"}]
    cultures.append({**c, "culture_id": "negative", "patient_id": "P0", "sample_date": "2026-09-01",
                     "iso_week": "36", "specimen": "blood", "specimen_fi": "veri", "result": "negative", "n_isolates": "0"})
    antibiotic = decode_rows(bundle, "antibiotics")[0]
    ward = decode_rows(bundle, "wards")[0]
    values = {"patients": patients, "cultures": cultures, "isolates": isolates, "susceptibility": tests,
              "antibiotics": [antibiotic, {**antibiotic, "antibiotic_code": "CIP", "antibiotic": "ciprofloxacin", "antibiotic_fi": "siprofloksasiini"}],
              "wards": [ward, {**ward, "ward_code": "NEW-WARD", "ward_en": "New ward", "ward_fi": "New ward"}]}
    files = {f"{table}.csv": encode_rows(table, rows) for table, rows in values.items()}
    import_csv_bundle(files, settings)
    return files


def test_empty_database_and_navigation(tmp_path, monkeypatch):
    monkeypatch.setenv("AEI_DATABASE_PATH", str(tmp_path / "app.db"))
    app = open_app()
    assert app.warning and "No Data" in app.warning[0].value
    navigate(app,"Detailed Analysis")
    assert not app.exception and app.warning
    navigate(app,"Data Management")
    assert not app.exception
    assert metrics(app)["Patients"] == "0"
    next(button for button in app.button if button.label == "Check database integrity").click().run()
    assert not app.exception
    assert any("No foreign key violations" in item.value for item in app.success)


def test_dashboard_shows_counts_status_and_latest_date(ui_data):
    app = choose_cip(open_app())
    values = metrics(app)
    assert values["Number of Tested Isolates"] == "30"
    assert values["Number of Resistant Isolates"] == "7"
    assert [m.value for m in app.metric if m.label.startswith("Resistance Rate")] == ["23.33%", "23.33%"]
    assert any("S: 23" in item.value and "R: 7" in item.value for item in app.caption)
    assert app.date_input(key="_analysis_date").value == date(2026, 9, 27)
    assert len(app.get("plotly_chart")) == 4
    assert len(app.dataframe) == 1
    assert "overlap" in " ".join(item.value for item in app.caption)


def test_original_import_button_reports_invalid_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AEI_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("AEI_DATASET_PATH", str(tmp_path / "missing"))
    app = open_app()
    navigate(app,"Data Management")
    next(button for button in app.button if button.label == "Import original six CSV files").click().run()
    assert not app.exception
    assert any("Cannot read" in item.value for item in app.error)


def test_options_come_from_database_and_parameters_reach_engine(ui_data):
    app = choose_cip(open_app())
    assert "New ward (NEW-WARD)" in app.selectbox(key="_ward_code").options
    assert "Klebsiella pneumoniae" in app.selectbox(key="_organism").options
    assert app.selectbox(key="_specimen").options == ["All specimen types", "Blood", "Urine"]
    app.selectbox(key="_ward_code").set_value("NEW-WARD").run()
    assert [m.value for m in app.metric if m.label.startswith("Resistance Rate")] == ["No Data", "No Data"]
    app.selectbox(key="_ward_code").set_value("__all__").run()
    app.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    assert metrics(app)["Number of Tested Isolates"] == "1"
    assert [m.value for m in app.metric if m.label.startswith("Resistance Rate")] == ["Insufficient Data", "Insufficient Data"]
    app.selectbox(key="_specimen").set_value("blood").run()
    assert metrics(app)["Number of Tested Isolates"] == "0"


def test_antibiotic_change_updates_counts_without_reusing_previous_results(ui_data):
    app = choose_cip(open_app())
    app.selectbox(key="_antibiotic_code").set_value("AMP").run()
    assert metrics(app)["Number of Resistant Isolates"] == "23"
    assert [m.value for m in app.metric if m.label.startswith("Resistance Rate")] == ["76.67%", "76.67%"]


def test_analysis_date_changes_period_and_use_latest_restores_it(ui_data):
    app = choose_cip(open_app())
    app.date_input(key="_analysis_date").set_value(date(2026, 9, 26)).run()
    assert metrics(app)["Number of Tested Isolates"] == "0"
    assert app.session_state["analysis_date"] == date(2026, 9, 26)
    next(b for b in app.button if b.label == "Use latest sample").click().run()
    assert metrics(app)["Number of Tested Isolates"] == "30"


def test_detailed_tables_custom_period_charts_and_export_control(ui_data):
    app = choose_cip(open_app())
    navigate(app,"Detailed Analysis")
    assert not app.exception and not app.error
    assert len(app.dataframe) == 4 and len(app.get("plotly_chart")) == 3
    assert len(app.get("download_button")) == 2
    row = app.dataframe[0].value.iloc[0]
    assert (row["S"], row["R"], row["Total"]) == (23, 7, 30)
    assert row["Resistance (%)"] == pytest.approx(100 * 7 / 30)
    app.date_input(key="_custom_period").set_value((date(2026, 9, 1), date(2026, 9, 26))).run()
    assert metrics(app)["Resistance Rate — Selected Period"] == "No Data"
    assert app.dataframe[0].value.iloc[0]["Total"] == 0
    assert app.dataframe[0].value.iloc[1]["Total"] == 30


def test_incomplete_custom_range_has_message_instead_of_error(ui_data):
    app = open_app()
    navigate(app,"Detailed Analysis")
    app.date_input(key="_custom_period").set_value((date(2026, 9, 1),)).run()
    assert not app.exception and not app.error
    assert any("both a start date" in item.value for item in app.info)
    assert not app.dataframe


def test_filters_survive_page_changes_in_same_session(ui_data):
    app = choose_cip(open_app())
    app.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    navigate(app,"Data Management")
    navigate(app,"Dashboard")
    assert not app.exception
    assert app.selectbox(key="_organism").value == "Klebsiella pneumoniae"
    assert app.selectbox(key="_antibiotic_code").value == "CIP"


def test_user_sessions_are_independent(ui_data):
    first, second = choose_cip(open_app()), choose_cip(open_app())
    first.selectbox(key="_organism").set_value("Klebsiella pneumoniae").run()
    first.date_input(key="_analysis_date").set_value(date(2026, 9, 26)).run()
    second.run()
    assert second.selectbox(key="_organism").value == "Escherichia coli"
    assert second.date_input(key="_analysis_date").value == date(2026, 9, 27)
    assert metrics(second)["Number of Tested Isolates"] == "30"
    assert metrics(first)["Number of Tested Isolates"] == "0"


def test_viewing_all_pages_does_not_change_any_database_record(ui_data, settings):
    with connect(settings, read_only=True) as con:
        before = list(con.iterdump())
    app = choose_cip(open_app())
    for page in ["Detailed Analysis", "Data Management", "Dashboard"]:
        navigate(app,page)
        assert not app.exception and not app.error
    with connect(settings, read_only=True) as con:
        assert list(con.iterdump()) == before


@pytest.mark.parametrize("pinned", [False, True])
def test_new_import_refreshes_version_results_and_default_date(ui_data, settings, pinned):
    app = choose_cip(open_app())
    if pinned:
        app.date_input(key="_analysis_date").set_value(date(2026, 9, 26)).run()
    changes = {name: decode_rows(ui_data, name[:-4]) for name in ui_data}
    changes["patients.csv"].append({**changes["patients.csv"][0], "patient_id": "P-new"})
    changes["cultures.csv"].append({**changes["cultures.csv"][0], "culture_id": "C-new", "patient_id": "P-new",
                                   "sample_date": "2026-09-28", "iso_week": "40"})
    changes["isolates.csv"].append({**changes["isolates.csv"][0], "isolate_id": "I-new", "culture_id": "C-new"})
    changes["susceptibility.csv"].append({**changes["susceptibility.csv"][0], "isolate_id": "I-new", "culture_id": "C-new"})
    import_csv_bundle({name: encode_rows(name[:-4], rows) for name, rows in changes.items()}, settings)
    next(b for b in app.button if b.label == "Refresh data").click().run()
    assert not app.exception and not app.error
    assert metrics(app)["Current Data Version"] == "2"
    if pinned:
        assert app.date_input(key="_analysis_date").value == date(2026, 9, 26)
        assert metrics(app)["Number of Tested Isolates"] == "0"
    else:
        assert app.date_input(key="_analysis_date").value == date(2026, 9, 28)
        assert metrics(app)["Number of Tested Isolates"] == "31"
        assert metrics(app)["Number of Resistant Isolates"] == "8"


def test_query_failure_displays_clear_error(ui_data, monkeypatch):
    from src.resistance import ResistanceEngine
    def fail(*args, **kwargs):
        raise RuntimeError("Database unavailable")
    monkeypatch.setattr(ResistanceEngine, "last_28_days", fail)
    app = AppTest.from_file(str(APP)).run(timeout=30)
    navigate(app,"Dashboard")
    assert not app.exception
    assert any("Unable to complete" in item.value for item in app.error)


def test_engine_receives_selected_filters_and_date(ui_data, monkeypatch):
    from src.resistance import ResistanceEngine, ResistanceFilters
    calls = []
    original = ResistanceEngine.last_28_days
    def capture(self, filters, *, as_of_date=None):
        calls.append((filters, as_of_date))
        return original(self, filters, as_of_date=as_of_date)
    monkeypatch.setattr(ResistanceEngine, "last_28_days", capture)
    app = choose_cip(open_app())
    app.selectbox(key="_ward_code").set_value("ED").run()
    app.date_input(key="_analysis_date").set_value(date(2026, 9, 26)).run()
    assert calls[-1] == (ResistanceFilters("CIP", "ED", "Escherichia coli", "urine"), date(2026, 9, 26))


def test_revision_change_during_render_hides_analytics(ui_data, monkeypatch):
    from dataclasses import replace
    from src.resistance import ResistanceEngine
    original = ResistanceEngine.last_365_days
    def changed(self, *args, **kwargs):
        return replace(original(self, *args, **kwargs), data_version=999)
    monkeypatch.setattr(ResistanceEngine, "last_365_days", changed)
    app = open_app()
    assert any("dataset changed" in item.value for item in app.warning)
    assert not app.metric and not app.get("plotly_chart")


@pytest.mark.integration
@pytest.mark.parametrize("antibiotic,recent,annual,S,R", [
    ("TMP", "21.21%", "24.04%", 26, 7), ("CIP", "15.15%", "9.20%", 28, 5)])
def test_working_database_reference_values_in_interface(antibiotic, recent, annual, S, R):
    from src.config import get_settings
    if not get_settings().database_path.exists():
        pytest.skip("Working database is unavailable")
    app = open_app()
    if "Urology (URO)" not in app.selectbox(key="_ward_code").options:
        pytest.skip("Original synthetic dataset is unavailable")
    app.selectbox(key="_ward_code").set_value("URO").run()
    app.selectbox(key="_organism").set_value("Escherichia coli").run()
    app.selectbox(key="_antibiotic_code").set_value(antibiotic).run()
    app.selectbox(key="_specimen").set_value("urine").run()
    app.date_input(key="_analysis_date").set_value(date(2026, 9, 27)).run()
    assert [m.value for m in app.metric if m.label.startswith("Resistance Rate")] == [recent, annual]
    row = app.dataframe[0].value.iloc[0]
    assert (row["S"], row["R"], row["Total"]) == (S, R, 33)
