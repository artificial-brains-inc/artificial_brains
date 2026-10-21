# Artificial Brains SDK

Build artificial brains, connect them to the synthetic neurons on **Prima-Lux or ABi**, explore biomimetic intelligence, create applications that let robots and other machines sense, decide, and act as they experience the world.

The SDK provides a common interface for designing an Artificial Brain, preparing it on Prima-Lux, publishing live sensory information, receiving neural outputs, decoding commands, and running a robot's sensor–actuator loop. **Your application owns its sensors, actuators, safety limits, and environment.**

## Quick start

Python 3.10 or later is required. Install the SDK supplied for your environment:

```bash
python -m pip install artificialbrains_sdk
ab --help
```

Import its Python API with `from ab import ...`. You can use the CLI as `ab` or `python -m ab`.

A typical project looks like this:

```text
my-robot/
├── .env
├── brain.py                 # optional Python source for the Brain
├── brain.abdesign.json      # saved Brain design used by the application
└── run.py                   # your sensor and actuator application
```

The usual workflow is:

1. Design or edit an Artificial Brain using Python or Canvas.
2. Build it on the synthetic neurons on Prima-Lux or ABi and wait for **READY**.
3. Run your Python application, which sends sensory observations and handles neural outputs.
4. Stop the application and wait for Prima-Lux to finish saving neural state.

## 1. Create your first Artificial Brain

Create `brain.py`:

```python
from ab import Brain

brain = Brain("First Brain")

# Named external input populations
brain.input("SENSE", 64)

# OPUs: rhythm and, where applicable, TNorm
brain.opu("SENSE_RELAY", "high-gamma")
brain.opu("T1", "theta", 3)
brain.opu("T2", "theta", 2.01)
brain.opu("MOTOR", "beta", 5)

# Named output populations
brain.output("ACTION")       # One output neuron by default
brain.output("STATE", 4)     # Four output neurons

brain.connect("SENSE", "SENSE_RELAY")
brain.connect("SENSE_RELAY", "T1")
brain.connect("T1", "T2")
brain.connect("T2", "MOTOR")
brain.connect("MOTOR", "ACTION")
brain.connect("MOTOR", "STATE")

brain.save("brain.abdesign.json")
```

Run it once to save the design:

```bash
python brain.py
```

### Brain API

| Python method | What it does |
| --- | --- |
| `Brain("Name")` | Creates a named Artificial Brain design. |
| `brain.input("NAME", n)` | Declares an external input population with `n` neurons. |
| `brain.opu("NAME", "theta", 2.01)` | Adds an OPU with a rhythm and TNorm. |
| `brain.opu("RELAY", "high-gamma")` | Adds an input-adjacent high-gamma relay, without TNorm. |
| `brain.output("NAME")` | Adds a named, one-neuron output. |
| `brain.output("NAME", 4)` | Adds a named output with four neurons. |
| `brain.connect("A", "B")` | Connects A to B (excitatory by default). |
| `brain.connect("A", "B", "inh")` | Adds an inhibitory connection. |
| `brain.save("brain.abdesign.json")` | Saves the editable Brain design. |
| `Brain.load("brain.abdesign.json")` | Opens a saved design from Python. |
| `brain.validate()` | Returns design warnings you can review. |
| `brain.render()` | Opens the visual Brain editor. |

OPUs can use **high-gamma, gamma, beta, alpha, theta, or delta**. Rhythmic OPUs take a TNorm from 0 to 10; decimal values such as `2.01` are valid. High-gamma is a fast relay immediately after an external input and does not take a TNorm. All connections are excitatory unless you explicitly choose inhibition. Output neurons hold their state until the Brain clears them.

Choose input population widths to match the sensory encoding that your application will publish. Choose output widths to match the way your application will decode the result.

### Build visually with Canvas or Python

From your project folder:

```bash
ab design --brain brain.abdesign.json
```

The Designer has two views of the **same Brain**: **Canvas** for arranging OPUs and connections and **Python** for editing code. Changes can be made in either view. Save the Brain to retain your work.

Its **Build brain**, **Run brain**, and **Stop brain** controls operate the connected Prima-Lux device. Running the Brain in the Designer starts its neural activity; it does **not** launch a robot application or automatically connect sensors and motors. Use `ab run` to launch your application.

## 2. Connect to Prima-Lux

Complete the initial Prima-Lux or ABi onboarding, obtain your developer API key, and connect your computer to the device. Create a `.env` file in your project:

