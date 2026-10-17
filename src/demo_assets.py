"""Owner-prepared immutable synthetic snapshots; visitors never create databases."""

from dataclasses import replace
import hashlib
import json
import os
from functools import lru_cache
from contextlib import closing
from pathlib import Path
import sqlite3

from src.config import PROJECT_ROOT, Settings

FILES = ('laboratory.db', 'scenario-0.db', 'scenario-1.db', 'scenario-2.db', 'scenario-3.db')
MAX_ASSET_BYTES = 256 * 1024 * 1024


def asset_root():
    return Path(os.environ.get('AEI_DEMO_ASSETS', PROJECT_ROOT / 'data/runtime/public-demo')).absolute()


def safe_path(path):
    path = Path(path).absolute()
    if any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents)):
        raise RuntimeError('Public-demo storage must not contain symlinks or directory junctions.')
    return path.resolve()


def _signature(root):
    safe_path(root)
    names = ('manifest.json', *FILES)
    if not root.is_dir() or {item.name for item in root.iterdir()} != set(names):
        raise RuntimeError('Public-demo snapshots are unavailable. Run the owner preparation command locally.')
    signature = tuple((name, safe_path(root / name).stat().st_size, (root / name).stat().st_mtime_ns) for name in names)
    if signature[0][1] > 16384 or sum(item[1] for item in signature[1:]) > MAX_ASSET_BYTES:
        raise RuntimeError('Public-demo assets exceed their fixed storage budget.')
    return signature


@lru_cache(maxsize=4)
def _verify(root, signature):
    root = Path(root)
    raw = (root / 'manifest.json').read_bytes()
    if len(raw) > 16384:
        raise RuntimeError('Invalid public-demo manifest.')
    manifest = json.loads(raw)
    if manifest.get('format') != 1 or set(manifest.get('files', {})) != set(FILES):
        raise RuntimeError('Invalid public-demo manifest.')
    total = 0
    for name in FILES:
        raw = (root / name).read_bytes()
        total += len(raw)
        if total > MAX_ASSET_BYTES or len(raw) != manifest['files'][name]['bytes'] or hashlib.sha256(raw).hexdigest() != manifest['files'][name]['sha256']:
            raise RuntimeError('Public-demo snapshot integrity failed. Stop and ask the owner to inspect it.')
    return manifest


def verified_assets():
    root = asset_root()
    return root.resolve(), _verify(str(root.resolve()), _signature(root))


def public_settings(primary, *, step=None):
    root, manifest = verified_assets()
    if step is not None and (type(step) is not int or not 0 <= step <= 3):
        raise ValueError('Choose one of the four predefined scenario steps.')
    name = 'laboratory.db' if step is None else f'scenario-{step}.db'
    return replace(primary, database_path=root / name)


def is_immutable_asset(path):
    root, manifest = verified_assets()
    return safe_path(path) in {root / name for name in FILES}


def _snapshot(source, target):
    with closing(sqlite3.connect(source)) as reader, closing(sqlite3.connect(target)) as writer:
        reader.backup(writer)
        writer.execute('PRAGMA journal_mode=DELETE')


def prepare_demo(directory, *, laboratory_bundle=None):
    """Explicit owner action, never app startup. Optional bundle is for isolated tests."""
    from src.runtime_policy import public_demo
    if public_demo():
        raise RuntimeError('Snapshot preparation is disabled in Public Demo Mode.')
    from src.database import initialize_database
    from src.data_loader import import_csv_bundle
    from src.simulator import scenario_plan
    from src.anomalies import AnomalyEngine
    from src.resistance import ResistanceEngine, ResistanceFilters
    target = Path(directory).absolute()
    safe_path(target.parent)
    if target.exists():
        raise RuntimeError('Snapshot destination already exists. Use a new dedicated directory; nothing was overwritten.')
    target.mkdir()
    bundled = PROJECT_ROOT / 'data/synthetic'
    checksums = json.loads((bundled / 'SHA256SUMS.json').read_text())
    if laboratory_bundle is None:
        laboratory_bundle = {name: (bundled / name).read_bytes() for name in checksums}
        if any(hashlib.sha256(raw).hexdigest() != checksums[name] for name, raw in laboratory_bundle.items()):
            raise RuntimeError('The bundled synthetic inputs do not match their checksums.')
    laboratory = Settings(database_path=target / '.laboratory-build.db', dataset_path=bundled)
    import_csv_bundle(laboratory_bundle, laboratory, expect_original=False)
    _snapshot(laboratory.database_path, target / FILES[0])
    scenario = replace(laboratory, database_path=target / '.scenario-build.db')
    initialize_database(scenario)
    _snapshot(scenario.database_path, target / FILES[1])
    plan = scenario_plan()
    stages = []
    for step in range(1, 4):
        import_csv_bundle(plan.bundle(step - 1), scenario)
        _snapshot(scenario.database_path, target / f'scenario-{step}.db')
        scan = AnomalyEngine(scenario).scan(as_of_date='2026-09-02')
        current = ResistanceEngine(scenario).last_28_days(ResistanceFilters('CIP', 'SIM', 'Escherichia coli', 'urine'), as_of_date='2026-09-02')
        stages.append(dict(step=step, n=current.n_tested, resistance=current.resistance_pct, alerts=len(scan.alerts)))
    assert stages[1]['n'] == 40 and stages[1]['resistance'] == 10
    assert stages[2]['n'] == 100 and stages[2]['resistance'] == 40 and stages[2]['alerts'] == 1
    # Only exact disposable files created above, under this newly created directory.
    for name in ('.laboratory-build.db', '.scenario-build.db'):
        for suffix in ('', '-wal', '-shm', '-journal'):
            item = target / (name + suffix)
            assert safe_path(item).parent == target.resolve() and item.name.startswith('.')
            item.unlink(missing_ok=True)
    files = {name: dict(bytes=(target / name).stat().st_size,
                       sha256=hashlib.sha256((target / name).read_bytes()).hexdigest()) for name in FILES}
    if sum(item['bytes'] for item in files.values()) > MAX_ASSET_BYTES:
        raise RuntimeError('Prepared snapshots exceed the fixed storage budget.')
    manifest = dict(format=1, files=files, synthetic_csv_sha256=checksums, scenario=stages)
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
