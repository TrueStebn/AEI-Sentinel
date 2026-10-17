"""Controlled synthetic cohorts; the original working dataset is never edited."""

import csv
import io
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import date

import pytest

from test_resistance import observation, seed
from src.anomalies import AnomalyEngine, adjust_p_values, comparison_windows
from src.config import get_settings
from src.database import connect
from src.reporting import export_anomalies_csv
from src.resistance import ResistanceEngine, ResistanceFilters

FILTERS = ResistanceFilters("CIP", "ED", "Escherichia coli", "urine")
END = "2026-09-27"


def cohort_rows(current=30, current_r=0, baseline=30, baseline_r=0, *, shared=0,
                 organism="Escherichia coli", prefix="", tests=None):
    rows = []
    for period, count, resistant, sampled in [("c", current, current_r, END),
                                             ("b", baseline, baseline_r, "2026-03-15")]:
        for index in range(count):
            patient = f"{prefix}shared-{index}" if index < shared else f"{prefix}{period}P{index}"
            kwargs = {"tests": tests} if tests is not None else {}
            rows.append(observation(patient, sampled, "R" if index < resistant else "S",
                culture_id=f"{prefix}{period}C{index}", isolate_id=f"{prefix}{period}I{index}",
                organism=organism, **kwargs))
    return rows


def analyze(settings, filters=FILTERS):
    return AnomalyEngine(settings).analyze(filters, as_of_date=END)


@pytest.mark.parametrize("current,baseline,status", [(0, 0, "no_data"), (0, 30, "no_data"),
    (30, 0, "no_data"), (1, 30, "insufficient_data"), (29, 30, "insufficient_data"),
    (30, 1, "insufficient_data"), (30, 29, "insufficient_data"), (30, 30, "no_significant_change")])
def test_sample_boundaries(settings, seed, current, baseline, status):
    seed(cohort_rows(current=current, baseline=baseline))
    result = analyze(settings)
    assert result.status == status
    assert result.current.n_tested == current and result.baseline.n_tested == baseline
    assert result.data_version == 7
    if status != "no_significant_change":
        assert result.p_value is None
    if current < 30:
        assert result.current.resistance_pct is None and result.current_ci is None


@pytest.mark.parametrize("current_r,baseline_r,status", [(10, 10, "no_significant_change"),
    (40, 10, "potential_increase"), (10, 40, "potential_decrease")])
def test_unchanged_increase_and_decrease(settings, seed, current_r, baseline_r, status):
    seed(cohort_rows(100, current_r, 100, baseline_r))
    result = analyze(settings)
    assert result.status == status
    assert result.difference_pp == pytest.approx(current_r - baseline_r)
    assert result.shared_patients == 0 and result.p_value is not None
    assert result.adjusted_p_value is None
    if status.startswith("potential"):
        assert result.p_value < 0.05 and "Flagged for review" in result.explanation
    else:
        assert result.p_value == pytest.approx(1.0)


def test_fisher_p_value_against_independent_hypergeometric_enumeration(settings, seed):
    seed(cohort_rows(30, 20, 30, 5))
    # Conditional on margins: 25 resistant outcomes among 60, sample row size 30.
    denominator = math.comb(60, 30)
    probabilities = [math.comb(25, k) * math.comb(35, 30 - k) / denominator for k in range(26)]
    observed = probabilities[20]
    expected = math.fsum(p for p in probabilities if p <= observed * (1 + 1e-12))
    result = analyze(settings)
    assert result.p_value == pytest.approx(expected, rel=1e-12)
    assert result.test_method == "Fisher's exact test (two-sided)"


def test_wilson_known_reference(settings, seed):
    seed(cohort_rows(30, 7, 30, 7))
    result = analyze(settings)
    assert result.current_ci.lower_pct == pytest.approx(11.792388144489496)
    assert result.current_ci.upper_pct == pytest.approx(40.928326158122164)
    assert result.current_ci.confidence_level == 0.95


