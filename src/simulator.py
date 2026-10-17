"""Session-owned replay databases, feeding the normal atomic CSV importer."""

import csv
import io
import json
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from src.config import PROJECT_ROOT, Settings
from src.database import connect, initialize_database, transaction
from src.data_loader import HEADERS, import_csv_bundle
from src.replay_files import ReplayLease, replay_path
from src.workspaces import WorkspaceError, validate_state

SCENARIO = "Simulated Surveillance Scenario"
HISTORICAL = "Historical Replay"


def csv_bundle(rows):
    """Header-only tables are valid: references may already exist in the target."""
    files = {}
    for table, headers in HEADERS.items():
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows.get(table, []))
        files[f"{table}.csv"] = stream.getvalue().encode("utf-8")
    return files


@dataclass(frozen=True)
class ReplayPlan:
    label: str
    periods: tuple[tuple[date, date], ...]
    source: Settings | None = None
    source_version: int | None = None
    bundles: tuple[dict[str, bytes], ...] = ()

    def bundle(self, index):
        if self.source is None:
            return self.bundles[index]
        start, end = self.periods[index]
        with connect(self.source, read_only=True) as con:
            con.execute("BEGIN")
            revision = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
            if revision != self.source_version:
                raise ValueError("Historical source changed. Reset Replay to load a new source snapshot.")
            # Only this week's cultures and their dependencies; never re-import the full source.
            params = (start.isoformat(), end.isoformat())
            scope = "c.sample_date BETWEEN ? AND ?"
            queries = {
                "cultures": f"""SELECT c.*,w.ward_fi,w.ward_en,w.ward_type,w.institution,
                    c.n_isolates_declared AS n_isolates FROM cultures c JOIN wards w USING(ward_code) WHERE {scope}""",
                "wards": f"SELECT DISTINCT w.* FROM wards w JOIN cultures c USING(ward_code) WHERE {scope}",
                "patients": f"SELECT DISTINCT p.* FROM patients p JOIN cultures c USING(patient_id) WHERE {scope}",
                "isolates": f"SELECT i.* FROM isolates i JOIN cultures c USING(culture_id) WHERE {scope}",
                "susceptibility": f"""SELECT s.*,i.culture_id,i.organism,a.antibiotic,a.antibiotic_fi
                    FROM susceptibility s JOIN isolates i USING(isolate_id) JOIN cultures c USING(culture_id)
                    JOIN antibiotics a USING(antibiotic_code) WHERE {scope}""",
                "antibiotics": f"""SELECT DISTINCT a.*,a.antibiotic_class AS class FROM antibiotics a
                    JOIN susceptibility s USING(antibiotic_code) JOIN isolates i USING(isolate_id)
                    JOIN cultures c USING(culture_id) WHERE {scope}""",
            }
            rows = {table: [{key: row[key] if row[key] is not None else "" for key in HEADERS[table]}
                            for row in con.execute(query, params)] for table, query in queries.items()}
        return csv_bundle(rows)


def historical_plan(source: Settings) -> ReplayPlan:
    with connect(source, read_only=True) as con:
        con.execute("BEGIN")
        version = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
        dates = [date.fromisoformat(row[0]) for row in con.execute("SELECT DISTINCT sample_date FROM cultures ORDER BY sample_date")]
    weeks = sorted({day - timedelta(days=day.weekday()) for day in dates})
    return ReplayPlan(HISTORICAL, tuple((week, week + timedelta(days=6)) for week in weeks), source, version)


