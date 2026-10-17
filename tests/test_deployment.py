"""Admission, reconnect isolation and real kernel memory enforcement."""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.deployment_gate import AdmissionGate, AdmissionStore, MAX_FRAME, RATES
from src.demo_limits import DemoBusy
from src.windows_job import run_bounded


@pytest.fixture
def store(tmp_path):
    return AdmissionStore.prepare(tmp_path / 'admission')


def scope(kind='http', path='/', headers=None, method='GET'):
    return dict(type=kind, path=path, raw_path=path.encode(), query_string=b'',
                method=method, headers=headers if headers is not None else
                [(b'host', b'127.0.0.1:8515'), (b'origin', b'http://127.0.0.1:8515')])


def call(store, request, events=None, inner=None):
    sent, seen = [], []
    incoming = iter(events or [])
    async def receive():
        return next(incoming)
    async def send(message):
        sent.append(message)
    async def app(s, r, w):
        seen.append(s)
        if inner:
            await inner(s, r, w)
        elif s['type'] == 'http':
            await w(dict(type='http.response.start', status=200, headers=[]))
            await w(dict(type='http.response.body', body=b'ok'))
    asyncio.run(AdmissionGate(app, store=store)(request, receive, send))
    return sent, seen


@pytest.mark.parametrize('path', ['/', '/favicon.ico', '/_stcore/health', '/_stcore/host-config', '/static/js/main.js', '/media/report.pdf'])
def test_allowed_public_routes(store, path):
    sent, seen = call(store, scope(path=path))
    assert sent[0]['status'] == 200 and seen


@pytest.mark.parametrize('path', ['/_stcore/upload_file', '/app/static/private', '/_stcore/metrics', '/_stcore/script-health-check', '/component/test', '/private', '/.git/config'])
def test_private_and_debug_routes_denied_before_app(store, path):
    sent, seen = call(store, scope(path=path))
    assert sent[0]['status'] == 403 and not seen


@pytest.mark.parametrize('method', ['POST', 'PUT', 'DELETE', 'PATCH'])
def test_unsafe_methods_do_not_read_request_body(store, method):
    sent, seen = call(store, scope(method=method))
    assert sent[0]['status'] == 403 and not seen


@pytest.mark.parametrize('headers', [[], [(b'host', b'evil.test')], [(b'host', b'127.0.0.1:8515'), (b'host', b'evil.test')], [(b'host', b'127.0.0.1:8515'), (b'origin', b'https://evil.test')]])
def test_host_origin_fail_closed(store, headers):
    sent, seen = call(store, scope(headers=headers))
    assert sent[0]['status'] == 403 and not seen


@pytest.mark.parametrize('extra', [(b'content-length', b'1'), (b'transfer-encoding', b'chunked'), (b'x-large', b'x' * 16384)])
def test_oversized_or_body_requests_denied(store, extra):
    request = scope()
    request['headers'].append(extra)
    sent, seen = call(store, request)
    assert sent[0]['status'] == 413 and not seen


def test_long_url_denied(store):
    request = scope()
    request['query_string'] = b'x' * 2049
    sent, seen = call(store, request)
    assert sent[0]['status'] == 413 and not seen


def test_forwarded_identity_is_removed(store):
    request = scope()
    request['headers'] += [(b'x-forwarded-for', b'admin'), (b'forwarded', b'admin'), (b'x-real-ip', b'admin'), (b'cf-access-user', b'admin')]
    sent, seen = call(store, request)
    assert len(seen[0]['headers']) == 2


def test_reconnect_id_is_never_forwarded(store):
    request = scope('websocket', '/_stcore/stream')
    request['headers'].append((b'sec-websocket-protocol', b'streamlit, xsrf, victim-session, extra'))
    request['subprotocols'] = ['streamlit', 'xsrf', 'victim-session', 'extra']
    _, seen = call(store, request)
    assert seen[0]['subprotocols'] == ['streamlit', 'xsrf']
    assert (b'sec-websocket-protocol', b'streamlit, xsrf') in seen[0]['headers']
    assert b'victim-session' not in repr(seen).encode()


def test_ws_requires_origin(store):
    sent, seen = call(store, scope('websocket', '/_stcore/stream', [(b'host', b'127.0.0.1:8515')]))
    assert sent[0]['type'] == 'websocket.close' and not seen


def test_four_slots_and_persistent_disconnect_cooldown(store):
    with ExitStack() as stack:
        for _ in range(4):
            stack.enter_context(store.connection())
        with pytest.raises(DemoBusy):
            with store.connection():
                pass
    reopened = AdmissionStore(store.root)
    with pytest.raises(DemoBusy):
        with reopened.connection():
            pass


