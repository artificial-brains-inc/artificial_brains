"""Loopback-only designer with optional authenticated Prima-Lux controls."""
from __future__ import annotations
import json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import threading
import time
import webbrowser
from urllib.parse import urlsplit
from typing import Any

from .model import Brain, DesignError
from .code import parse_python

STATIC = Path(__file__).parent / "static"
MIMES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
MAX_REQUEST_SIZE = 1024 * 1024


class DesignerServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, brain: Brain, *, env_path: str | Path | None = None):
        super().__init__(("127.0.0.1", port), DesignerHandler)
        self.brain = brain
        self.brain_lock = threading.Lock()
        self.device_lock = threading.Lock()
        self.env_path = env_path  # Loaded server-side, never sent to the browser.
        self.ready_digest = None
        self.building = False


class DesignerHandler(BaseHTTPRequestHandler):
    server: DesignerServer
    server_version = "ABDesigner/0.3.7"

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
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
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
        if path == "/api/device/status":
            self._device_status()
            return
        if path in ("/api/design", "/api/health"):
            with self.server.brain_lock:
                result = {
                    "design": self.server.brain.to_dict(),
                    "code": self.server.brain.to_python(),
                    "warnings": self.server.brain.validate(),
                } if path == "/api/design" else {"ok": True, "mode": "designer", "version": "0.3.7"}
            self._json(result)
            return
        static_map = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js", "/favicon.png": "favicon.png", "/artificial-brains-wordmark.svg": "artificial-brains-wordmark.svg"}
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
        if path in ("/api/device/build", "/api/device/run", "/api/device/stop"):
            self._device_action(path)
            return
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

    def _device_status(self) -> None:
        with self.server.brain_lock:
            current_hash = self._brain_digest(self.server.brain)
        if self.server.building:
            self._json({'connected': True, 'state': 'BUILDING', 'ready_for_run': False})
            return
        try:
            from ab_sdk.primalux import PrimaLuxClient
            with PrimaLuxClient.from_env(self.server.env_path or ".env") as device:
                status = device.status()
            engine = status.get('engine') or {}
            state = str(engine.get('state', 'UNKNOWN')).upper()
            installed = engine.get('has_brain') is True
            match = (self.server.ready_digest == current_hash and
                     self._confirmed_digest(status) == current_hash)
            self._json({'connected': True, 'state': state, 'has_brain': installed,
                        'ready_for_run': bool(match and installed and state in ('READY', 'STOPPED'))})
        except Exception:
            self._json({'connected': False, 'state': 'DISCONNECTED', 'ready_for_run': False})

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
            with PrimaLuxClient.from_env(self.server.env_path or ".env") as device:
                status = device.status()
                engine = status.get('engine') or {}
                state = str(engine.get('state', 'UNKNOWN')).upper()
                if path == '/api/device/build':
                    if state == 'RUNNING':
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
                    self._json({'ok': True, 'state': result['status'], 'ready': result['device_ready']})
                elif path == '/api/device/run':
                    if self.server.ready_digest != expected_hash or self._confirmed_digest(status) != expected_hash:
                        self._json({'error': 'Build this brain on Prima-Lux before running it.'}, 409)
                        return
                    if engine.get('has_brain') is not True or state not in ('READY', 'STOPPED'):
                        self._json({'error': 'Prima-Lux is not ready to run this brain.'}, 409)
                        return
                    acknowledgement = device.run()
                    if acknowledgement.get('ok') is not True:
                        raise PrimaLuxError('Prima-Lux did not accept Run.')
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        reported = device.status()
                        if ((reported.get('engine') or {}).get('state') or '').upper() == 'RUNNING':
                            self._json({'ok': True, 'state': 'RUNNING'})
                            return
                        time.sleep(.25)
                    raise PrimaLuxError('Prima-Lux did not confirm RUNNING.')
                elif path == '/api/device/stop':
                    if state != 'RUNNING':
                        self._json({'error': 'The brain is not running.'}, 409)
                        return
                    confirmed = device.stop_and_wait()
                    self._json({'ok': bool(confirmed.get('confirmed')), 'state': 'STOPPED'})
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
                'Stop was not confirmed',
            )
            if not any(message.startswith(prefix) for prefix in public_errors):
                message = 'Prima-Lux could not complete this operation. Check the device and try again.'
            self._json({'error': message[:250]}, 409)
        finally:
            self.server.building = False
            self.server.device_lock.release()


def serve(brain: Brain | None = None, *, port: int = 0, open_browser: bool = True, env_path: str | Path | None = None) -> None:
    brain = brain or Brain("Untitled Brain")
    server = DesignerServer(port, brain, env_path=env_path)
    url = f"http://127.0.0.1:{server.server_port}/"
    print("\n  Artificial Brains Designer")
    print(f"  Open: {url}")
    print("  This editor stores only your brain design.")
    print("  Press Ctrl+C to close the designer.\n")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.15)
    except KeyboardInterrupt:
        print("\nDesigner closed.")
    finally:
        server.server_close()
