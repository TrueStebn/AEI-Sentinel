"""Explicit offline preparation; does not touch the development database or start networking."""
import argparse
import json
from pathlib import Path
from src.demo_assets import asset_root, prepare_demo
from src.demo_limits import prepare_controls

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=asset_root())
    args = parser.parse_args()
    manifest = prepare_demo(args.directory)
    prepare_controls()
    print(json.dumps(dict(files=len(manifest['files']), bytes=sum(item['bytes'] for item in manifest['files'].values()),
                          scenario=manifest['scenario'], primary_development_database_touched=False)))
