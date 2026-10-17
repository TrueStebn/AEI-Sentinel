"""Foreground localhost-only public-policy server. Ctrl+C stops this server only."""
import argparse
import os
from urllib.parse import urlsplit

from src.config import PROJECT_ROOT
from src.demo_assets import verified_assets
from src.demo_limits import prepare_controls, file_lock
from src.model_identity import verify_identity, LocalModelError


class PublicHTTPPolicy:
    """Reject uploads and unsafe HTTP before reading bodies, even empty/chunked files."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            path = scope.get('path', '').rstrip('/')
            if scope['method'] not in ('GET', 'HEAD', 'OPTIONS') or path == '/_stcore/upload_file' or path.startswith('/_stcore/upload_file/') or path == '/app/static' or path.startswith('/app/static/'):
                from starlette.responses import PlainTextResponse
                response = PlainTextResponse('Public Demo Mode is read-only. Uploads are disabled.', status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def server_options(port=8515, origin=None):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Choose a nonprivileged local port.')
    hosts = ['127.0.0.1', 'localhost']
    origins = [f'http://127.0.0.1:{port}', f'http://localhost:{port}']
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.path not in ('', '/') or parsed.query or parsed.fragment or '*' in origin:
            raise ValueError('The future origin must be one exact HTTPS hostname without credentials or a path.')
        hosts.append(parsed.hostname)
        origins.append('https://' + parsed.hostname)
    return {
        'server.address': '127.0.0.1', 'server.port': port, 'server.headless': True,
        'server.enableCORS': True, 'server.enableXsrfProtection': True,
        'server.allowedHosts': hosts, 'server.corsAllowedOrigins': origins,
        'server.enableStaticServing': False, 'server.maxUploadSize': 0,
        'server.maxMessageSize': 16, 'server.maxWidgetStateSize': 1,
        'server.disconnectedSessionTTL': 20, 'server.fileWatcherType': 'none',
        'server.runOnSave': False, 'server.enableWebsocketCompression': False,
        'server.trustedUserHeaders': {}, 'client.showErrorDetails': 'none',
        'client.toolbarMode': 'minimal',
        'browser.gatherUsageStats': False, 'logger.level': 'error',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8515)
    parser.add_argument('--origin', help='Exact future HTTPS origin; configures validation only, never a tunnel.')
    args = parser.parse_args()
    options = server_options(args.port, args.origin)
    os.environ['AEI_PUBLIC_DEMO'] = '1'
    os.environ['AEI_OLLAMA_MODEL'] = 'qwen3:8b'
    os.environ['AEI_OLLAMA_URL'] = 'http://127.0.0.1:11434'
    verified_assets()
    controls = prepare_controls()
    with file_lock(controls / 'server.lock'):
        try:
            identity = verify_identity()
            print(f"Verified {identity['model']} / {identity['quantization']} / pinned digest.", flush=True)
        except LocalModelError as exc:
            print(str(exc), flush=True)  # Manual Search remains usable.
        from streamlit.web import bootstrap
        bootstrap.load_config_options(options)
        from streamlit import config
        for key, expected in options.items():
            if config.get_option(key) != expected:
                raise RuntimeError(f'The public server configuration was not applied: {key}')
        print(f'Public Demo Mode: http://127.0.0.1:{args.port} (local only). Ctrl+C stops this server.', flush=True)
        from streamlit import App
        from starlette.middleware import Middleware
        App(str(PROJECT_ROOT / 'app.py'), middleware=[Middleware(PublicHTTPPolicy)]).run(config=options)


if __name__ == '__main__':
    main()
