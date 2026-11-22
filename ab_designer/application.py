"""Project-owned controller process and optional application panel."""
from collections import deque
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit


class Application:
    def __init__(self, project, env_path, workspace_url, workspace_token, on_exit):
        self.project = Path(project).resolve()
        self.env_path = str(Path(env_path or self.project / '.env').resolve())
        self.url, self.token, self.on_exit = workspace_url, workspace_token, on_exit
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.process = None
        self.stopping = False
        self.session = None
        self.panel = {'title': 'Application', 'buttons': [], 'values': {}, 'url': ''}
        self.events = deque()
        self.logs = deque(maxlen=400)
        self.exit_code = None
        self.error = None
        self.config = self._read_config()

    def _read_config(self):
        path = self.project / 'ab.workspace.json'
        if path.is_file():
            try: return self.validate(json.loads(path.read_text()).get('application', {}))
            except (OSError, ValueError, TypeError):
                self.error = 'Check the application settings in ab.workspace.json.'
        candidates = [name for name in ('run.py', 'main.py', 'app.py') if (self.project/name).is_file()]
        entry = candidates[0] if len(candidates) == 1 else 'run.sh' if not candidates and (self.project/'run.sh').is_file() else ''
        return {'entry': entry, 'arguments': 'ui' if entry == 'run.sh' else '', 'python': '', 'ui': ''}

    def validate(self, value):
        if not isinstance(value, dict) or set(value) - {'entry', 'arguments', 'python', 'ui'}:
            raise ValueError('Application settings need entry, arguments, python and ui fields.')
        result = {k: value.get(k, '') for k in ('entry','arguments','python','ui')}
        if any(not isinstance(v, str) for v in result.values()):
            raise ValueError('Application settings must be text.')
        if result['entry']:
            path = (self.project/result['entry']).resolve()
            if not path.is_relative_to(self.project) or not path.is_file():
                raise ValueError('Choose an application file inside this project.')
            if path.suffix not in ('.py', '.sh'):
                raise ValueError('Choose a Python entry file or an existing .sh launcher.')
        shlex.split(result['arguments'])
        if result['ui']:
            url = urlsplit(result['ui'])
            if url.scheme:
                if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:
                    raise ValueError('Use an HTTP(S) application URL without credentials.')
            else:
                path = (self.project/result['ui']).resolve()
                if not path.is_relative_to(self.project) or not path.is_file() or path.suffix.lower() not in ('.html','.htm'):
                    raise ValueError('Choose a project HTML file or your application URL.')
        return result

    def configure(self, value):
        result = self.validate(value)
        with self.lock:
            if self.running: raise ValueError('Stop the application before changing its launch settings.')
            path = self.project/'ab.workspace.json'
            data = json.loads(path.read_text()) if path.is_file() else {}
            if not isinstance(data, dict): raise ValueError('ab.workspace.json must contain an object.')
            data['application'] = result
            temporary = path.with_name('.ab.workspace.json.tmp')
            temporary.write_text(json.dumps(data, indent=2)+'\n')
            temporary.chmod(0o600); temporary.replace(path)
            self.config = result; self.error = None
        return self.status()

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def _ui_url(self):
        ui = self.panel.get('url') or self.config['ui']
        return ui if urlsplit(ui).scheme else '/application/content/' if ui else ''

    def status(self):
        with self.lock:
            return {'configured': bool(self.config['entry']), 'running': self.running,
                    'config': dict(self.config), 'project': str(self.project),
                    'exit_code': self.exit_code, 'error': self.error, 'session': self.session,
                    'panel': {**self.panel, 'url': self._ui_url()}, 'logs': list(self.logs)}

    def start(self, device_config=None):
        with self.lock:
            if self.running: return self.status()
            cfg = self.validate(self.config)
            if not cfg['entry']: return self.status()
            entry = self.project/cfg['entry']
            python = cfg['python'] or next((str(p) for p in
                (self.project/'.venv/bin/python', self.project/'.venv/Scripts/python.exe') if p.is_file()), sys.executable)
            command = [python, '-u', str(entry)] if entry.suffix == '.py' else ['bash', str(entry)]
            command += shlex.split(cfg['arguments'])
            self.session = secrets.token_urlsafe(24)
            self.panel = {'title': 'Application', 'buttons': [], 'values': {}, 'url': ''}
            self.events.clear(); self.logs.clear(); self.error = None; self.exit_code = None
            env = os.environ.copy()
            env.update(AB_BACKEND='primalux', AB_UNIFIED_TARGET='primalux',
                       AB_UNIFIED_PROJECT=str(self.project), AB_ENV=self.env_path,
                       AB_WORKSPACE_URL=self.url, AB_WORKSPACE_TOKEN=self.token,
                       AB_APPLICATION_SESSION=self.session, PYTHONUNBUFFERED='1')
            if device_config:
                env.update(AB_DEVICE_URL=device_config['url'], AB_DEVICE_API_KEY=device_config['api_key'])
            options = {'start_new_session':True} if os.name != 'nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}
            self.stopping = False
            process = subprocess.Popen(command, cwd=self.project, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', bufsize=1, **options)
            self.process = process
            secret = (device_config or {}).get('api_key', '')
            threading.Thread(target=self._observe, args=(process,secret), daemon=True).start()
            return self.status()

    def _read_output(self, process, secret):
        for line in process.stdout:
            text = line.rstrip()
            if secret: text = text.replace(secret, '[device key]')
            text = text.replace(self.token, '[workspace token]')
            with self.lock: self.logs.append(text)
        process.stdout.close()

    def _observe(self, process, secret):
        threading.Thread(target=self._read_output,args=(process,secret),daemon=True).start()
        code = process.wait()
        with self.lock:
            if process is not self.process: return
            self.exit_code = code
            self.panel['buttons'] = []; self.events.clear()
            stop_brain = not self.stopping
            if code and stop_brain: self.error = f'Application exited with code {code}. See the console below.'
            self.condition.notify_all()
        if stop_brain: self.on_exit()

    def stop(self):
        with self.lock:
            process = self.process
            if not self.running: return
            self.stopping = True
        try:
            if os.name == 'nt': process.send_signal(signal.CTRL_BREAK_EVENT)
            else: os.killpg(process.pid, signal.SIGINT)
            try: process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                if os.name == 'nt': process.terminate()
                else: os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt': process.kill()
                    else: os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=4)
        except ProcessLookupError: pass

    def attach(self, payload):
        with self.lock:
            session = payload.get('session')
            if not isinstance(session, str) or not session: raise ValueError('Application session required.')
            if self.running and session != self.session: raise ValueError('Another application is running in this workspace.')
            if self.session != session:
                self.session = session; self.events.clear()
            buttons = payload.get('buttons', [])
            if not isinstance(buttons,list) or any(not isinstance(b,dict) or set(b)!={'id','label'} or not all(isinstance(v,str) and v for v in b.values()) for b in buttons):
                raise ValueError('Application buttons need an id and label.')
            if len({b['id'] for b in buttons}) != len(buttons): raise ValueError('Application button ids must be distinct.')
            url = payload.get('url','')
            if url:
                parsed = urlsplit(url)
                if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('Application interface needs an HTTP(S) URL without credentials.')
            if not isinstance(payload.get('values', {}),dict): raise ValueError('Application values must be an object.')
            self.panel = {'title': str(payload.get('title') or 'Application'), 'buttons': buttons,
                          'values': payload.get('values', {}), 'url': url}
            if not isinstance(self.panel['values'],dict): raise ValueError('Application values must be an object.')
            return {'ok': True, 'session': self.session}

    def action(self, payload):
        with self.condition:
            if payload.get('session') != self.session: raise ValueError('The application changed. Try the current controls.')
            action = payload.get('action')
            if action not in {b['id'] for b in self.panel['buttons']}: raise ValueError('Unknown application command.')
            self.events.append({'id': secrets.token_hex(12), 'action': action})
            self.condition.notify_all()
            return {'ok': True}

    def poll(self, session):
        with self.condition:
            if session != self.session: raise ValueError('Application session changed.')
            if not self.events: self.condition.wait(10)
            if session != self.session: raise ValueError('Application session changed.')
            events = list(self.events); self.events.clear()
            return {'events': events}

    def publish(self, payload):
        with self.lock:
            if payload.get('session') != self.session: raise ValueError('Application session changed.')
            if not isinstance(payload.get('values'),dict): raise ValueError('Application values must be an object.')
            self.panel['values'].update(payload['values'])
            return {'ok': True}

    def asset(self, relative):
        ui = self.config['ui']
        if not ui or urlsplit(ui).scheme: return None
        entry = (self.project/ui).resolve()
        path = (entry.parent/(relative or entry.name)).resolve()
        if not path.is_relative_to(entry.parent) or any(p.startswith('.') for p in Path(relative).parts): return None
        if path.suffix.lower() not in ('.html','.htm','.css','.js','.json','.png','.jpg','.jpeg','.svg','.webp','.ico','.woff','.woff2'): return None
        return path if path.is_file() else None