```dotenv
AB_DEVICE_URL=http://prima-lux.local:8080/
AB_DEVICE_API_KEY=YOUR_DEVICE_API_KEY
```

The URL is an example; use your device's actual address. Keep your `.env` private and out of version control. Prima-Lux also needs Internet access when preparing a new Brain.

To inspect the device:

```bash
ab device --env .env status
ab device --env .env events
```

## 3. Build the Brain

From the project directory:

```bash
ab build brain --brain brain.abdesign.json --env .env
```

You may also build directly from a Python Artificial Brain design:

```bash
ab build brain --brain brain.py --env .env
```

Building prepares and installs the selected Brain on Prima-Lux. Wait for **READY** before starting your application. **Build does not start the Brain.** The compiled Brain is mapped onto the synthetic neurons in Prima-Lux or ABi.

You can provide an Artificial Brain path from a subdirectory with `--brain path/to/brain.abdesign.json`. For `ab run`, also keep an appropriate local design as `brain.abdesign.json` or `brain.py` in the application directory, or explicitly provide its input/output contract. The application needs the correct input and output names to talk to the installed Brain.

## 4. How a robot application works

```text
Your sensors  →  SpikeEncoder  →  RealtimeSession  →  Artificial Brain on Prima-Lux or ABi
Your motors   ←  your motor code ← output decoder ← neural output events
                           ↑
            RobotLoop (optional sensor polling and command handling)
```

| SDK component | Responsibility |
| --- | --- |
| `ABClient` | Connects to Prima-Lux and starts an application session. |
| `RealtimeSession` | Publishes observations, receives output events, registers callbacks, and manages the session lifecycle. |
| `SpikeEncoder` | Converts application sensor readings into input populations. |
| `GenericSpikeDecoder` | Converts neuron-identified output events into named command changes. |
| `ActuationChannelDecoder` | Converts device actuation-channel counts into named command changes. |
| `RobotLoop` | Polls sensors, publishes inputs and optional rewards, and dispatches decoded commands. |

The SDK does not know your robot's wheel geometry, joint limits, camera calibration, or safe operating range. Your application supplies sensor readers, interprets commands, and enforces motor limits and emergency-stop behavior.

## 5. Start a session and use live inputs

Create `run.py` and use a session as the boundary between the robot and the installed Brain:

```python
import os
from ab import ABClient

env_path = os.environ.get("AB_ENV", ".env")
client = ABClient.from_env(env_path=env_path)
session = None

try:
    session = client.start_from_env(env_path=env_path)
    session.on_output(lambda event: print("Brain output:", event))

    # Replace these values with the latest reading from your sensor.
    session.publish_input("SENSE", [0.0] * 64, mode="vector_f32")

    # In a real application, continue capturing sensors and processing outputs.
finally:
    if session is not None:
        session.stop()
    client.close()
```

A session starts the installed Brain if necessary. Register handlers before beginning sensor activity. Use `finally` to stop even if the application encounters an error.

### Session methods

| Method | Use |
| --- | --- |
| `session.publish_input(sensor, signal, ...)` | Send one **current** sensor observation immediately. |
| `session.publish_sensor_frame(readings)` | Send multiple channels captured **at the same time** as one frame. |
| `session.publish_reward(input_name, magnitude)` | Send a reward to an existing named reward input. |
| `session.on_output(callback)` | Observe raw neural output events. |
| `session.set_decoder(decoder)` | Set the output decoder for the application's command representation. |
| `session.on_command(callback)` | Receive decoded commands. |
| `session.on_control(callback)` | Receive session-control events. |
| `session.stop()` | Stop the session and wait for the device's Stop/save confirmation. |

Keep output callbacks short: they run on the SDK output-stream thread. Avoid slow camera or motor operations directly inside an output callback.

### Encoding sensory information

Input names such as `SENSE` must match your Brain's named input populations. The encoded width must match the selected input's neuron count.

For example:

```python
# A 64-value sensory population
session.publish_input("SENSE", sensor_vector, mode="vector_f32")

# One nonnegative measurement represented across its input population
session.publish_input(
    "DISTANCE",
    distance_meters,
    mode="positive_scalar_population",
    vmax=2.0,
    radius=1,
)
```

The following encoding modes are available:

