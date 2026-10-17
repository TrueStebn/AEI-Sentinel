"""Owner-only runtime preparation, invoked as a module instead of inline code."""
import argparse
from importlib import metadata
from pathlib import Path
import sys


def check_packages(packages, requirements):
    """Inspect the copied installation without downloading or changing packages."""
    normalize = lambda value: value.lower().replace('_', '-').replace('.', '-')
    expected = {}
    for line in requirements.read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            name, version = line.strip().split('==')
            expected[normalize(name)] = version
    actual = {}
    for dist in metadata.distributions(path=[str(packages)]):
        info = dist.metadata
        if not info.get('Name') or not info.get('Version'):
            raise RuntimeError('Copied package metadata is unreadable or incomplete. Check the isolated directory ACLs.')
        name = normalize(info['Name'])
        if name in actual:
            raise RuntimeError('Duplicate installed package: ' + name)
        actual[name] = info['Version']
    if any(actual.get(name) != version for name, version in expected.items()):
        raise RuntimeError('Copied packages do not match requirements-lock.txt.')
    # Build tools can be present in an existing owner environment used for tests.
    if set(actual) - set(expected) - {'pip', 'setuptools', 'wheel'}:
        raise RuntimeError('Unexpected copied packages.')
    return len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', required=True, type=Path)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    from src.config import PROJECT_ROOT
    from src.demo_assets import safe_path
    from src.demo_limits import prepare_controls, verify_controls
    from src.deployment_gate import AdmissionStore
    packages = safe_path(args.packages)
    count = check_packages(packages, PROJECT_ROOT / 'requirements-lock.txt')
    sys.path.insert(0, str(packages))
    runtime = safe_path(args.runtime_root or PROJECT_ROOT / 'data/runtime')
    if args.verify_only:
        verify_controls(runtime / 'demo-control')
        AdmissionStore(runtime / 'demo-admission')
    else:
        prepare_controls(runtime / 'demo-control')
        AdmissionStore.prepare(runtime / 'demo-admission')
    print(f'Isolated runtime prepared; {count} pinned packages verified. No server or tunnel started.')


if __name__ == '__main__':
    main()
