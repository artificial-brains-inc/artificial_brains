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
    """Attach to the persistent SDK workspace; closing a run leaves its tab open."""
    def __init__(self, client, design=None):
        threading.Thread(target=self._open,args=(client,design),daemon=True).start()

    def _open(self, client, design):
        try:
            from ab_designer.workspace import open_workspace
            self.url=open_workspace(design,client=client)
        except Exception as exc:
            print(f'[AB] Workspace unavailable: {exc}',flush=True)

    def close(self):
        pass  # The workspace belongs to the project, not to one controller run.
