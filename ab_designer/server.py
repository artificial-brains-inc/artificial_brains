"""Loopback-only designer with optional authenticated Prima-Lux controls."""
from __future__ import annotations
import json
import secrets
import os
import httpx
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import threading
import time
import webbrowser
from urllib.parse import urlsplit, unquote, parse_qs
import mimetypes
from typing import Any

from .model import Brain, DesignError
from .code import parse_python

STATIC = Path(__file__).parent / "static"
MIMES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
MAX_REQUEST_SIZE = 1024 * 1024


class DesignerServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, brain: Brain, *, env_path: str | Path | None = None, project: str | Path | None = None):
        super().__init__(("127.0.0.1", port), DesignerHandler)
        self.brain = brain
        self.brain_lock = threading.Lock()
        self.device_lock = threading.Lock()
        self.env_path = env_path  # Loaded server-side, never sent to the browser.
        self.ready_digest = None
        self.building = False
        self.device_config = None
        self.workspace_root = None
        self.workspace_token = secrets.token_urlsafe(32)
        self.attached_design = None
        self.tuning_binding = None
        self.tuning_missing_after_build = False
        self.project = Path(project or Path.cwd()).resolve()
        from .application import Application
        self.application = Application(self.project, self.env_path,
            f'http://127.0.0.1:{self.server_port}/', self.workspace_token, self.application_exited)

    def application_exited(self):
        with self.device_lock:
            try:
                with self.device() as device:
                    if (device.status().get('engine') or {}).get('state') == 'RUNNING':
                        device.stop_and_wait()
            except (ValueError, RuntimeError):
                self.application.error = 'Application ended; stopping the brain could not be confirmed.'

    def close_workspace(self):
        if self.application.running:
            self.application.stop()
            with self.device_lock:
                with self.device() as device:device.stop_and_wait()

    def device(self):
        from ab_sdk.primalux import PrimaLuxClient
        config = self.device_config
        return PrimaLuxClient(**config) if config else PrimaLuxClient.from_env(self.env_path or '.env')

    def save_draft(self):
        if self.workspace_root:
            target=self.workspace_root/'draft.json';temporary=target.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.brain.to_dict(),ensure_ascii=False))
            temporary.chmod(0o600);temporary.replace(target)



