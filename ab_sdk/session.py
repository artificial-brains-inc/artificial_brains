# ab_sdk/session.py

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Any, Callable, Dict, Mapping, Optional

from .encoder import SpikeEncoder
from .maps import InputSensorMap, OutputMotorMap, RewardMap
from .output_stream import OutputStream


@dataclass
class SessionConfig:
    telemetry: bool = True
    poll_interval: float = 0.05
    output_limit: int = 100
    checkpoint_every_ticks: int = 250
    output_telemetry: bool = False


class RealtimeSession:
    def __init__(
        self,
        *,
        project_id: Optional[str],
        compile_id: str,
        contract: Dict[str, Any],
        node_client: Any,
        python_client: Any,
        config: SessionConfig,
    ) -> None:
        self.project_id = project_id
        self.compile_id = compile_id
        self.contract = contract
        self.node_client = node_client
        self.python_client = python_client
        self.config = config

        self.input_map = InputSensorMap.from_contract(contract)
        self.output_map = OutputMotorMap.from_contract(contract)
        self.encoder = SpikeEncoder(self.input_map)

        self.input_client = python_client.clone()
        self.output_client = python_client.clone()

        self.decoder: Optional[Any] = None
        self.output_stream = OutputStream(
            self.output_client,
            compile_id=compile_id,
            poll_interval=config.poll_interval,
            limit=config.output_limit,
        )
        self._output_handlers: list[Callable[[Dict[str, Any]], None]] = []
        self._command_handlers: list[Callable[[Any], None]] = []
        self._control_handlers: list[Callable[[Dict[str, Any]], None]] = []
        self._running = False
        self._last_checkpoint_step = -1
        self._checkpoint_every_steps = int(config.checkpoint_every_ticks or 250)

        # AB_NONBLOCKING_OUTPUT_TELEMETRY_V1
        #
        # Output telemetry is observational I/O and must not block the
        # realtime output -> decoder -> command path.
        #
        # NodeRealtimeClient.publish_output() remains the canonical SDK
        # transport. It is simply executed by this dedicated worker.
        self._output_telemetry_queue = queue.Queue(maxsize=32)
        self._output_telemetry_thread = None
        self._output_telemetry_dropped = 0

        if (
            self.config.output_telemetry
            and self.node_client
            and self.project_id
        ):
            self._output_telemetry_thread = threading.Thread(
                target=self._output_telemetry_worker,
                daemon=True,
                name=f"ABOutputTelemetry:{self.compile_id}",
            )
            self._output_telemetry_thread.start()

            print(
                "[AB][OUTPUT_TELEMETRY] mode=background queue_max=32",
                flush=True,
            )

        self.output_stream.on_item(self._dispatch_output)
        self.output_stream.on_control(self._dispatch_control)

        print(
            f"[AB][CHECKPOINT] enabled every {self._checkpoint_every_steps} output steps",
            flush=True,
        )
        print(
            f"[AB][OUTPUT_TELEMETRY] enabled={self.config.output_telemetry}",
            flush=True,
        )

    def set_decoder(self, decoder: Any) -> None:
        self.decoder = decoder

    def on_output(self, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._output_handlers.append(handler)

    def on_command(self, handler: Callable[[Any], None]) -> None:
        self._command_handlers.append(handler)

    def start_output_stream(self) -> None:
        self.output_stream.start()
        self._running = True

    def publish_input(
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
    ) -> Dict[str, Any]:
        encoded = self.encoder.encode(
            sensor,
            signal,
            mode=mode,
            vmax=vmax,
            vmin=vmin,
            absmax=absmax,
            radius=radius,
            meta=meta,
        )
        payload = self.encoder.build_input_request(
            compile_id=self.compile_id,
            encoded=encoded,
        )
        return self.input_client.send_input(payload=payload)
    
    def publish_sensor_frame(
        self,
        readings: Mapping[str, Any],
        *,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Publish one simultaneous multichannel observation immediately.

        Each sensor is encoded against the session's input map. This is an
        atomic capture, not a time-series buffer or a future neural step.
        """
        if not isinstance(readings, Mapping) or not readings:
            raise ValueError("Provide a nonempty simultaneous sensor snapshot.")

        events = []
        for sensor, data in readings.items():
            if isinstance(data, Mapping):
                encoded = self.encoder.encode(
                    sensor,
                    data.get("signal"),
                    mode=data.get("mode", mode),
                    vmax=data.get("vmax"),
                    vmin=data.get("vmin"),
                    absmax=data.get("absmax"),
                    radius=int(data.get("radius", 1)),
                    meta=data.get("meta"),
                )
            else:
                encoded = self.encoder.encode(sensor, data, mode=mode)
            events.append({"target": encoded.target, "payload": encoded.payload})

        return self.input_client.send_frame(
            compile_id=self.compile_id,
            events=events,
        )

    def publish_reward(
        self,
        reward_input: str,
        magnitude: float,
        *,
        meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        return self.publish_input(
            reward_input,
            max(0.0, min(1.0, float(magnitude))),
            mode="reward_population",
            meta=meta,
        )

    def stop(self, *, notify_node: bool = True) -> None:
        if not self._running:
            return
        self._running = False
        
        try:
            self.checkpoint(reason="final")
        except Exception as exc:
            print(f"[AB][CHECKPOINT][FINAL][ERROR] {exc}", flush=True)

        self.python_client.run_stop(self.compile_id)
        self.output_stream.stop()
        if (
            self.config.output_telemetry
            and self.node_client
            and self.project_id
        ):
            try:
                self.node_client.clear_output_cache(self.project_id, self.compile_id)
            except Exception as exc:
                print(f"[AB][OUTPUT_TELEMETRY][CLEAR_ERROR] {exc}", flush=True)
        if notify_node and self.config.telemetry and self.node_client and self.project_id:
            self.node_client.sdk_run_stopped(self.project_id, self.compile_id)
    
    def close_from_runtime(self, *, notify_node: bool = True) -> None:
        if not self._running:
            return
        self._running = False

        try:
            self.checkpoint(reason="final")
        except Exception as exc:
            print(f"[AB][CHECKPOINT][FINAL][ERROR] {exc}", flush=True)

        self.output_stream.stop()
        if (
            self.config.output_telemetry
            and self.node_client
            and self.project_id
        ):
            try:
                self.node_client.clear_output_cache(self.project_id, self.compile_id)
            except Exception as exc:
                print(f"[AB][OUTPUT_TELEMETRY][CLEAR_ERROR] {exc}", flush=True)

        if notify_node and self.config.telemetry and self.node_client and self.project_id:
            self.node_client.sdk_run_stopped(self.project_id, self.compile_id)


    def _output_telemetry_worker(self) -> None:
        """Publish output telemetry without blocking robot control."""
        while True:
            item = self._output_telemetry_queue.get()

            try:
                self.node_client.publish_output(
                    self.project_id,
                    self.compile_id,
                    item,
                )
            except Exception as exc:
                print(
                    f"[AB][OUTPUT_TELEMETRY][ERROR] {exc}",
                    flush=True,
                )


    def _enqueue_output_telemetry(
        self,
        item: Dict[str, Any],
    ) -> None:
        """Best-effort telemetry enqueue; never wait for Node."""

        payload = dict(item)

        try:
            self._output_telemetry_queue.put_nowait(payload)
            return
        except queue.Full:
            pass

        # Node is behind. Discard the oldest observational frame rather
        # than ever blocking the realtime control path.
        try:
            self._output_telemetry_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            self._output_telemetry_queue.put_nowait(payload)
        except queue.Full:
            # A race can fill it again. Control still wins.
            pass

        self._output_telemetry_dropped += 1

        if (
            self._output_telemetry_dropped == 1
            or self._output_telemetry_dropped % 100 == 0
        ):
            print(
                "[AB][OUTPUT_TELEMETRY][DROP] "
                f"count={self._output_telemetry_dropped}",
                flush=True,
            )


    def _dispatch_output(self, item: Dict[str, Any]) -> None:
        if (
            self.config.output_telemetry
            and self.node_client
            and self.project_id
        ):
            self._enqueue_output_telemetry(item)
        for handler in list(self._output_handlers):
            handler(item)

        self._maybe_checkpoint_from_output(item)

        if self.decoder is None:
            return

        command = self.decoder.decode(
            item,
            context={
                "session": self,
                "contract": self.contract,
            },
        )
        if command is None:
            return
        for handler in list(self._command_handlers):
            handler(command)

    def _dispatch_control(self, control: Dict[str, Any]) -> None:
        print(f"[AB][CONTROL] received {control}")

        for handler in list(self._control_handlers):
            handler(control)

        for handler in list(self._command_handlers):
            handler(control)
    
    def on_control(self, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._control_handlers.append(handler)
    
    def checkpoint(self, *, reason: str = "periodic") -> Optional[Dict[str, Any]]:
        if not self.node_client or not self.project_id:
            print("[AB][CHECKPOINT] skipped: missing node_client/project_id", flush=True)
            return None

        if reason == "final":
            try:
                payload = self.python_client.get_outputs(
                    compile_id=self.compile_id,
                    after_step=self.output_stream.after_step,
                    limit=100,
                )
                items = payload.get("items") or []
                if items:
                    self.output_stream.latest_item = items[-1]
                    self.output_stream.after_step = payload.get(
                        "next_after_step",
                        self.output_stream.after_step,
                    )
            except Exception as exc:
                print(f"[AB][CHECKPOINT][FINAL][REFRESH_ERROR] {exc}", flush=True)

        weights_payload = self.python_client.get_weights(
            compile_id=self.compile_id,
        )

        step = weights_payload.get("step")
        weights = weights_payload.get("weights") or []

        if not weights:
            print("[AB][CHECKPOINT] skipped: no weights available", flush=True)
            return None
       
        payload = {
            "compileId": self.compile_id,
            "projectId": self.project_id,
            "reason": reason,
            "step": step,
            "weights": weights,
        }

        print(
            f"[AB][CHECKPOINT] sending reason={reason} step={payload.get('step')}",
            flush=True,
        )

        result = self.node_client.checkpoint(self.project_id, payload)
        print(f"[AB][CHECKPOINT] sent ok result={result}", flush=True)
        return result
    
    def _maybe_checkpoint_from_output(self, item: Dict[str, Any]) -> None:
        if self._checkpoint_every_steps <= 0:
            return

        try:
            step = int(item.get("step", -1))
        except Exception:
            return

        if step <= 0:
            return

        if step - self._last_checkpoint_step < self._checkpoint_every_steps:
            return

        self._last_checkpoint_step = step
    
        threading.Thread(
            target=self._checkpoint_async,
            kwargs={"reason": "periodic", "step": step},
            daemon=True,
            name=f"ABCheckpoint:{self.compile_id}:{step}",
        ).start()

    def _checkpoint_async(self, *, reason: str, step: int) -> None:
        try:
            print(f"[AB][CHECKPOINT][ASYNC] output_step={step}", flush=True)
            self.checkpoint(reason=reason)
        except Exception as exc:
            print(f"[AB][CHECKPOINT][ASYNC][ERROR] {exc}", flush=True)