"""Optional application controls inside the SDK workspace."""
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit
import httpx


class ApplicationUI:
    """Register controller-owned buttons, readouts, or an existing web interface.

    Button callbacks run on the application UI thread, not the neural output
    callback. Keep callbacks short or hand work to your controller's own queue.
    """
    def __init__(self, title='Application', *, url=None):
        self.title, self.interface_url = title, url or ''
        self._buttons, self._values = {}, {}
        self._stop = threading.Event()
        self._thread = None
        self._publisher = None
        self._value_lock = threading.Lock()
        self._changed = threading.Event()
        self._pending_values = {}
        self._url = None
        self._token = None
        self._session = os.getenv('AB_APPLICATION_SESSION') or secrets.token_urlsafe(24)

    def button(self, name, label, callback):
        if not isinstance(name,str) or not name or not isinstance(label,str) or not callable(callback):
            raise ValueError('A button needs a name, label, and callback.')
        self._buttons[name] = (label, callback)
        if self._url: self._register()
        return self

    def value(self, name, value):
        with self._value_lock:
            self._values[name] = value
            self._pending_values[name] = value
        self._changed.set()
        return self

    def _post(self, action, payload):
        if not self._url: raise RuntimeError('Application UI is closed.')
        with httpx.Client(trust_env=False, timeout=15) as client:
            result = client.post(self._url+'api/application/'+action,
                headers={'X-AB-Workspace': self._token}, json={'session': self._session, **payload})
            result.raise_for_status()
            return result.json()

    def _register(self):
        return self._post('attach', {'title':self.title, 'url':self.interface_url,
            'buttons':[{'id':name,'label':label} for name,(label,_) in self._buttons.items()],
            'values':dict(self._values)})

    def start(self):
        if self._thread and self._thread.is_alive(): return self
        url, token = os.getenv('AB_WORKSPACE_URL'), os.getenv('AB_WORKSPACE_TOKEN')
        if not url or not token:
            from ab_designer.workspace import open_workspace, _directory, _existing
            project = Path(os.getenv('AB_UNIFIED_PROJECT') or Path.cwd()).resolve()
            env = Path(os.getenv('AB_ENV') or project/'.env').resolve()
            open_workspace(project=project, env_path=env, open_browser=False)
            registry = _existing(_directory(project,env))
            if not registry: raise RuntimeError('The SDK workspace could not be opened.')
            url, token = registry['url'], registry['token']
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path != '/' or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Application controls require the local SDK workspace.')
        self._url, self._token = url, token
        self._stop.clear(); self._register()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        self._publisher = threading.Thread(target=self._publish_values,daemon=True)
        self._publisher.start()
        return self

    def _listen(self):
        while not self._stop.is_set():
            try:
                result = self._post('poll', {})
                for event in result.get('events', []):
                    if self._stop.is_set(): return
                    entry = self._buttons.get(event.get('action'))
                    if entry:
                        try: entry[1]()
                        except Exception as exc:
                            self.value('Last command', f'{entry[0]} failed: {type(exc).__name__}')
            except (httpx.HTTPError, ValueError, RuntimeError):
                if self._stop.wait(1): return

    def _publish_values(self):
        # Display updates never wait on the sensor or neural-output callback.
        while not self._stop.is_set():
            self._changed.wait(.25); self._changed.clear()
            if self._stop.is_set():return
            with self._value_lock:
                values=self._pending_values;self._pending_values={}
            if not values:continue
            try:self._post('publish',{'values':values})
            except (httpx.HTTPError,ValueError,RuntimeError):
                with self._value_lock:self._pending_values={**values,**self._pending_values}
                if self._stop.wait(1):return
                self._changed.set()

    def close(self):
        self._stop.set(); self._changed.set()
        if self._url:
            try: self._post('detach', {})
            except (httpx.HTTPError, ValueError, RuntimeError): pass
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=1)
        if self._publisher and self._publisher is not threading.current_thread():self._publisher.join(timeout=1)
        self._url = None

    def __enter__(self): return self.start()
    def __exit__(self, *_): self.close()
