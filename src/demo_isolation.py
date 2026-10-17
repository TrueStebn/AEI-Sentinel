"""Fail-closed startup checks under the approved standard Windows account."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import tempfile

from src.config import PROJECT_ROOT
from src.demo_assets import safe_path, asset_root
from src.demo_limits import control_root

ROOT = Path('C:/ProgramData/AEI-Sentinel-Demo')


def _known_folder(csidl):
    shell = C.WinDLL('shell32', use_last_error=True)
    shell.SHGetFolderPathW.argtypes = [W.HWND, C.c_int, W.HANDLE, W.DWORD, W.LPWSTR]
    shell.SHGetFolderPathW.restype = C.c_long
    buffer = C.create_unicode_buffer(32768)
    if shell.SHGetFolderPathW(None, csidl, None, 0, buffer) != 0:
        raise RuntimeError('The isolated Windows profile is unavailable.')
    return Path(buffer.value)


def _windows_directory():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetWindowsDirectoryW.argtypes = [W.LPWSTR, W.UINT]
    kernel.GetWindowsDirectoryW.restype = W.UINT
    buffer = C.create_unicode_buffer(32768)
    length = kernel.GetWindowsDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise RuntimeError('Windows directory lookup failed.')
    return Path(buffer.value)


def prepare_environment():
    """Derive folders from the logged-on token, never the caller's environment."""
    raw_profile = _known_folder(0x28).absolute()
    if raw_profile == Path('C:/Users/Denis'):
        raise RuntimeError('Known folders escaped the isolated account profile.')
    profile = safe_path(raw_profile)
    # Local/Roaming known-folder expansion can consult the caller's USERPROFILE.
    # This is a newly provisioned local account: use its own profile directories.
    local = safe_path(profile / 'AppData/Local')
    roaming = safe_path(profile / 'AppData/Roaming')
    windows = safe_path(_windows_directory())
    if not local.is_relative_to(profile) or not roaming.is_relative_to(profile):
        raise RuntimeError('Known folders escaped the isolated account profile.')
    temporary = local / 'AEI-Sentinel/Temp'
    safe_path(temporary)
    temporary.mkdir(parents=True, exist_ok=True)
    roaming.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=temporary):
        pass
    clean = dict(SystemRoot=str(windows), WINDIR=str(windows),
                 COMSPEC=str(windows / 'System32/cmd.exe'),
                 PATH=os.pathsep.join(map(str, (ROOT / 'python', ROOT / 'python/DLLs', windows / 'System32', windows))),
                 PATHEXT='.COM;.EXE;.BAT;.CMD', OS='Windows_NT',
                 USERNAME='AEIDemo', USERPROFILE=str(profile), HOME=str(profile),
                 HOMEDRIVE=profile.drive, HOMEPATH=str(profile)[len(profile.drive):],
                 LOCALAPPDATA=str(local), APPDATA=str(roaming), PROGRAMDATA=str(ROOT.parent),
                 TEMP=str(temporary), TMP=str(temporary),
                 PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                 AEI_ISOLATED_DEMO='1', AEI_PUBLIC_DEMO='1',
                 AEI_OLLAMA_MODEL='qwen3:8b', AEI_OLLAMA_URL='http://127.0.0.1:11434',
                 NO_PROXY='127.0.0.1,localhost')
    os.environ.clear()
    os.environ.update(clean)
    tempfile.tempdir = None
    return dict(profile=str(profile), temporary=str(temporary), inherited_environment_removed=True)


def token_sid():
    if os.name != 'nt':
        raise RuntimeError('Windows account isolation is required.')
    from src.windows_job import api, check
    kernel = api()
    adv = C.WinDLL('advapi32', use_last_error=True)
    adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    adv.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    token = W.HANDLE()
    check(adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)))
    sid_text = W.LPWSTR()
    try:
        size = W.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, C.byref(size))
        data = C.create_string_buffer(size.value)
        check(adv.GetTokenInformation(token, 1, data, size, C.byref(size)))
        sid = C.cast(data, C.POINTER(C.c_void_p))[0]
        check(adv.ConvertSidToStringSidW(sid, C.byref(sid_text)))
        return sid_text.value
    finally:
        if sid_text:
            kernel.LocalFree(C.cast(sid_text, C.c_void_p))
        kernel.CloseHandle(token)


def verify_isolation():
    root = safe_path(ROOT)
    if safe_path(PROJECT_ROOT) != root / 'app':
        raise RuntimeError('Isolated deployment must use the dedicated source copy.')
    manifest_path = root / 'isolation.json'
    if manifest_path.stat().st_size > 4096:
        raise RuntimeError('Invalid isolation marker.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if manifest.get('format') != 1 or manifest.get('sid') != token_sid() or manifest.get('origin') != 'https://secular-bundle-uncapped.ngrok-free.dev' or manifest.get('owner_profile') != 'C:\\Users\\Denis':
        raise RuntimeError('The worker account does not match the owner-approved deployment.')
    if C.WinDLL('shell32').IsUserAnAdmin():
        raise RuntimeError('The demo worker must not be an administrator.')
    # Opening a handle tests access without reading credentials or writing source.
    for path in (Path('C:/Users/Denis'), Path('C:/Users/Denis/.codex'),
                 Path('C:/Users/Denis/PycharmProjects/JunctionX/AEI-Sentinel')):
        try:
            with os.scandir(path):
                pass
        except PermissionError:
            continue
        raise RuntimeError('Personal directory access is not isolated: ' + str(path))
    for path in (Path('C:/Users/Denis/.codex/auth.json'), Path('C:/Users/Denis/PycharmProjects/JunctionX/AEI-Sentinel/app.py')):
        try:
            with open(path, 'rb'):
                pass
        except PermissionError:
            continue
        raise RuntimeError('Personal file access is not isolated: ' + str(path))
    if asset_root() != root / 'app/data/runtime/public-demo' or control_root() != root / 'app/data/runtime/demo-control':
        raise RuntimeError('Runtime storage escaped the isolated copy.')
    for path in (root / 'isolation.json', PROJECT_ROOT / 'app.py',
                 asset_root() / 'laboratory.db', root / 'python/python.exe'):
        try:
            with open(safe_path(path), 'r+b'):
                pass
        except PermissionError:
            continue
        raise RuntimeError('Deployment source or snapshot is writable: ' + str(path))
    for directory in ('demo-control', 'demo-admission'):
        lock = 'budget.lock' if directory == 'demo-control' else 'state.lock'
        with open(safe_path(PROJECT_ROOT / 'data/runtime' / directory / lock), 'r+b'):
            pass
    return dict(account_sid=manifest['sid'], personal_access_denied=True,
                source_and_assets_readonly=True, origin=manifest['origin'])
