"""Validate a complete six-file CSV bundle, then append it atomically."""

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from src.config import Settings, get_settings
from src.database import (DatabaseBusyError, connect, database_summary,
                          initialize_database, transaction)

HEADERS = {
    "wards": "ward_code ward_fi ward_en ward_type institution".split(),
    "antibiotics": "antibiotic antibiotic_code antibiotic_fi class reported_for".split(),
    "patients": "patient_id sex birth_year home_area".split(),
    "cultures": "culture_id patient_id age_years sample_date iso_year iso_week ward_code ward_fi ward_en ward_type institution admission_date specimen specimen_fi result n_isolates".split(),
    "isolates": "isolate_id culture_id isolate_no organism quantity esbl mrsa vre cpe".split(),
    "susceptibility": "isolate_id culture_id organism antibiotic antibiotic_code antibiotic_fi result guideline".split(),
}
ORDER = tuple(HEADERS)
COLUMNS = {
    "wards": HEADERS["wards"],
    "antibiotics": "antibiotic_code antibiotic antibiotic_fi antibiotic_class reported_for".split(),
    "patients": HEADERS["patients"],
    "cultures": "culture_id patient_id age_years sample_date iso_year iso_week ward_code admission_date specimen specimen_fi result n_isolates_declared".split(),
    "isolates": HEADERS["isolates"],
    "susceptibility": "isolate_id antibiotic_code result guideline".split(),
}
KEYS = {table: ("isolate_id", "antibiotic_code") if table == "susceptibility"
        else (COLUMNS[table][0],) for table in ORDER}
INTEGER_FIELDS = {"birth_year", "age_years", "iso_year", "iso_week", "n_isolates",
                  "isolate_no", "esbl", "mrsa", "vre", "cpe"}
ALLOWED = {
    ("patients", "sex"): {"F", "M"},
    ("wards", "ward_type"): {"in", "out"},
    ("wards", "institution"): {"hospital", "primary_care"},
    ("antibiotics", "reported_for"): {"all", "urine"},
    ("cultures", "specimen"): {"urine", "blood", "wound"},
    ("cultures", "result"): {"positive", "negative", "mixed_growth"},
    ("susceptibility", "result"): {"S", "R"},
}
RANGES = {"birth_year": (1, 9999), "age_years": (0, 130), "iso_year": (1, 9999),
          "iso_week": (1, 53), "n_isolates": (0, 2), "isolate_no": (1, 2),
          **{flag: (0, 1) for flag in ("esbl", "mrsa", "vre", "cpe")}}
ORIGINAL_COUNTS = {"patients": 10000, "cultures": 46792, "isolates": 24571,
                   "susceptibility": 172381, "wards": 9, "antibiotics": 13}


class ImportValidationError(ValueError):
    """The bundle is invalid; no laboratory changes have been committed."""


@dataclass(frozen=True)
class ImportResult:
    batch_id: int
    rows_inserted: int
    rows_unchanged: int
    data_version: int
    inserted_by_table: dict[str, int]


def _fail(table, line, message):
    raise ImportValidationError(f"{table}.csv, row {line}: {message}")


def _key(table, row):
    return tuple(row[col] for col in KEYS[table])


def _parse(files):
    expected = {f"{name}.csv" for name in ORDER}
    if set(files) != expected:
        raise ImportValidationError(
            f"Provide exactly six CSV files. Missing: {sorted(expected - set(files))}; "
            f"unexpected: {sorted(set(files) - expected)}")
    parsed, metadata = {}, []
    for table in ORDER:
        filename = f"{table}.csv"
        blob = files[filename]
        try:
            reader = csv.DictReader(io.StringIO(blob.decode("utf-8-sig"), newline=""), strict=True)
            if (reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames))
                    or set(reader.fieldnames) != set(HEADERS[table])):
                _fail(table, 1, f"Expected columns: {', '.join(HEADERS[table])}")
            rows = []
            for row in reader:
                line = reader.line_num
                if None in row or any(value is None for value in row.values()):
                    _fail(table, line, "Wrong number of fields")
                for col, value in row.items():
                    if value == "" and col not in {"admission_date", "quantity"}:
                        _fail(table, line, f"{col} is required")
                    if value and value != value.strip():
                        _fail(table, line, f"{col} contains surrounding whitespace")
                    if col in INTEGER_FIELDS:
                        if not re.fullmatch(r"[0-9]+", value):
                            _fail(table, line, f"{col} must be an integer")
                        row[col] = int(value)
                        low, high = RANGES[col]
                        if not low <= row[col] <= high:
                            _fail(table, line, f"{col} must be between {low} and {high}")
                    if (table, col) in ALLOWED and value not in ALLOWED[table, col]:
                        _fail(table, line, f"Unsupported {col}; expected {sorted(ALLOWED[table, col])}")
                for col in ("sample_date", "admission_date"):
                    if col in row and row[col]:
                        try:
                            if date.fromisoformat(row[col]).isoformat() != row[col]:
                                raise ValueError()
                        except ValueError:
                            _fail(table, line, f"{col} must be a valid YYYY-MM-DD date")
                row = {col: (None if col in {"admission_date", "quantity"} and val == "" else val)
                       for col, val in row.items()}
                if table == "antibiotics":
                    row["antibiotic_class"] = row["class"]
                if table == "cultures":
                    row["n_isolates_declared"] = row["n_isolates"]
                rows.append((line, row))
            parsed[table] = rows
            metadata.append((filename, hashlib.sha256(blob).hexdigest(), len(rows)))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ImportValidationError(f"{filename}: invalid UTF-8 CSV ({exc})") from exc
    return parsed, metadata


