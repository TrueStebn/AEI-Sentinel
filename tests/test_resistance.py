"""Hand-checkable cohorts testing the reporting contract, not SQL internals."""

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from src.config import get_settings
from src.database import connect, transaction
from src.resistance import ResistanceEngine, ResistanceFilters

FILTERS = ResistanceFilters("CIP")
START, END = "2026-09-01", "2026-09-27"


def observation(patient, sampled=END, result="S", **kwargs):
    return {"patient": patient, "sampled": sampled, "result": result, **kwargs}


@pytest.fixture
def seed(settings):
    """Write small valid laboratory cohorts directly, independent of CSV parsing."""
    def write(observations, version=7):
        with connect(settings) as con, transaction(con):
            con.executemany("INSERT OR IGNORE INTO wards VALUES (?,?,?,?,?)", [
                ("ED", "Emergency", "Emergency", "out", "hospital"),
                ("URO", "Urology", "Urology", "in", "hospital"),
            ])
            con.executemany("INSERT OR IGNORE INTO antibiotics VALUES (?,?,?,?,?)", [
                ("CIP", "ciprofloxacin", "siprofloksasiini", "fluoroquinolones", "all"),
                ("AMP", "ampicillin", "ampisilliini", "penicillins", "all"),
                ("TMP", "trimethoprim", "trimetopriimi", "trimethoprim", "urine"),
            ])
            for index, row in enumerate(observations):
                patient = row["patient"]
                sampled = date.fromisoformat(row["sampled"])
                ward = row.get("ward_code", "ED")
                specimen = row.get("specimen", "urine")
                culture = row.get("culture_id", f"C{index:04d}")
                isolate = row.get("isolate_id", f"I{index:04d}")
                con.execute("INSERT OR IGNORE INTO patients VALUES (?,'F',1980,'Centre')", (patient,))
                con.execute("INSERT OR IGNORE INTO cultures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    culture, patient, sampled.year - 1980, sampled.isoformat(),
                    sampled.isocalendar().year, sampled.isocalendar().week, ward,
                    sampled.isoformat() if ward == "URO" else None, specimen,
                    {"urine": "virtsa", "blood": "veri", "wound": "haava"}[specimen], "positive", 0,
                ))
                con.execute("INSERT INTO isolates VALUES (?,?,?,?,?,0,0,0,0)", (
                    isolate, culture, row.get("isolate_no", 1),
                    row.get("organism", "Escherichia coli"), None,
                ))
                con.execute("UPDATE cultures SET n_isolates_declared=n_isolates_declared+1 "
                            "WHERE culture_id=?", (culture,))
                tests = row.get("tests", {"CIP": row["result"]})
                con.executemany("INSERT INTO susceptibility VALUES (?,?,?,?)", [
                    (isolate, antibiotic, result, f"EUCAST {sampled.year}")
                    for antibiotic, result in tests.items()
                ])
            con.execute("UPDATE data_version SET version=? WHERE singleton_id=1", (version,))
    return write


def calculate(settings, filters=FILTERS, start=START, end=END):
    return ResistanceEngine(settings).calculate(filters, start_date=start, end_date=end)


@pytest.mark.parametrize("count,status", [(0, "no_data"), (1, "insufficient_data"),
                                         (29, "insufficient_data"), (30, "ok"), (45, "ok")])
def test_threshold_boundaries_and_actual_counts(settings, seed, count, status):
    seed([observation(f"P{i}", result="R" if i < 7 else "S") for i in range(count)])
    result = calculate(settings)
    assert result.status == status
    assert result.n_tested == result.n_first_isolates == count
    assert result.n_resistant == min(7, count)
    assert result.n_susceptible == max(count - 7, 0)
    assert result.minimum_sample_size == 30 and result.data_version == 7
    assert result.start_date == date(2026, 9, 1) and result.end_date == date(2026, 9, 27)
    assert result.filters == FILTERS
    if count < 30:
        assert result.resistance_pct is None
    else:
        assert result.resistance_pct == pytest.approx(100 * 7 / count)


@pytest.mark.parametrize("resistant,expected", [(0, 0.0), (7, 100 * 7 / 30), (30, 100.0)])
def test_formula_including_true_zero_and_hundred_percent(settings, seed, resistant, expected):
    seed([observation(f"P{i}", result="R" if i < resistant else "S") for i in range(30)])
    result = calculate(settings)
    assert result.status == "ok"
    assert result.n_susceptible == 30 - resistant
    assert result.n_resistant == resistant
    assert result.n_tested == result.n_susceptible + result.n_resistant == 30
    assert result.resistance_pct == pytest.approx(expected)


