# Automatic TNorm tuning

SDK 0.4.6 adds `autotune()`. Choose the OPUs whose TNorms may change, provide a
repeatable action, and describe the response you want. The SDK tries adjustments
and compares measured results. The brain continues to produce the robot's actions.

## Where measurements come from

The controller already receives observations from its robot drivers or simulator.
The trial returns the observations needed to judge the action. These can be raw
physical measurements, decoded brain outputs, or both. The tuner does not discover
sensors, convert encoded input vectors back into physical units, or invent missing
measurements.

For example, a gripper trial can return:

```python
{
    "hold_force_n": 1.7,
    "peak_force_n": 2.3,
}
```

Those values come from the controller's force measurements. A standing-up trial
could return body tilt from an IMU and the time the robot remained upright. Body
height is an option when an actual position/distance measurement is available;
a simulator can supply its body's position. Use the same units in the measurements
and targets. Scoring does not require adding more external inputs to the brain.

`trial()` owns preparing the body, sending the task cue through the existing
controller, collecting observations, and ending the attempt. It must return when
that attempt is complete. It does not need to calculate an error for ordinary
targets.

## Tune a selected pathway

The example below is a reusable function. `device` is an existing
`PrimaLuxClient`, and `close_and_release` is the developer's callable that returns
the two measurements above after each complete grasp-and-release attempt.

```python
from ab import autotune


def tune_grip(device, close_and_release):
    return autotune(
        device=device,
        opus=["GRIP_T1", "GRIP_T2", "GRIP_MOTOR"],
        trial=close_and_release,
        target={
            "hold_force_n": {"value": 2.0, "tolerance": 0.2},
            "peak_force_n": {"max": 2.5, "scale": 0.2},
        },
        max_trials=30,
        save_to="grip-tuning.json",
    )
```

Use the actual OPU names in the installed brain. The list may cover a pathway,
branches, or feedback connections; it does not have to be a simple chain. Only
the listed TNorm parameters are changed. Their effects can propagate through the
connected network. No learning rule is required: static, STDP, STDP3, and
STDP3_SLOW pathways can all be tuned. The existing 0–10 TNorm range and precision
of 0.01 apply.

The force maximum in this example contributes to scoring. Physical limits and
immediate stopping remain the responsibility of the robot controller.

## Targets

A target name must match a key returned by the trial. Nested dictionaries can
identify phases or separate neural outputs from physical measurements.

| Target | Meaning |
| --- | --- |
| `{"force_n": 2.0}` | Match 2.0 exactly. For noisy measurements, specify a tolerance. |
| `{"force_n": {"value": 2.0, "tolerance": 0.2}}` | Aim for 2.0; readings within 0.2 meet this target. |
| `{"height_m": {"min": 0.3}}` | Reach at least 0.3. |
| `{"tilt_deg": {"max": 5}}` | Stay at or below 5. |
| `{"force_n": {"min": 1.8, "max": 2.2}}` | Stay inside the inclusive range. |
| `{"command": {"value": [0, 1, 1, 0], "tolerance": 0.05}}` | Match an aligned recorded sequence. |

For named phases and one-neuron outputs:

```python
target = {
    "hold": {"outputs": {"CLOSE": 1, "OPEN": 0}},
    "release": {"outputs": {"CLOSE": 0, "OPEN": 1}},
}
```

The trial returns the same structure with the observed values. In this example,
1 means active and 0 means inactive. Wider outputs may use vectors or a defined
decoded measurement. The `outputs` key is a label in your returned data; it does
not automatically subscribe to the device. Use your existing output callbacks
to capture the relevant values.

For teaching by demonstration, the recorded values are the targets and the trial
supplies corresponding observations. Match the sampling times and dimensions
before returning a sequence. The tuner does not silently align, interpolate, or
truncate different traces. A replay trial supplies the same recorded inputs;
a physical trial measures the outcome of the robot acting under its own control.

The score is the weighted mean squared normalized error. Each target can specify
`scale` to express a meaningful error size in its own units and `weight` to set
its relative importance. Scale defaults to a positive tolerance, or 1 when
tolerance is zero; weight defaults to 1. Sequences contribute their mean squared
error so a longer trace does not automatically outweigh another measurement.

For a value target, the tuner aims at the specified value. For a range target,
there is no error inside the range. Tolerance determines whether every sample is
acceptable; a range tolerance allows that amount beyond its boundaries. Missing,
non-finite, or differently shaped observations are errors, never successful trials.

## Use it with an existing controller

Import `autotune` from `ab`, `ab_sdk`, or `ab_sdk.autotune`. The caller supplies an
authenticated device client and keeps its normal sensor/output session running.

```python
import os
from ab import PrimaLuxClient, autotune


def tune_existing_controller(run_trial):
    # run_trial is the application's existing trial routine. It returns
    # {"hold_force_n": ..., "peak_force_n": ...} from real observations.
    with PrimaLuxClient.from_env(os.getenv("AB_ENV", ".env")) as device:
        return autotune(
            device,
            opus=["GRIP_T1", "GRIP_T2", "GRIP_MOTOR"],
            trial=run_trial,
            target={"hold_force_n": {"value": 2.0, "tolerance": 0.2}},
            max_trials=30,
        )
```