@pytest.mark.parametrize('category', list(RATES))
def test_global_short_budget_survives_restart(store, category):
    for _ in range(RATES[category][0]):
        store.charge(category, now=120)
    with pytest.raises(DemoBusy):
        AdmissionStore(store.root).charge(category, now=179)
    with pytest.raises(DemoBusy):
        store.charge(category, now=119)
    store.charge(category, now=240)
    assert (store.root / 'state.json').stat().st_size < 16384


@pytest.mark.parametrize('tamper', ['extra', 'oversized', 'corrupt', 'duplicate'])
def test_tampered_storage_does_not_reach_app(store, tamper):
    if tamper == 'extra':
        (store.root / 'unknown').write_text('')
    elif tamper == 'oversized':
        (store.root / 'state.json').write_text('x' * 16385)
    elif tamper == 'corrupt':
        (store.root / 'state.json').write_text('{}')
    else:
        state = json.loads((store.root / 'state.json').read_text())
        state['buckets']['http'] = [[0, 1], [0, 1]]
        (store.root / 'state.json').write_text(json.dumps(state))
    sent, seen = call(store, scope())
    assert sent[0]['status'] == 429 and not seen


@pytest.mark.parametrize('body,limit,expected', [(b'x'*(MAX_FRAME+1), 1, 1009), (b'x', 31, 1008)], ids=['oversized-frame', 'message-rate'])
def test_established_ws_frames_are_bounded(store, body, limit, expected):
    async def inner(s, r, w):
        await w(dict(type='websocket.accept'))
        for _ in range(limit):
            await r()
    sent, _ = call(store, scope('websocket', '/_stcore/stream'),
                   [dict(type='websocket.receive', bytes=body)] * limit, inner)
    assert sent[-1] == dict(type='websocket.close', code=expected)


def test_ws_denial_http_extension(store):
    request = scope('websocket', '/bad')
    request['extensions'] = {'websocket.http.response': {}}
    sent, _ = call(store, request)
    assert sent[0]['status'] == 403


def test_static_burst_waits_with_bounded_queue_and_dynamic_http_rejects(store):
    from src.deployment_gate import HTTP_ACTIVE, STATIC_PENDING
    async def scenario():
        released = asyncio.Event()
        started = 0
        async def inner(s, r, w):
            nonlocal started
            started += 1
            await released.wait()
            await w(dict(type='http.response.start', status=200, headers=[]))
            await w(dict(type='http.response.body', body=b'asset'))
        gate = AdmissionGate(inner, store=store)
        async def request(path):
            sent = []
            async def receive(): return dict(type='http.request', body=b'')
            async def send(value): sent.append(value)
            await gate(scope(path=path), receive, send)
            return sent[0]['status']
        active = [asyncio.create_task(request('/static/js/module.js')) for _ in range(HTTP_ACTIVE)]
        await asyncio.sleep(0)
        assert started == HTTP_ACTIVE
        queued = [asyncio.create_task(request('/static/js/module.js')) for _ in range(STATIC_PENDING)]
        await asyncio.sleep(0)
        assert gate.static_waiting == STATIC_PENDING and started == HTTP_ACTIVE
        assert await request('/static/js/excess.js') == 503
        assert await request('/_stcore/health') == 503
        released.set()
        assert await asyncio.gather(*active, *queued) == [200] * (HTTP_ACTIVE+STATIC_PENDING)
        assert gate.http_active == gate.static_waiting == 0
    asyncio.run(scenario())


def test_static_wait_timeout_and_cancellation_release_queue_capacity(store, monkeypatch):
    import src.deployment_gate as deployment
    monkeypatch.setattr(deployment, 'STATIC_WAIT_SECONDS', 0.01)
    async def scenario():
        async def inner(*args): pytest.fail('Saturated request reached app')
        gate = AdmissionGate(inner, store=store)
        for _ in range(deployment.HTTP_ACTIVE):
            await gate.http_slots.acquire()
        sent = []
        async def receive(): return dict(type='http.request', body=b'')
        async def send(value): sent.append(value)
        await gate(scope(path='/static/js/module.js'), receive, send)
        assert sent[0]['status'] == 503 and gate.static_waiting == 0
        monkeypatch.setattr(deployment, 'STATIC_WAIT_SECONDS', 5)
        pending = asyncio.create_task(gate(scope(path='/static/js/module.js'), receive, send))
        await asyncio.sleep(0)
        assert gate.static_waiting == 1
        pending.cancel()
        with pytest.raises(asyncio.CancelledError): await pending
        assert gate.static_waiting == 0
    asyncio.run(scenario())


def test_developer_tree_cannot_claim_isolated_deployment():
    from src.demo_isolation import verify_isolation
    with pytest.raises(RuntimeError, match='dedicated source copy'):
        verify_isolation()