def test_deduplicate_patient_species_specimen_within_period(settings, seed):
    seed([
        observation("P1", "2026-09-01", "S"),
        observation("P1", "2026-09-02", "R"),
        observation("P1", "2026-09-03", "R", specimen="blood"),
        observation("P1", "2026-09-04", "R", organism="Klebsiella pneumoniae"),
        observation("P2", "2026-09-05", "S"),
    ])
    result = calculate(settings)
    assert (result.n_first_isolates, result.n_tested) == (4, 4)
    assert (result.n_susceptible, result.n_resistant) == (2, 2)


def test_first_untested_isolate_is_not_replaced_by_later_test(settings, seed):
    seed([
        observation("P1", "2026-09-01", tests={"AMP": "R"}),
        observation("P1", "2026-09-02", "R"),
        observation("P2", "2026-09-03", "S"),
    ])
    result = calculate(settings)
    assert (result.n_first_isolates, result.n_tested) == (2, 1)
    assert (result.n_susceptible, result.n_resistant) == (1, 0)


def test_threshold_uses_tested_first_isolates_not_all_first_isolates(settings, seed):
    seed([observation("untested", tests={"AMP": "R"})] +
         [observation(f"P{i}") for i in range(29)])
    result = calculate(settings)
    assert result.n_first_isolates == 30 and result.n_tested == 29
    assert result.status == "insufficient_data" and result.resistance_pct is None


def test_multiple_antibiotic_rows_do_not_multiply_isolates(settings, seed):
    seed([observation("P1", tests={"CIP": "S", "AMP": "R", "TMP": "R"})])
    cip = calculate(settings)
    amp = calculate(settings, ResistanceFilters("AMP"))
    assert cip.n_first_isolates == cip.n_tested == amp.n_tested == 1
    assert cip.n_susceptible == 1 and amp.n_resistant == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_ties_use_culture_id_then_isolate_number_not_insertion_order(settings, seed, reverse):
    rows = [
        observation("P1", "2026-09-02", "R", culture_id="C0002"),
        observation("P1", "2026-09-02", "S", culture_id="C0001"),
        observation("P2", "2026-09-03", "R", culture_id="C0003", isolate_no=2, isolate_id="I-a"),
        observation("P2", "2026-09-03", "S", culture_id="C0003", isolate_no=1, isolate_id="I-z"),
    ]
    seed(list(reversed(rows)) if reverse else rows)
    result = calculate(settings)
    assert (result.n_tested, result.n_susceptible, result.n_resistant) == (2, 2, 0)
    assert calculate(settings) == result


def test_ward_filter_precedes_deduplication_and_all_wards_deduplicate_together(settings, seed):
    seed([
        observation("P1", "2026-09-01", "S", ward_code="ED"),
        observation("P1", "2026-09-02", "R", ward_code="URO"),
    ])
    combined = calculate(settings)
    uro = calculate(settings, ResistanceFilters("CIP", ward_code="URO"))
    assert (combined.n_susceptible, combined.n_resistant) == (1, 0)
    assert (uro.n_susceptible, uro.n_resistant) == (0, 1)


@pytest.mark.parametrize("filters,expected", [
    (FILTERS, (5, 2, 2)),
    (ResistanceFilters("CIP", ward_code="ED"), (4, 2, 1)),
    (ResistanceFilters("CIP", organism="Escherichia coli"), (4, 2, 1)),
    (ResistanceFilters("AMP"), (5, 0, 1)),
    (ResistanceFilters("CIP", specimen="urine"), (4, 1, 2)),
    (ResistanceFilters("CIP", "URO", "Escherichia coli", "urine"), (1, 0, 1)),
])
def test_each_filter_and_combined_filters(settings, seed, filters, expected):
    seed([
        observation("P1", "2026-09-01", "S"),
        observation("P2", "2026-09-02", "R", ward_code="URO"),
        observation("P3", "2026-09-03", "R", organism="Klebsiella pneumoniae"),
        observation("P4", "2026-09-04", "S", specimen="blood"),
        observation("P5", "2026-09-05", tests={"AMP": "R"}),
    ])
    result = calculate(settings, filters)
    assert (result.n_first_isolates, result.n_susceptible, result.n_resistant) == expected
    assert result.filters == filters


