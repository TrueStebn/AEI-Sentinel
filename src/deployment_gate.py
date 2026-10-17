"""Bounded, anonymous ASGI admission. No client identity or question is stored."""
import asyncio
from collections import deque
from contextlib import contextmanager
import json
import math
from pathlib import Path
import time

from src.demo_assets import safe_path
from src.demo_limits import DemoBusy, file_lock

ORIGIN = 'https://secular-bundle-uncapped.ngrok-free.dev'
RATES = {'http': (240, 3600), 'connect': (12, 60),
         'message': (120, 900), 'download': (20, 120)}
FILES = {'owner.json', 'state.json', 'state.tmp', 'state.lock',
         *(f'ws-{i}.lock' for i in range(4))}
MAX_FRAME = 1024 * 1024
HTTP_ACTIVE = 8
STATIC_PENDING = 56
STATIC_WAIT_SECONDS = 5


class AdmissionStore:
    """Minute buckets persist across restarts; OS locks never wait or queue."""
    def __init__(self, root):
        self.root = safe_path(root)
        self.verify()

    @classmethod
    def prepare(cls, root):
        root = Path(root).absolute()
        safe_path(root.parent)
        if not root.exists():
            root.mkdir()
            (root / 'owner.json').write_text('{"format":1,"purpose":"aei-admission"}')
            (root / 'state.json').write_text(json.dumps(dict(clock=0, buckets={k: [] for k in RATES}, cooldown=[0]*4)))
            for name in FILES - {'owner.json', 'state.json', 'state.tmp'}:
                (root / name).write_bytes(b'0')
        return cls(root)

    def verify(self):
        names = {p.name for p in self.root.iterdir()}
        if not FILES - {'state.tmp'} <= names or not names <= FILES:
            raise DemoBusy('Admission storage is incomplete.')
        for path in self.root.iterdir():
            safe_path(path)
            if not path.is_file() or path.stat().st_size > 16384:
                raise DemoBusy('Admission storage failed validation.')
        if json.loads((self.root / 'owner.json').read_text()) != dict(format=1, purpose='aei-admission'):
            raise DemoBusy('Invalid admission owner marker.')

    @contextmanager
    def state(self, now):
        self.verify()
        with file_lock(self.root / 'state.lock'):
            value = json.loads((self.root / 'state.json').read_text())
            number = lambda n: type(n) in (int, float) and math.isfinite(n) and n >= 0
            if set(value) != {'clock', 'buckets', 'cooldown'} or not number(value['clock']) or not number(now) or now < value['clock']:
                raise DemoBusy('Admission clock requires an owner check.')
            if set(value['buckets']) != set(RATES) or len(value['cooldown']) != 4 or not all(number(n) for n in value['cooldown']):
                raise DemoBusy('Invalid admission state.')
            minute = int(now // 60)
            for category, rows in value['buckets'].items():
                if not isinstance(rows, list) or len(rows) > 61:
                    raise DemoBusy('Invalid admission buckets.')
                for row in rows:
                    if not isinstance(row, list) or len(row) != 2 or any(type(n) is not int for n in row) or not 0 <= row[0] <= minute or not 1 <= row[1] <= RATES[category][1]:
                        raise DemoBusy('Invalid admission bucket.')
                if [r[0] for r in rows] != sorted(set(r[0] for r in rows)):
                    raise DemoBusy('Duplicate admission bucket.')
                value['buckets'][category] = [r for r in rows if r[0] >= minute - 60]
            yield value
            value['clock'] = now
            encoded = json.dumps(value, separators=(',', ':')).encode()
            if len(encoded) > 16384:
                raise DemoBusy('Admission storage limit reached.')
            (self.root / 'state.tmp').write_bytes(encoded)
            (self.root / 'state.tmp').replace(self.root / 'state.json')

    def charge(self, category, now=None):
        now = time.time() if now is None else now
        minute = int(now // 60)
        with self.state(now) as state:
            rows = state['buckets'][category]
            short, long = RATES[category]
            # Include the previous whole minute: conservative sliding-window bound.
            if sum(n for m, n in rows if m >= minute - 1) >= short or sum(n for m, n in rows) >= long:
                raise DemoBusy('The shared demo budget is exhausted. Retry later.')
            if rows and rows[-1][0] == minute:
                rows[-1][1] += 1
            else:
                rows.append([minute, 1])

    @contextmanager
    def connection(self):
        for slot in range(4):
            lock = file_lock(self.root / f'ws-{slot}.lock')
            try:
                lock.__enter__()
            except DemoBusy:
                continue
            try:
                with self.state(time.time()) as state:
                    available = state['cooldown'][slot] <= time.time()
                    if available:
                        # A crash must not immediately recycle an in-flight script.
                        state['cooldown'][slot] = time.time() + 945
                if not available:
                    continue
                try:
                    yield
                finally:
                    with self.state(time.time()) as state:
                        state['cooldown'][slot] = time.time() + 45
                return
            finally:
                lock.__exit__(None, None, None)
        raise DemoBusy('All four demo session slots are occupied or cooling down.')


class AdmissionGate:
    def __init__(self, app, *, store, port=8515):
        self.app, self.store = app, store
        self.hosts = {'secular-bundle-uncapped.ngrok-free.dev', f'127.0.0.1:{port}', f'localhost:{port}'}
        self.origins = {ORIGIN, f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
        self.http_active = 0
        self.http_slots = asyncio.Semaphore(HTTP_ACTIVE)
        self.static_waiting = 0

    async def reject(self, scope, receive, send, status=429):
        if scope['type'] == 'websocket':
            if 'websocket.http.response' in scope.get('extensions', {}):
                await send(dict(type='websocket.http.response.start', status=status, headers=[(b'retry-after', b'60')]))
                await send(dict(type='websocket.http.response.body', body=b'Demo admission limit. Retry later.'))
            else:
                await send(dict(type='websocket.close', code=1008))
        else:
            from starlette.responses import PlainTextResponse
            await PlainTextResponse('Demo admission limit or request policy. Retry later.', status_code=status,
                                    headers={'Retry-After': '60'})(scope, receive, send)

    async def __call__(self, scope, receive, send):
        kind = scope['type']
        if kind not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        values = lambda key: [v for k, v in headers if k.lower() == key]
        hosts, origins = values(b'host'), values(b'origin')
        if len(hosts) != 1 or hosts[0].decode('latin1').lower() not in self.hosts or len(origins) > 1 or (origins and origins[0].decode('latin1') not in self.origins) or (kind == 'websocket' and not origins):
            return await self.reject(scope, receive, send, 403)
        if sum(len(k)+len(v)+4 for k,v in headers) > 16384 or len(scope.get('raw_path', b''))+len(scope.get('query_string', b'')) > 2048:
            return await self.reject(scope, receive, send, 413)
        path = scope.get('path', '')
        scope = dict(scope)
        scope['headers'] = [(k,v) for k,v in headers if not k.lower().startswith((b'x-forwarded-', b'cf-access-')) and k.lower() not in (b'forwarded', b'x-real-ip')]
        try:
            self.store.charge('http')
            if kind == 'websocket':
                if path != '/_stcore/stream':
                    return await self.reject(scope, receive, send, 403)
                self.store.charge('connect')
                # Session IDs are not credentials. Retain protocol and XSRF only.
                cleaned = []
                for k,v in scope['headers']:
                    if k.lower() == b'sec-websocket-protocol':
                        v = b','.join(v.split(b',')[:2])
                    cleaned.append((k,v))
                scope['headers'] = cleaned
                scope['subprotocols'] = scope.get('subprotocols', [])[:2]
                with self.store.connection():
                    await self.websocket(scope, receive, send)
                return
            if scope['method'] not in ('GET', 'HEAD', 'OPTIONS') or not (path in ('/', '/favicon.ico', '/favicon.png', '/_stcore/health', '/_stcore/host-config') or path.startswith(('/static/', '/media/'))):
                return await self.reject(scope, receive, send, 403)
            if values(b'transfer-encoding') or any(v != b'0' for v in values(b'content-length')):
                return await self.reject(scope, receive, send, 413)
            if path.startswith('/media/'):
                self.store.charge('download')
        except (DemoBusy, ValueError, OSError, KeyError, TypeError):
            return await self.reject(scope, receive, send)
        if self.http_slots.locked():
            # Module preloads arrive together through the reverse proxy. Keep
            # eight active handlers and only a short, bounded static-file wait.
            # Analysis, downloads and arbitrary routes never join this queue.
            if not path.startswith('/static/') or self.static_waiting >= STATIC_PENDING:
                return await self.reject(scope, receive, send, 503)
            self.static_waiting += 1
            try:
                await asyncio.wait_for(self.http_slots.acquire(), STATIC_WAIT_SECONDS)
            except TimeoutError:
                return await self.reject(scope, receive, send, 503)
            finally:
                self.static_waiting -= 1
        else:
            await self.http_slots.acquire()
        self.http_active += 1
        try:
            # Bounded socket lifetime. Uvicorn also bounds connections and queues.
            async with asyncio.timeout(30):
                await self.app(scope, receive, send)
        except TimeoutError:
            pass
        finally:
            self.http_active -= 1
            self.http_slots.release()

    async def websocket(self, scope, receive, send):
        events = deque()
        count = 0
        class Rejected(Exception):
            pass
        async def limited_receive():
            nonlocal count
            event = await receive()
            if event['type'] == 'websocket.receive':
                body = event.get('bytes') or (event.get('text') or '').encode()
                now = time.monotonic()
                while events and now - events[0] >= 60:
                    events.popleft()
                if len(body) > MAX_FRAME or len(events) >= 30 or count >= 300:
                    await send(dict(type='websocket.close', code=1009 if len(body) > MAX_FRAME else 1008))
                    raise Rejected()
                try:
                    self.store.charge('message')
                except (DemoBusy, ValueError, OSError, KeyError, TypeError):
                    await send(dict(type='websocket.close', code=1013))
                    raise Rejected()
                events.append(now)
                count += 1
            return event
        try:
            async with asyncio.timeout(900):
                await self.app(scope, limited_receive, send)
        except TimeoutError:
            await send(dict(type='websocket.close', code=1001))
        except Rejected:
            pass
