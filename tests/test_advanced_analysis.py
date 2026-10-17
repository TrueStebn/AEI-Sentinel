"""Hand-counted cohorts: filters, first isolates, independence and read isolation."""

import json
from dataclasses import replace
from contextlib import contextmanager
from datetime import date

import pytest

from src.advanced_analysis import AGE_GROUPS, AdvancedAnalysisEngine, CohortFilters
from src.analysis_service import DatasetChangedError
from src.database import connect, transaction
from src.resistance import ResistanceEngine, ResistanceFilters
from test_resistance import observation, seed

EC, KP, SA = "Escherichia coli", "Klebsiella pneumoniae", "Staphylococcus aureus"


@pytest.fixture
def advanced_seed(settings, seed):
    def write(rows, version=7):
        seed(rows, version)
        with connect(settings) as con, transaction(con):
            for index, row in enumerate(rows):
                con.execute("UPDATE patients SET sex=?,home_area=? WHERE patient_id=?",
                    (row.get("sex", "F"), row.get("home_area", "Centre"), row["patient"]))
                culture, isolate = row.get("culture_id", f"C{index:04d}"), row.get("isolate_id", f"I{index:04d}")
                con.execute("UPDATE cultures SET age_years=? WHERE culture_id=?", (row.get("age", 70), culture))
                if "admission" in row:
                    con.execute("UPDATE cultures SET admission_date=? WHERE culture_id=?", (row["admission"], culture))
                con.execute("UPDATE isolates SET esbl=?,mrsa=?,vre=?,cpe=? WHERE isolate_id=?",
                    tuple(row.get(marker, 0) for marker in ("esbl", "mrsa", "vre", "cpe")) + (isolate,))
    return write


def analyze(settings, cohort=None, **kwargs):
    return AdvancedAnalysisEngine(settings).analyze(cohort or CohortFilters(), code="CIP", as_of_date="2026-09-27", **kwargs)


@pytest.mark.parametrize("filters,expected", [
    ({"sex": "M"}, 2), ({"sex": "F"}, 1), ({"age_min": 65}, 2), ({"age_min": 71, "age_max": 75}, 1),
    ({"home_area": "East"}, 1), ({"ward_type": "in"}, 2), ({"institution": "primary_care"}, 0),
    ({"ward_code": "URO", "sex": "M", "age_min": 65, "home_area": "East", "ward_type": "in", "institution": "hospital"}, 1),
])
def test_optional_and_combined_filters(settings, advanced_seed, filters, expected):
    advanced_seed([observation("a", sex="M", age=70, ward_code="URO", home_area="East"),
                   observation("b", sex="M", age=73, ward_code="URO"), observation("c", age=17)])
    assert analyze(settings, CohortFilters(**filters)).current.n_observed == expected


@pytest.mark.parametrize("label,count", [("0–17", 2), ("18–44", 2), ("45–64", 2), ("65+", 2)])
def test_inclusive_age_boundaries(settings, advanced_seed, label, count):
    advanced_seed([observation(str(i), age=age) for i, age in enumerate([0, 17, 18, 44, 45, 64, 65, 100])])
    low, high = AGE_GROUPS[label]
    assert analyze(settings, CohortFilters(age_min=low, age_max=high)).current.n_observed == count


@pytest.mark.parametrize("timing,count", [(None, 8), ("all_applicable", 3), ("days_1_2", 2), ("day_3_plus", 1)])
def test_hospital_day_calendar_boundaries_and_invalid_admissions(settings, advanced_seed, timing, count):
    admissions = ["2026-09-27", "2026-09-26", "2026-09-25", None, "2026-09-28", "2026-02-30", "bad"]
    advanced_seed([observation(str(i), ward_code="URO", admission=admission) for i, admission in enumerate(admissions)] +
                  [observation("out", admission="2026-09-26")])
    assert analyze(settings, CohortFilters(admission_timing=timing)).current.n_observed == count


@pytest.mark.parametrize("filters", [{"ward_code": "ED", "admission_timing": "days_1_2"}, {"ward_code": "URO", "ward_type": "out"},
                                    {"ward_code": "URO", "institution": "primary_care"}, {"ward_type": "out", "admission_timing": "all_applicable"}])