@pytest.mark.parametrize("filters", [ResistanceFilters("unknown"),
                                    ResistanceFilters("CIP", ward_code="unknown"),
                                    ResistanceFilters("CIP", organism="unknown"),
                                    ResistanceFilters("CIP", ward_code="ED' OR 1=1 --")])
def test_nonmatching_filters_return_no_data_without_assuming_zero_percent(settings, seed, filters):
    seed([observation("P1")])
    result = calculate(settings, filters)
    assert result.status == "no_data" and result.resistance_pct is None
    assert result.n_tested == result.n_susceptible == result.n_resistant == 0


def test_first_isolates_exist_but_no_tests_is_distinct_from_no_isolates(settings, seed):
    seed([observation("P1", tests={"AMP": "R"})])
    untested = calculate(settings)
    absent = calculate(settings, ResistanceFilters("CIP", specimen="wound"))
    assert untested.status == absent.status == "no_data"
    assert untested.n_first_isolates == 1 and absent.n_first_isolates == 0


@pytest.mark.parametrize("days,method", [(28, "last_28_days"), (365, "last_365_days")])
def test_inclusive_window_boundaries(settings, seed, days, method):
    end = date(2026, 9, 27)
    start = end - timedelta(days=days - 1)
    seed([observation("before", (start - timedelta(days=1)).isoformat(), "R"),
          observation("first", start.isoformat(), "S"),
          observation("last", end.isoformat(), "R"),
          observation("after", (end + timedelta(days=1)).isoformat(), "S")])
    result = getattr(ResistanceEngine(settings), method)(FILTERS, as_of_date=end)
    assert (result.start_date, result.end_date) == (start, end)
    assert (result.n_susceptible, result.n_resistant, result.n_tested) == (1, 1, 2)


def test_rolling_365_days_includes_leap_day_and_is_not_a_calendar_year(settings, seed):
    seed([observation("before", "2023-03-01", "R"),
          observation("start", "2023-03-02", "S"),
          observation("leap", "2024-02-29", "R")])
    result = ResistanceEngine(settings).last_365_days(FILTERS, as_of_date="2024-02-29")
    assert result.start_date == date(2023, 3, 2)
    assert (result.n_susceptible, result.n_resistant) == (1, 1)


def test_windows_independently_choose_first_isolates(settings, seed):
    seed([observation("P1", "2026-08-30", "S"), observation("P1", "2026-08-31", "R"),
          observation("anchor", END, tests={"AMP": "R"})])
    engine = ResistanceEngine(settings)
    recent = engine.last_28_days(FILTERS)
    annual = engine.last_365_days(FILTERS)
    assert recent.start_date == date(2026, 8, 31)
    assert annual.start_date == date(2025, 9, 28)
    assert recent.end_date == annual.end_date == date(2026, 9, 27)
    assert (recent.n_susceptible, recent.n_resistant) == (0, 1)
    assert (annual.n_susceptible, annual.n_resistant) == (1, 0)


def test_default_anchor_is_latest_culture_even_when_negative_or_outside_filters(settings, seed):
    seed([observation("P1", "2026-08-31", "S")])
    with connect(settings) as con:
        con.execute("INSERT INTO cultures VALUES "
                    "('negative','P1',46,'2026-09-28',2026,40,'URO',"
                    "'2026-09-28','blood','veri','negative',0)")
    result = ResistanceEngine(settings).last_28_days(
        ResistanceFilters("CIP", ward_code="ED", specimen="urine"))
    assert result.end_date == date(2026, 9, 28) and result.start_date == date(2026, 9, 1)
    assert result.status == "no_data"


def test_arbitrary_period_and_single_day_include_both_endpoints(settings, seed):
    seed([observation("P1", "2026-09-01", "R"), observation("P2", END, "S")])
    result = calculate(settings)
    assert result.n_tested == 2
    one_day = calculate(settings, start=END)
    assert one_day.start_date == one_day.end_date == date(2026, 9, 27)
    assert one_day.n_tested == one_day.n_susceptible == 1
    outside = calculate(settings, start="2020-01-01", end="2020-12-31")
    assert outside.status == "no_data" and outside.resistance_pct is None


