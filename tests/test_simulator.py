"""Replay uses real validation/transactions and never writes to its source."""

import hashlib
from dataclasses import replace
from datetime import date

import pytest

from conftest import decode_rows, encode_rows
from src.anomalies import AnomalyEngine
from src.database import check_integrity, connect, database_summary, import_history
from src.data_loader import ImportValidationError, import_csv_bundle
from src.resistance import ResistanceEngine, ResistanceFilters
from src.signals import surveillance_signals
from src.simulator import ReplayPlan, ReplaySession, historical_plan, scenario_plan
from test_anomalies import cohort_rows, seed

FILTERS = ResistanceFilters("CIP", "SIM", "Escherichia coli", "urine")


def dump(settings):
    with connect(settings, read_only=True) as con:
        return list(con.iterdump())


def scenario(settings):
    return ReplaySession(settings, scenario_plan())


def test_initial_replay_is_empty_and_source_untouched(settings, bundle):
    import_csv_bundle(bundle, settings)
    before = dump(settings)
    replay = scenario(settings)
    assert replay.settings.database_path != settings.database_path
    assert replay.cursor == 0 and not replay.running and not replay.complete
    summary = database_summary(replay.settings)
    assert summary["version"] == 0
    assert all(value == 0 for value in summary["counts"].values())
    assert summary["latest_sample"] is None
    assert dump(settings) == before


def test_scenario_steps_recalculate_rates_and_supported_signal(settings):
    replay = scenario(settings)
    assert replay.step().data_version == 1
    assert database_summary(replay.settings)["latest_sample"] == "2026-08-01"
    replay.step()
    engine = ResistanceEngine(replay.settings)
    before = engine.last_28_days(FILTERS)
    annual_before = engine.last_365_days(FILTERS)
    assert (before.n_susceptible, before.n_resistant, before.n_tested, before.resistance_pct) == (36, 4, 40, 10)
    assert annual_before.resistance_pct == 10 and annual_before.n_tested == 140
    assert not surveillance_signals(AnomalyEngine(replay.settings).scan())
    replay.step()
    after = engine.last_28_days(FILTERS)
    annual_after = engine.last_365_days(FILTERS)
    assert (after.n_susceptible, after.n_resistant, after.n_tested, after.resistance_pct) == (60, 40, 100, 40)
    assert annual_after.resistance_pct == 25 and annual_after.n_tested == 200
    scan = AnomalyEngine(replay.settings).scan()
    assert scan.data_version == 3 and scan.tested_comparisons == 1
    result = scan.alerts[0]
    assert result.shared_patients == 0 and result.current_distinct_patients == result.baseline_distinct_patients == 100
    assert result.baseline.n_resistant == 10 and result.baseline.n_susceptible == 90
    assert result.difference_pp == 30 and result.adjusted_p_value < .05
    assert result.correction_family_size == 1 and result.adjusted_p_value == result.p_value
    signal, = surveillance_signals(scan)
    assert signal.statistically_supported
    assert replay.complete and not replay.running and replay.step() is None
    assert check_integrity(replay.settings) == {"integrity": ["ok"], "foreign_key_errors": []}


def test_duplicate_retries_do_not_change_revision(settings):
    replay = scenario(settings)
    replay.step()
    # Models a crash after DB commit but before the in-memory cursor advances.
    replay.cursor = 0
    result = replay.step()
    assert result.rows_inserted == 0 and result.rows_unchanged == 402
    assert result.data_version == 1 and replay.cursor == 1
    assert database_summary(replay.settings)["counts"]["patients"] == 100


@pytest.mark.parametrize("failure", ["foreign_key", "conflict", "missing_isolate", "schema"])
def test_replay_failure_rolls_back_without_advancing_cursor(settings, failure):
    plan = scenario_plan()
    bad = dict(plan.bundles[1])
    if failure == "foreign_key":
        rows = decode_rows(bad, "cultures")
        rows[-1]["patient_id"] = "unknown"
        bad["cultures.csv"] = encode_rows("cultures", rows)
    elif failure == "conflict":
        rows = decode_rows(bad, "patients")
        rows.append({**decode_rows(plan.bundles[0], "patients")[0], "sex": "M"})
        bad["patients.csv"] = encode_rows("patients", rows)
    elif failure == "missing_isolate":
        bad["isolates.csv"] = encode_rows("isolates", decode_rows(bad, "isolates")[:-1])
    else:
        bad["patients.csv"] = b"wrong\nheader\n"
    replay = ReplaySession(settings, replace(plan, bundles=(plan.bundles[0], bad, plan.bundles[2])))
    replay.step()
    before = database_summary(replay.settings)
    replay.start()
    with pytest.raises(ImportValidationError):
        replay.step()
    assert replay.cursor == 1 and not replay.running
    assert database_summary(replay.settings) == before
    assert import_history(replay.settings)[0]["status"] == "failed"
    # Corrected retry succeeds and increments exactly once.
    replay.plan = plan
    assert replay.step().data_version == 2 and replay.cursor == 2


def test_reset_rotates_database_and_can_repeat_scenario(settings):
    replay = scenario(settings)
    for _ in range(3):
        replay.step()
    old = replay.settings
    replay.reset()
    assert replay.cursor == 0 and not replay.running and replay.last_result is None
    assert replay.settings.database_path != old.database_path
    assert database_summary(replay.settings)["version"] == 0
    assert database_summary(old)["version"] == 3
    for _ in range(3):
        replay.step()
    assert ResistanceEngine(replay.settings).last_28_days(FILTERS).resistance_pct == 40


