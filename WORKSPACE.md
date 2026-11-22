# Artificial Brains workspace · SDK 0.4.5

Open the workspace from your controller project:

```bash
python -m ab design --project .
```

Canvas, Neuron activity, Application and Python share the same workspace. Build
installs the current brain. Run starts that brain and, if configured, your
controller. Stop ends the controller and waits for the brain to stop and save.
Run does not build a different graph automatically.

## Tune an OPU while the brain runs

Click an OPU on the canvas. Its small TNorm panel opens beside it. Move the slider
or enter an exact number; the latest value is sent automatically. There is no
separate enable button or copy-to-design step.

The panel distinguishes a value being sent from one the device has confirmed.
While a request is in flight, newer changes replace older unsent positions. The
next request uses the latest values and the device's current revision. A change
is never replayed onto a different installed brain.

Confirmed values update the canvas and its generated Python. **Save brain** exports
the current design. **Stop** saves neural state and live TNorm on the device.
**Reset dynamics**, in the ••• menu, restores the compiled TNorm values and clears
accumulated dynamics and learning. It also refreshes matching values on the canvas.

TNorm belongs to the OPU, including high-gamma and dopaminergic OPUs. It is not a
learning-rule setting, an inhibition percentage, or a fuzzy truth value. The
existing 0–10 range and two-decimal precision are unchanged. TNorm 0 does not mean
OFF. Learning-rule and output behavior are unchanged.

Live tuning uses the existing Device Manager/runtime endpoint. An installed brain
must contain the tuning data produced by Brain Builder 0.4.13 and the corrected
ABP Live TNorm v1 compiler. Factory/Device Manager 0.6.21 supplies that runtime.
Older packages still run, but a UI update cannot add missing tuning data to them.

The panel now identifies a missing endpoint, connection failure, a different
canvas design, or a package without tuning data separately. If a fresh build
still lacks tuning data, it points to the server builder/compiler installation
instead of repeatedly telling you to build again. The interface refreshes this
state after Build and Run. The server compiler repair remains a separate install.

## Primitives follow the booklet

Choose **Add a primitive**. Pick its kind, give the instance a name, and optionally
connect existing source populations to its input ports. You can also connect the
ports on the canvas afterward. Every internal population and connection remains
visible and editable. Drag a primitive's header to move its circuit together.
Each family has its own color and symbol.

These are public circuit templates, expanded into the ordinary Brain design.
They do not introduce new neuron types, software Boolean evaluation, or new
compiler calculations. Existing saved designs retain their existing circuits.

| Primitive | Starting circuit |
| --- | --- |
| Sensor | Input → high-gamma relay |
| Reflex / ACK | High-gamma → bistable output → high-gamma reset → inhibition to the output |
| Motor | Theta T3 → Theta T2.01 → Beta T5 → output; Beta T3 feedback resets the state and clears the output |
| Decision nucleus | One or more supporting signals, and optional opposing signals, feed a Theta T3 candidate state through compatible Theta T3 operands |
| Boolean OR | A + B into Theta T5 |
| Boolean AND | Delta T2 A and B → local Delta T2 OR helper; A + B − OR into a separate Delta T2 result |
| Boolean XOR | 2 OR − A − B into Alpha T5; the Theta T5 OR passes through a separate Alpha T5 canonical relay |
| Boolean NOT | Active REF − A into Theta T5 |
| Boolean NOR | Active REF − local OR into Beta T5 |
| Fuzzy OR | Theta T5 OR at each matching channel position |
| Fuzzy AND | The complete Delta T2 AND circuit at each matching channel position |
| Fuzzy NOT | Reverse the input channel order, then apply Theta T5 reference-based NOT at each position |

The Boolean connections use `role="operand"` and the indicated units. Inhibitory
arithmetic terms keep the default strength of 1. AND does not substitute the
half-unit coincidence rule or a hard veto for its local OR cancellation. XOR
keeps the two-unit OR contribution. Operand units are also visible on canvas edges.

NOT and NOR require an active reference: connect REF to a population that actually
supplies it. An unconnected REF does not manufacture activity. A and B ports accept
already formed neural conditions through the template's canonical populations.

Fuzzy primitives have a selectable bank width. Eight is the initial selection,
not a cap. Their A_1…A_m and B_1…B_m ports refer to ordered internal Boolean
populations. No raw sensor input is created for each threshold. Connect the full
bank in order and read it across the receiving rhythm's complete cycles. A fuzzy
value is the bank's activity pattern, not a TNorm setting.

The decision primitive is an evidence stage for a candidate action. It has at
least one supporting input and optional opposing inputs; it is not an automatic
winner-take-all selector. Connect its `decision` port into the candidate's motor
pathway. The booklet's two-input Delta T2 half-unit coincidence motif is a
separate local rule, not this general candidate stage or Boolean AND.

Recipe references: *OPU–OOS Formula Booklet*, Sep27 edition, §§10.6–10.9,
10.12–10.14.6 and 10.15; *Building Artificial Brains*, Boolean logic, fuzzy
composition, shallow decisions and motor-state guidance. Public code contains
rhythms, TNorm and operand units; private construction remains in the builder.

### Python declarations

```python
from ab import Brain

brain = Brain("Reach")
brain.primitive("sensor", "SENSE", neurons=6)
brain.primitive("decision", "CHOOSE", inputs=2, contradictions=1,
                sources={"SUPPORT_1": "SENSE_RELAY"})
brain.primitive("motor", "REACH", source="CHOOSE_DECISION")
```