def test_empty_database_has_no_invented_default_date(settings):
    engine = ResistanceEngine(settings)
    result = engine.last_28_days(FILTERS)
    assert result.status == "no_data" and result.resistance_pct is None
    assert result.start_date is result.end_date is None
    assert result.n_tested == result.n_first_isolates == result.data_version == 0
    explicit = engine.last_365_days(FILTERS, as_of_date=END)
    assert explicit.end_date == date(2026, 9, 27)
    assert explicit.start_date == date(2025, 9, 28) and explicit.status == "no_data"


def test_weekly_buckets_reset_deduplication_include_gaps_and_clip_edges(settings, seed):
    seed([observation("P1", "2026-09-06", "S"),
          observation("P1", "2026-09-07", "R"),
          observation("P1", "2026-09-08", "S"),
          observation("P1", "2026-09-22", "S")])
    engine = ResistanceEngine(settings)
    series = engine.time_series(FILTERS, start_date="2026-09-06", end_date="2026-09-22")
    assert [(p.start_date.isoformat(), p.end_date.isoformat()) for p in series.points] == [
        ("2026-09-06", "2026-09-06"), ("2026-09-07", "2026-09-13"),
        ("2026-09-14", "2026-09-20"), ("2026-09-21", "2026-09-22"),
    ]
    assert [(p.n_susceptible, p.n_resistant) for p in series.points] == [(1, 0), (0, 1), (0, 0), (1, 0)]
    assert series.points[2].status == "no_data" and series.points[2].resistance_pct is None
    assert series.frequency == "week" and series.data_version == 7
    assert all(point.data_version == 7 and point.filters == FILTERS for point in series.points)
    # This patient is eligible once in the whole period, once in each active bucket.
    assert sum(p.n_tested for p in series.points) == 3
    assert engine.calculate(FILTERS, start_date="2026-09-06", end_date="2026-09-22").n_tested == 1


def test_week_crosses_iso_year_without_splitting_january_first(settings, seed):
    seed([observation("P1", "2020-12-31", "S"), observation("P1", "2021-01-01", "R"),
          observation("P1", "2021-01-04", "R")])
    series = ResistanceEngine(settings).time_series(
        FILTERS, start_date="2020-12-30", end_date="2021-01-04")
    assert [(p.start_date, p.end_date) for p in series.points] == [
        (date(2020, 12, 30), date(2021, 1, 3)), (date(2021, 1, 4), date(2021, 1, 4))]
    assert [(p.n_susceptible, p.n_resistant) for p in series.points] == [(1, 0), (0, 1)]


def test_monthly_buckets_handle_leap_year_gaps_and_per_month_dedup(settings, seed):
    seed([observation("P1", "2024-01-31", "S"), observation("P1", "2024-02-01", "R"),
          observation("P1", "2024-02-29", "S"), observation("P1", "2024-04-01", "S")])
    series = ResistanceEngine(settings).time_series(
        FILTERS, start_date="2024-01-31", end_date="2024-04-02", frequency="month")
    assert [(p.start_date.isoformat(), p.end_date.isoformat()) for p in series.points] == [
        ("2024-01-31", "2024-01-31"), ("2024-02-01", "2024-02-29"),
        ("2024-03-01", "2024-03-31"), ("2024-04-01", "2024-04-02")]
    assert [(p.n_susceptible, p.n_resistant) for p in series.points] == [(1, 0), (0, 1), (0, 0), (1, 0)]
    assert series.frequency == "month" and series.points[2].status == "no_data"


def test_series_applies_all_filters_and_sample_threshold_per_bucket(settings, seed):
    rows = [observation(f"P{i}", "2026-09-07", "R" if i < 6 else "S", ward_code="URO")
            for i in range(30)]
    rows += [observation("P0", "2026-09-14", "R", ward_code="URO"),
             observation("other_ward", "2026-09-07", "R"),
             observation("other_species", "2026-09-07", "R", ward_code="URO", organism="Other"),
             observation("other_specimen", "2026-09-07", "R", ward_code="URO", specimen="blood")]
    seed(rows)
    filters = ResistanceFilters("CIP", "URO", "Escherichia coli", "urine")
    series = ResistanceEngine(settings).time_series(
        filters, start_date="2026-09-07", end_date="2026-09-20")
    first, second = series.points
    assert (first.n_tested, first.n_resistant, first.resistance_pct, first.status) == (30, 6, 20.0, "ok")
    assert second.n_tested == 1 and second.status == "insufficient_data"
    assert second.resistance_pct is None


