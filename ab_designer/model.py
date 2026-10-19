"""Public, non-executable brain-design model.

There is deliberately no neuron simulation, physical graph or hardware data here.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import math
import re
from typing import Any

SCHEMA = "artificial-brains/design/v1"
RHYTHMS = ("high-gamma", "gamma", "beta", "alpha", "theta", "delta")
HIGH_GAMMA = "high-gamma"
EDGE_KINDS = ("exc", "inh")
NODE_KINDS = ("input", "opu", "output")
MAX_NODES = 256
MAX_EDGES = 512
MAX_INPUT_NEURONS = 498  # Per-sensor contract supported on the current Prima-Lux.

# Advisory ranges in the current rhythm design guide. Not a device acceptance test.
# Do not infer the size of a receiving OPU from its input size in this public module.
SUGGESTED_DIRECT_INPUT_MAX = {
    "gamma": 57,
    "beta": 339,
    "alpha": 396,
    "theta": 1414,
    "delta": 4524,
}


class DesignError(ValueError):
    """An invalid public brain definition or unsupported edit."""


def _check_name(value: Any, label: str = "Name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignError(f"{label} must be a non-empty string.")
    if value != value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise DesignError(f"{label} must be 1–80 characters with no leading spaces or line breaks.")
    return value


def _tnorm(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise DesignError("TNorm must be a number between 0.00 and 10.00.")
    if not (0 <= value <= 10):
        raise DesignError("TNorm must be between 0.00 and 10.00.")
    if abs(value * 100 - round(value * 100)) > 1e-7:
        raise DesignError("TNorm supports up to two decimal places (such as 2.01).")
    return round(float(value), 2)


def _neurons(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_INPUT_NEURONS:
        raise DesignError(f"Input size must be a positive integer (maximum {MAX_INPUT_NEURONS:,} for file safety).")
    return value


def _inhibition_strength(value: Any) -> float:
    """Public inhibitory multiplier of the calculated reference, at least 0.1.

    A connection omitted entirely represents 0%; the installed executable
    synapse format does not represent a zero-weight connection.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesignError("Inhibition strength must be a finite number of at least 0.1.")
    if not math.isfinite(float(value)) or float(value) < 0.1:
        raise DesignError("Inhibition strength must be a finite number of at least 0.1.")
    return float(value)