def scenario_plan() -> ReplayPlan:
    """Openly artificial: baseline 10/100 R, then 4/40 R, then 36/60 new R."""
    periods, bundles = [], []
    ward = dict(zip(HEADERS["wards"], ["SIM", SCENARIO, SCENARIO, "out", "hospital"]))
    antibiotic = dict(zip(HEADERS["antibiotics"], ["ciprofloxacin", "CIP", "siprofloksasiini", "fluoroquinolones", "all"]))
    for batch, (day, count, resistant) in enumerate([(date(2026, 8, 1), 100, 10),
                                                    (date(2026, 9, 1), 40, 4), (date(2026, 9, 2), 60, 36)]):
        rows = {"wards": [ward], "antibiotics": [antibiotic], "patients": [], "cultures": [], "isolates": [], "susceptibility": []}
        for number in range(count):
            identifier = f"SIM-{batch}-{number:03}"
            patient, culture, isolate = (f"{prefix}-{identifier}" for prefix in ("P", "C", "I"))
            rows["patients"].append(dict(zip(HEADERS["patients"], [patient, "F", 1980, SCENARIO])))
            rows["cultures"].append(dict(zip(HEADERS["cultures"], [culture, patient, 46, day.isoformat(), day.isocalendar().year,
                day.isocalendar().week, "SIM", SCENARIO, SCENARIO, "out", "hospital", "", "urine", "virtsa", "positive", 1])))
            rows["isolates"].append(dict(zip(HEADERS["isolates"], [isolate, culture, 1, "Escherichia coli", "", 0, 0, 0, 0])))
            rows["susceptibility"].append(dict(zip(HEADERS["susceptibility"], [isolate, culture, "Escherichia coli",
                "ciprofloxacin", "CIP", "siprofloksasiini", "R" if number < resistant else "S", "EUCAST 2026"])))
        bundles.append(csv_bundle(rows))
        periods.append((day, day))
    return ReplayPlan(SCENARIO, tuple(periods), bundles=tuple(bundles))


