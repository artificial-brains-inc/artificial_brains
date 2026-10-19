"""Public Brain design validation and device submission.

The design contains only developer-specified OPUs, I/O and signed connections.
This module does not implement or reveal neural dynamics, physical construction,
or package compilation. Those operations are performed by authenticated services.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from ab_designer import Brain
from ab_designer.code import parse_python

from .primalux import PrimaLuxClient, PrimaLuxError

MAX_PUBLIC_DESIGN_BYTES = 1024 * 1024


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def semantic_design(value: Brain | dict) -> dict:
    """Canonical public design with visual layout removed, never physical state."""
    data = value.to_dict() if isinstance(value, Brain) else Brain.from_dict(value).to_dict()
    nodes = []
    for node in data['nodes']:
        entry = {k: v for k, v in node.items() if k not in ('x', 'y')}
        nodes.append(entry)
    nodes.sort(key=lambda n: n['name'])
    connections = sorted(data['connections'], key=lambda e:(e['from'],e['to'],e['type']))
    return {'schema': data['schema'], 'name': data['name'], 'nodes': nodes,
            'connections': connections}


def design_sha256(value: Brain | dict) -> str:
    return hashlib.sha256(_canonical(semantic_design(value))).hexdigest()


def load_design(file: str | Path) -> Brain:
    path = Path(file).expanduser().resolve()
    if not path.is_file() or path.stat().st_size > MAX_PUBLIC_DESIGN_BYTES:
        raise ValueError('Brain design not found or larger than 1 MiB.')
    if path.suffix == '.py':
        return parse_python(path.read_text(encoding='utf-8'))
    return Brain.load(path)


def find_design(project: Path) -> Brain | None:
    """Local design lookup; never search for the private neural implementation."""
    matches = []
    for path in (project/'brain.abdesign.json', project/'brain.py'):
        if path.is_file(): matches.append(path)
    matches += sorted(project.glob('*.abdesign.json'))
    matches = list(dict.fromkeys(matches))
    if len(matches) > 1:
        raise ValueError('More than one Brain design found. Select one explicitly with --brain PATH.')
    return load_design(matches[0]) if matches else None


def design_contract(brain: Brain) -> dict:
    """I/O names and sizes only. No neural topology, weights or dynamics."""
    nodes = brain.to_dict()['nodes']
    inputs = [{'id': n['name'], 'kind': 'Input', 'n': n['neurons']}
              for n in nodes if n['kind'] == 'input']
    outputs = [{'id': n['name'], 'n': n['neurons']}
               for n in nodes if n['kind'] == 'output']
    return {
        'inputs': inputs, 'outputs': outputs,
        'maps': {
            'input_sensor_map': [{'inputId': n['id'], 'sensor': n['id'],
                                  'n': n['n'], 'kind': n['kind']} for n in inputs],
            'output_motor_map': [{'outputId': n['id'], 'motor': n['id']}
                                 for n in outputs],
        }
    }


def _require_build_ready(result: dict, expected_hash: str) -> bool:
    """A success is bound to this exact design and must confirm installed READY."""
    if not isinstance(result, dict) or result.get('design_sha256') != expected_hash:
        raise PrimaLuxError('Device did not confirm the requested brain design.')
    if result.get('failed') or result.get('status') in ('ERROR', 'FAILED'):
        raise PrimaLuxError('Brain could not be prepared. Check the device status.')
    return bool(result.get('status') == 'READY' and result.get('installed') is True)


def build_on_device(device: PrimaLuxClient, brain: Brain, *, timeout: float = 240.0,
                    poll_interval: float = .5) -> dict:
    """Transmit only the developer's design to Prima-Lux.

    Prima-Lux is the only caller of the cloud build service. Compiled packages
    are delivered directly to the device; the SDK sees only design/status.
    """
    if timeout <= 0 or poll_interval <= 0:
        raise ValueError('Timeouts must be positive.')
    design = semantic_design(brain)
    body = _canonical(design)
    if len(body) > MAX_PUBLIC_DESIGN_BYTES:
        raise ValueError('Brain design exceeds the maximum supported size.')
    digest = hashlib.sha256(body).hexdigest()
    try:
        answer = device._request('POST', '/api/brain/build', json={
            'design': design, 'design_sha256': digest,
        }, timeout=20.0)
    except PrimaLuxError as exc:
        if 'HTTP 404' in str(exc) or 'HTTP 405' in str(exc):
            raise PrimaLuxError('Prima-Lux Device Manager update required for Brain builds.') from exc
        raise
    if _require_build_ready(answer, digest):
        status = device.status()
        engine = status.get('engine') or {}
        if not (engine.get('state') == 'READY' and engine.get('has_brain') is True
                and not (status.get('package') or {}).get('pending')):
            raise PrimaLuxError('Brain build completed but Prima-Lux has not confirmed READY.')
        return {'status': 'READY', 'design_sha256': digest, 'device_ready': True}
    build_id = answer.get('build_id')
    if not answer.get('accepted') or not isinstance(build_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', build_id):
        raise PrimaLuxError('Device did not accept the brain build request.')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = device._request('GET', f'/api/brain/build/{build_id}')
        if _require_build_ready(result, digest):
            status = device.status()
            engine = status.get('engine') or {}
            if (engine.get('state') == 'READY' and engine.get('has_brain') is True
                    and not (status.get('package') or {}).get('pending')):
                return {'status':'READY','design_sha256':digest,'device_ready':True,
                        'device_id':status.get('device_id')}
            raise PrimaLuxError('Brain build completed but device has not confirmed READY.')
        time.sleep(min(poll_interval, max(0, deadline-time.monotonic())))
    raise PrimaLuxError('Timed out waiting for Prima-Lux to finish preparing the brain.')