def _output_neurons(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 498:
        raise DesignError("Output neurons must be between 1 and 498.")
    return value


def _position(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DesignError(f"{name} position must be a finite number.")
    if not 0 <= value <= 100000:
        raise DesignError(f"{name} position is outside the editing area.")
    return round(float(value), 1)


def _node(data: Any, index: int) -> dict:
    if not isinstance(data, dict):
        raise DesignError(f"Node {index + 1} must be an object.")
    name = _check_name(data.get("name"), "Node name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise DesignError("Population names must use letters, numbers and underscores, beginning with a letter.")
    kind = data.get("kind")
    if kind not in NODE_KINDS:
        raise DesignError(f"{name}: node kind must be input, opu, or output.")
    x = _position(data.get("x", 180 + (index % 5) * 220), "X")
    y = _position(data.get("y", 160 + (index // 5) * 150), "Y")
    value = {"name": name, "kind": kind, "x": x, "y": y}
    if kind == "input":
        value["neurons"] = _neurons(data.get("neurons"))
    elif kind == "output":
        value["neurons"] = _output_neurons(data.get("neurons", 1))
    elif kind == "opu":
        rhythm = data.get("rhythm")
        if not isinstance(rhythm, str) or rhythm.lower() not in RHYTHMS:
            raise DesignError(f"{name}: choose high-gamma, gamma, beta, alpha, theta, or delta.")
        value["rhythm"] = rhythm.lower()
        # An omitted high-gamma TNorm preserves the established unit-weight design.
        # Explicit TNorm uses the same public 0..10 contract as every other OPU.
        if value["rhythm"] != HIGH_GAMMA or data.get("tnorm") is not None:
            value["tnorm"] = _tnorm(data.get("tnorm"))
    return value


def _edge(data: Any, index: int, by_name: dict) -> dict:
    if not isinstance(data, dict):
        raise DesignError(f"Connection {index + 1} must be an object.")
    source = _check_name(data.get("from"), "Connection source")
    target = _check_name(data.get("to"), "Connection target")
    kind = data.get("type", "exc")
    if kind not in EDGE_KINDS:
        raise DesignError("Connection type must be 'exc' or 'inh'.")
    if source not in by_name or target not in by_name:
        raise DesignError(f"Unknown node in connection {source} → {target}.")
    if by_name[source]["kind"] == "output" and by_name[target]["kind"] != "opu":
        raise DesignError("Output feedback must target an OPU.")
    if by_name[target]["kind"] == "input":
        raise DesignError(f"An input cannot receive a connection: {target}.")
    if by_name[source]["kind"] == "input" and by_name[target]["kind"] == "output":
        raise DesignError("Connect an input through an OPU before connecting to an output.")
    result = {"from": source, "to": target, "type": kind}
    if kind == "inh":
        if "strength" in data:
            strength = _inhibition_strength(data["strength"])
            # Do not change saved-design hashes for old unscaled connections.
            if strength != 1.0:
                result["strength"] = strength
    elif "strength" in data:
        raise DesignError("Inhibition strength only applies to inhibitory connections.")
    role = data.get("role")
    if role not in (None, "feedback", "operand"):
        raise DesignError("Connection role must be feedback or operand.")
    if role is not None:
        if by_name[target]["kind"] != "opu":
            raise DesignError("Connection roles require an OPU target.")
        if role == "feedback" and by_name[source]["kind"] == "input":
            raise DesignError("An input connection cannot be feedback.")
        result["role"] = role
    if "units" in data:
        units = data["units"]
        if role != "operand" or type(units) not in (int,float) or not math.isfinite(units) or units <= 0:
            raise DesignError("Positive units require an operand connection.")
        result["units"] = float(units)
    return result


def normalize(data: Any) -> dict:
    """Normalize and validate only the *public design*, never its implementation."""
    if not isinstance(data, dict):
        raise DesignError("Brain design must be a JSON object.")
    schema = data.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise DesignError(f"Unsupported design format {schema!r}; expected {SCHEMA}.")
    name = _check_name(data.get("name", "Untitled Brain"), "Brain name")
    nodes = data.get("nodes", [])
    edges = data.get("connections", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise DesignError("Nodes and connections must be lists.")
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise DesignError(f"Design exceeds the editor safety limit ({MAX_NODES} nodes, {MAX_EDGES} connections).")
    normalized_nodes = [_node(node, i) for i, node in enumerate(nodes)]
    by_name = {}
    for node in normalized_nodes:
        if node["name"] in by_name:
            raise DesignError(f"Duplicate node name: {node['name']}.")
        by_name[node["name"]] = node
    normalized_edges = [_edge(edge, i, by_name) for i, edge in enumerate(edges)]
    for target in normalized_nodes:
        if target["kind"] != "opu" or target["rhythm"] == HIGH_GAMMA:
            continue
        N = sum(by_name[e["from"]]["neurons"] for e in normalized_edges
                if e["to"] == target["name"] and e["type"] == "exc"
                and by_name[e["from"]]["kind"] == "input")
        if N > SUGGESTED_DIRECT_INPUT_MAX[target["rhythm"]]:
            raise DesignError(f"{target['name']} has too many direct input neurons for its rhythm.")
    seen = set()
    for edge in normalized_edges:
        identity = (edge["from"], edge["to"])
        if identity in seen:
            raise DesignError(f"Duplicate connection: {edge['from']} → {edge['to']}.")
        seen.add(identity)
    return {"schema": SCHEMA, "name": name, "nodes": normalized_nodes, "connections": normalized_edges}


def inspect_design(data: dict) -> list[str]:
    """Cautions only. This is not hardware or biological behavior validation."""
    by_name = {node["name"]: node for node in data["nodes"]}
    edges = data["connections"]
    messages = []
    for node in data["nodes"]:
        inc = [e for e in edges if e["to"] == node["name"]]
        out = [e for e in edges if e["from"] == node["name"]]
        if node["kind"] == "output" and not inc:
            messages.append(f"Output {node['name']} is not connected.")
        if node["kind"] == "input" and not out:
            messages.append(f"Input {node['name']} is not connected.")
        if node["kind"] == "opu" and not inc:
            messages.append(f"OPU {node['name']} has no incoming connection.")
        if node["kind"] == "opu" and node["rhythm"] != HIGH_GAMMA and node.get("tnorm") in (0, 10):
            messages.append(f"{node['name']} uses TNorm {node['tnorm']:g}, an operating boundary; test its behavior carefully.")
        if node["kind"] == "opu" and node["rhythm"] != HIGH_GAMMA:
            direct_inputs = [by_name[e["from"]]["neurons"] for e in inc if by_name[e["from"]]["kind"] == "input"]
            if direct_inputs and sum(direct_inputs) > SUGGESTED_DIRECT_INPUT_MAX[node["rhythm"]]:
                messages.append(
                    f"Direct inputs to {node['name']} total {sum(direct_inputs):,}, above the "
                    f"current {node['rhythm']} practical input guideline. Receiving activity may saturate; "
                    "actual feasibility is checked when the brain is built."
                )
    if data["nodes"] and not any(n["kind"] == "output" for n in data["nodes"]):
        messages.append("No named output has been added yet.")
    return messages


class Brain:
    """Create a human-readable brain design, not an executable neural network."""
    def __init__(self, name: str):
        self._data = normalize({"name": name, "nodes": [], "connections": []})

    @property
    def name(self) -> str:
        return self._data["name"]

    def input(self, name: str, neurons: int) -> "Brain":
        self._append({"kind": "input", "name": name, "neurons": neurons})
        return self

    def opu(self, name: str, rhythm: str, tnorm: float | None = None) -> "Brain":
        node = {"kind": "opu", "name": name, "rhythm": rhythm}
        if tnorm is not None:
            node["tnorm"] = tnorm
        self._append(node)
        return self

    def output(self, name: str, n: int = 1) -> "Brain":
        self._append({"kind": "output", "name": name, "neurons": n})
        return self

    def connect(self, source: str, target: str, connection: str = "exc",
                strength: float | None = None, *, role: str | None = None,
                units: float | None = None) -> "Brain":
        """Connect populations; strength scales inhibitory reference (minimum 0.1, no upper cap).

        The fourth argument is not a physical weight or a guarantee of target
        spike suppression. Existing connect(A, B, 'inh') means full reference.
        """
        d = self.to_dict()
        edge = {"from": source, "to": target, "type": connection}
        if strength is not None:
            edge["strength"] = strength
        if role is not None: edge["role"] = role
        if units is not None: edge["units"] = units
        d["connections"].append(edge)
        self._data = normalize(d)
        return self

    def _append(self, node: dict) -> None:
        d = self.to_dict()
        count = len(d["nodes"])
        node["x"] = 160 + (count % 5) * 290
        node["y"] = 160 + (count // 5) * 150
        d["nodes"].append(node)
        self._data = normalize(d)

    def to_dict(self) -> dict:
        return deepcopy(self._data)

    @classmethod
    def from_dict(cls, data: dict) -> "Brain":
        d = normalize(data)
        brain = cls(d["name"])
        brain._data = d
        return brain

    def _replace(self, other: "Brain") -> None:
        self._data = other.to_dict()

    def validate(self) -> list[str]:
        return inspect_design(self._data)

    def to_python(self) -> str:
        from .code import to_python
        return to_python(self)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Brain":
        p = Path(path)
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def render(self, *, port: int = 0, open_browser: bool = True) -> None:
        """Launch the interactive standalone visual editor. Ctrl+C to stop."""
        from .server import serve
        serve(self, port=port, open_browser=open_browser)

    def __repr__(self) -> str:
        return f"Brain({self.name!r}, {len(self._data['nodes'])} nodes, {len(self._data['connections'])} connections)"