class ReplaySession:
    """One controller per Streamlit session; settings are never changed globally.

    Cursor advances only after commit. Retrying a committed packet is idempotent.
    Reset rotates the owned database rather than deleting files or source records.
    """

    def __init__(self, primary: Settings, plan: ReplayPlan, *, owner_key=None):
        from src.runtime_policy import public_demo
        if public_demo():
            raise RuntimeError('Writable Replay is disabled in Public Demo Mode. Use predefined scenario snapshots.')
        self.primary = primary
        self.plan = plan
        self.directory = primary.database_path.parent / "replay" / uuid4().hex
        self.cursor = 0
        self.running = False
        self.interval = 2.0
        self.next_due = 0.0
        self.last_result = None
        self.owner_key = owner_key
        self.lease = None
        self.recovery_notice = None
        self._new_database()

    def _new_database(self):
        from src.runtime_policy import public_demo
        if public_demo():
            raise RuntimeError('Writable Replay is disabled in Public Demo Mode.')
        target = self.directory / f"{uuid4().hex}.db"
        protected = [self.primary.database_path, PROJECT_ROOT / "data/runtime/aei.db"]
        if self.plan.source:
            protected.append(self.plan.source.database_path)
        if any(target.resolve() == path.resolve() or (target.exists() and path.exists() and target.samefile(path)) for path in protected):
            raise ValueError("Replay must never use a source or working database.")
        candidate = replace(self.primary, database_path=target)
        lease = ReplayLease(target)
        try:
            initialize_database(candidate)
            if self.owner_key:
                with connect(candidate) as con, transaction(con):
                    con.execute("INSERT INTO user_sessions(session_id,user_key,state_json) VALUES ('replay_owner',?,?)",
                                (self.owner_key, json.dumps({"mode": self.plan.label})))
        except BaseException:
            lease.close()
            raise
        if self.lease:
            self.lease.close()
        self.settings, self.lease = candidate, lease

    def descriptor(self):
        with connect(self.settings, read_only=True) as con:
            version = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
        return {"mode": self.plan.label, "path": self.settings.database_path.relative_to(self.primary.database_path.parent / "replay").as_posix(),
                "cursor": self.cursor, "interval": self.interval, "source_version": self.plan.source_version,
                "expected_version": version, "last_committed_batch": self.cursor, "complete": self.complete,
                "was_running": self.running, "periods": [[start.isoformat(), end.isoformat()] for start, end in self.plan.periods]}

    @classmethod
    def recover(cls, primary, saved, owner_key):
        from src.runtime_policy import public_demo
        if public_demo():
            raise RuntimeError('Replay recovery is disabled in Public Demo Mode.')
        validate_state({"format": 1, "replays": {saved["mode"]: saved}})
        target = replay_path(primary, saved["path"])
        if not target.is_file():
            raise WorkspaceError("Saved Replay database is missing. Use Reset Replay to create an empty demonstration.")
        candidate = replace(primary, database_path=target)
        lease = ReplayLease(target)
        try:
            with connect(candidate, read_only=True) as con:
                con.execute("BEGIN")
                if con.execute("PRAGMA user_version").fetchone()[0] != 1 or [row[0] for row in con.execute("PRAGMA integrity_check")] != ["ok"] or con.execute("PRAGMA foreign_key_check").fetchall():
                    raise WorkspaceError("Saved Replay database failed integrity checks. Use Reset Replay.")
                owner = con.execute("SELECT user_key,state_json FROM user_sessions WHERE session_id='replay_owner'").fetchone()
                if owner is None or owner[0] != owner_key or json.loads(owner[1]).get("mode") != saved["mode"]:
                    raise WorkspaceError("Saved Replay database does not belong to this workspace and mode.")
                version = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
                origins = [row[0] for row in con.execute("SELECT origin FROM import_batches WHERE status='completed'")]
            prefix = saved["mode"] + ":batch-"
            if any(not origin.startswith(prefix) or not origin[len(prefix):].isdigit() for origin in origins):
                raise WorkspaceError("Saved Replay contains unrecognized imports. Use Reset Replay.")
            committed = {int(origin[len(prefix):]) for origin in origins}
            if committed != set(range(1, version + 1)) or version < saved["expected_version"] or version > len(saved["periods"]):
                raise WorkspaceError("Saved Replay version or commit history is inconsistent. Use Reset Replay.")
            periods = tuple((date.fromisoformat(start), date.fromisoformat(end)) for start, end in saved["periods"])
            plan = scenario_plan() if saved["mode"] == SCENARIO else ReplayPlan(HISTORICAL, periods, primary, saved["source_version"])
            if plan.periods != periods:
                raise WorkspaceError("The saved scenario plan is incompatible. Use Reset Replay.")
            replay = cls.__new__(cls)
            replay.primary, replay.plan, replay.settings = primary, plan, candidate
            replay.directory, replay.owner_key, replay.lease = target.parent, owner_key, lease
            replay.cursor, replay.running, replay.interval = version, False, float(saved["interval"])
            replay.next_due, replay.last_result = 0.0, None
            replay.recovery_notice = ("Recovered committed Replay batches after an interrupted workspace save; playback is paused."
                                      if version > saved["expected_version"] else "Replay restored in a paused state.")
            return replay
        except BaseException:
            lease.close()
            raise

    @property
    def complete(self):
        return self.cursor >= len(self.plan.periods)

    def set_speed(self, interval):
        if isinstance(interval, bool) or interval not in (1, 2, 5, 10):
            raise ValueError("Playback interval must be 1, 2, 5 or 10 seconds")
        if self.interval != interval:
            self.interval = float(interval)
            self.next_due = time.monotonic() + self.interval

    def start(self):
        self.running = not self.complete
        self.next_due = time.monotonic() + self.interval

    def pause(self):
        self.running = False

    def step(self):
        if self.complete:
            self.pause()
            return None
        try:
            result = import_csv_bundle(self.plan.bundle(self.cursor), self.settings,
                                       origin=f"{self.plan.label}:batch-{self.cursor + 1}")
        except Exception:
            self.pause()
            raise
        self.cursor += 1
        self.last_result = result
        self.next_due = time.monotonic() + self.interval
        if self.complete:
            self.pause()
        return result

    def tick(self, now=None):
        if self.running and (time.monotonic() if now is None else now) >= self.next_due:
            return self.step()
        return None

    def reset(self):
        self.pause()
        # Rebuild the historical date inventory if the shared source has changed.
        if self.plan.source:
            self.plan = historical_plan(self.plan.source)
        self._new_database()
        self.cursor = 0
        self.last_result = None
