"""Independent small-cohort checks for the set-based matrix contract."""

import json
from contextlib import contextmanager
from dataclasses import replace

import pytest

from src.advanced_analysis import CohortFilters
from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine
from src.database import connect, transaction
from src.map_charts import cell_hover, cell_value, matrix_chart
from src.resistance import ResistanceEngine
from src.surveillance_map import SurveillanceMapEngine, matching_indicators
from test_advanced_analysis import advanced_seed, seed
from test_resistance import observation


def matrix(settings, cohort=None, **kwargs):
    return SurveillanceMapEngine(settings).analyze(cohort or CohortFilters(organism="Escherichia coli", specimen="urine"),
        as_of_date="2026-09-27", include_indicators=False, **kwargs)


def cell(report, ward="ED", antibiotic="CIP"):
    return next(item for item in report.cells if (item.ward_code, item.antibiotic_code) == (ward, antibiotic))


@pytest.mark.parametrize("count,resistant,status,rate", [(0, 0, "no_data", None), (1, 1, "insufficient_data", None),
    (29, 10, "insufficient_data", None), (30, 0, "ok", 0), (30, 30, "ok", 100), (40, 10, "ok", 25)])
def test_counts_minimum_and_true_zero_hundred(settings, advanced_seed, count, resistant, status, rate):
    advanced_seed([observation(str(i), result="R" if i < resistant else "S") for i in range(count)])
    result = cell(matrix(settings))
    assert result.status == status
    assert (result.level.n_first_isolates, result.level.n_susceptible, result.level.n_resistant, result.level.n_tested) == (count, count-resistant, resistant, count)
    assert result.level.resistance_pct == rate


def test_first_ranked_before_multi_antibiotic_join_and_untested_not_replaced(settings, advanced_seed):
    advanced_seed([observation("a", "2026-09-01", tests={"CIP": "S", "AMP": "R"}),
                  observation("a", "2026-09-02", tests={"CIP": "R", "AMP": "S", "TMP": "R"}),
                  observation("b", "2026-09-01", tests={}), observation("b", "2026-09-02", "R")])
    report = matrix(settings)
    cip, amp, tmp = (cell(report, antibiotic=code).level for code in ("CIP", "AMP", "TMP"))
    assert (cip.n_first_isolates, cip.n_tested, cip.n_susceptible) == (2, 1, 1)
    assert (amp.n_first_isolates, amp.n_tested, amp.n_resistant) == (2, 1, 1)
    assert (tmp.n_first_isolates, tmp.n_tested) == (2, 0)
    assert "missing tests=1" in cell_hover(report, cell(report))


def test_patient_can_contribute_independently_to_multiple_wards(settings, advanced_seed):
    advanced_seed([observation("same", "2026-09-01", "S"), observation("same", "2026-09-02", "R", ward_code="URO")])
    report = matrix(settings)
    assert cell(report, "ED").level.n_susceptible == cell(report, "URO").level.n_resistant == 1
    assert cell(report, "ED").level.n_tested == cell(report, "URO").level.n_tested == 1


@pytest.mark.parametrize("filters,n", [({"sex": "F"}, 2), ({"sex": "M"}, 1), ({"age_min": 65}, 1),
    ({"age_min": 18, "age_max": 44}, 1), ({"age_min": 0, "age_max": 17}, 1),
    ({"sex": "F", "age_min": 65, "ward_type": "in", "institution": "hospital"}, 1)])
def test_patient_and_healthcare_filters(settings, advanced_seed, filters, n):
    advanced_seed([observation("a", age=17), observation("b", sex="M", age=44), observation("c", age=65, ward_code="URO")])
    report = matrix(settings, CohortFilters(organism="Escherichia coli", specimen="urine", **filters))
    assert sum(cell.level.n_tested for cell in report.cells if cell.antibiotic_code == "CIP") == n


def test_age_filter_before_first_isolate(settings, advanced_seed):
    advanced_seed([observation("a", "2026-09-01", "S", age=64), observation("a", "2026-09-02", "R", age=65)])
    assert cell(matrix(settings, CohortFilters(age_min=65))).level.n_resistant == 1


def test_documented_unavailable_differs_from_untested_and_rare(settings, advanced_seed):
    advanced_seed([observation("a", specimen="blood", tests={})])
    report = matrix(settings, CohortFilters(specimen="blood"))
    assert cell(report, antibiotic="TMP").status == "unavailable"
    assert cell(report, antibiotic="CIP").status == "no_data"
    assert cell(report, antibiotic="CIP").level.n_first_isolates == 1
    assert cell_value(report, cell(report, antibiotic="TMP")) is None
    assert "Unavailable" in cell_hover(report, cell(report, antibiotic="TMP"))


def test_fixed_nonoverlapping_period_boundaries_and_pp_change(settings, advanced_seed):
    advanced_seed([observation(f"b{i}", "2026-08-30", "R" if i < 6 else "S") for i in range(40)] +
                  [observation(f"c{i}", "2026-08-31", "R" if i < 12 else "S") for i in range(40)] +
                  [observation("outside", "2025-08-30", "R")])
    result = cell(matrix(settings, view="change"))
    assert (result.current.n_tested, result.baseline.n_tested) == (40, 40)
    assert (result.current.resistance_pct, result.baseline.resistance_pct, result.difference_pp) == (30, 15, 15)
    assert result.current.start_date.isoformat() == "2026-08-31"
    assert result.baseline.end_date.isoformat() == "2026-08-30"
    assert result.status == "ok"


