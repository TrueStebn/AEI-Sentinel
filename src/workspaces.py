"""Token-scoped workspace persistence using the existing schema in a separate DB."""

import base64
import hashlib
import json
import math
import re
import secrets
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from uuid import uuid4

from src.config import PROJECT_ROOT
from src.database import connect, initialize_database, transaction

MODES = ("Laboratory Dataset", "Historical Replay", "Simulated Surveillance Scenario")
PAGES = ("Overview", "Explore", "Reports", "Data & Settings")
LEGACY_PAGES = ("Dashboard", "Detailed Analysis", "Data Management", "Advanced Anomaly Explorer", "Surveillance Map", "Surveillance Briefing")
EXPLORE_VIEWS = ("Surveillance Map", "Resistance Trends", "Detailed Period", "Investigation")
REPORT_VIEWS = ("Surveillance Briefing", "Current Investigation")
EXPLORER_CHOICES = {
    "explorer_frequency": ("Monthly", "Weekly"),
    "explorer_mode": ("antibiotic", "phenotype"), "explorer_marker": ("ESBL", "MRSA", "VRE", "CPE"),
    "explorer_sex": (None, "F", "M"), "explorer_age_group": ("all", "0–17", "18–44", "45–64", "65+", "custom"),
    "explorer_ward_type": (None, "in", "out"), "explorer_institution": (None, "hospital", "primary_care"),
    "explorer_admission_timing": (None, "all_applicable", "days_1_2", "day_3_plus"),
    "explorer_group_dimension": ("sex", "age", "ward", "ward_type", "institution", "home_area", "admission_timing"),
    "explorer_group_period": ("current", "baseline", "annual"),
    "explorer_section": ("overview", "patient_aware"),
}
EXPLORER_FILTERS = (*EXPLORER_CHOICES, "explorer_age_min", "explorer_age_max", "explorer_home_area", "explorer_origin")
MAP_CHOICES = {"map_view": ("level", "change"), "map_period": ("current", "annual"), "map_sort": ("name", "volume"),
    "map_sex": (None, "F", "M"), "map_age_group": EXPLORER_CHOICES["explorer_age_group"],
    "map_ward_type": (None, "in", "out"), "map_institution": (None, "hospital", "primary_care"),
    "map_scale": ("full", "focused"), "map_columns": ("show", "hide_empty")}
MAP_FILTERS = (*MAP_CHOICES, "map_age_min", "map_age_max", "map_antibiotic_class", "map_selected_ward", "map_selected_antibiotic")
BRIEFING_CHOICES = {"briefing_sort": ("Review Priority", "Absolute change", "Current sample size", "Ward", "Latest observation"),
    "briefing_status": ("All", "Preliminary", "Statistically supported"),
    "briefing_report_scope": ("Hospital-wide summary", "Selected investigation")}
BRIEFING_FILTERS = (*BRIEFING_CHOICES, "briefing_ward", "briefing_organism", "briefing_specimen", "briefing_antibiotic")
PREFERENCE_CHOICES = {**EXPLORER_CHOICES, **MAP_CHOICES, **BRIEFING_CHOICES,
    "presentation_mode": ("Guided", "Expert"), "explore_view": EXPLORE_VIEWS, "reports_view": REPORT_VIEWS}
FILTERS = ("ward_code", "organism", "antibiotic_code", "specimen", "analysis_date", "follow_latest_date", "custom_period", *EXPLORER_FILTERS, *MAP_FILTERS, *BRIEFING_FILTERS)
PREFERENCES = (*FILTERS, "navigation", "data_source", "signal_threshold", "presentation_mode", "explore_view", "reports_view")


class WorkspaceError(ValueError):
    """Invalid state, missing identity or optimistic write conflict."""


