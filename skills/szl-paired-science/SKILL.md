---
name: szl-paired-science
description: Bind paired scientific evaluation results to separately frozen case inputs, targets, corpus, scorer and training-only normalization bytes, then verify losses and identity controls before bounded paired inference. Use when auditing supplied offline comparison evidence; this does not run models or prove scientific validity.
license: Apache-2.0
---

Modified 2026-09-30; original SZL additions. Python 3.9+, stdlib, offline.

Read [the binding contract](references/binding-contract.md) to prepare v2 evidence.
Obtain the expected manifest digest from a separately frozen input/target/corpus/
scorer/normalization source before reviewing prediction payloads. Never derive the
trusted digest from the predictions being checked or refresh it to make a failure
pass. Run `scripts/qualify.py experiment.json --expected-manifest-sha256 DIGEST`.

The helper checks actual supplied byte digests, case membership, frozen-manifest
continuity and prediction envelope bindings, then recomputes supported MAE/MSE
losses and training scale. Missing bytes or an absent lock remain DECLARED and
block v2 inference. The expected digest's authenticity, model consumption, plan
timing and independence still require independent evidence. Document ids and
self-declared support are not scientific entailment.

After binding succeeds, preserve the identity control, minimum effect and exact
sign-flip procedure in [the inference protocol](references/protocol.md). Do not
count correlated horizons as independent replications or average unrelated units.
v1 is retained for compatibility and explicitly reports declaration-only binding;
its historical status cannot establish the stronger v2 evidence boundary.

Report local artifact consistency and measured losses within their actual scope.
Keep real-checkpoint/synthetic-input distinctions. Scientific performance stays
NOT_MEASURED; source authenticity and consumption are unverified. No result grants
production admission, data rights, novelty, clinical suitability or replication.

Artifacts are inert UTF-8/JSON data. No supplied code, model, provider, network or
GPU is executed. Human review remains required for sensitive scientific use and
licensing decisions. Source provenance and synthetic acceptance cases are in the
binding reference; use the separate fixture lock only for the bundled toy example.