The Designer does not need to be open. `autotune()` does not start a second robot
controller, start or stop the brain, rebuild a package, reset neural dynamics, or
disable learning. Keep the application's existing startup and cleanup. Use a
dedicated controller thread if the main thread must continue handling a simulator
or GUI. Do not run the tuning loop inside a sensory or neural-output callback.

For a project whose `run.py` integrates this function, the normal workflow remains:

```bash
ab design --project . --brain brain.abdesign.json --env .env
ab build brain --brain brain.abdesign.json --env .env
ab run --env .env
```

Build once and wait for READY. Later tuning attempts use the installed brain;
they do not rebuild it. The installed package must already support live TNorm.
The SDK uses the existing local tuning endpoint. No compilation-server call is
made during optimization, and no new factory, Device Manager, compiler, or RTL
change is needed for this feature once live TNorm works.

## How a run proceeds

1. Read the installed brain's current TNorm values and measure a baseline.
2. Try an increase or decrease in a selected OPU's TNorm.
3. Compare the candidate and current accepted settings in repeated trials. Pair
   order alternates so the candidate is not always measured second.
4. Accept a candidate only when it improves every paired comparison.
5. Reduce the search step when a full pass finds no improvement, down to 0.01.
6. Measure the final accepted settings again and return the result.

The initial search step defaults to 0.5. `step=0.1` starts with smaller changes.
`verify_trials=2` requests two measurements for each compared setting and for
final verification. `max_trials` includes baseline, reference, candidate, and
verification attempts. A larger list of OPUs generally needs more trials. The
search stops when the target is confirmed, there is not enough budget for another
complete comparison, cancellation is requested, or no coordinate improvement is
found at the smallest step. It may finish before using every remaining trial.

Pass `max_trials=None` to remove the trial-count limit. It can then continue
beyond 30 attempts, while still stopping when the target is confirmed, the
search finishes without further improvement, or you cancel. Passing a positive
integer sets a limit; leaving the argument out keeps the existing default of 30.

This is a local search over the selected TNorms. It can improve an existing
pathway; it cannot guarantee that a target is reachable or that it found a global
optimum. It does not infer a missing circuit or change topology.

The brain retains history and learning between attempts. Trials must provide
comparable opportunities to act. Interleaving and repeating comparisons help
with noise and drift, but cannot make changing physical or neural conditions
identical. Restoring TNorm values does not rewind memory or learned weights.

## Results, cancellation, and progress

```python
print(result.best_values)
print(result.baseline_score)
print(result.verification_score)
print(result.target_met)
print(result.reason)
result.save("grip-tuning.json")
```

`best_values` contains the settings accepted by the comparisons. Normal
completion leaves them applied. `baseline_score` is the initial average;
`best_score` is the score when the selected settings were accepted;
`verification_score` is the fresh final average. Under continuing learning or
changing conditions, these scores can differ even at the same TNorm values.

`verified` means the requested final measurement series completed. It does not
mean the goal was reached; check `target_met`. `history` records every completed
trial's settings, score, normalized residuals, and target status. Result files
contain public tuning results, never construction formulas or credentials.
Saving to the same path replaces that results file.

Pass `on_trial=callback` to receive a `TuneTrial` after each completed attempt.
For an existing `ApplicationUI` panel:

```python
def show_progress(record):
    panel.value("Autotune attempt", record.index)
    panel.value("Autotune score", record.score)
```

Pass a `threading.Event` as `cancel`, and call its `set()` method from a Stop
button. The tuner checks cancellation between attempts. For prompt interruption
inside a long attempt, the trial itself must also observe that event and perform
its normal robot cleanup. On cancellation, the last accepted TNorm settings are
reapplied only if the device still confirms the same brain and tuning state;
no further trials are started.

An external TNorm edit, a different installed brain, or a neural reset interrupts
the tuner. It never silently replays changes onto another brain. Do not have the
trial alter TNorm or reset dynamics itself. A failed trial or connection raises
`AutoTuneError`; its `best_values` and `history` describe completed work. No
automatic device write follows an error, so the last probe may remain applied.
The controller still owns stopping the body and session on failure.

Stop and wait to save neural state and live TNorm on Prima-Lux. The JSON result
does not replace that neural checkpoint. The local Brain design is not rewritten;
save chosen defaults in the design explicitly before a future build.

## Custom goals

Use `error=` instead of `target=` when ordinary values, ranges, or sequences do
not express the goal. It receives the trial result and returns a finite number
or numeric sequence of residuals. All-zero residuals mean the target is met.
The score is their mean square.

```python
def grip_error(observation):
    force_error = max(0.0, abs(observation["hold_force_n"] - 2.0) - 0.2) / 0.2
    excess_peak = max(0.0, observation["peak_force_n"] - 2.5) / 0.2
    return [force_error, excess_peak]
```

This calculation scores an attempt. Neural computation and action selection
continue inside the brain. No target firing pattern or per-neuron spike labels
are required.