| Mode | Input | Encoding |
| --- | --- | --- |
| `vector_f32` | Numeric vector | Values passed through to the named input population. |
| `binary_spikes` | Numeric vector | Values greater than zero become 1; other values become 0. |
| `positive_scalar_population` | Nonnegative scalar | A local group of neurons represents a value; requires `vmax`. |
| `positive_scalar_cumulative_population` | Nonnegative scalar | A cumulative active range; requires `vmax`. |
| `ranged_scalar_population` | Scalar in a range | Maps `[vmin, vmax]` into a local group. |
| `signed_scalar_population` | Signed scalar | Maps `[-absmax, absmax]` into a local group. |
| `reward_population` | Reward magnitude | Represents a reward between 0 and 1. |

Supply finite, normalized input values compatible with your device and encoding mode. For live Prima-Lux vector inputs, values should be in `[0, 1]`. Sensor preprocessing (such as image resizing and normalization) is performed by your application.

For a simultaneous capture—for example, three color channels from a single image—use:

```python
session.publish_sensor_frame({
    "RETINA_R": red_values,
    "RETINA_G": green_values,
    "RETINA_B": blue_values,
})
```

**Never pre-schedule sensory observations for future neural steps.** `publish_input()` sends each reading when it arrives. `publish_sensor_frame()` is for channels captured together, not a queue of samples from different times. Output step numbers are useful for reading events, not for scheduling inputs.

To inspect how a signal is encoded before sending it:

```python
encoded = session.encoder.encode(
    "DISTANCE", 0.75,
    mode="positive_scalar_population", vmax=2.0,
)
print(encoded.target, encoded.payload, encoded.meta)
```

`session.encoder` is the session's configured `SpikeEncoder`; it already knows the Brain's input map.


### Live neuron telemetry

To see individual neurons firing while your application runs, add this to your project’s `.env`:

```dotenv
AB_TELEMETRY=true
```

The SDK opens a telemetry window when your application starts its Prima-Lux session.

Each dot represents one neuron: bright means firing in the displayed timestep; dark means inactive. Select an OPU to inspect its neurons and firing history, with one row per neuron. Input stimulation is displayed separately from neural firing.

You can pan, zoom, and freeze the view. **Freeze view pauses only the display; the Brain and controller continue running.**

Set `AB_TELEMETRY=false`, or omit it, to disable the viewer. Output streaming works independently of this setting.

This feature requires the updated SDK and Prima-Lux software with neuron telemetry support.


## 6. Decode neural outputs into actions

Outputs are **neural signals**, not robot motor instructions. You decide how to interpret them and which physical actuator limits to apply. Choose the decoder according to the output event representation.

### Live output streaming

Prima-Lux and ABi push neural outputs to the SDK through a continuous connection. Your application does not need to repeatedly request them.

**Every completed timestep is delivered, including unchanged output states and timesteps with no active outputs.** Bistable outputs remain active until the Brain switches them off, so the same active output can appear in successive timesteps.

Use the existing output callback:

```python
def handle_output(event):
    print("Step:", event["step"])
    print("Outputs:", event["outputs"])

session.on_output(handle_output)
```

For motor control, continue using a decoder with `session.on_command(...)` or `RobotLoop`, as shown below.

Output delivery is independent of sensor polling. `RobotLoop.tick_hz` controls how often your application reads sensors; it does not control when neural outputs arrive.

If the connection is interrupted, the SDK attempts to resume from the last received frame while the device’s retained history is available. It reports missing history or a changed Brain run explicitly rather than silently skipping frames. Start a fresh session after resetting neural dynamics.


### Actuation-channel outputs

If a device output event includes `actuation.channels`, use `ActuationChannelDecoder`:

```python
from ab import ActuationChannelDecoder

decoder = ActuationChannelDecoder({
    "ACTION": {
        "motor": "drive",
        "gain": 0.02,
        "limit": [-0.1, 0.1],
    }
})

session.set_decoder(decoder)

def apply_motor_command(command):
    delta = float(command.get("deltas", {}).get("drive", 0.0))
    delta = max(-0.1, min(0.1, delta))
    # Send this bounded value to your actual motor driver.
    print("Drive change:", delta)

session.on_command(apply_motor_command)
```

### Neuron-identified outputs

If the device supplies neuron identities in output events, use `GenericSpikeDecoder`:

```python
from ab import GenericSpikeDecoder

decoder = GenericSpikeDecoder([
    {
        "output_id": "STATE",
        "channel": "drive",
        "scheme": "bipolarSplit",
        "n": 4,
        "per_step_max": 0.05,
        "gain": 1.0,
        "clamp": [-0.1, 0.1],
    }
])

session.set_decoder(decoder)
session.on_command(apply_motor_command)
```

