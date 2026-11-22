from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import math
import re
from typing import Any

SCHEMA = "artificial-brains/design/v1"
RHYTHMS = ("high-gamma", "gamma", "beta", "alpha", "theta", "delta", "dopaminergic")
HIGH_GAMMA = "high-gamma"
EDGE_KINDS = ("exc", "inh")
LEARNING_RULES = ("static", "STDP", "STDP3", "STDP3_SLOW")
NODE_KINDS = ("input", "opu", "output")
MAX_INPUT_NEURONS = 498  # Per-sensor contract supported on the current Prima-Lux.

# Recommended maximum per input, using the receiving rhythm (also through high-gamma).
# Do not infer the size of a receiving OPU from its input size in this public module.
SUGGESTED_DIRECT_INPUT_MAX = {
    "gamma": 6,
    "beta": 37,
    "alpha": 44,
    "theta": 156,
    "delta": 498,
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
        raise DesignError(f"Input size must be a positive integer (maximum {MAX_INPUT_NEURONS:,} per input on the current Prima-Lux).")
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
    if value < 0:
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
            raise DesignError(f"{name}: choose high-gamma, gamma, beta, alpha, theta, delta, or dopaminergic.")
        value["rhythm"] = rhythm.lower()
        # An omitted high-gamma TNorm preserves the established unit-weight design.
        # Explicit TNorm uses the same public 0..10 contract as every other OPU.
        if value["rhythm"] != HIGH_GAMMA or data.get("tnorm") is not None:
            value["tnorm"] = _tnorm(data.get("tnorm"))
    return value


def _validate_rewards(data, by_name):
    """Validate multiple named dopamine sources; preserve the legacy single source."""
    if "rewards" not in data:
        return
    if "reward" in data or "reward_type" in data:
        raise DesignError("Use rewards or reward/reward_type, not both.")
    if data.get("rule") not in ("STDP3", "STDP3_SLOW"):
        raise DesignError("Reward sources apply only to STDP3 or STDP3_SLOW.")
    sources = data["rewards"]
    if not isinstance(sources, list) or not sources:
        raise DesignError("Select at least one dopaminergic reward source.")
    for item in sources:
        if not isinstance(item, dict) or set(item) != {"source", "type"}:
            raise DesignError("Each reward needs source and type (LTP or LTD).")
        source = item["source"]
        if (not isinstance(source, str) or source not in by_name
                or by_name[source].get("kind") != "opu"
                or by_name[source].get("rhythm") != "dopaminergic"):
            raise DesignError("Reward source must name a dopaminergic population in this brain.")
        if not isinstance(item["type"], str) or item["type"] not in ("LTP", "LTD"):
            raise DesignError("Reward type must be LTP or LTD.")


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
    rule = data.get("rule", "static")
    if not isinstance(rule, str) or rule not in LEARNING_RULES:
        raise DesignError("Learning rule must be static, STDP, STDP3, or STDP3_SLOW.")
    # Omit the existing default so unchanged designs retain their hashes.
    if rule != "static":
        result["rule"] = rule
    _validate_rewards(data, by_name)
    if "rewards" in data:
        result["rewards"] = [dict(item) for item in data["rewards"]]
    if "reward" in data:
        reward = data["reward"]
        if rule not in ("STDP3", "STDP3_SLOW"):
            raise DesignError("A reward source applies only to STDP3 or STDP3_SLOW.")
        if (not isinstance(reward, str) or reward not in by_name
                or by_name[reward].get("kind") != "opu"
                or by_name[reward].get("rhythm") != "dopaminergic"):
            raise DesignError("Reward source must name a dopaminergic population in this brain.")
        result["reward"] = reward
    if "reward_type" in data:
        reward_type = data["reward_type"]
        if rule not in ("STDP3", "STDP3_SLOW"):
            raise DesignError("Reward type applies only to STDP3 or STDP3_SLOW.")
        if not isinstance(reward_type, str) or reward_type not in ("LTP", "LTD"):
            raise DesignError("Reward type must be LTP or LTD.")
        result["reward_type"] = reward_type
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
        if role != "operand" or type(units) not in (int, float) or not math.isfinite(units) or units <= 0:
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
    normalized_nodes = [_node(node, i) for i, node in enumerate(nodes)]
    by_name = {}
    for node in normalized_nodes:
        if node["name"] in by_name:
            raise DesignError(f"Duplicate node name: {node['name']}.")
        by_name[node["name"]] = node
    normalized_edges = [_edge(edge, i, by_name) for i, edge in enumerate(edges)]
    seen = set()
    for edge in normalized_edges:
        identity = (edge["from"], edge["to"])
        if identity in seen:
            raise DesignError(f"Duplicate connection: {edge['from']} → {edge['to']}.")
        seen.add(identity)
    result = {"schema": SCHEMA, "name": name, "nodes": normalized_nodes, "connections": normalized_edges}
    if 'groups' in data:
        if not isinstance(data['groups'],list):raise DesignError('Primitive groups must be a list.')
        groups=[];names=set()
        for group in data['groups']:
            if not isinstance(group,dict):raise DesignError('Invalid primitive group.')
            label=_check_name(group.get('name'),'Primitive name')
            if label in names:raise DesignError('Primitive group names must be unique.')
            names.add(label)
            members=group.get('members',[]);ports=group.get('ports',{})
            if not isinstance(members,list) or any(not isinstance(n,str) for n in members):
                raise DesignError('Primitive members must be population names.')
            # Removing a population also removes its visual group membership.
            members=list(dict.fromkeys(n for n in members if n in by_name))
            if not isinstance(ports,dict):raise DesignError('Primitive ports must be a mapping.')
            ports={_check_name(k,'Port name'):v for k,v in ports.items() if v in members}
            if members:groups.append({'name':label,'kind':_check_name(group.get('kind','custom')),
                                      'members':members,'ports':ports})
        result['groups']=groups
    return result


def inspect_design(data: dict) -> list[str]:
    """Cautions only. This is not hardware or biological behavior validation."""
    by_name = {node["name"]: node for node in data["nodes"]}
    edges = data["connections"]
    messages = []
    for edge in edges:
        if edge.get("rule") in ("STDP3", "STDP3_SLOW") and "reward" not in edge and "rewards" not in edge:
            messages.append(f"{edge['from']} → {edge['to']}: select a dopaminergic reward source before building.")
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
        if node["kind"] == "input":
            pending = [e["to"] for e in out]
            visited = set()
            while pending:
                name = pending.pop()
                if name in visited:
                    continue
                visited.add(name)
                target = by_name[name]
                if target["kind"] != "opu":
                    continue
                if target["rhythm"] == HIGH_GAMMA:
                    pending.extend(e["to"] for e in edges if e["from"] == name)
                    continue
                limit = SUGGESTED_DIRECT_INPUT_MAX.get(target["rhythm"])
                if limit is None:
                    continue
                if node["neurons"] > limit:
                    messages.append(
                        f"Input {node['name']} has {node['neurons']:,} neurons; "
                        f"the recommended maximum per input for {target['name']} "
                        f"({target['rhythm']}) is {limit:,}. "
                        "Receiving activity may saturate."
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
                units: float | None = None, rule: str = "static",
                reward: str | None = None, reward_type: str | None = None,
                rewards: list[dict[str, str]] | None = None) -> "Brain":
        """Connect populations; strength scales inhibitory reference (minimum 0.1, no upper cap).

        The fourth argument is not a physical weight or a guarantee of target
        spike suppression. Existing connect(A, B, 'inh') means full reference.
        rule selects static, STDP, STDP3, or STDP3_SLOW with internal settings.
        reward names a dopaminergic population for STDP3 or STDP3_SLOW.
        reward_type selects LTP or LTD; omitted means the existing LTP default.
        rewards accepts multiple {"source": name, "type": "LTP" or "LTD"} entries
        instead of reward/reward_type. Each source must be dopaminergic.
        """
        d = self.to_dict()
        edge = {"from": source, "to": target, "type": connection}
        edge["rule"] = rule
        if rewards is not None:
            edge["rewards"] = rewards
        if reward is not None:
            edge["reward"] = reward
        if reward_type is not None:
            edge["reward_type"] = reward_type
        if strength is not None:
            edge["strength"] = strength
        if role is not None:
            edge["role"] = role
        if units is not None:
            edge["units"] = units
        d["connections"].append(edge)
        self._data = normalize(d)
        return self

    def primitive(self, kind: str, name: str, *, neurons: int = 1,
                  source: str | None = None, definition: dict | None = None,
                  inputs: int = 1, contradictions: int = 0, width: int = 8, sources: dict | None = None) -> "Brain":
        """Insert an editable named circuit; it expands to ordinary populations and connections."""
        from .primitives import add_primitive
        self._replace(add_primitive(self,kind,name,neurons=neurons,source=source,definition=definition,
                                    inputs=inputs,contradictions=contradictions,width=width,sources=sources))
        return self

    def group(self, name: str, members: list[str], *, kind: str = 'custom', ports: dict | None = None) -> "Brain":
        data=self.to_dict()
        data.setdefault('groups',[]).append({'name':name,'kind':kind,'members':members,'ports':ports or {}})
        self._data=normalize(data)
        return self

    def port(self, primitive: str, port: str) -> str:
        """Return the actual population name behind a primitive port."""
        for group in self._data.get('groups',[]):
            if group['name']==primitive and port in group['ports']:return group['ports'][port]
        raise DesignError('Primitive port not found.')

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