def token_identity(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise WorkspaceError("Invalid or unknown restore token.")
    decoded = base64.urlsafe_b64decode(token + "=")
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != token:
        raise WorkspaceError("Invalid or unknown restore token.")
    return hashlib.sha256(token.encode()).hexdigest()


def _preferences(values, allowed):
    if not isinstance(values, dict) or set(values) - set(allowed):
        raise WorkspaceError("Unsupported workspace preferences.")
    for name, value in values.items():
        if name in PREFERENCE_CHOICES:
            if value not in PREFERENCE_CHOICES[name]:
                raise WorkspaceError(f"Invalid saved {name}.")
        elif name in ("explorer_age_min", "explorer_age_max", "map_age_min", "map_age_max"):
            if type(value) is not int or not 0 <= value <= 130:
                raise WorkspaceError("Invalid saved age bound.")
        elif name == "explorer_origin":
            if value is not None:
                keys = {"kind", "ward_code", "organism", "antibiotic_code", "specimen", "analysis_date", "data_version", "source", "database_fingerprint"}
                if not isinstance(value, dict) or set(value) != keys or value["kind"] not in ("preliminary", "BY-alert"):
                    raise WorkspaceError("Invalid saved investigation origin.")
                _preferences({k: value[k] for k in ("ward_code", "organism", "antibiotic_code", "specimen", "analysis_date")}, FILTERS)
                if (value["source"] not in MODES or type(value["data_version"]) is not int or value["data_version"] < 0
                        or not isinstance(value["database_fingerprint"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["database_fingerprint"])):
                    raise WorkspaceError("Invalid saved investigation source.")
        elif name in ("ward_code", "organism", "antibiotic_code", "specimen", "explorer_home_area", "map_antibiotic_class", "map_selected_ward", "map_selected_antibiotic", "briefing_ward", "briefing_organism", "briefing_specimen", "briefing_antibiotic"):
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 200):
                raise WorkspaceError(f"Invalid saved {name}.")
            if name in ("specimen", "briefing_specimen") and value not in (None, "urine", "blood", "wound"):
                raise WorkspaceError("Invalid saved specimen.")
        elif name == "analysis_date":
            if value is not None:
                date.fromisoformat(value)
        elif name == "custom_period":
            if not isinstance(value, (list, tuple)) or len(value) > 2:
                raise WorkspaceError("Invalid saved reporting period.")
            days = [date.fromisoformat(day) for day in value]
            if len(days) == 2 and days[0] > days[1]:
                raise WorkspaceError("Invalid saved reporting period order.")
        elif name == "follow_latest_date" and type(value) is not bool:
            raise WorkspaceError("Invalid saved date preference.")
        elif name == "navigation" and value not in (*PAGES, *LEGACY_PAGES):
            raise WorkspaceError("Invalid saved page.")
        elif name == "data_source" and value not in MODES:
            raise WorkspaceError("Invalid saved data source.")
        elif name == "signal_threshold" and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100):
            raise WorkspaceError("Invalid saved signal threshold.")
    return values


