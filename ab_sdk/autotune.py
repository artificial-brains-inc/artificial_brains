"""Tune named OPUs using measured trials and the existing local TNorm API.

Only public TNorm values and observations are used. This module does not build
graphs, compute private weights, reset dynamics, or select robot actions.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
import numbers
import os
from pathlib import Path
import tempfile
from typing import Any

from ab_designer.model import _tnorm


@dataclass(frozen=True)
class TuneTrial:
    """One measured attempt; scores are lower when the response is closer."""

    index: int
    kind: str
    tnorms: dict[str, float]
    score: float
    errors: tuple[float, ...]
    target_met: bool


@dataclass(frozen=True)
class AutoTuneResult:
    initial_values: dict[str, float]
    best_values: dict[str, float]
    baseline_score: float | None
    best_score: float | None
    verification_score: float | None
    verified: bool
    target_met: bool
    trials: int
    reason: str
    history: tuple[TuneTrial, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        """Overwrite one public results file; never store device credentials."""
        path = Path(path).expanduser()
        raw = json.dumps(self.to_dict(), indent=2, allow_nan=False) + '\n'
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


class AutoTuneError(RuntimeError):
    """An interrupted run. No extra device writes are made after this error.

    best_values and history describe completed measurements, not a saved neural
    checkpoint. The last probe may still be applied on the device.
    """

    def __init__(self, message: str, *, best_values=None, history=()):
        super().__init__(message)
        self.best_values = deepcopy(best_values or {})
        self.history = tuple(deepcopy(history))


def _number(value, label):
    if not isinstance(value, numbers.Real):
        raise ValueError(f'{label} must be a finite number.')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number.')
    return value


def _array(value, label):
    """Flatten a rectangular numeric observation without inferring its units."""
    if isinstance(value, numbers.Real):
        return (), [_number(value, label)]
    # Accept NumPy arrays without requiring NumPy in the public SDK.
    if hasattr(value, 'tolist'):
        return _array(value.tolist(), label)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError(f'{label} must be a number or a nonempty numeric sequence.')
    items = [_array(v, label) for v in value]
    shape = items[0][0]
    if any(s != shape for s, _ in items):
        raise ValueError(f'{label} must be a rectangular numeric sequence.')
    return (len(items), *shape), [v for _, values in items for v in values]


@dataclass(frozen=True)
class _Goal:
    path: tuple[str, ...]
    mode: str
    shape: tuple[int, ...]
    values: tuple[float, ...]
    lower: float | None
    upper: float | None
    tolerance: float
    scale: float
    weight: float


def _goals(target):
    if not isinstance(target, Mapping) or not target:
        raise ValueError('target must map measurement names to desired values or ranges.')
    found = []
    fields = {'value', 'min', 'max', 'tolerance', 'scale', 'weight'}

    def visit(value, path):
        label = '.'.join(path)
        if isinstance(value, Mapping) and not ({'value', 'min', 'max'} & value.keys()):
            if not value:
                raise ValueError(f'Target {label or "root"} cannot be empty.')
            for key, child in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError('Measurement names must be nonempty strings.')
                visit(child, (*path, key))
            return
        if not path:
            raise ValueError('Give each target measurement a name.')
        spec = dict(value) if isinstance(value, Mapping) else {'value': value}
        if spec.keys() - fields or ('value' in spec and {'min', 'max'} & spec.keys()):
            raise ValueError(f'Invalid target specification for {label}.')
        tolerance = _number(spec.get('tolerance', 0), f'{label} tolerance')
        scale = _number(spec.get('scale', tolerance if tolerance > 0 else 1), f'{label} scale')
        weight = _number(spec.get('weight', 1), f'{label} weight')
        if tolerance < 0 or scale <= 0 or weight <= 0:
            raise ValueError('Tolerance must be nonnegative; scale and weight must be positive.')
        if 'value' in spec:
            shape, values = _array(spec['value'], f'Target {label}')
            lower = upper = None
            mode = 'value'
        else:
            lower = _number(spec['min'], f'{label} minimum') if 'min' in spec else None
            upper = _number(spec['max'], f'{label} maximum') if 'max' in spec else None
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f'Target {label} minimum exceeds its maximum.')
            mode, shape, values = 'range', (), []
        found.append(_Goal(path, mode, shape, tuple(values), lower, upper, tolerance, scale, weight))

    # The root is always a collection of named signals, including a signal
    # legitimately called "value", "min", or "max".
    for key, value in target.items():
        if not isinstance(key, str) or not key:
            raise ValueError('Measurement names must be nonempty strings.')
        visit(value, (key,))
    return tuple(found)


class _Scorer:
    def __init__(self, target, error):
        if (target is None) == (error is None):
            raise ValueError('Supply target or error, but not both.')
        if error is not None and not callable(error):
            raise ValueError('error must be a callable returning numeric residuals.')
        self.goals = _goals(target) if target is not None else ()
        self.error = error
        self.shapes = None

    def __call__(self, observation):
        errors, terms, weights, shapes = [], [], [], []
        met = True
        if self.error is not None:
            shape, values = _array(self.error(observation), 'error result')
            errors = values
            shapes = [shape]
            terms = [math.fsum(v * v for v in values) / len(values)]
            weights = [1.0]
            met = all(v == 0 for v in values)
        else:
            for goal in self.goals:
                value = observation
                for name in goal.path:
                    if not isinstance(value, Mapping) or name not in value:
                        raise ValueError(f'Trial did not return measurement {".".join(goal.path)}.')
                    value = value[name]
                shape, values = _array(value, '.'.join(goal.path))
                shapes.append(shape)
                if goal.mode == 'value':
                    if shape != goal.shape:
                        raise ValueError(f'Measurement {".".join(goal.path)} has a different shape from its target.')
                    delta = [v - desired for v, desired in zip(values, goal.values)]
                else:
                    delta = [v - goal.lower if goal.lower is not None and v < goal.lower
                             else v - goal.upper if goal.upper is not None and v > goal.upper
                             else 0.0 for v in values]
                met = met and all(abs(d) <= goal.tolerance or math.isclose(
                    abs(d), goal.tolerance, rel_tol=1e-12, abs_tol=0.0) for d in delta)
                residuals = [d / goal.scale for d in delta]
                errors.extend(residuals)
                terms.append(goal.weight * math.fsum(v * v for v in residuals) / len(residuals))
                weights.append(goal.weight)
        if self.shapes is not None and shapes != self.shapes:
            raise ValueError('Trial measurement shapes changed; align recorded sequences before scoring.')
        self.shapes = shapes
        score = math.fsum(terms) / math.fsum(weights)
        if not math.isfinite(score) or any(not math.isfinite(v) for v in errors):
            raise ValueError('Trial score is not finite; check measurement units and scales.')
        return score, tuple(errors), met


def _state(value):
    if not isinstance(value, dict) or not value.get('available'):
        message = value.get('message') if isinstance(value, dict) else None
        raise ValueError(message or 'The installed Brain has no live TNorm support.')
    for key in ('package_id', 'session_id'):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValueError(f'Device tuning response is missing {key}.')
    if type(value.get('revision')) is not int or value['revision'] < 0:
        raise ValueError('Device tuning response has an invalid revision.')
    if not isinstance(value.get('opus'), list):
        raise ValueError('Device tuning response is missing OPUs.')
    names = {}
    for opu in value['opus']:
        if not isinstance(opu, dict) or not isinstance(opu.get('name'), str) or not opu['name']:
            raise ValueError('Device returned an invalid OPU.')
        if opu['name'] in names:
            raise ValueError('Device returned duplicate OPU names.')
        names[opu['name']] = _tnorm(opu.get('tnorm'))
    return deepcopy(value), names


class _Cancelled(Exception):
    pass


class _Runner:
    def __init__(self, device, names, trial, scorer, maximum, progress, cancelled):
        self.device, self.names, self.trial, self.scorer = device, names, trial, scorer
        self.maximum, self.progress, self.cancelled = maximum, progress, cancelled
        self.state, self.all_values = _state(device.tuning())
        missing = set(names) - self.all_values.keys()
        if missing:
            raise ValueError('Unknown tunable OPUs: ' + ', '.join(sorted(missing)))
        self.initial = {name: self.all_values[name] for name in names}
        self.count = 0
        self.history = []

    def check(self):
        fresh, values = _state(self.device.tuning())
        keys = ('package_id', 'session_id', 'revision')
        if any(fresh[k] != self.state[k] for k in keys) or values != self.all_values:
            raise RuntimeError('The installed Brain or TNorm state changed outside autotune. No further adjustments were sent.')
        self.state = fresh

    def apply(self, values):
        self.check()
        changes = {name: value for name, value in values.items() if value != self.all_values[name]}
        if not changes:
            return
        expected = {**self.all_values, **changes}
        answer, actual = _state(self.device.tune(changes, state=self.state))
        if (answer['package_id'] != self.state['package_id']
                or answer['session_id'] != self.state['session_id']
                or answer['revision'] != self.state['revision'] + 1 or actual != expected):
            raise RuntimeError('Device did not confirm the requested TNorm adjustment. No further adjustments were sent.')
        self.state, self.all_values = answer, actual

    def measure(self, values, kind):
        if self.cancelled():
            raise _Cancelled()
        if self.count >= self.maximum:
            raise RuntimeError('Autotune trial budget exhausted.')
        self.apply(values)
        if self.cancelled():
            raise _Cancelled()
        self.count += 1
        observation = self.trial()
        self.check()
        score, errors, met = self.scorer(observation)
        record = TuneTrial(self.count, kind, dict(values), score, errors, met)
        self.history.append(record)
        if self.progress:
            self.progress(deepcopy(record))
        return record


def autotune(device, *, opus: Sequence[str], trial: Callable[[], Any],
             target: Mapping | None = None, error: Callable | None = None,
             max_trials: int | None = 30, step: float = 0.5, verify_trials: int = 2,
             on_trial: Callable[[TuneTrial], None] | None = None,
             cancel=None, save_to: str | Path | None = None) -> AutoTuneResult:
    """Adjust the listed OPUs, evaluating real observations after each trial.

    ``trial()`` runs a comparable action and returns named measurements. Targets
    may be numbers, aligned numeric sequences, nested dictionaries, or specs
    such as ``{"value": 2.0, "tolerance": .2}`` and ``{"min": .3}``. An optional
    ``error(observation)`` may return residuals instead; zero means success.

    A coordinate search tries plus/minus TNorm changes on the existing 0.01 grid.
    Each candidate is compared with the incumbent in interleaved trials and
    must improve every comparison before being accepted. The final settings
    are measured again. ``max_trials`` includes all reference and verification
    attempts. Pass ``max_trials=None`` for no trial-count limit; the other
    stopping conditions still apply. Omitting it retains the default of 30.
    There is no guarantee that a target is achievable.

    This function owns TNorm adjustments only. The caller owns Brain startup,
    input/output streaming, the body, and cleanup. Neural dynamics and learning
    continue; neither is silently reset or disabled. Restoring TNorm settings
    is not restoring a previous neural checkpoint.

    On normal completion, best_values remain applied. Cancellation is checked
    between attempts; pass a threading.Event or a zero-argument predicate. A
    blocking trial must observe the same event itself for prompt cancellation.
    On error, AutoTuneError carries completed history and best_values; no
    automatic write follows a failed operation or an ownership conflict.
    """
    if isinstance(opus, (str, bytes)) or not isinstance(opus, Sequence) or not opus:
        raise ValueError('opus must be a nonempty sequence of OPU names.')
    if any(not isinstance(name, str) or not name for name in opus):
        raise ValueError('OPU names must be nonempty strings.')
    names = tuple(dict.fromkeys(opus))
    if not callable(trial) or (on_trial is not None and not callable(on_trial)):
        raise ValueError('trial and on_trial must be callable.')
    if max_trials is not None and (type(max_trials) is not int or max_trials < 1):
        raise ValueError('max_trials must be a positive integer or None.')
    if type(verify_trials) is not int or verify_trials < 1:
        raise ValueError('verify_trials must be a positive integer.')
    maximum = math.inf if max_trials is None else max_trials
    step = _number(step, 'step')
    ticks = round(step * 100)
    if step <= 0 or ticks < 1 or abs(ticks - step * 100) > 1e-7:
        raise ValueError('step must be positive in increments of 0.01.')
    cancelled = (lambda: False) if cancel is None else getattr(cancel, 'is_set', cancel)
    if not callable(cancelled):
        raise ValueError('cancel must be an Event or a zero-argument callable.')
    scorer = _Scorer(target, error)
    runner = _Runner(device, names, trial, scorer, maximum, on_trial, cancelled)
    best = dict(runner.initial)
    baseline = []
    confirmation = []
    best_score = None
    reason = 'budget_exhausted'
    searched = False

    def average(records):
        return math.fsum(r.score for r in records) / len(records)

    try:
        for _ in range(min(verify_trials, maximum)):
            baseline.append(runner.measure(best, 'baseline'))
        best_score = average(baseline)
        if len(baseline) == verify_trials and all(r.target_met for r in baseline):
            confirmation = baseline
            reason = 'target_met'
        else:
            # Reserve a fresh final measurement series. Every proposed move
            # also needs reference/candidate pairs, including confirmation.
            required = 2 * verify_trials + verify_trials
            while maximum - runner.count >= required:
                improved = False
                for name in names:
                    for direction in (1, -1):
                        value = min(1000, max(0, round(best[name] * 100) + direction * ticks)) / 100
                        if value == best[name]:
                            continue
                        if maximum - runner.count < required:
                            break
                        candidate = {**best, name: value}
                        references, probes = [], []
                        searched = True
                        for repeat in range(verify_trials):
                            # Alternate pair order to reduce a consistent
                            # advantage from always being measured second.
                            order = [('reference', best), ('probe', candidate)]
                            if repeat % 2:
                                order.reverse()
                            for kind, values in order:
                                record = runner.measure(values, kind)
                                (references if kind == 'reference' else probes).append(record)
                            if not probes[-1].score < references[-1].score:
                                break
                        if (len(probes) == verify_trials
                                and all(p.score < r.score for p, r in zip(probes, references))):
                            best, best_score = candidate, average(probes)
                            improved = True
                            if all(p.target_met for p in probes):
                                reason = 'candidate_target_met'
                            break
                    if reason == 'candidate_target_met':
                        break
                if reason == 'candidate_target_met':
                    break
                if not improved:
                    if ticks == 1:
                        reason = 'resolution_reached'
                        break
                    ticks = max(1, ticks // 2)
            runner.apply(best)
            if searched:
                for _ in range(min(verify_trials, maximum - runner.count)):
                    confirmation.append(runner.measure(best, 'verify'))
            else:
                confirmation = baseline
            if len(confirmation) == verify_trials and all(r.target_met for r in confirmation):
                reason = 'target_met'
            elif reason == 'candidate_target_met':
                reason = 'target_not_confirmed'
        runner.check()
    except _Cancelled:
        reason = 'cancelled'
        confirmation = []
        try:
            runner.apply(best)
        except Exception as exc:
            raise AutoTuneError(str(exc), best_values=best, history=runner.history) from exc
    except Exception as exc:
        raise AutoTuneError(str(exc), best_values=best, history=runner.history) from exc

    # A cancellation before the first attempt has no measured baseline.
    baseline_score = average(baseline) if baseline else None
    result = AutoTuneResult(
        dict(runner.initial), dict(best), baseline_score,
        best_score if best_score is not None else baseline_score,
        average(confirmation) if confirmation else None,
        len(confirmation) == verify_trials,
        len(confirmation) == verify_trials and all(r.target_met for r in confirmation),
        runner.count, reason, tuple(runner.history),
    )
    if save_to is not None:
        result.save(save_to)
    return result