@pytest.mark.parametrize("current_r,baseline_r", [(0, 30), (30, 0), (0, 0), (30, 30)])
def test_real_zero_and_hundred_percent_and_intervals(settings, seed, current_r, baseline_r):
    seed(cohort_rows(30, current_r, 30, baseline_r))
    result = analyze(settings)
    assert result.current.resistance_pct == 100 * current_r / 30
    assert result.baseline.resistance_pct == 100 * baseline_r / 30
    for ci in (result.current_ci, result.baseline_ci):
        assert 0 <= ci.lower_pct <= ci.upper_pct <= 100
    if current_r == 0:
        assert result.current_ci.lower_pct == 0
        assert result.current_ci.upper_pct == pytest.approx(11.3513393444)


def test_window_bounds_include_both_ends_and_exclude_neighbors(settings, seed):
    assert comparison_windows(END) == (date(2026, 8, 31), date(2026, 9, 27),
                                      date(2025, 8, 31), date(2026, 8, 30))
    dates = ["2025-08-30", "2025-08-31", "2026-08-30", "2026-08-31", END, "2026-09-28"]
    seed([observation(f"P{n}", sampled, "R" if n % 2 else "S") for n, sampled in enumerate(dates)])
    result = analyze(settings)
    assert result.current.n_tested == result.baseline.n_tested == 2
    assert result.current.n_resistant == result.baseline.n_resistant == 1
    assert (result.current.end_date - result.current.start_date).days + 1 == 28
    assert (result.baseline.end_date - result.baseline.start_date).days + 1 == 365


def test_leap_year_window_lengths_are_fixed():
    current_start, end, baseline_start, baseline_end = comparison_windows("2024-03-01")
    assert (end - current_start).days == 27 and (baseline_end - baseline_start).days == 364
    assert baseline_end < current_start


def test_patient_overlap_is_reported_without_discarding_observations_or_testing(settings, seed):
    seed(cohort_rows(100, 40, 100, 10, shared=1))
    result = analyze(settings)
    assert result.status == "analysis_limited" and result.shared_patients == 1
    assert result.current.n_tested == result.baseline.n_tested == 100
    assert result.p_value is None and result.adjusted_p_value is None
    assert result.difference_pp == 30
    assert result.current_ci is not None and result.baseline_ci is not None
    assert "not independent" in result.limitations[0]
    assert "withheld" in result.explanation


def test_pooled_within_period_repeated_patients_limit_test_and_interval(settings, seed):
    rows = cohort_rows(30, 15, 30, 0)
    rows.append(observation("cP0", END, "R", specimen="blood", culture_id="blood", isolate_id="blood-I"))
    seed(rows)
    pooled = analyze(settings, replace(FILTERS, specimen=None))
    assert pooled.status == "analysis_limited" and pooled.current.n_tested == 31
    assert pooled.current_distinct_patients == 30 and pooled.current_ci is None
    assert pooled.baseline_ci is not None
    assert analyze(settings).status == "potential_increase"


def test_first_untested_isolate_is_not_replaced_and_selection_resets_per_window(settings, seed):
    rows = cohort_rows(30, 15, 30, 0)
    rows.append(observation("cP0", "2026-09-01", tests={"AMP": "R"}, culture_id="earlier", isolate_id="earlier-I"))
    # Same patient in the older window remains independently selected there.
    rows.append(observation("cP0", "2026-02-01", "S", culture_id="older", isolate_id="older-I"))
    seed(rows)
    result = analyze(settings)
    assert result.current.n_first_isolates == 30 and result.current.n_tested == 29
    assert result.baseline.n_tested == 31
    assert result.status == "insufficient_data"
    resistance = ResistanceEngine(settings)
    assert result.current == resistance.calculate(FILTERS, start_date="2026-08-31", end_date=END)


def test_ties_duplicates_and_extra_antibiotics_do_not_change_selected_result(settings, seed):
    rows = cohort_rows(30, 15, 30, 0)
    rows += [observation("cP0", END, "S", culture_id="z-last", isolate_id="z-last-I"),
             observation("cP0", END, "S", culture_id="cC0", isolate_id="second-I", isolate_no=2)]
    seed(rows)
    with connect(settings) as con:
        con.execute("INSERT INTO susceptibility VALUES ('cI0','AMP','S','EUCAST 2026')")
    result = analyze(settings)
    assert (result.current.n_tested, result.current.n_resistant) == (30, 15)
    scan = AnomalyEngine(settings).scan(as_of_date=END)
    scanned = next(r for r in scan.results if r.filters == FILTERS)
    assert scanned.current == result.current and scanned.baseline == result.baseline