def _validate_state(state):
    """Allow only aggregate UI preferences and minimal replay descriptors; never records."""
    if not isinstance(state, dict) or set(state) - {"format", "preferences", "source_filters", "replays", "laboratory_version"} or state.get("format") != 1:
        raise WorkspaceError("Unsupported workspace format.")
    _preferences(state.get("preferences", {}), PREFERENCES)
    groups = state.get("source_filters", {})
    replays = state.get("replays", {})
    if not isinstance(groups, dict) or set(groups) - set(MODES) or not isinstance(replays, dict) or set(replays) - set(MODES[1:]):
        raise WorkspaceError("Invalid saved data source configuration.")
    for values in groups.values():
        _preferences(values, FILTERS)
    for mode, descriptor in replays.items():
        if not isinstance(descriptor, dict) or set(descriptor) != {"mode", "path", "cursor", "interval", "source_version", "expected_version", "last_committed_batch", "complete", "was_running", "periods"}:
            raise WorkspaceError("Invalid saved Replay descriptor.")
        if descriptor["mode"] != mode or not isinstance(descriptor["path"], str) or not re.fullmatch(r"[0-9a-f]{32}/[0-9a-f]{32}\.db", descriptor["path"]):
            raise WorkspaceError("Invalid saved Replay location.")
        for key in ("cursor", "expected_version", "last_committed_batch"):
            if type(descriptor[key]) is not int or descriptor[key] < 0:
                raise WorkspaceError("Invalid saved Replay progress.")
        if descriptor["cursor"] != descriptor["last_committed_batch"] or descriptor["expected_version"] != descriptor["cursor"]:
            raise WorkspaceError("Inconsistent saved Replay commit.")
        if descriptor["source_version"] is not None and (type(descriptor["source_version"]) is not int or descriptor["source_version"] < 0):
            raise WorkspaceError("Invalid historical source version.")
        if type(descriptor["complete"]) is not bool or type(descriptor["was_running"]) is not bool or type(descriptor["interval"]) not in (int, float) or descriptor["interval"] not in (1, 2, 5, 10):
            raise WorkspaceError("Invalid saved Replay controls.")
        periods = descriptor["periods"]
        if not isinstance(periods, list) or len(periods) > 10000:
            raise WorkspaceError("Invalid saved Replay periods.")
        previous = None
        for period in periods:
            if not isinstance(period, list) or len(period) != 2:
                raise WorkspaceError("Invalid saved Replay period.")
            start, end = map(date.fromisoformat, period)
            if start > end or (previous and start <= previous):
                raise WorkspaceError("Invalid saved Replay chronology.")
            previous = end
        if descriptor["cursor"] > len(periods) or descriptor["complete"] != (descriptor["cursor"] == len(periods)):
            raise WorkspaceError("Invalid saved Replay completion state.")
    if type(state.get("laboratory_version", 0)) is not int or state.get("laboratory_version", 0) < 0:
        raise WorkspaceError("Invalid saved dataset version.")
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > 512_000:
        raise WorkspaceError("Workspace is too large.")
    return json.loads(encoded)


def validate_state(state):
    try:
        return _validate_state(state)
    except (TypeError, KeyError, OverflowError) as exc:
        raise WorkspaceError("Invalid stored workspace values.") from exc


@dataclass(frozen=True)
class Workspace:
    identity: str
    revision: int
    state: dict
    last_saved_at: str


