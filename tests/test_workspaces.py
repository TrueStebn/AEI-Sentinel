"""Token-scoped durable preferences; all fixtures use disposable local databases."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from src.database import connect, database_summary
from src.data_loader import import_csv_bundle
from src.workspaces import WorkspaceError, WorkspaceStore, token_identity, validate_state


def changed(record, **preferences):
    return {**record.state, "preferences": {**record.state["preferences"], **preferences}}


def test_initial_identity_uses_256_bit_token_and_only_hash_is_stored(settings):
    store = WorkspaceStore(settings)
    token, record = store.create()
    other, second = store.create()
    assert len(token) == len(other) == 43 and token != other
    assert token_identity(token) == record.identity and second.identity != record.identity
    assert record.revision == 0 and record.last_saved_at
    assert record.state["preferences"] == {} and record.state["replays"] == {}
    with connect(store.settings, read_only=True) as con:
        assert token not in "\n".join(con.iterdump())
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert database_summary(settings)["version"] == 0


def test_autosave_roundtrip_noop_and_application_restart(settings):
    store = WorkspaceStore(settings)
    token, record = store.create()
    state = changed(record, ward_code="URO", organism="Escherichia coli", antibiotic_code="TMP", specimen="urine",
                    analysis_date="2026-09-20", custom_period=["2026-01-01", "2026-09-20"], navigation="Detailed Analysis",
                    data_source="Laboratory Dataset", follow_latest_date=False, signal_threshold=15.0)
    saved = store.save(token, state, record.revision)
    assert saved.revision == 1 and saved.state == state
    assert store.save(token, state, saved.revision) == saved  # No UPDATE/timestamp churn.
    restored = WorkspaceStore(settings).load(token)
    assert restored == saved


@pytest.mark.parametrize("token", [None, "", "demo", "123", "A" * 42, "!" * 43, "A" * 43, "B" * 43, "../" + "a" * 40])
def test_invalid_and_unknown_tokens_never_create_or_merge_workspaces(settings, token):
    store = WorkspaceStore(settings)
    own, record = store.create()
    with pytest.raises(WorkspaceError):
        store.load(token)
    assert store.load(own) == record
    with connect(store.settings, read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 1


@pytest.mark.parametrize("preferences", [{"patient_id": "P1"}, {"navigation": "Unknown"}, {"data_source": "Other"},
    {"signal_threshold": float("nan")}, {"signal_threshold": True}, {"signal_threshold": 101},
    {"analysis_date": "yesterday"}, {"analysis_date": []}, {"specimen": "other"},
    {"custom_period": ["2026-02-01", "2026-01-01"]}, {"custom_period": "2026"}, {"follow_latest_date": 1}])
def test_invalid_or_patient_level_workspace_fields_rejected(settings, preferences):
    store = WorkspaceStore(settings)
    token, record = store.create()
    with pytest.raises((WorkspaceError, ValueError)):
        store.save(token, changed(record, **preferences), 0)
    assert store.load(token) == record


def test_independent_users_and_owner_scoped_snapshot_crud(settings):
    store = WorkspaceStore(settings)
    a, first = store.create()
    b, second = store.create()
    first = store.save(a, changed(first, ward_code="URO"), 0)
    second = store.save(b, changed(second, ward_code="ED"), 0)
    key = store.save_snapshot(a, "Urology review", first.state)
    assert store.list_snapshots(b) == []
    for action in (lambda: store.snapshot(b, key), lambda: store.rename_snapshot(b, key, "Hijack"),
                   lambda: store.delete_snapshot(b, key, confirmed=True)):
        with pytest.raises(WorkspaceError):
            action()
    row, = store.list_snapshots(a)
    assert row["title"] == "Urology review" and row["created_at"] and row["updated_at"]
    assert store.snapshot(a, key) == first.state
    store.rename_snapshot(a, key, "Renamed review")
    assert store.list_snapshots(a)[0]["title"] == "Renamed review"
    with pytest.raises(WorkspaceError, match="Confirm"):
        store.delete_snapshot(a, key)
    store.delete_snapshot(a, key, confirmed=True)
    assert not store.list_snapshots(a)
    assert store.load(b) == second and store.load(a) == first


@pytest.mark.parametrize("title", ["", "   ", "a" * 81, None])
def test_snapshot_title_validation(settings, title):
    store = WorkspaceStore(settings)
    token, record = store.create()
    with pytest.raises(WorkspaceError):
        store.save_snapshot(token, title, record.state)


def test_concurrent_independent_writes_use_short_transactions(settings):
    store = WorkspaceStore(settings)
    identities = [store.create() for _ in range(12)]
    def save(item):
        index, (token, record) = item
        return store.save(token, changed(record, ward_code=f"WARD-{index}"), 0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(save, enumerate(identities)))
    assert len({result.identity for result in results}) == 12
    assert all(result.revision == 1 for result in results)
    assert [store.load(token).state["preferences"]["ward_code"] for token, _ in identities] == [f"WARD-{i}" for i in range(12)]


def test_same_workspace_stale_writer_cannot_overwrite_newer_preferences(settings):
    store = WorkspaceStore(settings)
    token, initial = store.create()
    first = store.save(token, changed(initial, ward_code="URO"), 0)
    with pytest.raises(WorkspaceError, match="another browser"):
        store.save(token, changed(initial, ward_code="ED"), 0)
    assert store.load(token) == first


def test_interrupted_workspace_update_rolls_back(settings):
    store = WorkspaceStore(settings)
    token, initial = store.create()
    with connect(store.settings) as con:
        con.execute("CREATE TRIGGER fail_save AFTER UPDATE ON user_sessions BEGIN SELECT RAISE(ABORT,'interrupted write'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.save(token, changed(initial, ward_code="URO"), 0)
    assert WorkspaceStore(settings).load(token) == initial
    with connect(store.settings) as con:
        con.execute("DROP TRIGGER fail_save")
    assert store.save(token, changed(initial, ward_code="URO"), 0).revision == 1


def test_laboratory_import_does_not_overwrite_workspace_preferences(settings, bundle):
    store = WorkspaceStore(settings)
    token, record = store.create()
    saved = store.save(token, changed(record, ward_code="URO", analysis_date="2026-09-01"), 0)
    import_csv_bundle(bundle, settings)
    assert store.load(token) == saved
    assert database_summary(settings)["version"] == 1


def test_workspace_database_cannot_replace_primary(settings):
    before = hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
    with pytest.raises(WorkspaceError):
        WorkspaceStore(replace(settings, workspace_database_path=settings.database_path))
    assert hashlib.sha256(settings.database_path.read_bytes()).hexdigest() == before


def test_uncommitted_process_exit_does_not_replace_saved_workspace(settings):
    import os
    import subprocess
    import sys
    store = WorkspaceStore(settings)
    token, record = store.create()
    script = "import sqlite3,sys,os; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); c.execute(\"UPDATE user_sessions SET state_json='broken'\"); os._exit(23)"
    process = subprocess.run([sys.executable, "-c", script, str(store.settings.database_path)],
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                             capture_output=True, timeout=10)
    assert process.returncode == 23
    assert WorkspaceStore(settings).load(token) == record


def test_workspace_and_replay_operations_preserve_laboratory_and_six_csv_hashes(settings, bundle):
    from src.replay_files import cleanup_replays
    from src.simulator import ReplaySession, scenario_plan
    import_csv_bundle(bundle, settings)
    for name, data in bundle.items():
        (settings.dataset_path / name).write_bytes(data)
    paths = [settings.database_path, *(settings.dataset_path / name for name in bundle)]
    digest = lambda: {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    before = digest()
    store = WorkspaceStore(settings)
    token, record = store.create()
    replay = ReplaySession(settings, scenario_plan(), owner_key=record.identity)
    replay.step()
    state = {**changed(record, ward_code="URO"), "replays": {replay.plan.label: replay.descriptor()}}
    record = store.save(token, state, record.revision)
    key = store.save_snapshot(token, "Preservation check", record.state)
    replay.reset()
    record = store.save(token, {**record.state, "replays": {replay.plan.label: replay.descriptor()}}, record.revision)
    store.delete_snapshot(token, key, confirmed=True)
    cleanup_replays(store, token, confirmed=True)
    assert digest() == before
