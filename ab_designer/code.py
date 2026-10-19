"""Deterministic Python export and non-executing, allowlisted AST import."""
from __future__ import annotations
import ast
from .model import Brain, DesignError


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def to_python(brain: Brain) -> str:
    d = brain.to_dict()
    lines = ["from ab import Brain", "", f"brain = Brain({d['name']!r})", ""]
    for node in d["nodes"]:
        if node["kind"] == "input":
            lines.append(f"brain.input({node['name']!r}, {node['neurons']})")
        elif node["kind"] == "opu":
            if node['rhythm'] == 'high-gamma' and 'tnorm' not in node:
                lines.append(f"brain.opu({node['name']!r}, 'high-gamma')")
            else:
                lines.append(f"brain.opu({node['name']!r}, {node['rhythm']!r}, {_num(node['tnorm'])})")
        else:
            lines.append(f"brain.output({node['name']!r}{', ' + str(node['neurons']) if node.get('neurons', 1) != 1 else ''})")
    lines.append("")
    for edge in d["connections"]:
        args = f"{edge['from']!r}, {edge['to']!r}"
        if edge["type"] == "inh":
            args += ", 'inh'"
            if 'strength' in edge:
                args += f", {_num(edge['strength'])}" if round(edge['strength'], 2) == edge['strength'] else f", {edge['strength']!r}"
        if "role" in edge: args += f", role={edge['role']!r}"
        if "units" in edge: args += f", units={edge['units']!r}"
        lines.append(f"brain.connect({args})")
    lines += ["", "brain.render()", ""]
    return "\n".join(lines)


def _literal_args(call: ast.Call, allowed_keywords: set[str], line: int):
    if any(isinstance(a, ast.Starred) for a in call.args):
        raise DesignError(f"Line {line}: *args are not supported in brain definitions.")
    values = []
    try:
        values = [ast.literal_eval(a) for a in call.args]
    except (ValueError, TypeError, SyntaxError, RecursionError) as e:
        raise DesignError(f"Line {line}: use literal names, rhythms, numbers and connection types only.") from e
    kwargs = {}
    for item in call.keywords:
        if item.arg not in allowed_keywords or item.arg is None or item.arg in kwargs:
            raise DesignError(f"Line {line}: unsupported keyword or keyword expansion.")
        try:
            kwargs[item.arg] = ast.literal_eval(item.value)
        except (ValueError, TypeError, SyntaxError, RecursionError) as e:
            raise DesignError(f"Line {line}: keywords must use literal values.") from e
    return values, kwargs


def parse_python(source: str, *, previous: Brain | None = None) -> Brain:
    """Accept only a small literal Brain API. Never eval/exec user text."""
    if not isinstance(source, str) or len(source) > 150000:
        raise DesignError("The Python brain definition is too large.")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise DesignError(f"Line {e.lineno}: {e.msg}.") from e
    result = None
    seen_assignment = False
    for stmt in tree.body:
        line = stmt.lineno
        if isinstance(stmt, ast.ImportFrom):
            if stmt.module not in ("ab", "ab_designer") or any(a.name != "Brain" or a.asname for a in stmt.names) or stmt.level:
                raise DesignError(f"Line {line}: only 'from ab import Brain' is supported.")
            continue
        if isinstance(stmt, ast.Assign):
            if seen_assignment or len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name) or stmt.targets[0].id != "brain":
                raise DesignError(f"Line {line}: define your brain once with 'brain = Brain(...)'.")
            call = stmt.value
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "Brain":
                raise DesignError(f"Line {line}: expected brain = Brain('Name').")
            a, k = _literal_args(call, {"name"}, line)
            try:
                result = Brain(*a, **k)
            except (TypeError, ValueError) as e:
                raise DesignError(f"Line {line}: {e}") from e
            seen_assignment = True
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            func = call.func
            if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name) or func.value.id != "brain":
                raise DesignError(f"Line {line}: only calls on 'brain' are supported.")
            if result is None:
                raise DesignError(f"Line {line}: create Brain('Name') first.")
            allowed = {
                "input": {"name", "neurons"},
                "opu": {"name", "rhythm", "tnorm"},
                "output": {"name", "n"},
                "connect": {"source", "target", "connection", "strength", "role", "units"},
                "render": set(),
                "save": set(),
            }
            if func.attr not in allowed:
                raise DesignError(f"Line {line}: unsupported method {func.attr!r}; use input, opu, output, connect or render.")
            a, k = _literal_args(call, allowed[func.attr], line)
            if func.attr in ("render", "save"):
                if func.attr == "render" and (a or k):
                    raise DesignError(f"Line {line}: render() takes no arguments in this editor.")
                if func.attr == "save":
                    from pathlib import Path
                    if len(a) != 1 or k or not isinstance(a[0], str) or not a[0].endswith('.abdesign.json') or Path(a[0]).name != a[0]:
                        raise DesignError(f"Line {line}: save() takes a local .abdesign.json filename.")
                continue  # Never launch a browser or write files while parsing.
            try:
                getattr(result, func.attr)(*a, **k)
            except (TypeError, ValueError) as e:
                raise DesignError(f"Line {line}: {e}") from e
            continue
        raise DesignError(f"Line {line}: only literal Brain declarations and methods are supported in the editor.")
    if result is None:
        raise DesignError("Add brain = Brain('Your Brain') before calling methods.")
    if previous is not None:
        old = {n["name"]: n for n in previous.to_dict()["nodes"]}
        d = result.to_dict()
        for node in d["nodes"]:
            if node["name"] in old:
                node["x"], node["y"] = old[node["name"]]["x"], old[node["name"]]["y"]
        result = Brain.from_dict(d)
    return result