class WorkspaceStore:
    def __init__(self, primary):
        from src.runtime_policy import public_demo
        if public_demo():
            raise RuntimeError('Persistent workspaces are disabled in Public Demo Mode.')
        self.primary = primary
        path = (primary.workspace_database_path or primary.database_path.parent / "workspaces.db").resolve()
        if path in (primary.database_path.resolve(), (PROJECT_ROOT / "data/runtime/aei.db").resolve()) or (path.exists() and primary.database_path.exists() and path.samefile(primary.database_path)):
            raise WorkspaceError("Workspace storage must be separate from the laboratory database.")
        self.settings = replace(primary, database_path=path)
        initialize_database(self.settings)

    def create(self):
        token = secrets.token_urlsafe(32)
        identity = token_identity(token)
        state = {"format": 1, "preferences": {}, "source_filters": {}, "replays": {}, "laboratory_version": 0}
        with connect(self.settings) as con, transaction(con):
            con.execute("INSERT INTO user_sessions(session_id,user_key,state_json) VALUES (?,?,?)",
                        (identity, identity, json.dumps({"revision": 0, "state": state}, sort_keys=True)))
        return token, self.load(token)

    def load(self, token):
        identity = token_identity(token)
        with connect(self.settings, read_only=True) as con:
            row = con.execute("SELECT * FROM user_sessions WHERE session_id=? AND user_key=?", (identity, identity)).fetchone()
        if row is None:
            raise WorkspaceError("Invalid or unknown restore token.")
        payload = json.loads(row["state_json"])
        if not isinstance(payload, dict) or set(payload) != {"revision", "state"} or type(payload["revision"]) is not int or payload["revision"] < 0:
            raise WorkspaceError("Invalid saved workspace revision.")
        return Workspace(identity, payload["revision"], validate_state(payload["state"]), row["last_saved_at"])

    def save(self, token, state, expected_revision):
        state = validate_state(state)
        identity = token_identity(token)
        with connect(self.settings) as con, transaction(con):
            row = con.execute("SELECT state_json,last_saved_at FROM user_sessions WHERE session_id=? AND user_key=?", (identity, identity)).fetchone()
            if row is None:
                raise WorkspaceError("Invalid or unknown restore token.")
            prior = json.loads(row["state_json"])
            if prior["state"] == state:
                return Workspace(identity, prior["revision"], state, row["last_saved_at"])
            if prior["revision"] != expected_revision:
                raise WorkspaceError("Workspace changed in another browser. Restore the latest workspace before editing again.")
            stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            revision = expected_revision + 1
            con.execute("UPDATE user_sessions SET page_name=?,state_json=?,last_saved_at=? WHERE session_id=? AND user_key=?",
                (state["preferences"].get("navigation", "Dashboard"), json.dumps({"revision": revision, "state": state}, sort_keys=True), stamp, identity, identity))
        return Workspace(identity, revision, state, stamp)

    def list_snapshots(self, token):
        identity = self.load(token).identity
        with connect(self.settings, read_only=True) as con:
            return [dict(row) for row in con.execute("SELECT workspace_id,title,created_at,updated_at FROM saved_workspaces WHERE owner_key=? ORDER BY updated_at DESC,workspace_id", (identity,))]

    def save_snapshot(self, token, title, state):
        owner = self.load(token).identity
        state = validate_state(state)
        title = self._title(title)
        key = uuid4().hex
        with connect(self.settings) as con, transaction(con):
            con.execute("INSERT INTO saved_workspaces(workspace_id,owner_key,session_id,title,state_json) VALUES (?,?,?,?,?)",
                        (key, owner, owner, title, json.dumps(state, sort_keys=True)))
        return key

    @staticmethod
    def _title(title):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
            raise WorkspaceError("Workspace title must contain 1–80 characters.")
        return title.strip()

    def snapshot(self, token, key):
        owner = self.load(token).identity
        with connect(self.settings, read_only=True) as con:
            row = con.execute("SELECT state_json FROM saved_workspaces WHERE workspace_id=? AND owner_key=?", (key, owner)).fetchone()
        if row is None:
            raise WorkspaceError("Saved workspace is unavailable for this identity.")
        return validate_state(json.loads(row[0]))

    def rename_snapshot(self, token, key, title):
        owner = self.load(token).identity
        with connect(self.settings) as con, transaction(con):
            result = con.execute("UPDATE saved_workspaces SET title=?,updated_at=? WHERE workspace_id=? AND owner_key=?",
                (self._title(title), datetime.now(timezone.utc).isoformat(timespec="milliseconds"), key, owner))
            if result.rowcount != 1:
                raise WorkspaceError("Saved workspace is unavailable for this identity.")

    def delete_snapshot(self, token, key, *, confirmed=False):
        if confirmed is not True:
            raise WorkspaceError("Confirm deletion of this saved workspace.")
        owner = self.load(token).identity
        with connect(self.settings) as con, transaction(con):
            result = con.execute("DELETE FROM saved_workspaces WHERE workspace_id=? AND owner_key=?", (key, owner))
            if result.rowcount != 1:
                raise WorkspaceError("Saved workspace is unavailable for this identity.")

    def referenced_replays(self, con=None):
        if con is None:
            with connect(self.settings, read_only=True) as reader:
                return self.referenced_replays(reader)
        references = set()
        for query in ("SELECT state_json FROM user_sessions", "SELECT state_json FROM saved_workspaces"):
            for row in con.execute(query):
                payload = json.loads(row[0])
                state = validate_state(payload.get("state", payload))
                references.update(descriptor["path"] for descriptor in state.get("replays", {}).values())
        return references
