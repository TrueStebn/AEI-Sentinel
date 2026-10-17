"""Fixed-size owner storage and nonwaiting OS locks shared by local worker processes."""

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import time

from src.config import PROJECT_ROOT
from src.demo_assets import safe_path

LIMITS = {'ai': (6, 30, 1), 'browse': (90, 900, 2)}
CONTROL_FILES = {'owner.json', 'budget.json', 'budget.tmp', 'budget.lock', 'ai-0.lock', 'browse-0.lock', 'browse-1.lock', 'server.lock'}


class DemoBusy(RuntimeError):
    pass


def control_root():
    return Path(os.environ.get('AEI_DEMO_CONTROL', PROJECT_ROOT / 'data/runtime/demo-control')).absolute()


def prepare_controls(directory=None):
    root = Path(directory or control_root()).absolute()
    safe_path(root.parent)
    if not root.exists():
        root.mkdir()
        (root / 'owner.json').write_text('{"format":1,"purpose":"aei-sentinel-demo-limits"}\n')
        (root / 'budget.json').write_text(json.dumps(dict(format=1, last_clock=0, ai=[], browse=[])))
        for name in CONTROL_FILES - {'owner.json', 'budget.json', 'budget.tmp'}:
            (root / name).write_bytes(b'0')
    verify_controls(root)
    return root


def verify_controls(root=None):
    root = safe_path(root or control_root())
    if not root.is_dir() or not (root / 'owner.json').is_file():
        raise DemoBusy('Demo resource controls are unavailable. Ask the owner to run the startup checks.')
    if not (CONTROL_FILES - {'budget.tmp'}) <= {item.name for item in root.iterdir()}:
        raise DemoBusy('Demo resource storage is incomplete. Contact the owner.')
    for item in root.iterdir():
        safe_path(item)
        if item.name not in CONTROL_FILES or not item.is_file() or item.stat().st_size > 65536:
            raise DemoBusy('Demo resource storage failed validation. Contact the owner.')
    if json.loads((root / 'owner.json').read_text()) != dict(format=1, purpose='aei-sentinel-demo-limits'):
        raise DemoBusy('Demo resource storage has an invalid owner marker.')
    return root


@contextmanager
def file_lock(path):
    safe_path(path)
    stream = open(path, 'r+b')
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise DemoBusy('The local demo is busy. Retry shortly; no request was queued.') from None
    try:
        yield
    finally:
        stream.close()  # The OS also releases this lock if the owning process dies.


def charge(category, root=None, now=None):
    if category not in LIMITS:
        raise ValueError('Unknown demo resource category.')
    root = verify_controls(root)
    now = time.time() if now is None else now
    with file_lock(root / 'budget.lock'):
        raw = (root / 'budget.json').read_bytes()
        if len(raw) > 65536:
            raise DemoBusy('Demo request budget is unavailable. Contact the owner.')
        state = json.loads(raw)
        if set(state) != {'format', 'last_clock', 'ai', 'browse'} or state['format'] != 1 or type(state['last_clock']) not in (int, float) or not math.isfinite(state['last_clock']) or not math.isfinite(now) or now < state['last_clock']:
            raise DemoBusy('Demo request budget requires an owner check.')
        for key in LIMITS:
            events = state[key]
            if not isinstance(events, list) or len(events) > 1000 or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 or t > now for t in events):
                raise DemoBusy('Demo request budget requires an owner check.')
            state[key] = [t for t in events if now - t < 3600]
        minute, hour, slots = LIMITS[category]
        if sum(now - t < 60 for t in state[category]) >= minute or len(state[category]) >= hour:
            raise DemoBusy('The shared demo request budget is temporarily exhausted. Please return later. Manual Search remains available when the AI budget is exhausted.')
        state[category].append(now)
        state['last_clock'] = now
        encoded = json.dumps(state, separators=(',', ':')).encode()
        if len(encoded) > 65536:
            raise DemoBusy('Demo request budget storage limit reached.')
        (root / 'budget.tmp').write_bytes(encoded)
        os.replace(root / 'budget.tmp', root / 'budget.json')


@contextmanager
def resource_slot(category, *, root=None):
    root = verify_controls(root)
    if category not in LIMITS:
        raise ValueError('Unknown demo resource category.')
    selected = None
    for number in range(LIMITS[category][2]):
        lock = file_lock(root / f'{category}-{number}.lock')
        try:
            lock.__enter__()
            selected = lock
            break
        except DemoBusy:
            pass
    if selected is None:
        raise DemoBusy('The local demo is busy. Retry shortly; no request was queued.')
    try:
        charge(category, root)
        yield
    finally:
        selected.__exit__(None, None, None)
