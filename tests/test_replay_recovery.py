"""Recovery validates ownership, confined paths, integrity and commit history."""

import hashlib
import os
from dataclasses import replace

import pytest

from src.database import connect, database_summary
from src.replay_files import cleanup_replays, replay_path
from src.simulator import HISTORICAL, SCENARIO, ReplaySession, historical_plan, scenario_plan
from src.workspaces import WorkspaceError, WorkspaceStore


def owned(settings):
    store = WorkspaceStore(settings)
    token, record = store.create()
    replay = ReplaySession(settings, scenario_plan(), owner_key=record.identity)
    return store, token, record, replay


def state_with(record, replay):
    return {**record.state, "replays": {replay.plan.label: replay.descriptor()}}


def test_replay_restores_paused_and_continues_without_duplicates(settings):
    store, token, record, replay = owned(settings)
    replay.step()
    replay.step()
    replay.set_speed(5)
    replay.start()
    record = store.save(token, state_with(record, replay), 0)
    saved = WorkspaceStore(settings).load(token)
    restored = ReplaySession.recover(settings, saved.state["replays"][SCENARIO], saved.identity)
    assert restored.cursor == 2 and restored.interval == 5 and not restored.running
    assert restored.settings.database_path == replay.settings.database_path
    assert restored.step().data_version == 3 and restored.complete
    assert database_summary(restored.settings)["counts"]["patients"] == 200


def test_recovery_reconciles_commit_before_workspace_autosave(settings):
    store, token, record, replay = owned(settings)
    replay.step()
    record = store.save(token, state_with(record, replay), 0)
    replay.step()  # Simulated interruption before the following workspace commit.
    restored = ReplaySession.recover(settings, record.state["replays"][SCENARIO], record.identity)
    assert restored.cursor == 2 and not restored.running
    assert "interrupted" in restored.recovery_notice
    assert restored.step().data_version == 3


@pytest.mark.parametrize("problem", ["missing", "corrupt", "wrong_owner", "unexpected_version", "unrecognized_import", "fk_violation"])
def test_missing_or_invalid_replay_cannot_be_silently_restored(settings, problem):
    store, token, record, replay = owned(settings)
    replay.step()
    saved = replay.descriptor()
    replay.lease.close()
    if problem == "missing":
        replay.settings.database_path.unlink()
    elif problem == "corrupt":
        replay.settings.database_path.write_bytes(b"not a sqlite database")
    else:
        with connect(replay.settings) as con:
            if problem == "wrong_owner":
                con.execute("UPDATE user_sessions SET user_key='another-user'")
            elif problem == "unexpected_version":
                con.execute("UPDATE data_version SET version=99")
            elif problem == "unrecognized_import":
                con.execute("UPDATE import_batches SET origin='unrecognized'")
            else:
                con.execute("PRAGMA foreign_keys=OFF")
                con.execute("UPDATE cultures SET patient_id='missing-parent'")
    with pytest.raises(Exception):
        ReplaySession.recover(settings, saved, record.identity)
    assert database_summary(settings)["version"] == 0


@pytest.mark.parametrize("location", ["../aei.db", "/tmp/aei.db", "C:/outside.db", "a/b.db", "a" * 32 + "/../aei.db"])
def test_recovery_rejects_unconfined_paths(settings, location):
    with pytest.raises(WorkspaceError):
        replay_path(settings, location)


def test_foreign_workspace_replay_file_cannot_be_opened(settings):
    store, token, owner, replay = owned(settings)
    other_token, other = store.create()
    with pytest.raises(WorkspaceError, match="belong"):
        ReplaySession.recover(settings, replay.descriptor(), other.identity)


def test_historical_source_revision_change_preserves_paused_replay(settings, bundle):
    from src.data_loader import import_csv_bundle
    import_csv_bundle(bundle, settings)
    store = WorkspaceStore(settings)
    token, record = store.create()
    replay = ReplaySession(settings, historical_plan(settings), owner_key=record.identity)
    replay.step()
    saved = replay.descriptor()
    with connect(settings) as con:
        con.execute("UPDATE data_version SET version=2")
    restored = ReplaySession.recover(settings, saved, record.identity)
    assert restored.plan.source_version == 1 and not restored.running
    restored.reset()
    assert restored.plan.source_version == 2 and restored.cursor == 0


def test_cleanup_preserves_active_referenced_and_other_users_replay(settings):
    store, token, record, replay = owned(settings)
    original = hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
    replay.step()
    key = store.save_snapshot(token, "Protected old generation", state_with(record, replay))
    old = replay.settings.database_path
    replay.reset()  # Old generation is inactive but still referenced by snapshot.
    record = store.save(token, state_with(record, replay), 0)
    other_token, other = store.create()
    foreign = ReplaySession(settings, scenario_plan(), owner_key=other.identity)
    active = ReplaySession(settings, scenario_plan(), owner_key=record.identity)
    assert cleanup_replays(store, token, confirmed=True)["reclaimed_bytes"] == 0
    assert old.exists() and foreign.settings.database_path.exists() and active.settings.database_path.exists()
    store.delete_snapshot(token, key, confirmed=True)
    preview = cleanup_replays(store, token)
    assert len(preview["files"]) == 1 and old.exists()
    cleaned = cleanup_replays(store, token, confirmed=True)
    assert cleaned["reclaimed_bytes"] > 0 and not old.exists()
    assert replay.settings.database_path.exists() and foreign.settings.database_path.exists() and active.settings.database_path.exists()
    active.lease.close()
    assert len(cleanup_replays(store, token)["files"]) == 1
    assert hashlib.sha256(settings.database_path.read_bytes()).hexdigest() == original


def test_cleanup_retains_unmanaged_legacy_and_corrupt_files(settings):
    store, token, record, replay = owned(settings)
    legacy = ReplaySession(settings, scenario_plan())
    legacy.lease.close()
    corrupt = ReplaySession(settings, scenario_plan(), owner_key=record.identity)
    corrupt.lease.close()
    corrupt.settings.database_path.write_bytes(b"corrupt")
    cleanup_replays(store, token, confirmed=True)
    assert legacy.settings.database_path.exists() and corrupt.settings.database_path.exists()


def test_replay_path_rejects_hardlink_to_main_database(settings):
    target = settings.database_path.parent / "replay" / ("a" * 32) / ("b" * 32 + ".db")
    target.parent.mkdir(parents=True)
    os.link(settings.database_path, target)
    with pytest.raises(WorkspaceError):
        replay_path(settings, target.relative_to(settings.database_path.parent / "replay").as_posix())
