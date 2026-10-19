from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional
import math
from .maps import InputSensorMap


@dataclass
class EncodedSignal:
    target: str
    kind: str
    payload: List[float]
    meta: dict


class SpikeEncoder:
    """
    JSON-compatible encoder for the Python backend.

    Frontend `kind` is the modality:
      - proprioception
      - proximity
      - depth
      - image
      - video
      - audio
      - touch
      - temperature
      - gyro
      - accelerometer

    Controller is responsible for supplying the scale values needed by the
    selected formula:
      - vmax
      - vmin/vmax
      - absmax

    Scalar population modes:

    Positive scalar local bump:
        q = clamp(floor((v / vmax) * n), 0, n - 1)
        s_i = 1 if max(0, q-r) <= i <= min(n-1, q+r) else 0

    Positive scalar cumulative population:
        q = clamp(floor((v / vmax) * n), 0, n - 1)
        s_i = 1 if i <= q else 0

    Ranged scalar:
        q = clamp(floor(((v - vmin) / (vmax - vmin)) * n), 0, n - 1)

    Signed scalar:
        q = clamp(floor(((v + absmax) / (2 * absmax)) * n), 0, n - 1)
    """

    def __init__(self, input_map: InputSensorMap) -> None:
        self.input_map = input_map

    def encode(
        self,
        sensor: str,
        signal: Any,
        *,
        mode: Optional[str] = None,
        vmax: Optional[float] = None,
        vmin: Optional[float] = None,
        absmax: Optional[float] = None,
        radius: int = 1,
        meta: Optional[dict] = None,
    ) -> EncodedSignal:
        binding = self.input_map.get_by_sensor(sensor)

        payload = self.transform_to_spikes(
            signal=signal,
            kind=binding.kind,
            n=binding.n,
            mode=mode,
            vmax=vmax,
            vmin=vmin,
            absmax=absmax,
            radius=radius,
        )

        out_meta = dict(meta or {})
        out_meta.setdefault("sensor", binding.sensor)
        out_meta.setdefault("kind", binding.kind)
        if binding.n is not None:
            out_meta.setdefault("n", binding.n)
        out_meta.setdefault("encoding", mode or self._default_mode_for_kind(binding.kind))
        if vmax is not None:
            out_meta.setdefault("vmax", vmax)
        if vmin is not None:
            out_meta.setdefault("vmin", vmin)
        if absmax is not None:
            out_meta.setdefault("absmax", absmax)
        out_meta.setdefault("radius", radius)

        return EncodedSignal(
            target=binding.input_id,
            kind=binding.kind,
            payload=payload,
            meta=out_meta,
        )

    def build_input_request(
        self,
        compile_id: str,
        encoded: EncodedSignal,
        *,
        step: Optional[int] = None,
    ) -> dict:
        event = {
            "target": encoded.target,
            "payload": encoded.payload,
            "meta": encoded.meta,
        }
        if step is not None:
            raise ValueError(
                "Live sensory inputs cannot specify 'step'. The brain "
                "owns neural execution time; publish signals as they arrive."
            )

        return {
            "compileId": compile_id,
            "events": [event],
        }

    def transform_to_spikes(
        self,
        *,
        signal: Any,
        kind: Optional[str],
        n: Optional[int],
        mode: Optional[str] = None,
        vmax: Optional[float] = None,
        vmin: Optional[float] = None,
        absmax: Optional[float] = None,
        radius: int = 1,
    ) -> List[float]:
        selected_mode = mode or self._default_mode_for_kind(kind)

        if selected_mode == "reward_population":
            return self._reward_magnitude_to_population(
                magnitude=self._to_scalar(signal),
                n=n,
            )

        if selected_mode == "vector_f32":
            return self._vector_to_n(signal, n)

        if selected_mode == "binary_spikes":
            values = self._vector_to_n(signal, n)
            return [1.0 if float(v) > 0.0 else 0.0 for v in values]

        scalar = self._to_scalar(signal)

        if selected_mode == "positive_scalar_population":
            if vmax is None:
                raise ValueError("positive_scalar_population requires vmax")
            return self._positive_scalar_to_population(
                value=scalar,
                vmax=float(vmax),
                n=n,
                radius=radius,
            )

        if selected_mode == "positive_scalar_cumulative_population":
            if vmax is None:
                raise ValueError("positive_scalar_cumulative_population requires vmax")
            return self._positive_scalar_to_cumulative_population(
                value=scalar,
                vmax=float(vmax),
                n=n,
            )

        if selected_mode == "ranged_scalar_population":
            if vmin is None or vmax is None:
                raise ValueError("ranged_scalar_population requires vmin and vmax")
            return self._ranged_scalar_to_population(
                value=scalar,
                vmin=float(vmin),
                vmax=float(vmax),
                n=n,
                radius=radius,
            )

        if selected_mode == "signed_scalar_population":
            if absmax is None:
                raise ValueError("signed_scalar_population requires absmax")
            return self._signed_scalar_to_population(
                value=scalar,
                absmax=float(absmax),
                n=n,
                radius=radius,
            )

        raise ValueError(f"unsupported encoder mode: {selected_mode}")

    @staticmethod
    def _default_mode_for_kind(kind: Optional[str]) -> str:
        k = (kind or "").strip().lower()
        if k == "reward":
            return "reward_population"

        if k in {"image", "video", "audio"}:
            return "vector_f32"

        if k in {"proximity", "depth", "temperature", "touch"}:
            return "positive_scalar_population"

        if k in {"gyro", "accelerometer", "proprioception"}:
            return "signed_scalar_population"

        return "vector_f32"

    @staticmethod
    def _reward_magnitude_to_population(
        *,
        magnitude: float,
        n: Optional[int],
    ) -> List[float]:
        if n is None or n <= 0:
            return [1.0 if magnitude > 0.0 else 0.0]

        mag = max(0.0, min(1.0, float(magnitude)))
        k = int(round(mag * n))
        if mag > 0.0:
            k = max(1, k)
        k = max(0, min(n, k))

        return [1.0 if i < k else 0.0 for i in range(n)]

    @staticmethod
    def _vector_to_n(signal: Any, n: Optional[int]) -> List[float]:
        if isinstance(signal, (list, tuple)):
            values = [float(x) for x in signal]
        elif hasattr(signal, "tolist"):
            values = [float(x) for x in signal.tolist()]
        else:
            values = [float(signal)]

        if n is None:
            return values

        if len(values) < n:
            values = values + [0.0] * (n - len(values))
        elif len(values) > n:
            values = values[:n]

        return values

    @staticmethod
    def _to_scalar(signal: Any) -> float:
        if isinstance(signal, (int, float)):
            return float(signal)

        if isinstance(signal, (list, tuple)):
            if not signal:
                return 0.0
            return float(signal[0])

        if hasattr(signal, "tolist"):
            v = signal.tolist()
            if isinstance(v, list):
                while isinstance(v, list) and len(v) > 0:
                    v = v[0]
                return float(v) if not isinstance(v, list) else 0.0
            return float(v)

        return float(signal)

    @staticmethod
    def _clamped_q(value: float, *, vmax: float, n: int) -> int:
        if vmax == 0:
            raise ValueError("vmax must not be 0")
        if not math.isfinite(value):
            return -1
        if not math.isfinite(vmax):
            raise ValueError("vmax must be finite")
        return max(0, min(n - 1, int((value / vmax) * n)))

    @staticmethod
    def _build_local_population(*, q: int, n: int, radius: int) -> List[float]:
        if q < 0:
            return [0.0] * n
        left = max(0, q - radius)
        right = min(n - 1, q + radius)

        out = [0.0] * n
        for i in range(left, right + 1):
            out[i] = 1.0
        return out

    @staticmethod
    def _build_cumulative_population(*, q: int, n: int) -> List[float]:
        if q < 0:
            return [0.0] * n
        return [1.0 if i <= q else 0.0 for i in range(n)]

    @classmethod
    def _positive_scalar_to_population(
        cls,
        *,
        value: float,
        vmax: float,
        n: Optional[int],
        radius: int = 1,
    ) -> List[float]:
        if n is None or n <= 1:
            return [1.0]
        q = cls._clamped_q(value, vmax=vmax, n=n)
        return cls._build_local_population(q=q, n=n, radius=radius)

    @classmethod
    def _positive_scalar_to_cumulative_population(
        cls,
        *,
        value: float,
        vmax: float,
        n: Optional[int],
    ) -> List[float]:
        if n is None or n <= 1:
            return [1.0]
        q = cls._clamped_q(value, vmax=vmax, n=n)
        return cls._build_cumulative_population(q=q, n=n)

    @classmethod
    def _ranged_scalar_to_population(
        cls,
        *,
        value: float,
        vmin: float,
        vmax: float,
        n: Optional[int],
        radius: int = 1,
    ) -> List[float]:
        if n is None or n <= 1:
            return [1.0]
        if vmax == vmin:
            raise ValueError("vmax and vmin must not be equal")
        if not math.isfinite(value):
            return [0.0] * n
        if not math.isfinite(vmin) or not math.isfinite(vmax):
            raise ValueError("vmin and vmax must be finite")

        q = int(((value - vmin) / (vmax - vmin)) * n)
        q = max(0, min(n - 1, q))
        return cls._build_local_population(q=q, n=n, radius=radius)

    @classmethod
    def _signed_scalar_to_population(
        cls,
        *,
        value: float,
        absmax: float,
        n: Optional[int],
        radius: int = 1,
    ) -> List[float]:
        if n is None or n <= 1:
            return [1.0]
        if absmax == 0:
            raise ValueError("absmax must not be 0")
        if not math.isfinite(value):
            return [0.0] * n
        if not math.isfinite(absmax):
            raise ValueError("absmax must be finite")

        q = int(((value + absmax) / (2 * absmax)) * n)
        q = max(0, min(n - 1, q))
        return cls._build_local_population(q=q, n=n, radius=radius)
