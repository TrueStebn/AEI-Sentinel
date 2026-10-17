"""Confined replay paths, active-file leases and conservative manual cleanup."""

import json
import os
import re
import weakref
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from src.database import connect, transaction
from src.workspaces import WorkspaceError


def _unsafe(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def replay_path(primary, relative):
    if not isinstance(relative, str) or not re.fullmatch(r"[0-9a-f]{32}/[0-9a-f]{32}\.db", relative):
        raise WorkspaceError("Unsafe Replay database location.")
    root = primary.database_path.parent / "replay"
    target = root / relative
    if any(_unsafe(path) for path in (root, target.parent, target)) or not target.resolve().is_relative_to(root.resolve()):
        raise WorkspaceError("Replay path may not use links or leave the permitted directory.")
    if target.resolve() == primary.database_path.resolve() or (target.exists() and primary.database_path.exists() and target.samefile(primary.database_path)):
        raise WorkspaceError("The laboratory database cannot be used as Replay data.")
    return target


def _lock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(stream, path):
    stream.close()  # Closing releases the OS lock, including after process termination.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


class ReplayLease:
    """One independently locked sidecar per live controller, across processes."""
    def __init__(self, target):
        self.path = target.with_name(f"{target.name}.{uuid4().hex}.active")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("x+b")
        stream.write(b"0")
        stream.flush()
        _lock(stream)
        self._finalizer = weakref.finalize(self, _release, stream, self.path)

    def close(self):
        self._finalizer()


def _active(target):
    for path in target.parent.glob(f"{target.name}.*.active"):
        if _unsafe(path):
            return True
        try:
            with path.open("r+b") as stream:
                _lock(stream)
        except OSError:
            return True
    return False


def _owned(primary, path, owner):
    try:
        with connect(replace(primary, database_path=path), read_only=True) as con:
            row = con.execute("SELECT user_key FROM user_sessions WHERE session_id='replay_owner'").fetchone()
            return row is not None and row[0] == owner
    except Exception:
        # Unmanaged legacy files and corrupt files cannot be proved safe to delete.
        return False


def cleanup_replays(store, token, *, confirmed=False):
    """Preview or remove only this identity's unreferenced, inactive generations.

    Hold the workspace writer lock across reference checks and deletion so an
    autosave or snapshot cannot add a reference between the check and unlink.
    Unknown legacy files are deliberately retained; ownership cannot be proved.
    """
    owner = store.load(token).identity
    root = store.primary.database_path.parent / "replay"
    result = {"files": [], "reclaimed_bytes": 0, "skipped": 0}
    if not root.exists():
        return result
    if _unsafe(root):
        raise WorkspaceError("Replay cleanup refuses a linked directory.")
    with connect(store.settings) as con, transaction(con):
        references = store.referenced_replays(con)  # Any malformed state aborts cleanup safely.
        for directory in root.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", directory.name) or not directory.is_dir() or _unsafe(directory):
                result["skipped"] += 1
                continue
            for path in directory.glob("*.db"):
                relative = path.relative_to(root).as_posix()
                try:
                    path = replay_path(store.primary, relative)
                    if path.resolve() == store.settings.database_path.resolve() or relative in references or _active(path) or not _owned(store.primary, path, owner):
                        result["skipped"] += 1
                        continue
                    companions = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"), *path.parent.glob(f"{path.name}.*.active")]
                    if any(_unsafe(item) for item in companions):
                        result["skipped"] += 1
                        continue
                    size = sum(item.stat().st_size for item in companions if item.exists())
                    result["files"].append({"path": relative, "bytes": size})
                    if confirmed is True:
                        # All paths are verified descendants of the explicit replay root.
                        for item in companions:
                            item.unlink(missing_ok=True)
                        result["reclaimed_bytes"] += size
                except (OSError, ValueError):
                    result["skipped"] += 1
    return result
