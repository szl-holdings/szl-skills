---
name: szl-negative-control-audit
description: Audit a prespecified computational negative-control registry against a declared mechanism graph and byte-bound supplied outcomes. Use to find inappropriate expected-null paths, changed scoring or inputs, post hoc tolerances, contamination, failed controls or missing outcomes before interpreting a computational experiment. Not for wet-lab design or identity-loss benchmark checks.
license: Apache-2.0
---

Audit computational controls that a researcher already selected. Read [the contract](references/contract.md) for the graph, registry and supplied artifact format, then run `scripts/run.py` on the bounded JSON. The helper hashes the actual supplied UTF-8 artifact bytes and inspects fixed protocol, scorer, execution and input bindings. It does not execute the supplied scorer or any experiment.

Check the declared graph and rationale with the researcher: an expected-null control needs no directed intervention-to-readout path and must share a declared nuisance ancestor of both endpoints in each mechanism. A common readout alone does not establish nuisance exposure. A graph edge is a scientific declaration, not a causal discovery. This catches structural contradictions without choosing physical interventions or offering wet-lab protocols.

Retain every registered control, including failed and aborted outcomes. Report each finding and its artifact digest; missing evidence, changed tolerances, contamination and null failures produce INCONCLUSIVE. A clean report is consistency on supplied evidence, not proof that the control is sensitive, that the graph is correct, or that the main effect is causal. Inspect power, nuisance coverage, actual execution and pre-data registration separately. Timestamp and digest consistency cannot authenticate preregistration.

For unchanged-baseline prediction identity checks, use the existing szl-paired-science protocol. This package never estimates efficacy, trains models, fetches data, calls providers, designs clinical or biological interventions, or grants scientific or licensing approval. Sensitive scientific use remains subject to human approval. Scientific/model performance is NOT_MEASURED.

Runtime: Python 3.9+ standard library; offline/keyless; at most 1 MiB input, 128 DAG nodes and 32 controls. See [provenance](references/provenance.md) for original-source scope and pinned capability boundaries. The synthetic `assets/example.json` is usable without external resources.
