"""Short-lived SQLite connections and explicit atomic transactions."""

import sqlite3
from contextlib import contextmanager
from src.config import Settings, get_settings

SOURCE_TABLES = ("patients", "cultures", "isolates", "susceptibility", "wards", "antibiotics")


class DatabaseBusyError(RuntimeError):
    """A writer could not acquire the SQLite lock within the configured timeout."""


@contextmanager
def connect(settings: Settings | None = None, *, read_only: bool = False):
    """Open a connection; read-only mode never creates a missing database."""
    settings = settings or get_settings()
    if read_only:
        database = settings.database_path.resolve().as_uri() + "?mode=ro"
    else:
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        database = settings.database_path
    con = sqlite3.connect(database, timeout=settings.busy_timeout_ms / 1000,
                          isolation_level=None, uri=read_only)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(f"PRAGMA busy_timeout={int(settings.busy_timeout_ms)}")
        con.execute("PRAGMA synchronous=FULL")
        if read_only:
            con.execute("PRAGMA query_only=ON")
        yield con
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise DatabaseBusyError("Database is busy. Wait a moment and retry the import.") from exc
        raise
    finally:
        con.close()


@contextmanager
def transaction(con):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise


def initialize_database(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    with connect(settings) as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError(f"Unsupported database schema version: {version}")
        con.execute("PRAGMA journal_mode=WAL")
        if version == 0:
            try:
                con.executescript(settings.schema_path.read_text(encoding="utf-8"))
            except BaseException:
                con.rollback()
                raise


def database_summary(settings: Settings | None = None) -> dict:
    with connect(settings) as con:
        con.execute("BEGIN")
        return {
            "counts": {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                       for table in SOURCE_TABLES},
            "version": con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0],
            "latest_sample": con.execute("SELECT MAX(sample_date) FROM cultures").fetchone()[0],
            "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0],
        }


def import_history(settings: Settings | None = None) -> list[dict]:
    with connect(settings) as con:
        return [dict(row) for row in con.execute(
            "SELECT * FROM import_batches ORDER BY batch_id DESC LIMIT 30")]


def check_integrity(settings: Settings | None = None) -> dict:
    with connect(settings) as con:
        con.execute("BEGIN")
        return {"integrity": [row[0] for row in con.execute("PRAGMA integrity_check")],
                "foreign_key_errors": [tuple(row) for row in con.execute("PRAGMA foreign_key_check")]}
