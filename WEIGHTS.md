# Read and load Prima-Lux weights

SDK 0.4.9 lets you read the connection weights currently used by your Brain, save them, and load them back. The values include changes made by learning and live TNorm adjustments. You can inspect or change all connection matrices, or select the ones between named populations.

Factory 0.6.22 Step 2 installs the required native support and Device Manager 0.6.22 on KR260 and KV260. Existing compiled brains can use weight access, including brains built without live TNorm metadata. The Designer does not need to be open.

## Export and load a file

Activate your controller's Python environment and run these commands from the folder containing its device `.env`:

```bash
# Save the weights currently used by the installed Brain.
python -m ab device --env .env weights export learned.abweights.json

# Deliberately restore the weights in that file.
python -m ab device --env .env weights import learned.abweights.json
```

Export copies the current weights at one neural timestep boundary. It does not stop or reset the Brain. Import replaces the selected weights between timesteps, after validating the entire request.

A running Brain keeps running and learning. When you finish, ordinary Stop saves its current weights and other neural dynamics:

```bash
python -m ab device --env .env stop
```

When the Brain is already READY or STOPPED, a changed import updates its existing checkpoint immediately. It does not create another checkpoint for each import. Exporting to the same local filename also replaces that file.

## Read weights from Python

```python
from ab_sdk.primalux import PrimaLuxClient

with PrimaLuxClient.from_env('.env') as device:
    snapshot = device.get_weights()
    print('Next neural timestep:', snapshot['next_step'])

    for connection in snapshot['layers']:
        print(connection['source'], '->', connection['target'])
        print('Type:', connection['type'])
        print('Shape:', connection['shape'])
        print('First weight:', connection['values'][0][0])

    device.export_weights('learned.abweights.json')
```

Each matrix describes one installed connection. **Rows are receiving neurons; columns are sending neurons.** For a connection from A to B:

```python
weight = connection['values'][b_neuron_index][a_neuron_index]
```

A shape of `[3, 2]` means three receiving neurons and two sending neurons: six weight entries. A changed entry means one stored weight changed. It does not mean that a neuron fired or that an entire OPU changed.

The values are raw float32 transmission weights. They are separate from the design's inhibitory `strength`, operand `units`, and OPU TNorm. Preserve the connection's `type`; do not add a minus sign merely because the connection is inhibitory.

## Load a saved snapshot

```python
from ab_sdk.primalux import PrimaLuxClient

with PrimaLuxClient.from_env('.env') as device:
    result = device.load_weights('learned.abweights.json')
    print('Changed weight entries:', result['changed_entries'])
    print('Effective from timestep:', result['effective_from_step'])
    print('Saved on the device:', result['persisted'])
```

`persisted=false` on a successful import means the new weights are in the running Brain's memory. Use ordinary Stop when finished to save the current state. Learning can change the weights again before Stop.

The file must match the installed neuron and connection layout. This includes the matrix mapping and installed connectivity masks. A rebuild can change that layout even if the public population names look similar. The SDK does not guess a new mapping or resize matrices to fit. A file can be loaded into another installation only if the device confirms the same layout.

## Edit only selected connections

Either `source` or `target` can be used alone. Using both selects connections between those two named populations. Replace A and B below with names in the installed Brain:

```python
from ab_sdk.primalux import PrimaLuxClient

with PrimaLuxClient.from_env('.env') as device:
    snapshot = device.get_weights(source='A', target='B')

    # Example adjustment: reduce these current weights by 5%.
    for connection in snapshot['layers']:
        connection['values'] = [
            [value * 0.95 for value in row]
            for row in connection['values']
        ]

    result = device.load_weights(snapshot, state=snapshot)
    print(result['changed_entries'])
```

Only the matrices included in the import are replaced. Others keep their current values. Keep the returned IDs, source/target names, connection types, rule labels, shape, and layout hash unchanged. Edit `values`; do not construct a new matrix mapping yourself.

The same selection is available when exporting a file:

```bash
python -m ab device --env .env weights export pathway.abweights.json --source A --target B
python -m ab device --env .env weights import pathway.abweights.json
```

Supplying `state=snapshot` checks that the installed Brain and manual weight/TNorm revision still match the read you edited. A conflicting manual edit, TNorm change, package installation, reset, or runtime restart causes rejection before any weight changes are applied.

Ordinary learning does not invalidate that check. Importing is an intentional replacement: learning changes made after the read are replaced in the selected matrices too. Without `state=`, the SDK fetches the current binding first, which allows deliberate restoration of an older matching file.

## What loading weights preserves

Memory and ongoing behavior depend on the Brain's neural dynamics as well as its current weights. A weight file contains only the weights and the information needed to map them. It is not a full recording of the Brain's state.

| Action | Result |
| --- | --- |
| Read or export weights | Copies current weight values; the Brain continues in its current state. |
| Load weights while RUNNING | Replaces selected weights at a step boundary; running and native learning continue. Ordinary Stop saves the current state. |
| Load changed weights while READY or STOPPED | Replaces selected weights and updates the existing checkpoint immediately. |
| Stop and then Start | Saves and restores the native neural state through the existing lifecycle. |
| Reset dynamics | Restores the installed package's initial weights and clears accumulated neural state. |
| Build and install a new Brain | Installs that package's initial weights and clears the previous Brain's saved dynamics. |

