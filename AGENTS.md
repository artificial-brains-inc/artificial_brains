# Artificial Brains Agent Guide

Use this file with the README and the **Introductory Guide to Building an Artificial Brain, version 0.4**. Build the user's requested behavior through the public SDK and the guideline to build an artificial brain. Keep existing application behavior and unrelated components intact.

## Scope and responsibilities

- The brain performs neural comparison, logic, association, memory, agreement/contradiction, and action selection.
- Python acquires grounded observations, encodes them, receives neural outputs, drives the body, and enforces its physical limits.
- Do not replace neural operations with controller-side fuzzy rules, target-minus-current error computation, baseline subtraction, action normalization, inverse-kinematics action selection, or argmax over candidates. A simulator may provide physical measurements such as forward-kinematic gripper position.
- Use only public design fields. Keep private construction formulas, calibrated weights, compiler internals, device identity secrets, and API keys out of public code and documentation.
- Do not alter the SDK, Device Manager, factory, builder, output semantics, bitstream, or RTL to make an example work unless the user explicitly asks for that change. Do not introduce extra node, edge, or strength caps.

## Public design API

```python
from ab import Brain

brain = Brain("Name")
brain.input("SENSE", 64)
brain.opu("RELAY", "high-gamma", 10)
brain.opu("STATE", "theta", 3)
brain.output("ACTION")
brain.connect("SENSE", "RELAY")
brain.connect("RELAY", "STATE")
brain.connect("STATE", "ACTION")
brain.save("brain.abdesign.json")
```

- Rhythms: `high-gamma`, `gamma`, `beta`, `alpha`, `theta`, `delta`.
- All accept TNorm 0–10, with up to two decimal places. Preserve recipe values such as 2.01. TNorm 0 is not OFF; TNorm is not a gain, inhibition percentage, or fuzzy degree.
- High-gamma can appear anywhere in the circuit, including internal and feedback paths. Omitted high-gamma TNorm preserves the default reference at 10. Other rhythms require TNorm.
- Connections are excitatory by default. Mark inhibition with `"inh"`; do not label every ordinary edge with a redundant positive sign.
- Inhibitory `strength` is finite and at least 0.1, with no upper cap. Default is 1. Values such as 1.25, 2.5, and 4 are allowed. Omit an edge for zero inhibition.
- Use positive `units` only with `role="operand"`. The connection type supplies the sign. Mark all arithmetic terms, including positive ones.
- Use `role="feedback"` for an actual return to an OPU. Connection roles require OPU targets; do not add them to output-clearing edges. An output can feed back through an OPU.
- `brain.connect()` connects entire named populations. Do not invent neuron-index, population-slice, synapse-weight, or leak-control arguments.

## Inputs and capacity

Use one external population per meaningful sensor signal or population code. An input can carry an ordered vector. Match its width, order, and name in the encoder and the saved design. Internal propositions and fuzzy thresholds are not separate external inputs by default.

| Receiving rhythm | Recommended maximum neurons per input |
| --- | --- |
| Gamma | 6 |
| Beta | 37 |
| Alpha | 44 |
| Theta | 156 |
| Delta | 498 |

Through high-gamma, use the following rhythmic receiver's recommendation. These are saturation guidance. The current SDK per-input contract is 1–498. The 10,000-neuron device capacity is a physical budget, not a limit on diagram boxes or graph edges. The build calculates population allocation; additional relays consume neurons.

## Source and target construction

- Define each population's computational role before selecting its rhythm and TNorm.
- Use the actual immediate upstream population as the source of the next stage. Do not copy a root input's assumptions through transformed states.
- When several OPUs excite a gamma, beta, alpha, theta, or delta receiver, use a common immediate source rhythm. For heterogeneous arithmetic operands, create one-source relays in the chosen operator rhythm/TNorm, then a separate receiving operator. Relay and target are distinct populations.
- Apply that representation rule to signed arithmetic operands as well as positive ones. High-gamma receivers, inhibitory vetoes, and feedback returns have different roles; do not invent a blanket ban on mixed connections.
- Topology defines the design. Canvas coordinates and declaration order do not define the computation.
- Preserve the complete source-to-target relationship when reusing a primitive. Changing a relay changes the next stage's source.

## Arithmetic and Boolean recipes

A canonical unit is an equal mathematical contribution at the receiving operation. It is not a raw weight or a neuron count. For subtraction:

```python
# A, B, and RESULT already exist.
brain.connect("A", "RESULT", role="operand", units=1)
brain.connect("B", "RESULT", "inh", role="operand", units=1)
```

