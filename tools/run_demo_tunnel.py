"""Owner-approved fixed ngrok endpoint, in its own bounded Windows Job Object."""
import argparse
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import subprocess

from src.demo_assets import safe_path
from src.demo_isolation import ROOT, verify_isolation, prepare_environment, _windows_directory
from src.deployment_gate import ORIGIN
from src.windows_job import run_bounded

AGENT_SHA256 = 'd339bcbd0713233337e860163f5249eea679cf26750a5700510dbc241d201748'
CONFIG_TEMPLATE = """version: 3
agent:
  authtoken: '<redacted>'
  web_addr: false
  console_ui: false
  remote_management: false
  update_check: false
  inspect_db_size: -1
  log: false
  log_level: error
"""


def checked_agent(root=ROOT):
    directory = safe_path(root / 'ngrok')
    binary, config = safe_path(directory / 'ngrok.exe'), safe_path(directory / 'ngrok.yml')
    if hashlib.sha256(binary.read_bytes()).hexdigest() != AGENT_SHA256:
        raise RuntimeError('The reviewed agent binary changed.')
    if config.stat().st_size > 4096:
        raise RuntimeError('Invalid private agent configuration.')
    text = config.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    token_line = r"(?m)^  authtoken: '[A-Za-z0-9_./+\-]{20,200}'$"
    if len(re.findall(token_line, text)) != 1 or re.sub(token_line, "  authtoken: '<redacted>'", text) != CONFIG_TEMPLATE:
        raise RuntimeError('Private agent settings differ from the reviewed fixed profile.')
    return binary, config


def checked_worker(expected_sid):
    """Read only process identity; no credentials, environment or user questions."""
    command = r"""$ErrorActionPreference='Stop'
$listeners=@(Get-NetTCPConnection -LocalPort 8515 -State Listen)
if($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1'){throw 'Unexpected listener'}
$worker=Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)"
$owner=Invoke-CimMethod -InputObject $worker -MethodName GetOwnerSid
[pscustomobject]@{pid=$worker.ProcessId;image=$worker.ExecutablePath;command=$worker.CommandLine;sid=$owner.Sid;result=$owner.ReturnValue}|ConvertTo-Json -Compress
"""
    shell = _windows_directory() / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Cannot verify the isolated local worker. No endpoint was started.')
    worker = json.loads(result.stdout)
    if worker['result'] != 0 or worker['sid'] != expected_sid or Path(worker['image']) != ROOT / 'python/python.exe' or 'tools.run_bounded_demo' not in worker['command']:
        raise RuntimeError('The listener is not the isolated bounded demo worker.')
    connection = HTTPConnection('127.0.0.1', 8515, timeout=5)
    try:
        connection.request('GET', '/_stcore/health')
        response = connection.getresponse()
        if response.status != 200 or response.read(1024) != b'ok':
            raise RuntimeError('The isolated local worker is not healthy.')
    finally:
        connection.close()
    return worker['pid']


def tunnel_command(binary, config):
    return [str(binary), 'http', 'http://127.0.0.1:8515', '--url=' + ORIGIN,
            '--config=' + str(config), '--inspect=false', '--log=false']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--check-only', action='store_true')
    choice.add_argument('--approved-public-exposure', action='store_true',
                        help='Use only after explicit final owner approval of public exposure.')
    args = parser.parse_args(argv)
    identity = verify_isolation()
    prepare_environment()
    binary, config = checked_agent()
    checked_worker(identity['account_sid'])
    if args.check_only:
        print('Isolated worker and fixed private agent profile verified. No endpoint was started.')
        return 0
    print('Owner-approved endpoint:', ORIGIN, flush=True)
    print('Agent cap: 512 MiB, 25% aggregate CPU, one process. Ctrl+C closes this owned endpoint.', flush=True)
    try:
        return run_bounded(tunnel_command(binary, config), cwd=ROOT, memory_mb=512)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