def test_two_sessions_do_not_share_demonstration_databases(settings):
    first, second = scenario(settings), scenario(settings)
    first.step()
    first.start()
    assert first.settings.database_path != second.settings.database_path
    assert second.cursor == 0 and not second.running
    assert database_summary(second.settings)["version"] == 0
    second.step()
    first.reset()
    assert second.cursor == 1 and database_summary(second.settings)["version"] == 1
    assert database_summary(settings)["version"] == 0


def test_pause_restart_speed_and_single_tick(settings):
    replay = scenario(settings)
    replay.set_speed(5)
    replay.start()
    due = replay.next_due
    assert replay.tick(due - .001) is None
    assert replay.tick(due).data_version == 1
    assert replay.cursor == 1  # No catch-up loop, even after a long idle interval.
    replay.pause()
    assert replay.tick(float("inf")) is None
    replay.start()
    assert replay.tick(replay.next_due).data_version == 2


@pytest.mark.parametrize("interval", [0, -1, True, 1.5, float("nan")])
def test_invalid_playback_speed(settings, interval):
    with pytest.raises(ValueError):
        scenario(settings).set_speed(interval)


def test_historical_weekly_packets_include_dependencies_and_only_their_dates(settings):
    source = scenario(settings)
    for _ in range(3):
        source.step()
    before = dump(source.settings)
    plan = historical_plan(source.settings)
    assert plan.periods == ((date(2026, 7, 27), date(2026, 8, 2)), (date(2026, 8, 31), date(2026, 9, 6)))
    assert len(decode_rows(plan.bundle(0), "cultures")) == 100
    assert len(decode_rows(plan.bundle(1), "cultures")) == 100
    replay = ReplaySession(settings, plan)
    replay.step()
    assert database_summary(replay.settings)["latest_sample"] == "2026-08-01"
    replay.step()
    assert database_summary(replay.settings)["counts"] == database_summary(source.settings)["counts"]
    assert dump(source.settings) == before
    assert check_integrity(replay.settings)["foreign_key_errors"] == []


def test_historical_source_change_requires_reset(settings, bundle):
    import_csv_bundle(bundle, settings)
    replay = ReplaySession(settings, historical_plan(settings))
    with connect(settings) as con:
        con.execute("UPDATE data_version SET version=2")
        con.commit()
    with pytest.raises(ValueError, match="source changed"):
        replay.step()
    assert replay.cursor == 0 and database_summary(replay.settings)["version"] == 0
    replay.reset()
    assert replay.plan.source_version == 2
    replay.step()
    assert replay.cursor == 1


def test_empty_historical_source_is_complete(settings):
    replay = ReplaySession(settings, historical_plan(settings))
    assert replay.complete
    replay.start()
    assert not replay.running and replay.step() is None


@pytest.mark.parametrize("shared", [0, 1, 30])
def test_preliminary_signal_does_not_bypass_patient_independence(settings, seed, shared):
    seed(cohort_rows(30, 15, 30, 0, shared=shared))
    scan = AnomalyEngine(settings).scan()
    signal, = surveillance_signals(scan, 10)
    assert signal.comparison.shared_patients == shared
    assert signal.statistically_supported == (shared == 0)
    if shared:
        assert signal.comparison.p_value is None
        assert signal.comparison.status == "analysis_limited"
        assert not scan.alerts


def test_signal_threshold_is_strict_and_sample_guards_remain(settings, seed):
    seed(cohort_rows(30, 15, 30, 0))
    scan = AnomalyEngine(settings).scan()
    assert surveillance_signals(scan, 49.99)
    assert not surveillance_signals(scan, 50)
    assert not surveillance_signals(scan, 100)
    limited = AnomalyEngine(replace(settings, anomaly_minimum_sample_size=31)).scan()
    assert not surveillance_signals(limited, 0)


def test_equal_threshold_is_not_exceeded_due_to_float_roundoff(settings, seed):
    seed(cohort_rows(30, 10, 30, 7))
    scan = AnomalyEngine(settings).scan()
    assert scan.results[0].difference_pp == pytest.approx(10)
    assert not surveillance_signals(scan, 10)
    assert surveillance_signals(scan, 9.99)


@pytest.mark.parametrize("threshold", [-1, 101, True, None, float("inf"), float("nan")])
def test_signal_threshold_validation(settings, threshold):
    with pytest.raises(ValueError):
        surveillance_signals(AnomalyEngine(settings).scan(), threshold)
    with pytest.raises(ValueError):
        replace(settings, signal_minimum_change_pp=threshold)


@pytest.mark.integration
def test_original_database_and_csv_files_are_preserved_by_replay():
    from src.config import get_settings
    primary = get_settings()
    assert primary.database_path.exists()
    paths = [primary.database_path, *primary.dataset_path.glob("*.csv")]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    before = dump(primary)
    replay = ReplaySession(primary, historical_plan(primary))
    replay.step()
    assert 0 < database_summary(replay.settings)["counts"]["cultures"] < 46792
    replay.pause()
    replay.start()
    replay.step()
    replay.reset()
    artificial = scenario(primary)
    for _ in range(3):
        artificial.step()
    artificial.reset()
    assert dump(primary) == before
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == hashes