| Operation | Positive units | Negative units | Target |
| --- | --- | --- | --- |
| OR | A: 1, B: 1 | None | Beta T5 or Theta T5 |
| AND | A: 1, B: 1 | Local OR: 1 | Delta T2 |
| XOR | OR: 2 | A: 1, B: 1 | Alpha T5 |
| NOT | Active REF: 1 | A: 1 | Theta T5 |
| NOR | Active REF: 1 | OR: 1 | Beta T5 |
| A greater than B | A: 1 | B: 1 | Theta T2 |
| B greater than A | B: 1 | A: 1 | Theta T2 |

- For the native AND construction, use Delta T2 operands, a local Delta T2 OR helper, and a distinct Delta T2 AND target. The helper contributes one negative operand unit.
- Preserve XOR's two units of OR. Do not replace them with an ordinary one-unit edge.
- NOT requires a positive reference. Silence cannot generate positive evidence. Silent comparison rails alone do not distinguish equality from absent input.
- Keep cancellation and signed contradiction at the recipe's units and default inhibition strength..
- A veto or reset is a separate suppression role. Do not apply hard-veto scaling to AND, XOR, or subtraction merely because an edge is inhibitory.
- ADD and SUB can retain history. Use settling or a local reset/canonicalization stage for independent successive operations; do not reset the whole brain after every observation.

## Fuzzy composition

- A reusable fuzzy proposition is an ordered bank of Boolean neural channels. Choose the bank width for the required resolution. Eight channels are an example, not a requirement.
- Preserve the full pattern and channel order. For eight positions, 11000000 represents 0.25 and 11111100 represents 0.75.
- Fuzzy OR uses matching positions with ordinary OR at Beta T5 or Theta T5.
- Fuzzy NOT reverses the channel order before applying reference-based NOT at Theta T5.
- Fuzzy AND uses Delta T2 operands, a local Delta T2 OR helper, and a separate Delta T2 AND target at every position. Do not substitute a standalone beta OR through a beta-to-delta relay or a half-unit coincidence rule.
- Feed generated banks downstream as actual immediate populations, with per-position relays when needed. Read the ordered state across complete receiving-rhythm cycles, especially Delta.
- Operands named A_1…A_m refer to already formed internal proposition populations. Do not invent one external sensor input per threshold or claim that population connections can select individual input neurons.
- Do not represent a fuzzy value by TNorm, a fractional edge, mean OPU firing, or Python-side membership logic.

## Shallow decisions and embodiment

- Use exact operators for reusable propositions and numerical relations. Use shallow local rules when several conditions support or oppose candidate actions.
- The Delta T2 positive-coincidence motif receives two compatible operands at 0.5 units each. This is a local rule construction, not reusable fuzzy AND or a fuzzy degree of 0.5.
- A positive anchor with a direct inhibitory veto can express a local “A unless B” rule. Pure-negative rules require an explicit reference/default mechanism.
- Form agreement and contradiction from the same predicted body effect and body context. Matching signed error supports the candidate; opposite signed error contributes inhibitory operand units to the same candidate.
- Contradiction is signed evidence, not automatically a hard veto. Preserve concurrent evidence instead of selecting a winner in Python.
- Encode intended importance through meaningful rule multiplicity or explicit units. Do not duplicate sensor inputs or normalize measured rule firing to manufacture equal importance.
- For reaching, supply observed object position, actual gripper position, and proprioception. Build the signed difference neurally.

## Motor states memory and output lifetime

- Starting motor chain: Theta T3 → Theta T2.01 → Beta T5 → bistable output. Rules can feed that pathway; do not add a serial Delta action stage by default.
- Use fast motor Beta T5 to inhibit an antagonist's Theta T2.01 and, when needed, clear its output. Competition follows physical antagonists; independent joints may act simultaneously.
- Motor self-reset: motor Beta T5 → reset Beta T3, then inhibition to its own Theta T2.01. For continuous re-evaluation, also inhibit the same motor's output. Internal reset alone can leave a stale bistable command active.
- For deliberate hold-until-cleared behavior, retain the output latch and name its clearing path. Do not change output semantics to make an ACK or motor command short-lived.
- ACK: one-neuron input → high-gamma relay → ACK output → separate high-gamma reset → inhibitory return to ACK. A repeated held ACK frame is not a new event. ACK marks entry into its pathway, not completion of all processing.
- Association can combine Beta T5 streams in Delta T2. The guide's recurrent recall chain returns through distinct Delta/Alpha populations to the same association OPU. Mark the return as feedback.
- Follow neuron identity and temporal patterns. Persistent firing alone does not establish what information is retained.


## Learning Rules

### Neural dynamics come first

Our artificial brains compute through neural dynamics. Connected populations interact, form associations, retain information, recall earlier states, and produce actions through their activity and feedback.

**These capabilities do not depend on adding a learning rule.** A network with static connections can still compute and hold memory. Static describes the connection TNorm, not the activity or capabilities of the brain.

Learning rules serve a different purpose: **adjusting behavior through experience**. They change how strongly particular pathways influence the network—for example, increasing opposition to an unwanted response or reducing inhibition so another response becomes available.

