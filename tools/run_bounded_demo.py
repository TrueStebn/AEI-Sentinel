"""Local admission/resource check. This does not certify filesystem isolation."""
import os


def main():
    from src.windows_job import verify_current_job
    print('Verified kernel limits:', verify_current_job(), flush=True)
    if os.environ.get('AEI_ISOLATED_DEMO') == '1':
        from src.demo_isolation import verify_isolation
        print('Verified isolated account:', verify_isolation(), flush=True)
    from src.config import PROJECT_ROOT
    from src.demo_assets import verified_assets
    from src.demo_limits import prepare_controls, file_lock
    from src.deployment_gate import AdmissionGate, AdmissionStore, ORIGIN
    from tools.run_public_demo import PublicHTTPPolicy, server_options
    os.environ.update(AEI_PUBLIC_DEMO='1', AEI_OLLAMA_MODEL='qwen3:8b',
                      AEI_OLLAMA_URL='http://127.0.0.1:11434')
    verified_assets()
    controls = prepare_controls()
    store = AdmissionStore.prepare(PROJECT_ROOT / 'data/runtime/demo-admission')
    with file_lock(controls / 'server.lock'):
        options = server_options(8515, ORIGIN)
        options.update({'server.disconnectedSessionTTL': 0, 'server.maxMessageSize': 1,
                        'global.developmentMode': False, 'global.e2eTest': False,
                        'server.scriptHealthCheckEnabled': False, 'server.baseUrlPath': ''})
        from streamlit.web import bootstrap
        bootstrap.load_config_options(options)
        from streamlit import config, App
        for key, expected in options.items():
            if config.get_option(key) != expected:
                raise RuntimeError('Deployment configuration was not applied: ' + key)
        from starlette.middleware import Middleware
        import uvicorn
        app = App(str(PROJECT_ROOT / 'app.py'), middleware=[Middleware(AdmissionGate, store=store), Middleware(PublicHTTPPolicy)])
        status = 'ISOLATED LOCAL SERVER' if os.environ.get('AEI_ISOLATED_DEMO') == '1' else 'LOCAL CHECK ONLY; personal-file isolation is not certified'
        print(status + ': http://127.0.0.1:8515. No tunnel is started. Stop the owned supervisor to stop the worker.', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=8515, workers=1, proxy_headers=False,
                    access_log=False, log_level='error', server_header=False,
                    limit_concurrency=64, backlog=64, timeout_keep_alive=1,
                    timeout_graceful_shutdown=5, ws_max_size=1048576, ws_max_queue=4,
                    ws_per_message_deflate=False, h11_max_incomplete_event_size=16384)


if __name__ == '__main__':
    main()