def _validate(con, parsed, expect_original):
    existing, merged, pending = {}, {}, {}
    unchanged = 0
    for table in ORDER:
        existing[table] = {_key(table, row): dict(row) for row in con.execute(f"SELECT * FROM {table}")}
        merged[table] = existing[table].copy()
        pending[table] = []
        seen_source = {}
        for line, row in parsed[table]:
            key = _key(table, row)
            if key in seen_source and row != seen_source[key]:
                _fail(table, line, "Conflicting duplicate key within the CSV")
            seen_source[key] = row
            normalized = {col: row[col] for col in COLUMNS[table]}
            prior = merged[table].get(key)
            if prior is not None:
                if prior != normalized:
                    _fail(table, line, "Existing key has different values; overwrites are not permitted")
                unchanged += 1
            else:
                pending[table].append(tuple(normalized.values()))
                merged[table][key] = normalized
        if expect_original and (len(parsed[table]) != ORIGINAL_COUNTS[table]
                                or len(seen_source) != ORIGINAL_COUNTS[table]):
            _fail(table, 1, f"Expected {ORIGINAL_COUNTS[table]:,} unique original records")

    def parent(table, key, source, line):
        row = merged[table].get((key,))
        if row is None:
            _fail(source, line, f"Unknown reference to {table}")
        return row

    for line, row in parsed["cultures"]:
        patient = parent("patients", row["patient_id"], "cultures", line)
        ward = parent("wards", row["ward_code"], "cultures", line)
        for col in ("ward_fi", "ward_en", "ward_type", "institution"):
            if row[col] != ward[col]:
                _fail("cultures", line, f"{col} does not match wards.csv")
        sample = date.fromisoformat(row["sample_date"])
        if (row["iso_year"], row["iso_week"]) != sample.isocalendar()[:2]:
            _fail("cultures", line, "ISO year/week does not match sample_date")
        if patient["birth_year"] > sample.year:
            _fail("cultures", line, "Sample predates the patient's birth year")
        admission = row["admission_date"]
        if ward["ward_type"] == "in" and admission is None:
            _fail("cultures", line, "Inpatient culture requires admission_date")
        if ward["ward_type"] == "out" and admission is not None:
            _fail("cultures", line, "Outpatient admission_date must be empty")
        if admission and admission > row["sample_date"]:
            _fail("cultures", line, "admission_date is after sample_date")
        if row["specimen_fi"] != {"urine": "virtsa", "blood": "veri", "wound": "haava"}[row["specimen"]]:
            _fail("cultures", line, "specimen_fi does not match specimen")
        if (row["result"] == "positive") != (row["n_isolates"] > 0):
            _fail("cultures", line, "Culture result conflicts with n_isolates")

    positions = set()
    counts = Counter()
    for row in merged["isolates"].values():
        if (row["culture_id"],) not in merged["cultures"]:
            raise ImportValidationError("isolates.csv: unknown culture reference")
        position = row["culture_id"], row["isolate_no"]
        if position in positions:
            raise ImportValidationError("isolates.csv: duplicate culture_id/isolate_no")
        positions.add(position)
        counts[row["culture_id"]] += 1
    for row in merged["cultures"].values():
        if counts[row["culture_id"]] != row["n_isolates_declared"]:
            raise ImportValidationError("cultures.csv: n_isolates does not match referenced isolate count")

    names = [row["antibiotic"] for row in merged["antibiotics"].values()]
    if len(names) != len(set(names)):
        raise ImportValidationError("antibiotics.csv: antibiotic names must be unique")
    for line, row in parsed["susceptibility"]:
        isolate = parent("isolates", row["isolate_id"], "susceptibility", line)
        antibiotic = parent("antibiotics", row["antibiotic_code"], "susceptibility", line)
        for col in ("culture_id", "organism"):
            if row[col] != isolate[col]:
                _fail("susceptibility", line, f"{col} does not match the referenced isolate")
        for col in ("antibiotic", "antibiotic_fi"):
            if row[col] != antibiotic[col]:
                _fail("susceptibility", line, f"{col} does not match the antibiotic catalogue")
        culture = merged["cultures"][(isolate["culture_id"],)]
        if antibiotic["reported_for"] == "urine" and culture["specimen"] != "urine":
            _fail("susceptibility", line, "Urine-only antibiotic reported for another specimen")
        if not re.fullmatch(r"EUCAST [0-9]{4}", row["guideline"]):
            _fail("susceptibility", line, "guideline must use the documented EUCAST YYYY format")
    return pending, unchanged