First establish the intended behavior through neural dynamics. Then add learning where a specific behavior should adapt. Inhibitory connections are especially useful for adjusting how existing computations influence behavior.

Learning on excitatory connections between OPUs can alter the computation itself. Use it deliberately when that relationship should change.


### What eligibility means

For learning that uses reward, each connection between neurons keeps a short memory of their recent firing. This is called eligibility. It allows a reward arriving afterward to affect connections involved in recent activity.

The memory can point toward making a connection stronger or weaker. Source-before-receiver firing contributes toward strengthening; the reverse order contributes toward weakening. These contributions combine and fade over time.

### The three learning rules

- `STDP`: changes connection strength from the relative firing of the connected neurons. It does not wait for a reward.
- `STDP3`: keeps eligibility and uses a selected dopaminergic population to apply changes when reward activity arrives.
- `STDP3_SLOW`: works like STDP3, but changes connection strength more slowly.

STDP learns directly from activity. STDP3 and STDP3_SLOW connect recent activity with reward. The slow version changes the pace of learning, not the meaning of the reward.

For STDP3 and STDP3_SLOW, `reward_type="LTP"` follows the direction recorded in eligibility; `"LTD"` reverses it. Do not assume LTP always makes a connection stronger or LTD always makes it weaker.

### Build the neural behavior first

Neural dynamics are the core of the architecture. First build a network whose intended behavior is stable with static connections. Then add learning to specific connections where experience should change a specific behavior.

Inhibitory connections are especially useful for this. A network can learn by changing which pathways it suppresses or releases, while its excitatory computations remain fixed.

Learning on an excitatory connection between OPUs can change the computation performed by that relationship. Apply it deliberately. Learning on an inhibitory arithmetic operand can also change a calculation, so preserve fixed operator connections when their meaning must remain unchanged.

### Where reward comes from

Reward can begin with an external observation or be produced by the network's own feedback. In either case, the selected dopaminergic OPU supplies the learning signal.

Use `reward="NAME"` to select that OPU. More neurons firing in the reward population in a timestep can produce a larger adjustment, depending on eligibility and the learning rule.

### Let reward activity end

Reward neurons have recovery dynamics, but sustained excitation can make them burst again. STDP3_SLOW does not automatically stop reward or prevent continued changes.

An ordinary OPU can inhibit the reward OPU. That inhibitory connection can use STDP3_SLOW and select the same reward OPU as its learning signal. This lets reward influence learning on the pathway that regulates it. Whether inhibition strengthens depends on eligibility and the chosen reward direction.

Do not use a direct dopaminergic self-connection as an inhibitory brake: dopaminergic spikes provide learning signals rather than ordinary inhibitory drive. Give the ordinary inhibitory OPU an activating pathway, and observe that reward activity can end and respond again to a later event.


## Save build and run

The Canvas Python editor and direct `--brain brain.py` import accept literal Brain declarations. For loops, variables, or functions, run an ordinary Python generator and build its saved JSON:

```bash
python make_brain.py
ab build brain --brain brain.abdesign.json --env .env
ab run --env .env
```

Wait for READY before running. Build installs the design; Run uses the installed brain and does not rebuild it. Keep the application's local I/O contract aligned with that design. Use the README for registration, installation, and the complete `run.py` pattern. Keep credentials in `.env`, out of code and version control.

## Live integration and neuron telemetry

- Use `ABClient.from_env(...)` and `client.start_from_env(...)`. Register output handlers before sending sensory activity.
- Publish each current reading immediately with `session.publish_input()`. Use `session.publish_sensor_frame()` only for simultaneous captures. Do not schedule future neural steps.
- Outputs are pushed for every completed timestep, including unchanged and empty states. Use `session.on_output()` for raw frames and a decoder with `session.on_command()` or RobotLoop for body commands.
- `RobotLoop.tick_hz` controls sensor polling, not neural timing or output delivery. Keep output callbacks short. Do not register the same motor callback both through RobotLoop's `command_executor` and `session.on_command()`.
- Respect the actual decoder representation. Actuation counts and neuron-identified events are not interchangeable. Apply body limits independently of decoding.
- `AB_TELEMETRY=true` opens neuron telemetry when the session starts. Inspect individual neurons and their histories, including inactivity; do not reduce the requested view to an OPU active flag. Freeze pauses the display only.
- Stop and wait for neural-state saving before power-off. Reset dynamics discards accumulated neural state, not the body's physical state. Start a fresh session after resetting.

## Deliver a usable brain

Provide the saved design, application integration, exact commands, and a short description of what each primitive receives and produces. State the operating points, operand units, inhibitory roles, and output-clearing behavior. Use actual SDK arguments and make each code block either complete or explicit about its existing source populations. Keep guidance practical; do not add experimental-status classifications or unanswered research questionnaires.