`GenericSpikeDecoder` supports `bipolarSplit`, `addition`, `booleanThreshold`, and `bipolarScalar`. The decoder returns a command such as `{"t": 123, "deltas": {"drive": 0.05}}`; your motor driver decides how to apply it. If you only want to inspect the Brain's raw outputs, register `session.on_output()` without setting a decoder.

**Do not assume actuation-channel counts and neuron-identified spikes are interchangeable.** Check the actual events from your Brain. Always apply robot-side speed, force, joint, and emergency-stop limits independently of decoding.

## 7. Use `RobotLoop` for continuous robot control

`RobotLoop` is the convenient option when your robot application polls sensors at a regular rate. It joins sensor reading, input publication, optional rewards, and decoded command handling.

For example, add the following to your application after creating the session and selecting the appropriate decoder:

```python
from ab import RobotLoop

def read_sensor_state():
    # Replace the example measurement with your robot's sensor reading.
    distance_meters = 0.75
    return {
        "SENSE": {
            "signal": distance_meters,
            "mode": "positive_scalar_population",
            "vmax": 2.0,
            "radius": 1,
        }
    }

loop = RobotLoop(
    session,
    state_provider=read_sensor_state,
    command_executor=apply_motor_command,
    input_mode="realtime",
    tick_hz=20.0,
    strict=True,
)

try:
    loop.run_forever()
finally:
    loop.stop()
    session.stop()
```

For this example, change the `SENSE` input and its encoding as appropriate for your real sensor; the Brain and encoder must agree on its width and meaning.

- `state_provider` returns current named sensor readings; it can supply raw values or per-sensor encoding options.
- `tick_hz` is **your robot's sensor polling rate**, not the Brain's neural execution rate.
- `input_mode="realtime"` sends readings immediately rather than scheduling future neural steps.
- `strict=True` reports unknown sensor names instead of silently ignoring them.
- `command_executor` applies decoded commands through your application's motor driver.

`RobotLoop` registers its own command callback when `command_executor` is supplied. Normally, **do not also register `session.on_command(apply_motor_command)`** for the same action, or you may process one command twice.

If a camera or another sensor already provides event-driven callbacks, use `session.publish_input()` directly at capture time instead of forcing it through a polling loop. Outputs are received independently of the sensor polling rate.

### Rewards

Rewards are sent to named input populations already defined in the Artificial Brain:

```python
session.publish_reward("REWARD", 0.8)
```

Or define a reward source for `RobotLoop`:

```python
def reward_state():
    return {"rewards": {"REWARD": 0.8}}

loop = RobotLoop(
    session,
    state_provider=read_sensor_state,
    command_executor=apply_motor_command,
    reward_provider=reward_state,
    input_mode="realtime",
)
```

Your robot application decides when and how rewards are measured. The SDK only encodes and publishes them to the corresponding Artificial Brain inputs.

## 8. Complete `run.py` example

This example demonstrates an application that reads one sensor, uses a decoder, and handles motor commands. Its sensor and motor functions are **demonstrations only**—replace them with your real robot drivers and safety controls. It expects an Artificial Brain with a 64-neuron `SENSE` input and an `ACTION` output exposed as a device actuation channel.

```python
"""Example robot application for an installed Brain on Prima-Lux."""
import os
from ab import ABClient, ActuationChannelDecoder, RobotLoop


def read_sensor_state():
    # Replace this demonstration reading with your robot sensor.
    distance = 0.75
    return {
        "SENSE": {
            "signal": distance,
            "mode": "positive_scalar_population",
            "vmax": 2.0,
            "radius": 1,
        }
    }


def apply_motor_command(command):
    change = float(command.get("deltas", {}).get("drive", 0.0))
    change = max(-0.1, min(0.1, change))
    # Replace with your safe actuator implementation.
    print("Drive change:", change)


def main():
    env_path = os.environ.get("AB_ENV", ".env")
    client = ABClient.from_env(env_path=env_path)
    session = None
    loop = None

    try:
        session = client.start_from_env(env_path=env_path)

        session.set_decoder(ActuationChannelDecoder({
            "ACTION": {
                "motor": "drive",
                "gain": 0.02,
                "limit": [-0.1, 0.1],
            }
        }))

        loop = RobotLoop(
            session,
            state_provider=read_sensor_state,
            command_executor=apply_motor_command,
            input_mode="realtime",
            tick_hz=20.0,
            strict=True,
        )
        loop.run_forever()
    finally:
        if loop is not None:
            loop.stop()
        if session is not None:
            session.stop()
        client.close()


if __name__ == "__main__":
    main()
```