def test_incompatible_healthcare_context_rejected(settings, advanced_seed, filters):
    advanced_seed([observation("a")])
    with pytest.raises(ValueError):
        analyze(settings, CohortFilters(**filters))


def test_filter_before_ranking_and_test_after_ranking(settings, advanced_seed):
    advanced_seed([observation("a", "2026-09-01", age=64, tests={}), observation("a", "2026-09-02", "R", age=65),
                   observation("b", "2026-09-01", tests={}), observation("b", "2026-09-02", "R")])
    all_ages = analyze(settings).current
    seniors = analyze(settings, CohortFilters(age_min=65)).current
    assert (all_ages.n_first_isolates, all_ages.n_observed, all_ages.missing_tests) == (2, 0, 2)
    assert (seniors.n_first_isolates, seniors.n_observed, seniors.n_positive) == (2, 1, 1)


@pytest.mark.parametrize("count,resistant,pct,status", [(0, 0, None, "no_data"), (29, 5, None, "insufficient_data"),
    (30, 0, 0, "ok"), (30, 30, 100, "ok"), (30, 7, 100*7/30, "ok")])
def test_minimum_and_real_zero_or_hundred(settings, advanced_seed, count, resistant, pct, status):
    advanced_seed([observation(str(i), result="R" if i < resistant else "S") for i in range(count)])
    result = analyze(settings).current
    assert (result.n_observed, result.n_positive, result.percentage, result.status) == (count, resistant, pct, status)
    assert bool(result.confidence_interval) == (status == "ok")


@pytest.mark.parametrize("shared,status", [(False, "potential_increase"), (True, "analysis_limited")])
def test_reuse_temporal_statistics_and_preliminary_separation(settings, advanced_seed, shared, status):
    advanced_seed([observation(f"p{i}", "2026-08-01", "S") for i in range(40)] +
                  [observation(f"p{i}" if shared else f"q{i}", result="R") for i in range(40)])
    report = analyze(settings)
    assert report.assessment.status == status and report.assessment.preliminary_signal
    assert report.assessment.shared_patients == (40 if shared else 0)
    assert (report.assessment.p_value is None) == shared
    assert report.assessment.correction_method == "none (selected exploratory comparison)"
    assert "adjusted_p_value" not in report.to_dict()["assessment"]


def test_pooled_repeated_patients_within_period_withhold_ci_and_fisher(settings, advanced_seed):
    rows = [observation(str(i), specimen=specimen) for i in range(20) for specimen in ("urine", "blood")]
    rows += [observation(f"old{i}", "2026-08-01") for i in range(40)]
    advanced_seed(rows)
    report = analyze(settings)
    assert report.current.n_observed == 40 and report.current.distinct_observed_patients == 20
    assert report.current.confidence_interval is None
    assert report.assessment.status == "analysis_limited" and report.assessment.p_value is None


def test_groups_rank_independently_and_do_not_sum(settings, advanced_seed):
    advanced_seed([observation("same", "2026-09-01", "S", ward_code="ED", age=64),
                   observation("same", "2026-09-02", "R", ward_code="URO", age=65)])
    report = analyze(settings, group_by="ward")
    groups = dict(report.groups)
    assert report.annual.n_observed == 1
    assert groups["ED"].n_observed == groups["URO"].n_observed == 1
    assert groups["ED"].n_negative == groups["URO"].n_positive == 1
    assert report.patients_in_multiple_groups == 1
    age = analyze(settings, CohortFilters(age_min=65), group_by="age")
    assert age.annual.n_positive == 1
    assert dict(age.groups)["45–64"].n_negative == 1  # Only grouped dimension is relaxed.


@pytest.mark.parametrize("cohort", [CohortFilters(ward_type="out"), CohortFilters(ward_code="ED")])
def test_outpatient_admission_groups_do_not_replace_context_with_inpatients(settings, advanced_seed, cohort):
    advanced_seed([observation("in", ward_code="URO"), observation("out")])
    report = analyze(settings, cohort, group_by="admission_timing")
    assert all(period.n_observed == 0 for _, period in report.groups)


