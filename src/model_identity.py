"""Verify the pinned local model without loading/downloading weights or using cloud APIs."""

import http.client
import json
from time import perf_counter

MODEL = 'qwen3:8b'
DIGEST = '500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41'
QUANTIZATION = 'Q4_K_M'


class LocalModelError(RuntimeError):
    pass


def request_json(host, port, path, *, payload=None, deadline=None, limit=65536):
    deadline = deadline or perf_counter() + 5
    remaining = deadline - perf_counter()
    if remaining <= 0:
        raise LocalModelError('Local AI timed out. Manual Search remains available.')
    connection = http.client.HTTPConnection(host, port, timeout=remaining)
    response = None
    try:
        body = None if payload is None else json.dumps(payload).encode()
        connection.request('GET' if body is None else 'POST', path, body, {'Content-Type': 'application/json'})
        remaining = deadline - perf_counter()
        if remaining <= 0:
            raise TimeoutError()
        if getattr(connection, 'sock', None) is not None:
            connection.sock.settimeout(remaining)
        response = connection.getresponse()
        if response.status == 404:
            raise LocalModelError('The pinned Qwen3 8B model is missing. Ask the owner to install it explicitly; Manual Search remains available.')
        if response.status != 200:
            raise LocalModelError('Local AI could not complete the request. Manual Search remains available.')
        parts, size = [], 0
        while True:
            remaining = deadline - perf_counter()
            if remaining <= 0:
                raise TimeoutError()
            # HTTPConnection detaches its socket when Connection: close is sent,
            # but the response still owns it until its body is consumed.
            stream = getattr(response, 'fp', None)
            active_socket = getattr(connection, 'sock', None) or getattr(getattr(stream, 'raw', None), '_sock', None)
            if active_socket is not None:
                active_socket.settimeout(remaining)
            block = response.read1(min(4096, limit + 1 - size))
            if not block:
                break
            parts.append(block)
            size += len(block)
            if size > limit:
                raise LocalModelError('Local AI returned an oversized response. Manual Search remains available.')
        value = json.loads(b''.join(parts))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except TimeoutError:
        raise LocalModelError('Local AI timed out. Manual Search remains available.') from None
    except (OSError, http.client.HTTPException):
        raise LocalModelError('Local AI is unavailable. Start the verified local Ollama service or use Manual Search.') from None
    except (ValueError, TypeError, KeyError):
        raise LocalModelError('Local AI returned malformed output. Manual Search remains available.') from None
    finally:
        if response is not None and hasattr(response, 'close'):
            response.close()
        connection.close()


def verify_identity(*, host='127.0.0.1', port=11434, deadline=None):
    deadline = deadline or perf_counter() + 5
    tags = request_json(host, port, '/api/tags', deadline=deadline, limit=131072)
    entries = tags.get('models', [])
    if not isinstance(entries, list):
        raise LocalModelError('Local AI returned malformed model metadata. Manual Search remains available.')
    item = next((item for item in entries if isinstance(item, dict) and item.get('name') == MODEL), None)
    if item is None:
        raise LocalModelError('The pinned Qwen3 8B model is missing. Manual Search remains available.')
    if item.get('digest') != DIGEST:
        raise LocalModelError('Qwen3 8B identity mismatch. AI Search is disabled until the owner verifies the expected digest. Use Manual Search.')
    shown = request_json(host, port, '/api/show', payload={'model': MODEL}, deadline=deadline, limit=262144)
    details = shown.get('details', {})
    if not isinstance(details, dict) or details.get('quantization_level') != QUANTIZATION or details.get('family') != 'qwen3':
        raise LocalModelError('Qwen3 8B quantization mismatch. Expected Q4_K_M; use Manual Search and contact the owner.')
    return dict(model=MODEL, digest=DIGEST, quantization=QUANTIZATION, endpoint='127.0.0.1:11434')