@pytest.mark.skipif(os.name != 'nt', reason='Windows token test')
def test_sid_is_read_from_real_windows_token():
    from src.demo_isolation import token_sid
    observed = token_sid()
    expected = json.loads(subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh']).decode().strip().split(',', 1)[1])
    assert observed == expected and observed.startswith('S-1-5-')


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell argument regression')
def test_windows_powershell_prepares_runtime_with_spaced_paths(tmp_path):
    import sysconfig
    from src.config import PROJECT_ROOT
    runtime = tmp_path / 'runtime with spaces'
    runtime.mkdir()
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = ("$ErrorActionPreference='Stop'; & " + quote(sys._base_executable)
               + ' -B -m tools.prepare_demo_runtime --packages '
               + quote(sysconfig.get_path('purelib')) + ' --runtime-root '
               + quote(runtime) + '; exit $LASTEXITCODE')
    powershell = Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    for _ in range(2):
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
                                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert 'pinned packages verified' in result.stdout
    from src.demo_limits import verify_controls
    verify_controls(runtime / 'demo-control')
    AdmissionStore(runtime / 'demo-admission')


def test_unreadable_package_metadata_fails_closed(tmp_path):
    from tools.prepare_demo_runtime import check_packages
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('sample==1.0\n')
    (tmp_path / 'sample-1.0.dist-info').mkdir()
    with pytest.raises(RuntimeError, match='metadata is unreadable or incomplete'):
        check_packages(tmp_path, requirements)


def test_isolated_environment_uses_token_folders_and_removes_owner_secrets(tmp_path, monkeypatch):
    import src.demo_isolation as isolation
    profile = tmp_path / 'AEIDemo'
    local, roaming = profile / 'AppData/Local', profile / 'AppData/Roaming'
    local.mkdir(parents=True)
    roaming.mkdir()
    monkeypatch.setattr(isolation, '_known_folder', lambda key: profile if key == 0x28 else pytest.fail('Do not expand caller-dependent AppData known folders'))
    monkeypatch.setattr(isolation, '_windows_directory', lambda: tmp_path / 'Windows')
    monkeypatch.setattr(os, 'environ', {'TEMP': 'C:/Users/Denis/private', 'USERPROFILE': 'C:/Users/Denis', 'APPDATA': 'C:/Users/Denis/AppData/Roaming',
                                      'GITHUB_TOKEN': 'sentinel', 'PYTHONPATH': 'untrusted', 'AEI_MINIMUM_SAMPLE_SIZE': '1'})
    monkeypatch.setattr(__import__('tempfile'), 'tempdir', None)
    result = isolation.prepare_environment()
    assert result['inherited_environment_removed']
    assert os.environ['TEMP'] == str(local / 'AEI-Sentinel/Temp')
    assert os.environ['USERPROFILE'] == str(profile)
    assert not {'GITHUB_TOKEN', 'PYTHONPATH', 'AEI_MINIMUM_SAMPLE_SIZE'} & os.environ.keys()
    assert os.environ['AEI_PUBLIC_DEMO'] == '1' and os.environ['AEI_OLLAMA_MODEL'] == 'qwen3:8b'


def test_isolated_environment_rejects_owner_profile(tmp_path, monkeypatch):
    import src.demo_isolation as isolation
    monkeypatch.setattr(isolation, '_known_folder', lambda key: Path('C:/Users/Denis'))
    monkeypatch.setattr(isolation, '_windows_directory', lambda: tmp_path / 'Windows')
    with pytest.raises(RuntimeError, match='escaped'):
        isolation.prepare_environment()


@pytest.mark.skipif(os.name != 'nt', reason='Windows kernel enforcement test')
def test_real_job_blocks_allocation_and_child_processes(tmp_path):
    script = tmp_path / 'job_probe.py'
    evidence = tmp_path / 'evidence.txt'
    script.write_text('''import subprocess,sys
from pathlib import Path
from src.windows_job import verify_current_job
assert verify_current_job(96)['memory_bytes'] == 96*1024**2
try:
    subprocess.run([sys.executable, '-c', 'pass'], check=True)
except (OSError, subprocess.SubprocessError):
    pass
else:
    raise AssertionError('Child process escaped active-process limit')
try:
    value = bytearray(160*1024**2)
except MemoryError:
    Path(sys.argv[1]).write_text('kernel-memory-and-process-cap-verified')
else:
    raise AssertionError('Memory escaped Job Object')
''')
    from src.config import PROJECT_ROOT
    # -c preserves the project import path without copying source into the probe.
    code = 'import sys; sys.argv=' + repr([str(script), str(evidence)]) + ';exec(compile(open(sys.argv[0]).read(),sys.argv[0],"exec"))'
    result = run_bounded([sys._base_executable, '-B', '-c', code], cwd=PROJECT_ROOT, memory_mb=96)
    assert result == 0 and evidence.read_text() == 'kernel-memory-and-process-cap-verified'