@pytest.mark.parametrize("dimension", ["sex", "age", "ward", "ward_type", "institution", "home_area", "admission_timing"])
def test_group_dimensions_keep_unrelated_filters(settings, advanced_seed, dimension):
    advanced_seed([observation("wanted", ward_code="URO"), observation("other", ward_code="URO", organism=KP)])
    report = analyze(settings, CohortFilters(organism=EC), group_by=dimension)
    assert sum(period.n_observed for _, period in report.groups) == 1


@pytest.mark.parametrize("ward", [None, "ED", "URO"])
@pytest.mark.parametrize("specimen", [None, "urine", "blood"])
def test_original_engine_parity_with_no_extended_restrictions(settings, advanced_seed, ward, specimen):
    advanced_seed([observation("a", "2026-09-01", tests={}), observation("a", "2026-09-02", "R", ward_code="URO"),
                  observation("b", specimen="blood"), observation("b", "2026-08-01", "R")])
    new = analyze(settings, CohortFilters(ward_code=ward, specimen=specimen))
    old = ResistanceEngine(settings)
    filters = ResistanceFilters("CIP", ward, None, specimen)
    for period in (new.current, new.baseline, new.annual):
        original = old.calculate(filters, start_date=period.start_date, end_date=period.end_date)
        assert (period.n_negative, period.n_positive, period.n_observed, period.n_first_isolates, period.percentage) == (
            original.n_susceptible, original.n_resistant, original.n_tested, original.n_first_isolates, original.resistance_pct)


def test_readonly_consistent_revision_and_aggregate_exports(settings, advanced_seed):
    advanced_seed([observation("PRIVATE_PATIENT", tests={})])
    with connect(settings, read_only=True) as con:
        before = list(con.iterdump())
    result = analyze(settings, expected_version=7)
    assert "PRIVATE_PATIENT" not in json.dumps(result.to_dict())
    with connect(settings, read_only=True) as con:
        assert list(con.iterdump()) == before
    with connect(settings) as con, transaction(con):
        con.execute("UPDATE data_version SET version=8")
    with pytest.raises(DatasetChangedError):
        analyze(settings, expected_version=7)
    assert analyze(settings).data_version == 8


def test_empty_database_without_date(settings):
    result = AdvancedAnalysisEngine(settings).analyze(CohortFilters())
    assert result.analysis_date is None and result.current.status == "no_data" and not result.weekly


@pytest.mark.parametrize("kwargs", [{"sex": "X"}, {"age_min": True}, {"age_min": -1}, {"age_max": 131},
                                    {"age_min": 65, "age_max": 17}, {"ward_type": "inpatient"}, {"home_area": " "}])
def test_invalid_filter_values(kwargs):
    with pytest.raises(ValueError):
        CohortFilters(**kwargs)


def test_bound_parameter_does_not_interpret_sql(settings, advanced_seed):
    advanced_seed([observation("a")])
    assert analyze(settings, CohortFilters(home_area="' OR 1=1 --")).current.n_first_isolates == 0


def test_set_based_query_budget_does_not_grow_with_categories(settings, advanced_seed, monkeypatch):
    import src.resistance as resistance_module
    advanced_seed([observation(str(i), home_area=f"Area {i}") for i in range(60)])
    original = resistance_module.connect
    statements = []
    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as con:
            con.set_trace_callback(statements.append)
            yield con
    monkeypatch.setattr(resistance_module, "connect", traced)
    report = analyze(settings, group_by="home_area")
    assert len(report.groups) == 60
    assert sum(sql.lstrip().upper().startswith(("WITH", "SELECT")) for sql in statements) == 6


def test_higher_configured_minimum_is_used_everywhere(settings, advanced_seed):
    advanced_seed([observation(str(i)) for i in range(30)])
    report = analyze(replace(settings, anomaly_minimum_sample_size=40))
    assert report.current.minimum_sample_size == 40 and report.current.percentage is None
    assert all(period.percentage is None for _, period in report.groups)