def test_configurable_effect_size_alpha_and_minimum_samples(settings, seed):
    seed(cohort_rows(100, 40, 100, 10))
    assert analyze(replace(settings, anomaly_minimum_change_pp=40)).status == "no_significant_change"
    assert analyze(replace(settings, anomaly_alpha=1e-30)).status == "no_significant_change"
    raised = analyze(replace(settings, anomaly_minimum_sample_size=101))
    assert raised.status == "insufficient_data" and raised.current.resistance_pct is None
    inherited = analyze(replace(settings, minimum_sample_size=101))
    assert inherited.status == "insufficient_data"


def test_effect_size_threshold_is_inclusive(settings, seed):
    seed(cohort_rows(1000, 200, 1000, 100))
    result = analyze(settings)
    assert result.difference_pp == 10 and result.status == "potential_increase"
    assert result.p_value < 0.05


def test_by_correction_known_values_ties_and_empty():
    assert adjust_p_values([]) == []
    assert adjust_p_values([0.01, 0.04, 0.03, 0.002]) == pytest.approx([1 / 24, 1 / 12, 1 / 12, 1 / 60])
    assert adjust_p_values([0, 0.05, 0.05, 1]) == pytest.approx([0, 5 / 36, 5 / 36, 1])
    assert adjust_p_values([0.03]) == pytest.approx([0.03])


def test_scan_only_observed_combinations_corrects_full_eligible_family_and_summarizes_quality(settings, seed):
    rows = cohort_rows(100, 40, 100, 10, prefix="increase-")
    rows += cohort_rows(30, 3, 30, 3, prefix="unchanged-", organism="Other organism")
    rows += cohort_rows(29, 20, 30, 0, prefix="small-", organism="Small organism")
    rows += cohort_rows(30, 15, 30, 0, shared=1, prefix="shared-", organism="Shared organism")
    rows += cohort_rows(0, 0, 30, 0, prefix="absent-", organism="Absent current")
    seed(rows)
    scan = AnomalyEngine(settings).scan(as_of_date=END)
    assert len(scan.results) == 5 and scan.tested_comparisons == 2
    assert len(scan.alerts) == 1 and scan.results[0].status == "potential_increase"
    assert scan.quality_counts["insufficient_data"] == scan.quality_counts["analysis_limited"] == scan.quality_counts["no_data"] == 1
    assert sum(scan.quality_counts.values()) == 5
    assert all(r.filters.antibiotic_code == "CIP" for r in scan.results)
    for result in scan.results:
        assert result.correction_family_size == 2
        if result.p_value is not None:
            assert result.adjusted_p_value >= result.p_value
        else:
            assert result.adjusted_p_value is None


def test_scan_discovers_tests_on_later_isolates_without_using_them_as_first(settings, seed):
    rows = cohort_rows(30, 15, 30, 0)
    rows.append(observation("cP0", "2026-09-01", tests={"AMP": "R"}, culture_id="first", isolate_id="first-I"))
    seed(rows)
    scan = AnomalyEngine(settings).scan(as_of_date=END)
    assert {r.filters.antibiotic_code for r in scan.results} == {"CIP", "AMP"}
    cip = next(r for r in scan.results if r.filters.antibiotic_code == "CIP")
    assert cip.current.n_tested == 29 and cip.current.n_first_isolates == 30
    assert cip.status == "insufficient_data"