def test_empty_series_still_has_explicit_no_data_buckets(settings):
    series = ResistanceEngine(settings).time_series(
        FILTERS, start_date="2026-08-30", end_date="2026-09-01")
    assert len(series.points) == 2
    assert all(p.status == "no_data" and p.resistance_pct is None for p in series.points)


def test_partial_first_bucket_only_considers_dates_inside_requested_range(settings, seed):
    seed([observation("P1", "2026-09-01", "S"), observation("P1", "2026-09-03", "R")])
    series = ResistanceEngine(settings).time_series(
        FILTERS, start_date="2026-09-02", end_date="2026-09-04")
    assert len(series.points) == 1
    assert (series.points[0].n_susceptible, series.points[0].n_resistant) == (0, 1)


def test_configurable_threshold_and_environment_setting(settings, seed, monkeypatch):
    seed([observation(f"P{i}") for i in range(30)])
    raised = ResistanceEngine(replace(settings, minimum_sample_size=31)).last_28_days(FILTERS)
    assert raised.status == "insufficient_data" and raised.minimum_sample_size == 31
    assert calculate(settings).status == "ok"
    monkeypatch.setenv("AEI_MINIMUM_SAMPLE_SIZE", "50")
    assert get_settings().minimum_sample_size == 50


@pytest.mark.parametrize("minimum", [0, 29, -1, True, 30.0, "30"])
def test_threshold_cannot_be_lower_than_thirty_or_non_integer(settings, minimum):
    with pytest.raises(ValueError, match="at least 30"):
        replace(settings, minimum_sample_size=minimum)


@pytest.mark.parametrize("kwargs", [
    {"antibiotic_code": ""}, {"antibiotic_code": None}, {"antibiotic_code": ["CIP", "AMP"]},
    {"antibiotic_code": " CIP"}, {"antibiotic_code": "CIP", "ward_code": ""},
    {"antibiotic_code": "CIP", "specimen": "saliva"},
])
def test_invalid_filter_parameters_fail_explicitly(kwargs):
    with pytest.raises(ValueError):
        ResistanceFilters(**kwargs)


@pytest.mark.parametrize("start,end", [(END, START), ("2026-02-30", END),
                                       ("20260901", END), (None, END),
                                       (datetime(2026, 9, 1), END)])
def test_invalid_periods_fail_before_querying(settings, start, end):
    with pytest.raises(ValueError):
        calculate(settings, start=start, end=end)


def test_invalid_frequency_and_as_of_date(settings):
    engine = ResistanceEngine(settings)
    with pytest.raises(ValueError, match="frequency"):
        engine.time_series(FILTERS, start_date=START, end_date=END, frequency="day")
    with pytest.raises(ValueError, match="as_of_date"):
        engine.last_28_days(FILTERS, as_of_date="invalid")
    with pytest.raises(ValueError, match="too early"):
        engine.last_365_days(FILTERS, as_of_date="0001-01-01")


@pytest.mark.parametrize("frequency", ["week", "month"])
def test_series_date_max_has_no_overflow(settings, frequency):
    series = ResistanceEngine(settings).time_series(
        FILTERS, start_date="9999-12-31", end_date="9999-12-31", frequency=frequency)
    assert len(series.points) == 1 and series.points[0].end_date == date.max


def test_results_serialize_to_json_and_repeat_exactly(settings, seed):
    seed([observation(f"P{i}", result="R" if i == 0 else "S") for i in range(30)])
    engine = ResistanceEngine(settings)
    result = engine.last_28_days(FILTERS)
    assert result == engine.last_28_days(FILTERS)
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["end_date"] == END and payload["filters"]["antibiotic_code"] == "CIP"
    series = engine.time_series(FILTERS, start_date=START, end_date=END, frequency="month")
    assert series == engine.time_series(FILTERS, start_date=START, end_date=END, frequency="month")
    assert json.loads(json.dumps(series.to_dict()))["points"][0]["n_tested"] == 30


def test_analysis_leaves_all_persistent_records_unchanged(settings, seed):
    seed([observation("P1", "2026-09-01", "S"), observation("P1", END, "R")])
    with connect(settings, read_only=True) as con:
        before = list(con.iterdump())
    engine = ResistanceEngine(settings)
    engine.last_28_days(FILTERS)
    engine.last_365_days(FILTERS)
    engine.calculate(FILTERS, start_date=START, end_date=END)
    engine.time_series(FILTERS, start_date=START, end_date=END)
    with connect(settings, read_only=True) as con:
        assert list(con.iterdump()) == before
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("DELETE FROM patients")


