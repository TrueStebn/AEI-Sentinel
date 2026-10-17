"""Fixed provider configuration and explicit approval without agent connections."""
import hashlib

import pytest

import tools.run_demo_tunnel as tunnel


@pytest.fixture
def agent(tmp_path, monkeypatch):
    directory = tmp_path / 'ngrok'
    directory.mkdir()
    binary = directory / 'ngrok.exe'
    binary.write_bytes(b'fake agent, never executable')
    monkeypatch.setattr(tunnel, 'AGENT_SHA256', hashlib.sha256(binary.read_bytes()).hexdigest())
    config = directory / 'ngrok.yml'
    config.write_text(tunnel.CONFIG_TEMPLATE.replace('<redacted>', 'example_' * 5))
    return tmp_path, binary, config


def test_fixed_agent_profile_accepts_reviewed_settings(agent):
    root, binary, config = agent
    assert tunnel.checked_agent(root) == (binary, config)


@pytest.mark.parametrize('edit', ['logging', 'web_api', 'management', 'policy', 'extra_endpoint', 'oversized', 'binary'])
def test_unreviewed_agent_configuration_fails_closed(agent, edit):
    root, binary, config = agent
    text = config.read_text()
    if edit == 'logging':
        config.write_text(text.replace('log: false', 'log: stdout'))
    elif edit == 'web_api':
        config.write_text(text.replace('web_addr: false', 'web_addr: 0.0.0.0:4040'))
    elif edit == 'management':
        config.write_text(text.replace('remote_management: false', 'remote_management: true'))
    elif edit == 'policy':
        config.write_text(text + 'traffic_policy: {}\n')
    elif edit == 'extra_endpoint':
        config.write_text(text + 'endpoints: []\n')
    elif edit == 'oversized':
        config.write_text('x' * 4097)
    else:
        binary.write_bytes(b'changed')
    with pytest.raises(RuntimeError):
        tunnel.checked_agent(root)


def test_tunnel_command_has_only_fixed_origin_upstream_and_private_config(agent):
    _, binary, config = agent
    command = tunnel.tunnel_command(binary, config)
    assert command == [str(binary), 'http', 'http://127.0.0.1:8515',
                       '--url=https://secular-bundle-uncapped.ngrok-free.dev',
                       '--config=' + str(config), '--inspect=false', '--log=false']
    assert not any('authtoken' in item for item in command)


def test_no_implicit_endpoint_start(monkeypatch):
    monkeypatch.setattr(tunnel, 'verify_isolation', lambda: pytest.fail('No identity or network access without explicit mode'))
    with pytest.raises(SystemExit) as exc:
        tunnel.main([])
    assert exc.value.code == 2


def test_check_only_does_not_execute_agent(agent, monkeypatch, capsys):
    root, binary, config = agent
    monkeypatch.setattr(tunnel, 'verify_isolation', lambda: {'account_sid': 'expected'})
    monkeypatch.setattr(tunnel, 'prepare_environment', lambda: None)
    monkeypatch.setattr(tunnel, 'checked_agent', lambda: (binary, config))
    monkeypatch.setattr(tunnel, 'checked_worker', lambda sid: 123)
    monkeypatch.setattr(tunnel, 'run_bounded', lambda *a, **k: pytest.fail('Check-only must not connect an agent'))
    assert tunnel.main(['--check-only']) == 0
    assert 'No endpoint was started' in capsys.readouterr().out