def test_scan_uses_bounded_queries_not_one_query_per_cohort(settings, seed, monkeypatch):
    seed(cohort_rows(30, 15, 30, 0) + cohort_rows(30, 3, 30, 3, prefix="other-", organism="Other"))
    import src.resistance as resistance_module
    original = resistance_module.connect
    queries = []
    @contextmanager
    def traced(*args, **kwargs):
        assert kwargs.get("read_only") is True
        with original(*args, **kwargs) as con:
            con.set_trace_callback(queries.append)
            yield con
    monkeypatch.setattr(resistance_module, "connect", traced)
    AnomalyEngine(settings).scan(as_of_date=END)
    reads = [query for query in queries if query.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert len(reads) == 4


def test_source_snapshot_and_revision_remain_consistent_during_commit(settings, seed, monkeypatch):
    seed(cohort_rows(30, 15, 30, 0))
    engine = AnomalyEngine(settings)
    original = engine._read
    def read_and_commit(*args, **kwargs):
        population = original(*args, **kwargs)
        seed([observation("new-patient", END, "R", culture_id="new-culture", isolate_id="new-I")], version=8)
        return population
    monkeypatch.setattr(engine, "_read", read_and_commit)
    first = engine.analyze(FILTERS, as_of_date=END)
    assert first.data_version == 7 and first.current.n_tested == 30
    updated = analyze(settings)
    assert updated.data_version == 8 and updated.current.n_tested == 31


def test_repeatability_serialization_exports_and_unchanged_database(settings, seed):
    seed(cohort_rows(30, 15, 30, 0))
    with connect(settings, read_only=True) as con:
        before = list(con.iterdump())
    engine = AnomalyEngine(settings)
    assert engine.analyze(FILTERS) == engine.analyze(FILTERS)
    scan = engine.scan()
    assert scan == engine.scan()
    payload = json.dumps(scan.to_dict())
    assert '"patient_id"' not in payload and '"isolate_id"' not in payload
    csv_rows = list(csv.DictReader(io.StringIO(export_anomalies_csv(scan.results).decode("utf-8-sig"))))
    assert csv_rows[0]["current_n_resistant"] == "15"
    assert csv_rows[0]["adjusted_p_value"] and csv_rows[0]["current_ci_lower_pct"]
    assert not {"patient_id", "isolate_id", "culture_id"}.intersection(csv_rows[0])
    with connect(settings, read_only=True) as con:
        assert list(con.iterdump()) == before


def test_empty_database_and_missing_database_do_not_invent_dates_or_create_files(settings):
    engine = AnomalyEngine(settings)
    result = engine.analyze(FILTERS)
    assert result.status == "no_data" and result.analysis_date is None
    assert result.current.start_date is None and result.baseline.end_date is None
    assert engine.scan().results == ()
    missing = settings.database_path.parent / "missing" / "new.db"
    with pytest.raises(sqlite3.OperationalError, match="unable to open"):
        AnomalyEngine(replace(settings, database_path=missing)).scan()
    assert not missing.parent.exists()


@pytest.mark.parametrize("kwargs", [
    {"anomaly_minimum_sample_size": 29}, {"anomaly_minimum_sample_size": True},
    {"anomaly_alpha": 0}, {"anomaly_alpha": 1}, {"anomaly_alpha": float("nan")},
    {"anomaly_minimum_change_pp": -1}, {"anomaly_minimum_change_pp": 101},
    {"anomaly_minimum_change_pp": float("inf")}, {"anomaly_confidence_level": 0},
    {"anomaly_confidence_level": 1}, {"anomaly_confidence_level": True},
])
def test_invalid_settings_fail_explicitly(settings, kwargs):
    with pytest.raises(ValueError):
        replace(settings, **kwargs)


def test_environment_thresholds(monkeypatch):
    monkeypatch.setenv("AEI_ANOMALY_ALPHA", "0.01")
    monkeypatch.setenv("AEI_ANOMALY_MINIMUM_CHANGE_PP", "15")
    monkeypatch.setenv("AEI_ANOMALY_MINIMUM_SAMPLE_SIZE", "50")
    monkeypatch.setenv("AEI_ANOMALY_CONFIDENCE_LEVEL", "0.9")
    settings = get_settings()
    assert (settings.anomaly_alpha, settings.anomaly_minimum_change_pp,
            settings.anomaly_minimum_sample_size, settings.anomaly_confidence_level) == (0.01, 15, 50, 0.9)


def test_invalid_dates_and_early_reference_date(settings):
    engine = AnomalyEngine(settings)
    with pytest.raises(ValueError):
        engine.analyze(FILTERS, as_of_date="invalid")
    with pytest.raises(ValueError, match="too early"):
        engine.scan(as_of_date="0001-01-01")
