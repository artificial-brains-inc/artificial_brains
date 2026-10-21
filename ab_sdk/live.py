"""Authenticated device push transport and an optional local neuron viewer."""
from __future__ import annotations
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
from .output_stream import OutputStream


class LiveOutputStream(OutputStream):
    def __init__(self, transport, *, replay=False):
        super().__init__(transport, compile_id='primalux-local')
        self.replay = replay
        self.cursor = None
        self.error = None
        self._response = None
        self._closed = threading.Event()

    def stop(self):
        self._running = False
        self._closed.set()
        if self._response is not None:
            self._response.close()
        super().stop()

    def _records(self):
        client = self.python_client.device
        params = {'replay': int(self.replay), 'cursor': self.cursor or ''}
        with client.http.stream('GET', '/api/brain/stream', params=params,
                                timeout=httpx.Timeout(60, connect=10)) as response:
            self._response = response
            try:
                if response.status_code != 200:
                    raise RuntimeError(f'Live outputs HTTP {response.status_code}. '
                                       'Check device access and install Factory v0.6.12 or later.')
                kind, data, cursor = 'message', [], None
                for line in response.iter_lines():
                    if not self._running:
                        return
                    if not line:
                        if data:
                            yield kind, json.loads('\n'.join(data)), cursor
                        kind, data, cursor = 'message', [], None
                    elif line.startswith('event:'):
                        kind = line[6:].strip()
                    elif line.startswith('data:'):
                        data.append(line[5:].lstrip())
                    elif line.startswith('id:'):
                        cursor = line[3:].strip()
            finally:
                self._response = None

    def _worker(self):
        while self._running:
            records = self._records()
            try:
                while self._running:
                    try:
                        kind, data, cursor = next(records)
                    except (httpx.TransportError, StopIteration):
                        if self._running:
                            print('[AB] Output connection interrupted; reconnecting from the last frame.', flush=True)
                        break
                    if kind == 'hello':
                        if self.cursor is not None and self.cursor != data['cursor']:
                            raise RuntimeError('Output cursor changed during reconnection.')
                        self.cursor = data['cursor']
                    elif kind == 'frame':
                        expected = int(self.cursor.rsplit(':', 1)[1]) + 1
                        if cursor != f"{data['epoch']}:{expected}" or data['sequence'] != expected:
                            raise RuntimeError('Output sequence gap; frames were not silently skipped.')
                        if self.cursor.split(':', 1)[0] != data['epoch']:
                            raise RuntimeError('Brain run changed. Reconnect explicitly.')
                        item = data['item']
                        if type(item.get('step')) is not int or not isinstance(item.get('outputs'), list):
                            raise RuntimeError('Invalid live output frame.')
                        self.cursor = cursor
                        self.after_step = item['step']
                        self.latest_item = item
                        for handler in list(self._handlers):
                            handler(item)
                    elif kind in ('gap', 'reset', 'error'):
                        raise RuntimeError(data.get('message', 'Live output stream ended.'))
            except Exception as exc:
                if self._running:
                    self.error = str(exc)
                    print(f'[AB] Live outputs stopped: {self.error}', flush=True)
                    self._running = False
                    for handler in list(self._control_handlers):
                        handler({'command': 'stop', 'reason': 'output_stream_error', 'error': self.error})
                return
            finally:
                records.close()
            self._closed.wait(0.5)


class TelemetryWindow:
    def __init__(self, client, design=None):
        self.responses = set()
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.server = None
        threading.Thread(target=self._open, args=(client, design), daemon=True).start()

    def _open(self, client, design):
        try:
            self._serve(client, design)
        except Exception as exc:
            if not self.closed.is_set():
                print(f'[AB] Neuron telemetry unavailable: {type(exc).__name__}', flush=True)

    def _serve(self, client, design):
        self.client = client
        response = client.http.get('/static/telemetry.html')
        response.raise_for_status()
        self.page = response.content
        self.design = None
        if design is not None:
            from .design_submit import design_sha256
            active = (client.status().get('package') or {}).get('active') or {}
            if active.get('design_sha256') == design_sha256(design):
                self.design = design
        window = self
        prefix = '/' + secrets.token_urlsafe(24) + '/'

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if (self.headers.get('Host') != window.address or
                    self.headers.get('Origin', window.origin) != window.origin or
                    not self.path.startswith(prefix)):
                    self.send_error(403)
                    return
                route = self.path[len(prefix):]
                if route == 'events':
                    params = {'telemetry': 1, 'cursor': self.headers.get('Last-Event-ID', '')}
                    try:
                        with client.http.stream('GET', '/api/brain/stream', params=params,
                                                timeout=httpx.Timeout(60, connect=10)) as upstream:
                            if upstream.status_code != 200:
                                self.send_error(upstream.status_code)
                                return
                            with window.lock:
                                window.responses.add(upstream)
                            try:
                                self.send_response(200)
                                self.send_header('Content-Type', 'text/event-stream')
                                self.send_header('Cache-Control', 'no-store')
                                self.end_headers()
                                for line in upstream.iter_lines():
                                    if window.closed.is_set():
                                        return
                                    self.wfile.write((line + '\n').encode())
                                    if not line:
                                        self.wfile.flush()
                            finally:
                                with window.lock:
                                    window.responses.discard(upstream)
                    except (OSError, httpx.HTTPError):
                        pass
                    return
                if route == '':
                    body, mime = window.page, 'text/html; charset=utf-8'
                elif route == 'design':
                    body, mime = json.dumps(window.design).encode(), 'application/json'
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.end_headers()
                self.wfile.write(body)

        with self.lock:
            if self.closed.is_set():
                return
            self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            self.server.daemon_threads = True
            self.address = f'127.0.0.1:{self.server.server_port}'
            self.origin = 'http://' + self.address
            self.url = self.origin + prefix
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
        print('[AB] Neuron telemetry: ' + self.url, flush=True)
        try:
            webbrowser.open(self.url)
        except Exception:
            pass  # The printed local URL remains usable on headless hosts.

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        with self.lock:
            responses = list(self.responses)
        for response in responses:
            response.close()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()