import sqlite3
import pytest

from src.config import Settings
from src.database import (DatabaseBusyError, check_integrity, connect,
                          database_summary, initialize_database, transaction)


def test_initialization_and_connections(settings):
    initialize_database(settings)
    initialize_database(settings)
    with connect(settings) as con:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert database_summary(settings)["journal_mode"] == "wal"
    assert check_integrity(settings) == {"integrity": ["ok"], "foreign_key_errors": []}


def test_transaction_rollback_and_fk_enforcement(settings):
    with pytest.raises(sqlite3.IntegrityError), connect(settings) as con, transaction(con):
        con.execute("INSERT INTO patients VALUES ('P1','F',1980,'Centre')")
        con.execute("INSERT INTO patients VALUES ('P2','invalid',1980,'Centre')")
    assert database_summary(settings)["counts"]["patients"] == 0
    with pytest.raises(sqlite3.IntegrityError), connect(settings) as con:
        con.execute("INSERT INTO susceptibility VALUES ('missing','missing','R','EUCAST 2026')")


def test_writer_contention_is_reported_without_partial_writes(settings):
    short = Settings(settings.database_path, settings.dataset_path, busy_timeout_ms=20)
    with connect(settings) as writer, transaction(writer):
        writer.execute("INSERT INTO patients VALUES ('P1','F',1980,'Centre')")
        with pytest.raises(DatabaseBusyError), connect(short) as other, transaction(other):
            other.execute("INSERT INTO patients VALUES ('P2','F',1980,'Centre')")
    assert database_summary(settings)["counts"]["patients"] == 1


def test_wal_reader_sees_only_committed_data(settings):
    with connect(settings) as writer, transaction(writer):
        writer.execute("INSERT INTO patients VALUES ('P1','F',1980,'Centre')")
        assert database_summary(settings)["counts"]["patients"] == 0
    assert database_summary(settings)["counts"]["patients"] == 1


def test_reject_newer_schema(settings):
    with connect(settings) as con:
        con.execute("PRAGMA user_version=2")
    with pytest.raises(RuntimeError, match="Unsupported"):
        initialize_database(settings)
