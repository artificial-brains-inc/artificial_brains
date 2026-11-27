"""Public weight-file encoding; no brain construction or neural calculations."""
from __future__ import annotations

import base64
import copy
import json
import math
import os
from pathlib import Path
import struct
import tempfile

SCHEMA = 'artificial-brains/weights/v1'
ENCODING = 'float32-le/base64'
MAX_FILE_BYTES = 48 * 1024 * 1024  # Covers the device's 32 MiB prepared image.


def packed(snapshot: dict) -> dict:
    """Validate and encode rows of float32 values for exact file/device transfer."""
    if (not isinstance(snapshot, dict) or snapshot.get('schema') != SCHEMA or
            snapshot.get('encoding') != ENCODING or not isinstance(snapshot.get('layers'), list)):
        raise ValueError('Not an Artificial Brains weight snapshot.')
    result = copy.deepcopy(snapshot)
    total = 0
    for layer in result['layers']:
        if not isinstance(layer, dict):
            raise ValueError('Invalid weight matrix.')
        shape = layer.get('shape')
        if (not isinstance(shape, list) or len(shape) != 2 or
                any(type(n) is not int or n <= 0 for n in shape)):
            raise ValueError('Each matrix needs a positive [target neurons, source neurons] shape.')
        rows, columns = shape
        size = rows * columns
        total += size * 4
        if total > 32 * 1024 * 1024:
            raise ValueError('Weight matrices exceed the device image capacity.')
        if 'values' in layer:
            if 'data_b64' in layer:
                raise ValueError('Supply values or data_b64 for a matrix, not both.')
            values = layer.pop('values')
            if (not isinstance(values, list) or len(values) != rows or
                    any(not isinstance(row, list) or len(row) != columns for row in values)):
                raise ValueError('Weight values do not match the matrix shape.')
            raw = bytearray()
            for row in values:
                for value in row:
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError('Weights must be finite float32 numbers.')
                    try:
                        raw.extend(struct.pack('<f', value))
                    except (OverflowError, struct.error):
                        raise ValueError('Weight cannot be represented as float32.') from None
            layer['data_b64'] = base64.b64encode(raw).decode('ascii')
        encoded = layer.get('data_b64')
        if not isinstance(encoded, str) or len(encoded) != 4 * ((size * 4 + 2) // 3):
            raise ValueError('Weight data length does not match its shape.')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ValueError('Invalid weight encoding.') from None
        if len(raw) != size * 4 or base64.b64encode(raw).decode('ascii') != encoded:
            raise ValueError('Invalid weight encoding.')
        if not all(math.isfinite(v) for (v,) in struct.iter_unpack('<f', raw)):
            raise ValueError('Weights must be finite float32 numbers.')
    return result


def decoded(snapshot: dict) -> dict:
    result = packed(snapshot)
    for layer in result['layers']:
        raw = base64.b64decode(layer.pop('data_b64'))
        rows, columns = layer['shape']
        layer['values'] = [list(struct.unpack_from('<' + str(columns) + 'f', raw, row * columns * 4))
                           for row in range(rows)]
    return result


def read_file(path: str | Path) -> dict:
    path = Path(path).expanduser()
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Weight file exceeds the device transfer capacity.')
    return packed(json.loads(raw))


def write_file(path: str | Path, snapshot: dict) -> Path:
    path = Path(path).expanduser().resolve()
    payload = json.dumps(packed(snapshot), ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError('Weight file exceeds the device transfer capacity.')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
