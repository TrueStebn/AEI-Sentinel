"""Storage-level denial, immutable analytics, hostile inputs and two independent visitors."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from streamlit.testing.v1 import AppTest

from src.config import PROJECT_ROOT
from src.database import connect, initialize_database, database_summary
from src.data_loader import import_csv_bundle, import_directory
from src.demo_assets import prepare_demo, public_settings, verified_assets, FILES
from src.demo_limits import prepare_controls, charge, resource_slot, file_lock, DemoBusy
from src.local_inference import OllamaInterpreter, InferenceUnavailable
from src.model_identity import MODEL, DIGEST, QUANTIZATION, LocalModelError, verify_identity
from src.runtime_policy import public_demo
from src.simulator import ReplaySession, scenario_plan
from src.workspaces import WorkspaceStore
from src.ui_data import load_catalog
from test_app import APP


@pytest.fixture
def demo(settings, bundle, tmp_path, monkeypatch):
    monkeypatch.setenv('AEI_PUBLIC_DEMO', '0')
    assets, controls = tmp_path / 'snapshots', tmp_path / 'controls'
    prepare_demo(assets, laboratory_bundle=bundle)
    prepare_controls(controls)
    monkeypatch.setenv('AEI_DEMO_ASSETS', str(assets))
    monkeypatch.setenv('AEI_DEMO_CONTROL', str(controls))
    monkeypatch.setenv('AEI_DATABASE_PATH', str(settings.database_path))
    monkeypatch.setenv('AEI_PUBLIC_DEMO', '1')
    return settings, assets, controls


def inventory(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}


@pytest.mark.parametrize('value,expected', [('0', False), ('1', True)])
def test_mode_is_explicit(monkeypatch, value, expected):
    monkeypatch.setenv('AEI_PUBLIC_DEMO', value)
    assert public_demo() is expected


@pytest.mark.parametrize('value', ['', 'true', 'yes', '2'])
def test_invalid_mode_fails_closed(monkeypatch, value):
    monkeypatch.setenv('AEI_PUBLIC_DEMO', value)
    with pytest.raises(ValueError): public_demo()


@pytest.mark.parametrize('step', [None, 0, 1, 2, 3])
def test_every_snapshot_denies_sql_and_imports_without_mutation(demo, bundle, step):
    primary, assets, controls = demo
    before = inventory(assets)
    settings = public_settings(primary, step=step)
    initialize_database(settings)
    with connect(settings, read_only=True) as con:
        assert con.execute('PRAGMA query_only').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError): con.execute('DELETE FROM patients')
        with pytest.raises(sqlite3.OperationalError): con.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError, match='read-only'):
        with connect(settings): pass
    with pytest.raises(RuntimeError, match='read-only'): import_csv_bundle(bundle, settings)
    with pytest.raises(RuntimeError, match='read-only'): import_directory(assets, settings)
    assert inventory(assets) == before


@pytest.mark.parametrize('step', [-1, 4, True, '1', 2.5])
def test_scenario_has_only_four_prepared_states(demo, step):
    with pytest.raises(ValueError): public_settings(demo[0], step=step)


def test_preparation_and_persistence_are_denied_below_ui(demo):
    primary, assets, controls = demo
    before = inventory(assets)
    with pytest.raises(RuntimeError): prepare_demo(assets.parent / 'attacker')
    with pytest.raises(RuntimeError): WorkspaceStore(primary)
    with pytest.raises(RuntimeError): ReplaySession(primary, scenario_plan())
    with pytest.raises(RuntimeError): ReplaySession.recover(primary, {}, 'client-controlled')
    from src.replay_files import cleanup_replays
    with pytest.raises(RuntimeError): cleanup_replays(primary, 'client-controlled')
    with pytest.raises(RuntimeError): database_summary(primary)
    assert inventory(assets) == before and not (assets.parent / 'attacker').exists()


@pytest.mark.parametrize('tamper', ['extra', 'changed', 'manifest', 'oversized'])
def test_asset_integrity_and_storage_budget_fail_closed(demo, monkeypatch, tamper):
    assets = demo[1]
    if tamper == 'extra': (assets / 'visitor.db').write_bytes(b'')
    elif tamper == 'changed': (assets / 'scenario-0.db').write_bytes(b'corrupt')
    elif tamper == 'manifest': (assets / 'manifest.json').write_bytes(b'x' * 16385)
    else: monkeypatch.setattr('src.demo_assets.MAX_ASSET_BYTES', 1)
    with pytest.raises((RuntimeError, ValueError)): verified_assets()


def test_readonly_scenario_preserves_original_statistics_and_pdf(demo):
    from src.advanced_analysis import AdvancedAnalysisEngine, CohortFilters
    from src.patient_aware_analysis import PatientAwareEngine
    from src.investigation_pdf import render_investigation_pdf
    from src.anomalies import AnomalyEngine
    primary, assets, controls = demo
    before = inventory(assets)
    second, third = [public_settings(primary, step=n) for n in (2, 3)]
    cohort = CohortFilters('SIM', 'Escherichia coli', 'urine')
    for settings, n, pct, alerts in [(second, 40, 10, 0), (third, 100, 40, 1)]:
        report = AdvancedAnalysisEngine(settings).analyze(cohort, code='CIP', as_of_date='2026-09-02')
        assert report.current.n_observed == n and report.current.percentage == pct
        assert len(AnomalyEngine(settings).scan(as_of_date='2026-09-02').alerts) == alerts
    patient = PatientAwareEngine(third).analyze(cohort, code='CIP', as_of_date='2026-09-02', source='Simulated Surveillance Scenario')
    pdf, manifest = render_investigation_pdf(report, patient, third)
    assert pdf.startswith(b'%PDF') and len(pdf) < 1024 * 1024
    assert inventory(assets) == before


def test_shared_budget_survives_new_sessions_and_time_windows(tmp_path):
    root = prepare_controls(tmp_path / 'limits')
    for n in range(6): charge('ai', root, now=1000 + n)
    with pytest.raises(DemoBusy, match='budget'): charge('ai', root, now=1006)
    # Reopening never resets a visitor-independent budget.
    prepare_controls(root)
    with pytest.raises(DemoBusy): charge('ai', root, now=1007)
    for batch in range(1, 5):
        for n in range(6): charge('ai', root, now=1000 + batch * 61 + n)
    with pytest.raises(DemoBusy): charge('ai', root, now=1400)
    charge('browse', root, now=1400)  # AI exhaustion does not disable manual browsing.
    charge('ai', root, now=5000)
    assert (root / 'budget.json').stat().st_size < 65536


@pytest.mark.parametrize('tamper', ['extra', 'missing', 'oversized', 'nan', 'rollback'])
def test_control_corruption_denies_requests(tmp_path, tamper):
    root = prepare_controls(tmp_path / 'limits')
    if tamper == 'extra': (root / 'unbounded.log').write_text('x')
    elif tamper == 'missing': (root / 'ai-0.lock').unlink()
    elif tamper == 'oversized': (root / 'budget.json').write_bytes(b'x' * 65537)
    else:
        state = dict(format=1, last_clock=2000 if tamper == 'rollback' else 0, ai=[float('nan')] if tamper == 'nan' else [], browse=[])
        (root / 'budget.json').write_text(json.dumps(state))
    with pytest.raises(DemoBusy): charge('ai', root, now=1000)


def test_os_lock_and_budget_are_cross_process_and_release_after_exit(tmp_path):
    root = prepare_controls(tmp_path / 'limits')
    code = "from pathlib import Path; import sys; from src.demo_limits import resource_slot; " + \
        "slot=resource_slot('ai',root=Path(sys.argv[1])); slot.__enter__(); print('locked',flush=True); sys.stdin.readline()"
    child = subprocess.Popen([sys.executable, '-c', code, str(root)], cwd=PROJECT_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(DemoBusy):
            with resource_slot('ai', root=root): pass
    finally:
        child.terminate()
        child.wait(timeout=10)
    with resource_slot('ai', root=root): pass
    assert len(json.loads((root / 'budget.json').read_text())['ai']) == 2


@pytest.mark.parametrize('size', [1201, 100000])
def test_oversized_direct_ai_request_never_contacts_ollama(demo, monkeypatch, size):
    def forbidden(**kwargs): raise AssertionError('Network called')
    monkeypatch.setattr('src.local_inference.verify_identity', forbidden)
    with pytest.raises(InferenceUnavailable, match='1,200'):
        OllamaInterpreter().generate('x' * size, load_catalog(public_settings(demo[0])))


@pytest.mark.parametrize('model', ['qwen3:4b', 'qwen3:14b', 'qwen3:latest'])
def test_no_model_fallback(model):
    with pytest.raises(ValueError, match='pinned'): OllamaInterpreter(model=model)


@pytest.mark.parametrize('failure', ['missing', 'digest', 'quantization', 'malformed'])
def test_identity_requires_exact_8b_digest_and_quantization(monkeypatch, failure):
    calls = []
    def request(host, port, path, **kwargs):
        calls.append(path)
        if path == '/api/tags':
            return dict(models=[] if failure == 'missing' else [dict(name=MODEL, digest='wrong' if failure == 'digest' else DIGEST)])
        return dict(details=None if failure == 'malformed' else dict(family='qwen3', quantization_level='Q8_0'))
    monkeypatch.setattr('src.model_identity.request_json', request)
    with pytest.raises(LocalModelError): verify_identity()
    assert all(path in ('/api/tags', '/api/show') for path in calls)


def click(app, label):
    next(b for b in app.button if b.label == label).click().run(timeout=30)
    assert not app.exception and not app.error


def test_public_visitors_have_independent_queries_replay_and_no_restore(demo, monkeypatch):
    from src.smart_query import SmartQuery
    calls = []
    def interpret(self, text, catalog):
        calls.append(text)
        return SmartQuery(organism='Escherichia coli', antibiotic='AMP'), {}
    monkeypatch.setattr('src.local_inference.OllamaInterpreter.interpret', interpret)
    before = inventory(demo[1])
    a = AppTest.from_file(str(APP))
    a.query_params['workspace'] = 'attacker-controlled-restore-token'
    a.run(timeout=30)
    assert not a.query_params and not a.exception
    a.text_input(key='_smart_text').set_value('Visitor A question')
    click(a, 'Search')
    b = AppTest.from_file(str(APP)).run(timeout=30)
    assert '_smart_result' not in b.session_state and b.text_input(key='_smart_text').value == ''
    click(a, 'Source & workspace settings')
    a.selectbox(key='data_source').set_value('Simulated Surveillance Scenario').run(timeout=30)
    for _ in range(3): click(a, 'Next Batch')
    assert a.session_state['_public_scenario_step'] == 3
    assert next(b for b in a.button if b.label == 'Next Batch').disabled
    b.run(timeout=30)
    assert b.session_state['data_source'] == 'Laboratory Dataset'
    assert b.session_state['_public_scenario_step'] == 0
    click(a, 'Reset Replay')
    assert a.session_state['_public_scenario_step'] == 0
    assert calls == ['Visitor A question'] and inventory(demo[1]) == before


def test_failed_ai_and_reruns_keep_manual_search_usable(demo, monkeypatch):
    calls = []
    def fail(self, text, catalog):
        calls.append(text)
        raise InferenceUnavailable('Local AI timed out. Use Manual Search.')
    monkeypatch.setattr('src.local_inference.OllamaInterpreter.interpret', fail)
    app = AppTest.from_file(str(APP)).run(timeout=30)
    app.text_input(key='_smart_text').set_value('Show E. coli resistance to AMP.')
    click(app, 'Search')
    app.run(timeout=30)
    assert len(calls) == 1 and any('timed out' in i.value for i in app.info)
    click(app, 'Run Manual Search')
    assert app.session_state['_smart_result'].period.n_observed == 1 and len(calls) == 1


def test_server_configuration_cannot_disable_core_protection():
    from tools.run_public_demo import server_options
    opts = server_options()
    assert opts['server.address'] == '127.0.0.1'
    assert opts['server.enableCORS'] and opts['server.enableXsrfProtection']
    assert not opts['server.enableStaticServing'] and opts['server.maxUploadSize'] == 0
    assert opts['server.trustedUserHeaders'] == {} and '*' not in opts['server.allowedHosts']
    assert server_options(origin='https://review.example.com')['server.allowedHosts'][-1] == 'review.example.com'
    for origin in ['http://example.com', 'https://*.example.com', 'https://user:pass@example.com', 'https://example.com/path']:
        with pytest.raises(ValueError): server_options(origin=origin)


def test_public_snapshots_do_not_register_revision_timer(demo, monkeypatch):
    def unexpected_timer(*args, **kwargs):
        pytest.fail('Immutable snapshots must not schedule WebSocket polling')
    monkeypatch.setattr('src.replay_interface.refresh_watcher', unexpected_timer)
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    assert any('Public Demo Mode' in item.value for item in app.caption)


def test_repeated_manual_submissions_use_newly_edited_filters(demo):
    app = AppTest.from_file(str(APP)).run(timeout=30)
    click(app, 'Run Manual Search')
    assert app.session_state['_smart_result'].period.n_observed == 1
    # Form edits are batched exactly as in a real browser, with no intervening
    # script run between changing the fields and submitting the form.
    app.checkbox(key='_smart_manual_use_min').set_value(True)
    app.number_input(key='_smart_manual_age_min').set_value(65)
    click(app, 'Run Manual Search')
    assert app.session_state['_smart_query'].age_min == 65
    assert app.session_state['_smart_result'].period.n_observed == 0
    app.checkbox(key='_smart_manual_use_min').set_value(False)
    click(app, 'Run Manual Search')
    assert app.session_state['_smart_query'].age_min is None
    assert app.session_state['_smart_result'].period.n_observed == 1


@pytest.mark.parametrize('method,path,denied', [
    ('GET', '/_stcore/health', False), ('HEAD', '/', False), ('OPTIONS', '/', False),
    ('GET', '/media/synthetic.pdf', False), ('POST', '/', True), ('PUT', '/', True),
    ('PATCH', '/', True), ('DELETE', '/', True),
    ('GET', '/_stcore/upload_file/session/file', True), ('OPTIONS', '/_stcore/upload_file', True),
    ('GET', '/app/static/secret.db', True),
])
def test_public_http_policy_denies_uploads_before_reading_body(method, path, denied):
    import asyncio
    from tools.run_public_demo import PublicHTTPPolicy
    messages, delegated = [], []
    async def inner(scope, receive, send): delegated.append(scope)
    async def receive(): raise AssertionError('Forbidden request body was read')
    async def send(message): messages.append(message)
    scope = dict(type='http', method=method, path=path, headers=[])
    asyncio.run(PublicHTTPPolicy(inner)(scope, receive, send))
    assert bool(delegated) is not denied
    if denied: assert messages[0]['status'] == 403


def test_public_http_policy_preserves_websocket_protocol():
    import asyncio
    from tools.run_public_demo import PublicHTTPPolicy
    delegated = []
    async def inner(scope, receive, send): delegated.append(scope)
    scope = dict(type='websocket', path='/_stcore/stream')
    asyncio.run(PublicHTTPPolicy(inner)(scope, None, None))
    assert delegated == [scope]


def test_cancelled_inference_releases_both_process_and_os_slots(demo, monkeypatch):
    from src.local_inference import _INFERENCE_SLOT
    monkeypatch.setattr('src.local_inference.verify_identity', lambda **kwargs: None)
    def cancel(*args, **kwargs): raise KeyboardInterrupt()
    monkeypatch.setattr('src.local_inference.request_json', cancel)
    with pytest.raises(KeyboardInterrupt):
        OllamaInterpreter().generate('Show resistance.', load_catalog(public_settings(demo[0])))
    assert _INFERENCE_SLOT.acquire(blocking=False)
    _INFERENCE_SLOT.release()
    with resource_slot('ai', root=demo[2]): pass


def test_http_deadline_includes_headers_and_detached_response_socket(monkeypatch):
    from types import SimpleNamespace
    from src.model_identity import request_json
    timeouts, closed = [], []
    sock = SimpleNamespace(settimeout=timeouts.append)
    response = SimpleNamespace(status=200, fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock)), read1=lambda size: b'{}')
    class Connection:
        def __init__(self, *args, **kwargs): self.sock = sock
        def request(self, *args): pass
        def getresponse(self):
            self.sock = None
            return response
        def close(self): closed.append(True)
    ticks = iter([0, 4, 7, 11])
    monkeypatch.setattr('src.model_identity.perf_counter', lambda: next(ticks))
    monkeypatch.setattr('src.model_identity.http.client.HTTPConnection', Connection)
    with pytest.raises(LocalModelError, match='timed out'):
        request_json('127.0.0.1', 11434, '/api/chat', deadline=10)
    assert timeouts == [6, 3] and closed == [True]
