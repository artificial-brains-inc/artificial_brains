"""Authenticated Prima-Lux device client and live I/O transport."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import threading
import time
import warnings
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import httpx

from .config import load_env_file
from .decoder import GenericSpikeDecoder
from .session import RealtimeSession, SessionConfig

MAX_ABP_BYTES = 64 * 1024 * 1024


class PrimaLuxError(RuntimeError):
    """A device request or SDK/hardware contract failed."""


class PrimaLuxProtocolError(PrimaLuxError):
    """A device response cannot be mapped faithfully to the existing SDK."""


def _validate_device_url(url: str) -> str:
    parsed = urlparse(url.rstrip('/'))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('AB_DEVICE_URL must be an http(s) URL without credentials.')
    if parsed.path not in ('', '/') or parsed.params or parsed.query or parsed.fragment:
        raise ValueError('AB_DEVICE_URL must refer to the Device origin, not an API path.')
    if parsed.scheme == 'http':
        try:
            private = ipaddress.ip_address(parsed.hostname).is_private or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            private = parsed.hostname.lower() == 'localhost' or parsed.hostname.lower().endswith('.local')
        if not private:
            raise ValueError('HTTP is permitted only for local/private device addresses. Use HTTPS elsewhere.')
    return url.rstrip('/')


@dataclass(frozen=True)
class PrimaLuxConfig:
    url: str
    api_key: str
    contract_path: Optional[Path] = None
    timeout: float = 15.0
    output_poll_interval: float = 0.05
    # Accepted for configuration compatibility; input delivery is immediate.
    sensor_flush_interval: float | None = None

    @classmethod
    def from_env(cls, env_path: str | Path | None = '.env') -> 'PrimaLuxConfig':
        if env_path is not None:
            load_env_file(Path(env_path))
        url = os.getenv('AB_DEVICE_URL', '').strip()
        api_key = os.getenv('AB_DEVICE_API_KEY', '').strip()
        if not url:
            raise ValueError('Missing AB_DEVICE_URL, e.g. http://10.42.0.2:8080.')
        if not api_key:
            raise ValueError('Missing AB_DEVICE_API_KEY (create it in the Prima-Lux UI).')
        path = os.getenv('AB_DEVICE_CONTRACT', '').strip()
        config = cls(
            url=_validate_device_url(url),
            api_key=api_key,
            contract_path=Path(path).expanduser() if path else None,
            timeout=float(os.getenv('AB_DEVICE_TIMEOUT', '15')),
            output_poll_interval=float(os.getenv('AB_DEVICE_OUTPUT_POLL_INTERVAL', '0.05')),
            sensor_flush_interval=(float(os.environ['AB_DEVICE_SENSOR_FLUSH_INTERVAL'])
                                   if 'AB_DEVICE_SENSOR_FLUSH_INTERVAL' in os.environ else None),
        )
        if config.sensor_flush_interval is not None:
            warnings.warn(
                "AB_DEVICE_SENSOR_FLUSH_INTERVAL is deprecated and ignored. "
                "Live sensory input is forwarded without SDK buffering or timed flushing.",
                FutureWarning,
                stacklevel=2,
            )
        if config.timeout <= 0 or config.output_poll_interval <= 0:
            raise ValueError('Invalid Prima-Lux timeout or output polling interval.')
        return config

    def load_contract(self) -> dict[str, Any]:
        if self.contract_path is None:
            raise ValueError('Provide contract=... or AB_DEVICE_CONTRACT with the installed .abp I/O mapping.')
        data = json.loads(self.contract_path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('inputs'), list) or not isinstance(data.get('outputs'), list):
            raise ValueError('Device contract must have lists named inputs and outputs.')
        return data


class PrimaLuxClient:
    """UI/API-level device management, loading, and robot I/O (not a simulator client)."""

    def __init__(self, url: str, api_key: str, *, timeout: float = 15.0, http: Any = None):
        self.url = _validate_device_url(url)
        if not api_key or not api_key.startswith('plx_'):
            raise ValueError('Provide a Prima-Lux developer API key, beginning plx_.')
        self.api_key = api_key
        self.http = http or httpx.Client(
            base_url=self.url,
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=httpx.Timeout(timeout),
            trust_env=False,
        )
        self._owns_http = http is None

    @classmethod
    def from_env(cls, env_path: str | Path | None = '.env') -> 'PrimaLuxClient':
        c = PrimaLuxConfig.from_env(env_path)
        return cls(c.url, c.api_key, timeout=c.timeout)

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> 'PrimaLuxClient':
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _response(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                body = response.json()
                reason = body.get('detail') if isinstance(body, dict) else None
            except ValueError:
                reason = None
            raise PrimaLuxError(f'Prima-Lux HTTP {response.status_code}: {reason or response.reason_phrase}') from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PrimaLuxProtocolError('Prima-Lux returned non-JSON data.') from exc
        if not isinstance(data, dict):
            raise PrimaLuxProtocolError('Prima-Lux response must be a JSON object.')
        return data

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return self._response(self.http.request(method, path, **kwargs))
        except httpx.RequestError as exc:
            raise PrimaLuxError(f'Cannot reach Prima-Lux at {self.url}: {exc.__class__.__name__}') from exc

    def status(self) -> dict[str, Any]:
        return self._request('GET', '/api/status')

    def events(self) -> dict[str, Any]:
        return self._request('GET', '/api/brain/events')

    def run(self) -> dict[str, Any]:
        return self._request('POST', '/api/brain/run')

    def stop(self) -> dict[str, Any]:
        return self._request('POST', '/api/brain/stop')

    def stop_and_wait(self, *, timeout: float = 180.0, poll_interval: float = 0.25) -> dict[str, Any]:
        """Wait for STOPPED and idle after requesting Stop.

        The Stop response acknowledges only the request. The brain saves its
        neural dynamics asynchronously. Do not close the session or report a
        successful save until the device confirms that Stop has completed.
        """
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError('Stop timeout and polling interval must be positive.')
        acknowledgement = self.stop()
        if acknowledgement.get('ok') is not True:
            raise PrimaLuxProtocolError('Prima-Lux did not accept Stop.')
        native = acknowledgement.get('native_response')
        if isinstance(native, dict) and native.get('accepted') != 'stop':
            raise PrimaLuxProtocolError('Prima-Lux did not acknowledge Stop.')

        deadline = time.monotonic() + timeout
        last_state = 'UNKNOWN'
        while True:
            response = self.status()
            engine = response.get('engine')
            if not isinstance(engine, dict) or not isinstance(engine.get('state'), str):
                raise PrimaLuxProtocolError('Cannot confirm Stop: missing device status.')
            last_state = engine['state'].upper()
            if engine.get('error'):
                raise PrimaLuxError(f"Prima-Lux Stop failed: {engine['error']}")
            if engine.get('available') is False or last_state in ('ERROR', 'FAULT', 'SAFE_MODE', 'UNAVAILABLE'):
                raise PrimaLuxError(f'Prima-Lux Stop failed: device state is {last_state}.')
            # READY does not prove the brain was stopped and saved. Only
            # STOPPED with no operation in progress confirms completion.
            if last_state == 'STOPPED' and not engine.get('busy'):
                return {'ok': True, 'confirmed': True, 'state': 'STOPPED'}
            if time.monotonic() >= deadline:
                raise PrimaLuxError(
                    f'Stop was not confirmed within {timeout:g}s '
                    f'(last state: {last_state}); neural dynamics save is unconfirmed.'
                )
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    def reset_dynamics(self) -> dict[str, Any]:
        """Restore installed neural dynamics to initial state, not FPGA firmware.

        The native reset is asynchronous. The returned JSON acknowledges only
        that it was requested; callers can poll status for completion.
        """
        return self._request('POST', '/api/brain/reset-dynamics')

    def reset(self) -> dict[str, Any]:
        """Original Reset route, retained for older Device Manager releases."""
        return self._request('POST', '/api/brain/reset')

    def send_sensors(self, channels: Mapping[str, Any]) -> dict[str, Any]:
        """Send exact normalized per-population vectors; no implicit recoding."""
        if not isinstance(channels, Mapping) or not channels or len(channels) > 128:
            raise ValueError('Provide 1 to 128 sensor population mappings.')
        clean: dict[str, list[float] | float] = {}
        for key, values in channels.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError('Each sensor needs a non-empty channel ID.')
            vector = values if isinstance(values, (list, tuple)) else [values]
            if not vector or len(vector) > 498:
                raise ValueError(f'{key}: population vector must contain 1..498 values.')
            for value in vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f'{key}: each normalized input value must be finite and within [0,1].')
            clean[key] = [float(v) for v in vector]
        return self._request('POST', '/api/brain/sensor', json=clean)

    def upload_abp(self, path: str | Path, *, wait_ready: bool = True, timeout: float = 180.0) -> dict[str, Any]:
        """Upload an ALREADY compiled/signed ABPKG001. Does not sign packages."""
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != '.abp' or not path.is_file():
            raise ValueError('Select an existing .abp file.')
        size = path.stat().st_size
        if not 32 <= size <= MAX_ABP_BYTES:
            raise ValueError('Package size must be 32 bytes through 64 MiB.')
        with path.open('rb') as stream:
            if stream.read(8) != b'ABPKG001':
                raise ValueError('Expected native signed ABPKG001 package, not graph JSON.')
            stream.seek(0)
            result = self._request('POST', '/api/brain/load', files={'file': (path.name, stream, 'application/octet-stream')}, timeout=timeout)
        if result.get('accepted') is not True:
            raise PrimaLuxError(result.get('message') or 'Package staged but NOT installed. Native engine unavailable.')
        if not wait_ready:
            return result
        expected = result.get('package') or {}
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.status()
            package = status.get('package') or {}
            engine = status.get('engine') or {}
            failure = package.get('failed_load') or {}
            if failure and (not expected.get('name') or failure.get('name') == expected.get('name')):
                raise PrimaLuxError(f"Native package load failed: {failure.get('error', 'unknown')}")
            active = package.get('active') or {}
            if (not package.get('pending') and engine.get('state') == 'READY' and
                    engine.get('has_brain') is True and active.get('sha256') == expected.get('sha256')):
                return {'ok': True, 'installed': True, 'package': active, 'status': status}
            if engine.get('state') in ('ERROR', 'FAULT', 'SAFE_MODE', 'UNAVAILABLE'):
                raise PrimaLuxError(f"Native manager is {engine.get('state')}: {engine.get('error')}")
            time.sleep(0.5)
        raise PrimaLuxError('Timed out waiting for installed brain to become READY. Inspect /api/status before Run.')

    def start_session(self, contract: Mapping[str, Any] | None = None, *,
                      config: PrimaLuxConfig | None = None, start_brain: bool = False,
                      decoder: Any = None) -> 'PrimaLuxSession':
        """Reuse RealtimeSession/RobotLoop/SpikeEncoder with a hardware transport.

        Contract must be the installed ABP's I/O mapping (not an unrelated sim graph).
        """
        if contract is None:
            if config is None:
                raise ValueError('Provide contract or PrimaLuxConfig with AB_DEVICE_CONTRACT.')
            contract = config.load_contract()
        if not isinstance(contract, Mapping) or not isinstance(contract.get('inputs'), list) or not isinstance(contract.get('outputs'), list):
            raise ValueError('Contract must define inputs and outputs arrays.')
        status = self.status()
        engine = status.get('engine') or {}
        if not engine.get('has_brain') or status.get('package', {}).get('pending'):
            raise PrimaLuxError('No installed ready brain. Load an .abp before starting a robot loop.')
        if start_brain:
            if engine.get('state') not in ('READY', 'STOPPED'):
                raise PrimaLuxError('Brain must be READY or STOPPED before Start.')
            self.run()
            deadline=time.monotonic()+15.0
            while time.monotonic()<deadline:
                confirmed=self.status()
                if confirmed.get('engine',{}).get('state')=='RUNNING':
                    break
                if confirmed.get('engine',{}).get('state') in ('ERROR','FAULT','SAFE_MODE','UNAVAILABLE'):
                    raise PrimaLuxError('Brain did not start: native manager reported a fault.')
                time.sleep(0.2)
            else:
                raise PrimaLuxError('Native Start did not reach RUNNING within 15 seconds.')
        elif engine.get('state') != 'RUNNING':
            raise PrimaLuxError('Brain is not running. Call device.run() or use start_brain=True.')
        poll = config.output_poll_interval if config else 0.05
        transport = _DeviceSessionTransport(self)
        session = PrimaLuxSession(
            project_id=None,
            compile_id='primalux-local',
            contract=dict(contract),
            node_client=None,
            python_client=transport,
            config=SessionConfig(telemetry=False, poll_interval=poll, output_limit=100,
                                 checkpoint_every_ticks=0, output_telemetry=False),
        )
        if decoder is not None:
            session.set_decoder(decoder)
        session.start_output_stream()
        return session


class _DeviceSessionTransport:
    """Immediate, acknowledged delivery of SDK-encoded sensor events.

    No flush timer, coalescing, substitution, or drop-on-overload behavior.
    A lock serializes concurrent SDK submissions in acquisition order. This
    does NOT assign precise capture timestamps or a guaranteed FPGA step;
    native hardware still owns scheduling and has a finite input queue.
    """
    def __init__(self, client: PrimaLuxClient, *, flush_interval: float | None = None):
        # This option does not control sensory delivery.
        self.device = client
        self._send_lock = threading.Lock()
        self._closed = False
        self._sent_events = 0
        self._sent_frames = 0
        self.require_raw_spikes = False

    def clone(self) -> '_DeviceSessionTransport':
        return self  # one ordering lock, client, and lifecycle shared

    def send_input(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        events = payload.get('events') if isinstance(payload, dict) else None
        items = self._extract_events(events)
        if len(items) > 1:
            warnings.warn(
                "Submitting multiple live sensory observations through one "
                "send_input call is deprecated. Publish input as it arrives. "
                "Use send_frame() only for an atomic, same-instant "
                "multi-channel snapshot.",
                FutureWarning,
                stacklevel=2,
            )
        # Validate all events before transmitting the first one. Keep repeated
        # input IDs as separate native requests; never turn a time series into
        # a dictionary of latest values.
        with self._send_lock:
            if self._closed:
                raise PrimaLuxError('Prima-Lux session is closed.')
            for key, vector in items:
                self._transmit_locked({key: vector}, event_count=1)
        return {'ok': True, 'queued_for_hardware': True, 'sent_events': len(items)}

    @staticmethod
    def _extract_events(events: Any) -> list[tuple[str, list[float]]]:
        if not isinstance(events, list) or not events:
            raise ValueError('Expected nonempty encoded input event list.')
        items: list[tuple[str, list[float]]] = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError('Malformed SDK encoded sensor event.')
            if 'step' in event:
                raise ValueError(
                    "Live sensory events cannot specify 'step'; "
                    "the brain owns neural execution time."
                )
            key, vector = event.get('target'), event.get('payload')
            if not isinstance(key, str) or not key or not isinstance(vector, list) or not vector:
                raise ValueError('Malformed SDK encoded sensor event.')
            items.append((key, vector))
        return items

    def _transmit_locked(self, channels: dict[str, list[float]], *, event_count: int) -> None:
        # No SDK delay or retry. Any rejection propagates on this call.
        reply = self.device.send_sensors(channels)
        if reply.get('ok') is not True or reply.get('queued') is not True:
            raise PrimaLuxProtocolError(
                'Device did not acknowledge the sensor frame; its delivery is unknown. '
                'Stop and inspect native /status instead of retrying blindly.'
            )
        self._sent_events += event_count
        self._sent_frames += 1

    def send_frame(self, *, events: list[dict[str, Any]]) -> dict[str, Any]:
        """One atomic snapshot explicitly sampled at one instant by the caller.

        Not a periodic accumulator. Duplicate populations cannot be combined
        because they represent separate temporal observations, not one frame.
        """
        items = self._extract_events(events)
        channels: dict[str, list[float]] = {}
        for key, vector in items:
            if key in channels:
                raise ValueError('Same population repeated in an atomic frame; send distinct events instead.')
            channels[key] = vector
        with self._send_lock:
            if self._closed:
                raise PrimaLuxError('Prima-Lux session is closed.')
            self._transmit_locked(channels, event_count=len(items))
        return {'ok': True, 'queued_for_hardware': True, 'sent_events': len(items), 'atomic_frame': True}

    def get_outputs(self, *, compile_id: str, after_step: int | None = None, limit: int = 100) -> dict[str, Any]:
        raw = self.device.events()
        if isinstance(raw.get('items'), list):
            items = raw['items']
        elif isinstance(raw.get('events'), list):
            items = raw['events']
        elif isinstance(raw.get('step'), int):
            items = [raw]
        elif raw.get('ok') is True and not (raw.get('items') or raw.get('events')):
            items = []
        else:
            raise PrimaLuxProtocolError('Native /events structure is unknown; no outputs were invented.')
        frames = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('step'), int):
                raise PrimaLuxProtocolError('Native event missing integer step.')
            if self.require_raw_spikes and not isinstance(item.get('outputs'), list):
                raise PrimaLuxProtocolError('Device provides actuation channel counts, not raw neuron IDs. Use a channel decoder, not GenericSpikeDecoder.')
            if after_step is None or item['step'] > after_step:
                frames.append(item)
        frames.sort(key=lambda row: row['step'])
        if len(frames) > limit:
            frames = frames[:limit]
        return {'ok': True, 'items': frames,
                'next_after_step': frames[-1]['step'] if frames else after_step,
                'control': raw.get('control')}

    def run_stop(self, compile_id: str) -> dict[str, Any]:
        # The application session must remain open until the requested neural
        # dynamics save is confirmed, even when Stop takes several seconds.
        print('[AB][PRIMA-LUX] Stopping brain; waiting for neural dynamics to save...', flush=True)
        return self.device.stop_and_wait()

    def get_weights(self, *, compile_id: str) -> dict[str, Any]:
        # Hardware checkpoint is owned by the native Stop API, not cloud weights.
        return {'weights': []}

    def close(self) -> None:
        with self._send_lock:
            self._closed = True

    @property
    def superseded_sensor_events(self) -> int:
        return 0  # compatibility: current release never supersedes an event

    @property
    def sent_sensor_events(self) -> int:
        return self._sent_events

    @property
    def sent_sensor_frames(self) -> int:
        return self._sent_frames


class PrimaLuxSession(RealtimeSession):
    """Existing RealtimeSession with only transport lifecycle specialization."""
    def publish_sensor_frame(self, readings: Mapping[str, Any], *, mode: Optional[str] = None) -> dict[str, Any]:
        """Encode and send populations observed *simultaneously* as one frame.

        Not for buffering successive timestamps. For asynchronous per-sensor
        updates, use the inherited publish_input(sensor, signal, ...) method.
        """
        if not isinstance(readings, Mapping) or not readings:
            raise ValueError('Provide a nonempty simultaneous sensor snapshot.')
        events = []
        for sensor, data in readings.items():
            if isinstance(data, Mapping):
                encoded = self.encoder.encode(
                    sensor, data.get('signal'), mode=data.get('mode', mode),
                    vmax=data.get('vmax'), vmin=data.get('vmin'),
                    absmax=data.get('absmax'), radius=int(data.get('radius', 1)),
                    meta=data.get('meta'),
                )
            else:
                encoded = self.encoder.encode(sensor, data, mode=mode)
            events.append({'target': encoded.target, 'payload': encoded.payload})
        return self.input_client.send_frame(events=events)

    def set_decoder(self, decoder: Any) -> None:
        self.python_client.require_raw_spikes = isinstance(decoder, GenericSpikeDecoder)
        super().set_decoder(decoder)

    def stop(self, *, notify_node: bool = True) -> None:
        was_running = self._running
        try:
            super().stop(notify_node=notify_node)
            if was_running:
                print('[AB][PRIMA-LUX] Stop confirmed: neural dynamics saved.', flush=True)
        finally:
            # A failing native Stop must not strand the output polling thread.
            self.output_stream.stop()
            self.python_client.close()


class ActuationChannelDecoder:
    """Explicit channel-count decoder for native controller outputs.

    This is NOT GenericSpikeDecoder: neuron identity is unavailable in channel
    counts. Each mapping is channel -> {'motor': str, 'gain': float}, optionally
    'limit': [negative_max, positive_max]. The developer supplies all gains.
    """
    def __init__(self, mapping: Mapping[str, Mapping[str, Any]]):
        self.mapping = {}
        for name, entry in mapping.items():
            if not isinstance(name, str) or not isinstance(entry, Mapping) or not isinstance(entry.get('motor'), str):
                raise ValueError('Actuation channel mapping requires a channel and motor name.')
            gain = entry.get('gain')
            if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain):
                raise ValueError(f'{name}: explicit finite gain required; no default motor movement.')
            limit = entry.get('limit')
            if limit is not None and (not isinstance(limit, (list, tuple)) or len(limit) != 2 or
                                      not all(isinstance(x, (int, float)) and math.isfinite(x) for x in limit) or limit[0] > limit[1]):
                raise ValueError(f'{name}: limit must be [min,max].')
            self.mapping[name] = {'motor': entry['motor'], 'gain': float(gain), 'limit': limit}

    def decode(self, output_event: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        actuation = output_event.get('actuation') or {}
        channels = actuation.get('channels')
        if not isinstance(channels, dict):
            raise PrimaLuxProtocolError('Native controller event has no actuation.channels; cannot decode motor command.')
        deltas: dict[str, float] = {}
        for name, spec in self.mapping.items():
            count = channels.get(name, 0)
            if isinstance(count, bool) or not isinstance(count, (int, float)) or not math.isfinite(count):
                raise PrimaLuxProtocolError(f'Native actuation count for {name} is invalid.')
            result = float(count) * spec['gain']
            if spec['limit'] is not None:
                lo, hi = spec['limit']
                result = min(hi, max(lo, result))
            if result:
                motor = spec['motor']
                deltas[motor] = deltas.get(motor, 0) + result
        return {'t': output_event['step'], 'deltas': deltas, 'actuation': channels}
