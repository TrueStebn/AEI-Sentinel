from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import decode_rows, encode_rows
from src.database import check_integrity, connect, database_summary, import_history
from src.data_loader import (ImportValidationError, ORIGINAL_COUNTS, import_csv_bundle,
                             import_directory)
from src.config import get_settings


def test_import_idempotency_and_history(settings, bundle):
    first = import_csv_bundle(bundle, settings)
    second = import_csv_bundle(bundle, settings)
    assert (first.rows_inserted, first.rows_unchanged, first.data_version) == (6, 0, 1)
    assert (second.rows_inserted, second.rows_unchanged, second.data_version) == (0, 6, 1)
    assert set(database_summary(settings)["counts"].values()) == {1}
    assert check_integrity(settings) == {"integrity": ["ok"], "foreign_key_errors": []}
    with connect(settings) as con:
        assert con.execute("SELECT admission_date FROM cultures").fetchone()[0] is None
        files = con.execute("SELECT * FROM import_files").fetchall()
        assert len(files) == 12
        assert all(len(row["sha256"]) == 64 and row["row_count"] == 1 for row in files)
    assert [row["status"] for row in import_history(settings)] == ["completed", "completed"]


@pytest.mark.parametrize("table,column,value", [
    ("patients", "sex", "X"), ("patients", "birth_year", "1980.5"),
    ("cultures", "age_years", "131"), ("cultures", "sample_date", "2026-02-30"),
    ("cultures", "iso_week", "1"), ("cultures", "ward_fi", "Wrong label"),
    ("cultures", "patient_id", "missing"), ("cultures", "ward_code", "missing"),
    ("cultures", "specimen", "saliva"), ("cultures", "specimen_fi", "wrong"),
    ("cultures", "n_isolates", "2"), ("cultures", "admission_date", "2026-09-28"),
    ("isolates", "culture_id", "missing"), ("isolates", "esbl", "2"),
    ("susceptibility", "isolate_id", "missing"),
    ("susceptibility", "antibiotic_code", "missing"),
    ("susceptibility", "culture_id", "wrong"),
    ("susceptibility", "organism", "wrong"),
    ("susceptibility", "antibiotic", "wrong"),
    ("susceptibility", "antibiotic_fi", "wrong"),
    ("susceptibility", "result", "I"), ("susceptibility", "guideline", "unknown"),
])
def test_invalid_values_preserve_empty_database(settings, bundle, table, column, value):
    rows = decode_rows(bundle, table)
    rows[0][column] = value
    bundle[f"{table}.csv"] = encode_rows(table, rows)
    with pytest.raises(ImportValidationError):
        import_csv_bundle(bundle, settings)
    summary = database_summary(settings)
    assert sum(summary["counts"].values()) == 0
    assert summary["version"] == 0
    assert import_history(settings)[0]["status"] == "failed"


def test_conflicting_existing_record_preserves_committed_data(settings, bundle):
    import_csv_bundle(bundle, settings)
    rows = decode_rows(bundle, "patients")
    rows[0]["home_area"] = "Changed"
    rows.append({**rows[0], "patient_id": "P2"})
    bundle["patients.csv"] = encode_rows("patients", rows)
    with pytest.raises(ImportValidationError, match="overwrites"):
        import_csv_bundle(bundle, settings)
    with connect(settings) as con:
        assert con.execute("SELECT home_area FROM patients").fetchone()[0] == "Centre"
    assert database_summary(settings)["version"] == 1
    assert database_summary(settings)["counts"]["patients"] == 1


def test_duplicate_rows_are_idempotent_but_conflicts_fail(settings, bundle):
    rows = decode_rows(bundle, "patients")
    bundle["patients.csv"] = encode_rows("patients", rows * 2)
    result = import_csv_bundle(bundle, settings)
    assert result.rows_inserted == 6 and result.rows_unchanged == 1
    bundle["patients.csv"] = encode_rows("patients", [rows[0], {**rows[0], "sex": "M"}])
    with pytest.raises(ImportValidationError, match="duplicate"):
        import_csv_bundle(bundle, settings)