@pytest.mark.parametrize("old,new,status", [(0, 40, "no_data"), (29, 40, "insufficient_data"), (40, 29, "insufficient_data"), (30, 30, "ok")])
def test_change_requires_both_periods(settings, advanced_seed, old, new, status):
    advanced_seed([observation(f"old{i}", "2026-08-01") for i in range(old)] + [observation(f"new{i}", result="R") for i in range(new)])
    result = cell(matrix(settings, view="change"))
    assert result.status == status
    assert (result.difference_pp is not None) == (status == "ok")


def test_class_filter_does_not_change_first_isolate(settings, advanced_seed):
    advanced_seed([observation("a", "2026-09-01", tests={"AMP": "S"}), observation("a", "2026-09-02", "R")])
    report = matrix(settings, antibiotic_class="fluoroquinolones")
    assert [code for code, _ in report.antibiotics] == ["CIP"]
    assert cell(report).level.n_first_isolates == 1 and cell(report).level.n_tested == 0
    assert not matrix(settings, antibiotic_class="' OR 1=1 --").cells


def test_matrix_matches_original_engine_for_all_axes(settings, advanced_seed):
    advanced_seed([observation("a", "2026-09-01", tests={"CIP": "S", "AMP": "R"}),
                  observation("a", "2026-09-02", "R", ward_code="URO"), observation("b", "2026-08-01", "R"),
                  observation("b", specimen="blood", tests={})])
    report = matrix(settings, CohortFilters())
    original = ResistanceEngine(settings)
    for item in report.cells:
        for result in (item.current, item.baseline, item.level):
            assert original.calculate(result.filters, start_date=result.start_date, end_date=result.end_date) == result


def test_exact_indicators_preliminary_and_by_not_subgroup_or_annual(settings, advanced_seed):
    advanced_seed([observation(f"p{i}", "2026-08-01", "S") for i in range(40)] +
                  [observation(f"p{i}", result="R") for i in range(40)])
    report = matrix(settings, view="change")
    scan = AnomalyEngine(settings).scan(as_of_date="2026-09-27")
    assert matching_indicators(report, scan, 10, source_identity=report.source_identity)["ED", "CIP"] == "P"  # Shared patients prevent BY alert.
    assert matching_indicators(report, scan, 10, source_identity="0"*64) == {}
    for changed in (replace(report, view="level", period="annual"), replace(report, cohort=replace(report.cohort, sex="F")),
                    replace(report, cohort=replace(report.cohort, age_min=65)), replace(report, cohort=CohortFilters())):
        assert matching_indicators(changed, scan, 10, source_identity=report.source_identity) == {}
    target = cell(report)
    altered = replace(target, current=replace(target.current, n_resistant=99))
    drift = replace(report, cells=(altered,))
    assert not matching_indicators(drift, scan, 10, source_identity=report.source_identity)


def test_by_alert_from_full_scan_and_filter_indicators_withheld(settings, advanced_seed):
    advanced_seed([observation(f"old{i}", "2026-08-01", "S") for i in range(40)] +
                  [observation(f"new{i}", result="R") for i in range(40)])
    report = SurveillanceMapEngine(settings).analyze(CohortFilters(organism="Escherichia coli", specimen="urine"), view="change", as_of_date="2026-09-27")
    assert cell(report).indicator == "A"
    filtered = SurveillanceMapEngine(settings).analyze(replace(report.cohort, sex="F"), view="change", as_of_date="2026-09-27")
    assert cell(filtered).indicator is None


def test_readonly_export_revision_guard_and_source_identity(settings, advanced_seed):
    advanced_seed([observation("PRIVATE_PATIENT")])
    with connect(settings, read_only=True) as con:
        before = list(con.iterdump())
    report = matrix(settings, expected_version=7)
    assert "PRIVATE_PATIENT" not in json.dumps(report.to_dict())
    with connect(settings, read_only=True) as con:
        assert list(con.iterdump()) == before
    scan = AnomalyEngine(settings).scan(as_of_date="2026-09-27")
    with pytest.raises(DatasetChangedError):
        matching_indicators(report, replace(scan, data_version=8), 10, source_identity=report.source_identity)
    with connect(settings) as con, transaction(con):
        con.execute("UPDATE data_version SET version=8")
    with pytest.raises(DatasetChangedError):
        matrix(settings, expected_version=7)
    assert matrix(settings).data_version == 8


def test_complete_matrix_uses_one_aggregation_query(settings, advanced_seed, monkeypatch):
    import src.resistance as resistance_module
    advanced_seed([observation(str(i)) for i in range(30)])
    statements = []
    original = resistance_module.connect
    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as con:
            con.set_trace_callback(statements.append)
            yield con
    monkeypatch.setattr(resistance_module, "connect", traced)
    report = matrix(settings)
    assert len(report.cells) == 6
    assert sum(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements) == 5
    assert sum(sql.lstrip().upper().startswith("WITH") for sql in statements) == 1


def test_empty_source_and_higher_minimum(settings, advanced_seed):
    empty = SurveillanceMapEngine(settings).analyze(CohortFilters())
    assert not empty.cells and empty.analysis_date is None
    advanced_seed([observation(str(i)) for i in range(30)])
    assert cell(matrix(replace(settings, anomaly_minimum_sample_size=40))).level.resistance_pct is None


def test_selectable_chart_preserves_textual_missing_status_and_zero(settings, advanced_seed):
    advanced_seed([observation(str(i)) for i in range(30)])
    report = matrix(settings)
    chart = matrix_chart(report)
    assert any("0.0" in trace.text for trace in chart.data)
    assert any("ND" in trace.text for trace in chart.data)
    assert chart.layout.clickmode == "event+select"