Import preserves the current TNorm values, neuron state, eligibility, traces, fatigue, reward state, learning rules, reward configuration, and learning/phase masks. It does not rewind the neural timestep or move the robot back to an earlier physical position. The response of the circuit can still change because its weights changed.

Excitatory weight changes can alter a primitive's computation. Inhibitory changes can alter suppression and competition. Measure the resulting behavior rather than treating a percentage change in weight as the same percentage change in firing or motion.

A previously zero weight made nonzero can transmit at that matrix position while its learning and phase masks remain unchanged. Weight import does not create new populations, reshape connections, or select new learning contacts. Change topology or learning connectivity in the design and rebuild when that is the intended change.

These operations do not rewrite `brain.abdesign.json` or the installed `.abp`. Export the desired weights to keep a separate local copy. A later rebuild starts from the design's compiled values, not from that exported weight file.

## Use with an external tuning loop

Your controller can read a baseline, propose selected weight changes, load them, run an action, and score the measured result. It can keep a local copy of the preferred weights and restore that copy explicitly. The API provides weight access; your application supplies the optimization method, observations, target, and definition of success.

This does not automatically add gradient descent, an RL algorithm, or a new native learning rule. While the Brain runs, its dynamics and any installed learning rules continue. Loading the same weights does not recreate an identical trial because other neural state and the physical situation may differ. Use the lifecycle your experiment requires; the SDK does not insert resets or stop/start cycles for you.

A large snapshot or import can briefly delay a timestep while the runtime copies or applies the matrices. This API is intended for snapshots and deliberate edits; use the existing neuron/output stream for continuous activity observation.

## File and API details

The public methods are on `ab_sdk.primalux.PrimaLuxClient`:

| Method | Returns or performs |
| --- | --- |
| `get_weights(source=None, target=None)` | Snapshot with nested Python `values` lists. |
| `get_weights(packed=True)` | Snapshot with compact `data_b64` values. |
| `get_weights(metadata=True)` | Matrix descriptors and the current binding, without weight values. |
| `export_weights(path, source=None, target=None)` | Writes a packed `.abweights.json` file; returns its path. |
| `load_weights(snapshot_or_path, state=None)` | Applies the selected matrices and returns the result. |

The schema is `artificial-brains/weights/v1`. Packed files use `float32-le/base64`: little-endian float32 bytes encoded as base64. This is a storage encoding, not file encryption. It preserves the device's weight bits exactly. Python numeric edits are converted to float32. Nonfinite values and float32 overflow are rejected; there is no added application-level weight cap or automatic clamping.

A decoded matrix has `values`; a packed matrix has `data_b64`. Supply one representation per matrix, not both. Keep the other snapshot fields returned by the SDK. The write binding belongs to a device/runtime session; `load_weights` handles it as described above.

Result fields:

| Field | Meaning |
| --- | --- |
| `ok` | The import was accepted and completed. |
| `matrices` | Number of matrices supplied in the request. |
| `changed_matrices` | Number whose stored weights changed. |
| `changed_entries` | Number of stored weight entries that changed. |
| `effective_from_step` | Next neural timestep using the applied weights. |
| `persisted` | Whether the weights are already durable on the device; a running import waits for ordinary Stop. |
| `binding` | Current package, session, manual weight revision, and TNorm revision. |

`next_step` in a snapshot identifies the next neural timestep at the moment of the copy. It is not a time in milliseconds. The descriptor `STDP3/STDP3_SLOW` identifies a shared runtime family; the installed rule remains whichever was compiled for that connection.

The HTTP endpoints are `GET /api/brain/weights` and `POST /api/brain/weights`. GET supports `metadata`, `source`, and `target`. Reading requires `brain:read`; loading requires `brain:operate`. Restoring through the SDK therefore needs a key with both scopes because it first reads the current binding. Existing developer keys with those scopes gain the corresponding access. The legacy session adapter's `get_weights(compile_id=...)` is a different cloud-checkpoint interface and is not the device matrix API.

## Errors and deployment

| Message or situation | What to do |
| --- | --- |
| Native weight access requires Factory 0.6.22 | Install its Step 2 on the device. Rebuilding the Brain does not install runtime support. |
| Weight file does not match this Brain layout | Use a snapshot for the installed layout. Do not remove its hash or invent a matrix mapping. |
| Brain or manual weight/TNorm revision changed | Read the current state and review the intended edit before applying it again. |
| Unknown source or target | Use exact population names from the installed Brain. |
| Installation or update in progress | Wait for completion before importing weights. |
| API key missing permission | Use the existing device key with the required scopes. |
| Import response was lost | Read the current weights before deciding whether to repeat the import. The SDK does not automatically retry it. |

The existing online updater distributes Device Manager application files. It cannot install the native extension in this release. Factory 0.6.22 Step 2 is required once; later Device Manager-only releases can continue through the existing signed online update flow. No FPGA reflash, re-encryption, or compiler change is needed for this capability.

Weight snapshots contain raw weights and population names. Store and share them deliberately. They are not automatically added to Agent conversations, model requests, or cloud checkpoints. The Agent/MCP tool set has no live weight-read/write tool; a developer-controlled Python controller can call the API. The endpoints do not export the compiled `.abp`, private construction formulas, complete prepared image, or bitstream.