def test_analysis_does_not_create_a_missing_database(settings):
    missing = settings.database_path.parent / "not-created" / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        ResistanceEngine(replace(settings, database_path=missing)).last_28_days(FILTERS)
    assert not missing.exists() and not missing.parent.exists()


def test_series_counts_and_revision_share_one_snapshot_during_a_commit(settings, seed, monkeypatch):
    seed([observation("P1", "2026-09-07", "S")])
    engine = ResistanceEngine(settings)
    original = engine._calculate
    committed = False

    def calculate_and_commit(*args):
        nonlocal committed
        result = original(*args)
        if not committed:
            # A different connection commits after the first bucket was read.
            seed([observation("P2", "2026-09-14", "R", culture_id="new-culture",
                              isolate_id="new-isolate")], version=8)
            committed = True
        return result

    monkeypatch.setattr(engine, "_calculate", calculate_and_commit)
    series = engine.time_series(FILTERS, start_date="2026-09-07", end_date="2026-09-20")
    assert series.data_version == 7
    assert [p.data_version for p in series.points] == [7, 7]
    assert [p.n_tested for p in series.points] == [1, 0]
    updated = ResistanceEngine(settings).time_series(
        FILTERS, start_date="2026-09-07", end_date="2026-09-20")
    assert updated.data_version == 8
    assert [p.n_tested for p in updated.points] == [1, 1]


def test_unsupported_database_schema_is_not_modified(settings):
    with connect(settings) as con:
        con.execute("PRAGMA user_version=2")
    with pytest.raises(RuntimeError, match="version-1"):
        ResistanceEngine(settings).last_28_days(FILTERS)
    with connect(settings, read_only=True) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.integration
def test_working_database_matches_independent_python_reference():
    """Check the existing synthetic DB without importing or changing any rows."""
    settings = get_settings()
    if not settings.database_path.exists():
        pytest.skip("Working database is unavailable; import the source CSVs first")
    engine = ResistanceEngine(settings)
    cases = [
        (ResistanceFilters("TMP", "URO", "Escherichia coli", "urine"), "last_28_days"),
        (ResistanceFilters("CIP", "URO", "Escherichia coli", "urine"), "last_365_days"),
        (ResistanceFilters("CIP", organism="Escherichia coli", specimen="urine"), "last_28_days"),
        (ResistanceFilters("FOX", "ICU", "Staphylococcus aureus", "blood"), "last_365_days"),
    ]
    with connect(settings, read_only=True) as con:
        con.execute("BEGIN")
        version = con.execute("SELECT version FROM data_version").fetchone()[0]
        latest = con.execute("SELECT MAX(sample_date) FROM cultures").fetchone()[0]
        if latest is None:
            pytest.skip("Working database contains no cultures")
        # Read raw isolate observations and tests separately, without window SQL.
        isolates = [dict(row) for row in con.execute(
            "SELECT c.patient_id,c.sample_date,c.culture_id,c.specimen,c.ward_code,"
            "i.organism,i.isolate_no,i.isolate_id FROM cultures c "
            "JOIN isolates i ON i.culture_id=c.culture_id")]
        tests = {(row[0], row[1]): row[2] for row in con.execute(
            "SELECT isolate_id,antibiotic_code,result FROM susceptibility")}
    isolates.sort(key=lambda row: (row["sample_date"], row["culture_id"],
                                   row["isolate_no"], row["isolate_id"]))
    for filters, method in cases:
        result = getattr(engine, method)(filters, as_of_date=latest)
        first = {}
        for row in isolates:
            if not result.start_date.isoformat() <= row["sample_date"] <= latest:
                continue
            if any(getattr(filters, field) is not None and getattr(filters, field) != row[field]
                   for field in ("ward_code", "organism", "specimen")):
                continue
            key = row["patient_id"], row["organism"], row["specimen"]
            first.setdefault(key, row["isolate_id"])
        values = [tests.get((isolate, filters.antibiotic_code)) for isolate in first.values()]
        assert result.n_first_isolates == len(first)
        assert result.n_susceptible == values.count("S")
        assert result.n_resistant == values.count("R")
        assert result.n_tested == values.count("S") + values.count("R")
        assert result.data_version == version