def _record_failure(settings, origin, message, metadata):
    with connect(settings) as con, transaction(con):
        batch = con.execute(
            "INSERT INTO import_batches(origin,status,error_message,finished_at) "
            "VALUES (?,'failed',?,CURRENT_TIMESTAMP)", (origin, message)).lastrowid
        con.executemany("INSERT INTO import_files(batch_id,filename,sha256,row_count) VALUES (?,?,?,?)",
                        [(batch, *item) for item in metadata])


def import_csv_bundle(files: dict[str, bytes], settings: Settings | None = None,
                      origin: str = "csv_upload", expect_original: bool = False) -> ImportResult:
    from src.runtime_policy import public_demo
    if public_demo():
        raise RuntimeError('Public Demo Mode is read-only. CSV imports are disabled.')
    settings = settings or get_settings()
    initialize_database(settings)
    metadata = []
    try:
        parsed, metadata = _parse(files)
        with connect(settings) as con, transaction(con):
            pending, unchanged = _validate(con, parsed, expect_original)
            batch = con.execute("INSERT INTO import_batches(origin,status) VALUES (?,'running')",
                                (origin,)).lastrowid
            for table in ORDER:
                columns = COLUMNS[table]
                con.executemany(f"INSERT INTO {table} ({','.join(columns)}) "
                                f"VALUES ({','.join('?' for _ in columns)})", pending[table])
            inserted = sum(map(len, pending.values()))
            if inserted:
                con.execute("UPDATE data_version SET version=version+1,updated_at=CURRENT_TIMESTAMP "
                            "WHERE singleton_id=1")
            version = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
            con.executemany("INSERT INTO import_files(batch_id,filename,sha256,row_count) VALUES (?,?,?,?)",
                            [(batch, *item) for item in metadata])
            con.execute("UPDATE import_batches SET status='completed',rows_inserted=?,rows_unchanged=?,"
                        "finished_at=CURRENT_TIMESTAMP WHERE batch_id=?", (inserted, unchanged, batch))
            result = ImportResult(batch, inserted, unchanged, version,
                                  {table: len(rows) for table, rows in pending.items()})
        return result
    except (ImportValidationError, sqlite3.Error, OSError, DatabaseBusyError) as exc:
        message = str(exc)
        try:
            _record_failure(settings, origin, message, metadata)
        except (sqlite3.Error, OSError, DatabaseBusyError):
            message += " Failed-attempt history could not be saved; retry after database access is restored."
        raise ImportValidationError(message) from exc


def import_directory(directory: Path, settings: Settings | None = None,
                     expect_original: bool = False) -> ImportResult:
    from src.runtime_policy import public_demo
    if public_demo():
        raise RuntimeError('Public Demo Mode is read-only. CSV imports are disabled.')
    settings = settings or get_settings()
    try:
        files = {f"{table}.csv": (Path(directory) / f"{table}.csv").read_bytes() for table in ORDER}
    except OSError as exc:
        initialize_database(settings)
        message = f"Cannot read the six CSV files from the selected directory: {exc}"
        _record_failure(settings, "initial_directory", message, [])
        raise ImportValidationError(message) from exc
    return import_csv_bundle(files, settings, origin="initial_directory", expect_original=expect_original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=get_settings().dataset_path)
    parser.add_argument("--expect-original", action="store_true")
    args = parser.parse_args()
    try:
        result = import_directory(args.directory, expect_original=args.expect_original)
    except (ImportValidationError, DatabaseBusyError, OSError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(1, f"Import failed: {exc}\n")
    print(json.dumps({"import": asdict(result), "database": database_summary()}, indent=2))


if __name__ == "__main__":
    main()