Connect the remaining support/opposition ports to the relevant circuit. For a
Boolean or fuzzy primitive:

```python
brain.primitive("boolean-and", "BOTH")
brain.primitive("fuzzy-and", "NEAR_AND_VISIBLE", width=8)
```

`brain.port("BOTH", "A")` returns `BOTH_A`. Boolean result ports are `RESULT`;
fuzzy result ports are `RESULT_1` through `RESULT_m`. Decision ports are
`SUPPORT_1`…`SUPPORT_n`, optional `OPPOSITION_1`…`OPPOSITION_n`, and `decision`.
Motor ports remain `drive`, `state`, `motor`, `reset`, and `output`.

For a literal canvas declaration, use `sources={"A": "EXISTING_A", "B": "EXISTING_B"}`
with existing names. For ordinary Python generators, `brain.port()` is also
available. The canvas exports the expanded circuit, so the generated Python
shows exactly what is connected. Canvas groups and coordinates do not control
compilation.

**Save selected circuit** and **Import a primitive** preserve the selected
internal connections and reward mappings. Include their reward populations when
saving learning connections. Connect external signals after insertion.

## Run your controller from Application

Open **Application → Configure application** and choose your project's Python
entry point. An unambiguous `run.py`, `main.py`, or `app.py` is detected
already. The existing `run.sh ui` convention is also supported where Bash is
available. Arguments are passed as arguments, without shell evaluation.

The workspace uses the project's `.venv` Python if present, otherwise its own
Python. If your controller uses another environment, choose that Python executable
under **Python environment**. Install SDK 0.4.5 in the controller's environment
as well when using `ApplicationUI`.

Settings are saved in `ab.workspace.json` in the project. The workspace retains
the normal `AB_BACKEND`, `AB_ENV`, and project variables. Device credentials are
passed only to the controller process, never to the browser.

Press **Run** after Build reports READY. The brain starts and your controller is
launched. A configured application opens in the Application tab. The controller
log is available below its interface. If the launched controller exits, the
workspace requests Stop and waits for neural-state saving. **Stop** first asks
the controller to exit, then stops and saves the brain. Keep your normal cleanup
in `finally` so robot resources are released.

Existing commands such as `ab run`, and externally started controllers, continue
to work. The workspace only owns the process it launches. Do not run a second copy
of the same controller from another terminal while launching it from Run.

### Buttons and readouts supplied by Python

Add `ApplicationUI` to your existing controller. In this example, `controller`
is your existing application object and its `home` and `pause` methods already
implement the requested body commands:

```python
from ab_sdk.workspace import ApplicationUI

panel = ApplicationUI("My robot")
panel.button("home", "Home", controller.home)
panel.button("pause", "Pause", controller.pause)
panel.value("Mode", "Ready")
panel.start()

# Your existing sensor/output loop continues unchanged.
# Call panel.value("Mode", "Moving") when the displayed state changes.
# Call panel.close() in your controller's existing finally block.
```

Buttons run the registered Python callbacks on a separate application-controls
thread. Keep callbacks short or forward work to your controller's queue. Readout
updates are sent on a separate publisher thread; `panel.value()` does not wait on
HTTP in a neural-output or sensor callback. Readout values must be JSON serializable.
Application controls do not replace the SDK's input or output streaming APIs.

### Use an existing web interface

Enter its URL, such as `http://127.0.0.1:8501/`, or a project HTML file such as
`ui/index.html`, in **Configure application**. Your entry point should start any
web server that interface needs. The Application tab displays it when Run is
pressed. A controller can also register its URL with:

```python
panel = ApplicationUI("My robot", url="http://127.0.0.1:8501/")
panel.start()
```

An existing interface that forbids embedding has an **open in its own window**
link. Local HTML assets are served from the selected HTML directory; the workspace
does not expose arbitrary project files or the `.env` file.

## Individual-neuron activity

The default activity view has a population list and large, numbered neurons.
Choose an OPU to inspect every neuron's current state and firing history. Large
populations scroll instead of becoming tiny dots. Inactive neurons remain visible.
Graph overview is available as a secondary view.

Input dots show applied stimulus. Bistable output dots show retained output state.
The labels distinguish these from OPU spikes. The display retains frame-gap and
stale-data reporting. Freeze pauses the display only; neural execution and output
delivery continue.

`AB_TELEMETRY=true` continues to attach controller sessions to this workspace.
Later sessions reuse it. Closing a browser tab does not stop the brain or
controller. Reopen the same project with `ab design`. **Close workspace**, in
•••, stops and saves a controller run launched by the workspace before closing;
it does not stop an externally owned controller merely because its display closes.

## Updating from SDK 0.4.4

The patch changes only SDK workspace, primitive and application-integration files.
It leaves README.md and AGENTS.md byte-for-byte unchanged. New usage information
is in this file. The full source archive includes the regression tests; the patch
omits tests so it also applies to repositories that ignore the tests directory.

A running 0.4.4 workspace otherwise keeps its old Python routes after a pip update.
On the next `ab design`, 0.4.5 retires that old UI service, retains its saved draft,
and starts the new service. External controllers are not stopped by this migration.

Software validation covered public design roundtrips, the existing learning-rule
and reward tests, slider coalescing and revision handling, HTTP controls, controller
launch/button/stop integration, and the current graph builder accepting all twelve
templates. No physical FPGA or interactive browser rendering was available for
this check. This update does not change the factory, private builder, compiler,
RTL, bitstream, or existing `.abp` files.
