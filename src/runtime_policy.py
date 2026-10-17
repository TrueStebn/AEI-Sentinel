"""Server-side write policy for an explicitly configured future demo process."""

import os

from src.config import PROJECT_ROOT, get_settings


def public_demo():
    value = os.environ.get('AEI_PUBLIC_DEMO', '0')
    if value not in ('0', '1'):
        raise ValueError('AEI_PUBLIC_DEMO must be 0 or 1.')
    return value == '1'


def protected_source(settings):
    if not public_demo():
        return False
    target = settings.database_path.resolve()
    for primary in (get_settings().database_path.resolve(), (PROJECT_ROOT / 'data/runtime/aei.db').resolve()):
        if target == primary or (target.exists() and primary.exists() and target.samefile(primary)):
            return True
    return False