Build the Artificial Brain first and wait for READY, then launch the application:

```bash
ab build brain --brain brain.abdesign.json --env .env
ab run -ab --env .env
```

The `-ab` flag is supported for the Prima-Lux and ABi target; `ab run --env .env` is also valid. The run command starts the installed Artificial Brain and the application's `run.py`. It does **not** rebuild or replace the Brain. Stop the application with `Ctrl+C` to initiate its normal Stop/save sequence.

## 9. Device commands and lifecycle

```bash
ab device --env .env status
ab device --env .env events
ab device --env .env run
ab device --env .env stop
ab device --env .env reset-dynamics
```

- **Build** maps an Artificial Brain into the Synthetic Neurons in your chip or device, and leaves it READY.
- **Run** starts or resumes the installed Artificial Brain. `ab run` also starts your Python application.
- **Stop** waits for Prima-Lux or ABi to confirm that the neural state has finished saving; request acceptance alone is not completion.
- **Reset dynamics** starts the installed Artificial Brain's neural dynamics from its initial state, discarding accumulated changes, memory and learnings. It does not change your robot's physical state.

An Artificial Brain's saved neural state and your robot's environment are separate. Your application is responsible for camera, joint, world, or other external state when resuming in a changed environment. Avoid disconnecting or powering down the device while Stop is still in progress.

## 10. Troubleshooting

| Symptom | Check |
| --- | --- |
| `ab` command not found | Ensure you installed the SDK in the currently active Python environment. |
| Cannot reach Prima-Lux or ABi | Verify Ethernet, `AB_DEVICE_URL`, API key, and device power. |
| Build is not READY | Check `ab device --env .env status` and `events`; verify Prima-Lux has Internet access for builds. |
| Brain input name not found | Match the sensor name exactly to `brain.input(...)` and the application's I/O contract. |
| Brain design not found when running | Keep `brain.abdesign.json` or `brain.py` in the application directory, or configure `AB_DEVICE_CONTRACT`. |
| Encoded input rejected | Check input width, finite values, normalization, and mode-specific limits (`vmax`, `vmin`, `absmax`). |
| Output events arrive but the robot does not move | Verify the correct decoder type, output names, `command_executor`, and actual motor driver. |
| Commands are applied twice | Don't register the same motor callback both through `RobotLoop` and `session.on_command()`. |
| The robot moves incorrectly | Verify sensor mapping, sign conventions, actuator limits, and the installed Brain before changing motor gains. |
| Application stops before sensor activity | Check that `run.py` starts the session and keeps the sensor loop or callbacks alive. |
| Changed Brain design has no effect | `ab run` uses the Brain already installed on Prima-Lux; rebuild explicitly to install changes. |

## Guidance for development agents

When an AI coding agent or another developer helps build your robot application, give it the "Introductory Guide to Building an Artificial Brain" and this README. The important integration rules are:

1. Use `from ab import Brain` to define inputs, OPUs, connections, and outputs. Keep the design in a saved `brain.abdesign.json` or Python source file.
2. Match every sensor and output name in application code to the installed Brain's input/output contract.
3. Keep sensor acquisition and actuator safety logic in the application. Use SDK encoding and decoding for the neural interface.
4. Publish current observations as they arrive; only group channels that were captured simultaneously. Never assign future neural steps to inputs.
5. Use `RobotLoop` for sensor polling, or publish inputs directly from event-driven sensor callbacks.
6. Test the actuator command handler and its physical limits independently.
7. Stop the session and wait for neural-state saving before disconnecting the device.

See [AGENTS.md](AGENTS.md) for a shorter agent-facing checklist.

## Next steps

Use the **"Introductory Guide to Building an Artificial Brain"** for understanding how synthetic neurons and artificial brains work, and visit our github repository to find examples, primitives and new documentation. Start with one sensory pathway and one observable output, then extend the Artificial Brain and your robot application as you verify each behavior.

Your Python application is the middleware between the Artificial Brain and the robot: connect cameras, proximity sensors, robot joints, simulators like Webots, Alakazam, or other sensor and actuator drivers through the same live SDK interface. 

## License

See [LICENSE](LICENSE) for the terms supplied with your SDK distribution.