class DesignerHandler(BaseHTTPRequestHandler):
    server: DesignerServer
    server_version = "ABWorkspace/0.4.7"

    def log_message(self, fmt: str, *args) -> None:
        # Quiet except for errors; avoid logging developer data.
        if args and str(args[1] if len(args) > 1 else "").startswith("5"):
            super().log_message(fmt, *args)

    def _headers(self, content_type: str, length: int, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if self.path.startswith(("/telemetry/", "/application/content/")) else "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self' http: https:; object-src 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers("application/json; charset=utf-8", len(body), status)
        self.wfile.write(body)

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        valid_hosts = (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
        if host not in valid_hosts:
            self._json({"error": "Requests must come from the local designer."}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in tuple(f"http://{h}" for h in valid_hosts):
            self._json({"error": "Cross-origin requests are not allowed."}, 403)
            return False
        return True

    def do_GET(self) -> None:
        if not self._allowed_host():
            return
        path = urlsplit(self.path).path
        if path == '/api/workspace/ping':
            if not secrets.compare_digest(self.headers.get('X-AB-Workspace',''),self.server.workspace_token):
                self._json({'error':'Workspace identity required'},403);return
            self._json({'workspace':1,'version':'0.4.7'});return
        if path == '/api/device/tuning':
            try:
                with self.server.device() as device: self._json(self._tuning_state(device))
            except (ValueError, RuntimeError) as exc:
                self._json({'available':False, 'reason':'CONNECTION', 'message':str(exc)[:300]})
            return
        if path == '/api/application/status':
            self._json(self.server.application.status()); return
        if path.startswith('/application/content/'):
            file = self.server.application.asset(unquote(path[len('/application/content/'):]))
            if file is None: self._json({'error':'Application asset not found.'},404); return
            data = file.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','SAMEORIGIN')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' http: https: ws: wss:; object-src 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(data); return
        if path == '/api/primitives/template':
            try:
                from .primitives import template
                query = parse_qs(urlsplit(self.path).query)
                result = template(query.get('kind',['motor'])[0],
                    inputs=int(query.get('inputs',['1'])[0]),
                    contradictions=int(query.get('contradictions',['0'])[0]),
                    width=int(query.get('width',['8'])[0]))
                self._json(result)
            except (ValueError,TypeError) as exc: self._json({'error':str(exc)},400)
            return
        if path == '/api/primitives':
            from .primitives import CATALOG
            self._json({'items':CATALOG});return
        if path.startswith('/telemetry/'):
            if self._telemetry(path):return
        if path == "/api/device/status":
            self._device_status()
            return
        if path in ("/api/design", "/api/health"):
            with self.server.brain_lock:
                result = {
                    "design": self.server.brain.to_dict(),
                    "code": self.server.brain.to_python(),
                    "warnings": self.server.brain.validate(),
                } if path == "/api/design" else {"ok": True, "mode": "designer", "version": "0.4.7"}
            self._json(result)
            return
        static_map = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js", "/favicon.png": "favicon.png", "/artificial-brains-wordmark.svg": "artificial-brains-wordmark.svg",
            "/workspace.js":"workspace.js", "/tuning.js":"tuning.js",
            "/telemetry/":"telemetry.html", "/telemetry/telemetry.js":"telemetry.js", "/telemetry/telemetry.css":"telemetry.css"}
        filename = static_map.get(path)
        if filename is None:
            self._json({"error": "Not found"}, 404)
            return
        file = STATIC / filename
        try:
            data = file.read_bytes()
        except OSError:
            self._json({"error": "Editor assets missing"}, 500)
            return
        self._headers(MIMES[file.suffix], len(data))
        self.wfile.write(data)

    def do_POST(self) -> None:
        if not self._allowed_host():
            return
        path = urlsplit(self.path).path
        if path.startswith('/api/application/'):
            self._application_action(path); return
        if path in ("/api/device/build", "/api/device/run", "/api/device/stop", "/api/device/reset"):
            self._device_action(path)
            return
        if path in ('/api/workspace/attach','/api/workspace/close','/api/device/tuning','/api/primitives/add','/api/primitives/extract'):
            self._workspace_action(path);return
        if path not in ("/api/design", "/api/code/apply"):
            self._json({"error": "Not found"}, 404)
            return
        length_header = self.headers.get("Content-Length")
        if not length_header or not length_header.isdigit() or int(length_header) > MAX_REQUEST_SIZE:
            self._json({"error": "Request body is missing or too large."}, 413)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self._json({"error": "Expected application/json"}, 415)
            return
        try:
            payload = json.loads(self.rfile.read(int(length_header)))
            if not isinstance(payload, dict):
                raise DesignError("Request body must be a JSON object.")
            if path == "/api/design":
                new_brain = Brain.from_dict(payload.get("design"))
            else:
                with self.server.brain_lock:
                    existing = Brain.from_dict(self.server.brain.to_dict())
                new_brain = parse_python(payload.get("code"), previous=existing)
            with self.server.brain_lock:
                self.server.brain._replace(new_brain)
                self.server.save_draft()
                data = self.server.brain.to_dict()
                code = self.server.brain.to_python()
                warnings = self.server.brain.validate()
            self._json({"design": data, "code": code, "warnings": warnings})
        except (DesignError, ValueError, TypeError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)
        except (UnicodeError, json.JSONDecodeError) as exc:
            self._json({"error": f"Invalid JSON: {exc}"}, 400)


    @staticmethod
    def _brain_digest(brain: Brain) -> str:
        from ab_sdk.design_submit import design_sha256
        return design_sha256(brain)

    @staticmethod
    def _confirmed_digest(status: dict) -> str | None:
        # A device may be RUNNING with a different installed brain. Never infer
        # identity from a display name, local history or mere READY state.
        package = status.get('package') or {}
        active = package.get('active') or {}
        engine = status.get('engine') or {}
        return (status.get('brain_design_sha256') or
                active.get('design_sha256') or engine.get('design_sha256'))

    @staticmethod
    def _structure_digest(brain):
        import hashlib
        from ab_sdk.design_submit import semantic_design
        data = semantic_design(brain)
        for node in data['nodes']: node.pop('tnorm', None)
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',',':')).encode()).hexdigest()

    def _tuning_state(self, device, status=None):
        try: result = device.tuning()
        except (ValueError, RuntimeError) as exc:
            message = str(exc)
            if 'HTTP 404' in message or 'HTTP 405' in message:
                return {'available':False,'reason':'DEVICE_UPDATE','message':'This Device Manager has no live TNorm endpoint. Install Factory 0.6.21 or later.'}
            return {'available':False,'reason':'CONNECTION','message':message[:300]}
        status = status or device.status()
        if not result.get('available'):
            if not (status.get('engine') or {}).get('has_brain'):
                return {**result,'reason':'NO_BRAIN','message':'Build and install this brain to tune its OPUs.'}
            message = str(result.get('message',''))
            if 'Build this brain once' in message:
                detail = ('The build completed, but its package has no live TNorm data. Check the server builder/compiler installation; rebuilding repeatedly will not fix that.'
                          if self.server.tuning_missing_after_build else
                          'This installed package has no live TNorm data. Build it with Brain Builder 0.4.13 and the corrected live-TNorm compiler.')
                return {**result,'reason':'PACKAGE_NO_TUNING','message':detail}
            return {**result,'reason':'DEVICE_UPDATE','message':message or 'The device runtime did not report live TNorm support.'}
        with self.server.brain_lock:
            digest = self._brain_digest(self.server.brain)
            structure = self._structure_digest(self.server.brain)
        identity = (result.get('package_id'), result.get('session_id'), structure)
        direct_match = digest in {result.get('design_sha256'),result.get('effective_design_sha256')}
        if direct_match: self.server.tuning_binding = identity
        matches = direct_match or self.server.tuning_binding == identity
        return {**result,'matches_design':matches,
                'reason':None if matches else 'DIFFERENT_DESIGN',
                'message':'' if matches else 'The canvas differs from the installed brain. Open its design to tune these OPUs.'}

    def _device_status(self) -> None:
        with self.server.brain_lock:
            current_hash = self._brain_digest(self.server.brain)
        if self.server.building:
            self._json({'connected':True,'state':'BUILDING','ready_for_run':False});return
        try:
            with self.server.device() as device:
                status = device.status()
                tuning = self._tuning_state(device,status)
            engine = status.get('engine') or {}
            state = str(engine.get('state','UNKNOWN')).upper()
            match = (tuning.get('effective_design_sha256') or self._confirmed_digest(status)) == current_hash
            self._json({'connected':True,'state':state,'has_brain':engine.get('has_brain') is True,
                        'ready_for_run':bool(match and engine.get('has_brain') and
                            (state in ('READY','STOPPED') or state=='RUNNING' and not self.server.application.running)),
                        'tuning':tuning,'application_running':self.server.application.running})
        except (ValueError,RuntimeError) as exc:
            self._json({'connected':False,'state':'DISCONNECTED','ready_for_run':False,'error':str(exc)[:300]})

    def _application_action(self,path):
        try:
            op = path.rsplit('/',1)[-1]
            if op in ('attach','poll','publish','detach') and not secrets.compare_digest(
                    self.headers.get('X-AB-Workspace',''),self.server.workspace_token):
                self._json({'error':'Application registration requires workspace identity.'},403);return
            payload = self._read_json()
            app = self.server.application
            if op == 'configure': result = app.configure(payload)
            elif op == 'attach': result = app.attach(payload)
            elif op == 'action': result = app.action(payload)
            elif op == 'poll': result = app.poll(payload.get('session'))
            elif op == 'publish': result = app.publish(payload)
            elif op == 'detach':
                with app.condition:
                    if payload.get('session') == app.session:
                        app.panel['buttons'] = []; app.events.clear(); app.condition.notify_all()
                result = {'ok':True}
            else: self._json({'error':'Unknown application action.'},404);return
            self._json(result)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
            self._json({'error':str(exc)[:500]},409)

    def _device_action(self, path: str) -> None:
        # Mutations require JSON, same-origin and local host. Credentials remain
        # only in the local SDK config; never in request bodies or responses.
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip() != 'application/json':
            self._json({'error': 'Expected application/json'}, 415)
            return
        length = self.headers.get('Content-Length', '0')
        if length != '2':  # exactly {} (no parameters or arbitrary device paths)
            self._json({'error': 'This action takes no parameters.'}, 400)
            return
        try:
            if json.loads(self.rfile.read(2)) != {}:
                raise ValueError()
        except ValueError:
            self._json({'error': 'Invalid request body.'}, 400)
            return
        if not self.server.device_lock.acquire(blocking=False):
            self._json({'error': 'Another Prima-Lux operation is in progress.'}, 409)
            return
        try:
            from ab_sdk.primalux import PrimaLuxClient, PrimaLuxError
            from ab_sdk.design_submit import build_on_device
            with self.server.brain_lock:
                current = Brain.from_dict(self.server.brain.to_dict())
            expected_hash = self._brain_digest(current)
            with self.server.device() as device:
                status = device.status()
                engine = status.get('engine') or {}
                state = str(engine.get('state', 'UNKNOWN')).upper()
                if path == '/api/device/build':
                    if state == 'RUNNING' or self.server.application.running:
                        self._json({'error': 'Stop and save the running brain before building a different one.'}, 409)
                        return
                    self.server.ready_digest = None
                    self.server.building = True
                    try:
                        result = build_on_device(device, current)
                    finally:
                        self.server.building = False
                    # Build reports READY only after installation confirmation.
                    # Run separately checks the device-confirmed design identity.
                    with self.server.brain_lock:
                        if self._brain_digest(self.server.brain) == expected_hash:
                            self.server.ready_digest = expected_hash
                    tuning = self._tuning_state(device)
                    self.server.tuning_missing_after_build = not tuning.get('available') and tuning.get('reason') == 'PACKAGE_NO_TUNING'
                    if self.server.tuning_missing_after_build: tuning = self._tuning_state(device)
                    self._json({'ok':True,'state':result['status'],'ready':result['device_ready'],'tuning':tuning})
                elif path == '/api/device/run':
                    try:tuning=device.tuning()
                    except Exception:tuning={}
                    if (tuning.get('effective_design_sha256') or self._confirmed_digest(status)) != expected_hash:
                        self._json({'error': 'Build this brain on Prima-Lux before running it.'}, 409)
                        return
                    if engine.get('has_brain') is not True or state not in ('READY', 'STOPPED', 'RUNNING'):
                        self._json({'error': 'Prima-Lux is not ready to run this brain.'}, 409)
                        return
                    acknowledgement = device.run() if state != 'RUNNING' else {'ok':True}
                    if acknowledgement.get('ok') is not True:
                        raise PrimaLuxError('Prima-Lux did not accept Run.')
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        reported = device.status()
                        if ((reported.get('engine') or {}).get('state') or '').upper() == 'RUNNING':
                            try:
                                app = self.server.application.start({'url':device.url,'api_key':device.api_key})
                            except (OSError,ValueError) as exc:
                                device.stop_and_wait()
                                raise PrimaLuxError('Application could not start: '+str(exc)) from exc
                            self._json({'ok':True,'state':'RUNNING','application':app})
                            return
                        time.sleep(.25)
                    raise PrimaLuxError('Prima-Lux did not confirm RUNNING.')
                elif path == '/api/device/reset':
                    if state not in ('READY','STOPPED'):
                        self._json({'error':'Stop and save before resetting dynamics.'},409);return
                    device.reset_dynamics()
                    deadline=time.monotonic()+60
                    while time.monotonic()<deadline:
                        status=device.status();engine=status.get('engine') or {}
                        if engine.get('state')=='READY' and not engine.get('busy'):
                            tuning=self._tuning_state(device,status)
                            if tuning.get('available') and tuning.get('matches_design'):
                                with self.server.brain_lock:
                                    data=self.server.brain.to_dict()
                                    values={n['name']:n['tnorm'] for n in tuning['opus']}
                                    for n in data['nodes']:
                                        if n['kind']=='opu' and n['name'] in values and ('tnorm' in n or values[n['name']]!=10):
                                            n['tnorm']=values[n['name']]
                                    self.server.brain._replace(Brain.from_dict(data));self.server.save_draft()
                                    tuning['code']=self.server.brain.to_python()
                            self._json({'ok':True,'state':'READY','tuning':tuning});return
                        time.sleep(.25)
                    raise PrimaLuxError('Reset did not confirm READY.')
                elif path == '/api/device/stop':
                    self.server.application.stop()
                    confirmed = device.stop_and_wait()
                    self._json({'ok':bool(confirmed.get('confirmed')),'state':'STOPPED'})
        except (ValueError, RuntimeError) as exc:
            # Only sanitized operator-level messages, no private graph or auth data.
            message = str(exc)
            public_errors = (
                'Missing AB_DEVICE_URL', 'Missing AB_DEVICE_API_KEY',
                'Cannot reach Prima-Lux',
                'Prima-Lux does not yet support building Brain designs',
                'Install the matching Device Manager update',
                'Brain build completed but',
                'Prima-Lux did not confirm RUNNING',
                'Stop was not confirmed', 'Application could not start:',
            )
            from ab_sdk.design_submit import BrainBuildError
            public_build_error = isinstance(exc, BrainBuildError)
            if not public_build_error and not any(message.startswith(prefix) for prefix in public_errors):
                message = 'Prima-Lux could not complete this operation. Check the device and try again.'
            self._json({'error': message[:2048] if public_build_error else message[:250]}, 409)
        finally:
            self.server.building = False
            self.server.device_lock.release()

    def _read_json(self):
        length=self.headers.get('Content-Length','')
        if not length.isdigit() or not 0<int(length)<=MAX_REQUEST_SIZE:
            raise ValueError('Invalid request body size.')
        if self.headers.get('Content-Type','').split(';',1)[0].strip()!='application/json':
            raise ValueError('Expected application/json.')
        self.connection.settimeout(20)
        result=json.loads(self.rfile.read(int(length)))
        if not isinstance(result,dict):raise ValueError('Expected a JSON object.')
        return result

    def _workspace_action(self,path):
        try:
            if path == '/api/workspace/attach' and not secrets.compare_digest(self.headers.get('X-AB-Workspace',''),self.server.workspace_token):
                self._json({'error':'Workspace identity required'},403);return
            payload=self._read_json()
            if path=='/api/workspace/attach':
                config=payload.get('device')
                if config:
                    from ab_sdk.primalux import PrimaLuxClient
                    with PrimaLuxClient(**config):pass
                    self.server.device_config=dict(config)
                if payload.get('design') is not None:
                    self.server.attached_design=Brain.from_dict(payload['design']).to_dict()
                self._json({'ok':True});return
            if path=='/api/workspace/close':
                self.server.close_workspace()
                self._json({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start();return
            if path=='/api/device/tuning':
                if not self.server.device_lock.acquire(False):raise ValueError('Another device operation is in progress.')
                try:
                    with self.server.device() as device:
                        fresh = self._tuning_state(device)
                        if not fresh.get('available') or not fresh.get('matches_design'):
                            self._json({'error':fresh.get('message'),'code':fresh.get('reason')},409);return
                        supplied = payload.get('state') or {}
                        if any(supplied.get(key)!=fresh.get(key) for key in ('package_id','session_id','revision')):
                            self._json({'error':'TNorm state changed; refreshing the latest values.','code':'STALE_TUNING','state':fresh},409);return
                        with self.server.brain_lock:
                            structure = self._structure_digest(self.server.brain)
                        result = device.tune(payload.get('values',{}),state=fresh)
                    with self.server.brain_lock:
                        still_matches = structure == self._structure_digest(self.server.brain)
                        if still_matches:
                            data = self.server.brain.to_dict()
                            values = {n['name']:n['tnorm'] for n in result['opus']}
                            for n in data['nodes']:
                                if n['kind']=='opu' and n['name'] in values:
                                    if 'tnorm' in n or values[n['name']] != 10: n['tnorm']=values[n['name']]
                            self.server.brain._replace(Brain.from_dict(data));self.server.save_draft()
                            result['code'] = self.server.brain.to_python()
                    result['matches_design'] = still_matches
                    if not still_matches: result['message']='The canvas changed while TNorm was being applied. Open the installed design to continue tuning.'
                    self._json(result);return
                finally:self.server.device_lock.release()
            from .primitives import extract
            with self.server.brain_lock:
                if path=='/api/primitives/extract':
                    self._json(extract(self.server.brain,payload['members'],payload.get('name','Custom primitive'),payload.get('ports')));return
                brain=Brain.from_dict(self.server.brain.to_dict())
                brain.primitive(payload['kind'],payload['name'],neurons=payload.get('neurons',1),
                                source=payload.get('source') or None,definition=payload.get('definition'),
                                inputs=payload.get('inputs',1),contradictions=payload.get('contradictions',0),
                                width=payload.get('width',8),sources=payload.get('sources'))
                self.server.brain._replace(brain);self.server.save_draft()
                self._json({'design':brain.to_dict(),'code':brain.to_python(),'warnings':brain.validate()})
        except (ValueError,RuntimeError,KeyError,TypeError) as exc:
            self._json({'error':str(exc)[:500]},409)

    def _telemetry(self,path):
        if path=='/telemetry/design':
            data=None
            try:
                with self.server.device() as device:
                    status=device.status()
                    try:tuning=device.tuning()
                    except Exception:tuning={}
                hashes={self._confirmed_digest(status),tuning.get('effective_design_sha256')}
                with self.server.brain_lock:
                    for candidate in (self.server.brain.to_dict(),self.server.attached_design):
                        if candidate and self._brain_digest(Brain.from_dict(candidate)) in hashes:
                            data=candidate;break
            except Exception:pass
            self._json(data);return True
        if path!='/telemetry/events':return False
        try:
            with self.server.device() as device:
                with device.http.stream('GET','/api/brain/stream',params={'telemetry':1,
                    'cursor':self.headers.get('Last-Event-ID','')},timeout=httpx.Timeout(60,connect=10)) as upstream:
                    if upstream.status_code!=200:
                        self._json({'error':'Neuron telemetry is not available yet.'},upstream.status_code);return True
                    self.send_response(200);self.send_header('Content-Type','text/event-stream')
                    self.send_header('Cache-Control','no-store');self.end_headers()
                    for line in upstream.iter_lines():
                        self.wfile.write((line+'\n').encode())
                        if not line:self.wfile.flush()
        except (OSError,httpx.HTTPError):pass
        except (ValueError,RuntimeError):self._json({'error':'Connect Prima-Lux to view neuron telemetry.'},503)
        return True


def serve(brain: Brain | None = None, *, port: int = 0, open_browser: bool = True,
          env_path: str | Path | None = None, project: str | Path | None = None) -> None:
    from .workspace import open_workspace
    open_workspace(brain,port=port,open_browser=open_browser,explicit=True,env_path=env_path,project=project)