def test_invalid_redundant_metadata_is_checked_on_reimport(settings, bundle):
    import_csv_bundle(bundle, settings)
    rows = decode_rows(bundle, "susceptibility")
    rows[0]["organism"] = "Wrong organism"
    bundle["susceptibility.csv"] = encode_rows("susceptibility", rows)
    with pytest.raises(ImportValidationError, match="organism"):
        import_csv_bundle(bundle, settings)
    assert database_summary(settings)["version"] == 1


def test_new_record_increments_revision_once(settings, bundle):
    import_csv_bundle(bundle, settings)
    with connect(settings) as con:
        con.execute("INSERT INTO analysis_cache VALUES ('test',1,'future','{}','{}',CURRENT_TIMESTAMP)")
    rows = decode_rows(bundle, "patients")
    rows.append({**rows[0], "patient_id": "P2"})
    bundle["patients.csv"] = encode_rows("patients", rows)
    result = import_csv_bundle(bundle, settings)
    assert (result.rows_inserted, result.data_version) == (1, 2)
    assert import_csv_bundle(bundle, settings).data_version == 2
    with connect(settings) as con:
        assert con.execute("SELECT COUNT(*) FROM analysis_cache WHERE data_revision="
                           "(SELECT version FROM data_version)").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["missing", "extra", "columns", "encoding", "ragged"])
def test_malformed_input_logged(settings, bundle, failure):
    if failure == "missing":
        del bundle["patients.csv"]
    elif failure == "extra":
        bundle["unknown.csv"] = b"id\n"
    elif failure == "columns":
        bundle["patients.csv"] = b"patient_id,wrong\nP1,F\n"
    elif failure == "encoding":
        bundle["patients.csv"] = b"\xff\xfe"
    elif failure == "ragged":
        bundle["patients.csv"] += b"P2,F\n"
    with pytest.raises(ImportValidationError):
        import_csv_bundle(bundle, settings)
    assert import_history(settings)[0]["status"] == "failed"
    assert database_summary(settings)["version"] == 0


def test_failure_during_insert_rolls_back_all_changes(settings, bundle):
    with connect(settings) as con:
        con.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON susceptibility "
                    "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
    with pytest.raises(ImportValidationError, match="injected"):
        import_csv_bundle(bundle, settings)
    assert sum(database_summary(settings)["counts"].values()) == 0
    assert database_summary(settings)["version"] == 0
    history = import_history(settings)
    assert len(history) == 1 and history[0]["status"] == "failed"


def test_concurrent_duplicate_imports_are_serialized(settings, bundle):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_csv_bundle(bundle, settings), range(2)))
    assert sorted(result.rows_inserted for result in results) == [0, 6]
    assert database_summary(settings)["version"] == 1


def test_expected_original_count_validation(settings, bundle):
    with pytest.raises(ImportValidationError, match="original records"):
        import_csv_bundle(bundle, settings, expect_original=True)


def test_missing_directory_is_logged(settings):
    with pytest.raises(ImportValidationError, match="Cannot read"):
        import_directory(settings.dataset_path / "missing", settings)
    assert import_history(settings)[0]["status"] == "failed"


@pytest.mark.integration
def test_original_dataset_and_repeat_import(settings):
    directory = get_settings().dataset_path
    if not (directory / "patients.csv").exists():
        pytest.skip("Original synthetic dataset is not available; set AEI_DATASET_PATH")
    first = import_directory(directory, settings, expect_original=True)
    assert first.rows_inserted == sum(ORIGINAL_COUNTS.values())
    assert database_summary(settings)["counts"] == ORIGINAL_COUNTS
    assert database_summary(settings)["latest_sample"] == "2026-09-27"
    second = import_directory(directory, settings, expect_original=True)
    assert second.rows_inserted == 0
    assert second.rows_unchanged == first.rows_inserted
    assert second.data_version == first.data_version == 1
    assert check_integrity(settings) == {"integrity": ["ok"], "foreign_key_errors": []}
