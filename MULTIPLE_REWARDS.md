# Multiple dopamine rewards

STDP3 and STDP3_SLOW can use several dopaminergic OPUs on the same connection.
Choose each source and whether it provides LTP or LTD:

```python
brain.connect(
    "CONTROL", "ACTION", "inh",
    rule="STDP3_SLOW",
    rewards=[
        {"source": "GOOD_RESULT", "type": "LTP"},
        {"source": "BAD_RESULT", "type": "LTD"},
    ],
)
```

`GOOD_RESULT` and `BAD_RESULT` must be dopaminergic OPUs in this brain. There is
no added reward-source count limit. The existing device capacity and design
payload size still apply. Use `rewards` instead of `reward`/`reward_type` on that
connection; the existing single-source syntax remains supported unchanged.

In the canvas, select the connection, choose STDP3 or STDP3 slow, and use
**Add reward population**. Each selected population has its own LTP/LTD selector
and Remove button. Renaming a population updates its reward references.

All selected sources contribute to the same connection's reward signal. Their
firing magnitudes matter. LTP and LTD contributions oppose each other and can
cancel when active together. They do not create separate copies of eligibility
or separate independently learned weights on the same connection. Learning
changes the existing connection; it does not replace the network's dynamics.

Requires Device Manager 0.6.19 and private Brain Builder 0.4.12. The existing
compiled-package format and installed runtime already support multiple sources.

All OPU rhythms can connect to outputs through the updated private builder.
Outputs retain their bistable behavior. Static connections and existing
single-reward designs keep their existing representation and parameters.
