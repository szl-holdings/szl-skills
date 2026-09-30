---
name: szl-reproducibility-capsule
description: Create and verify exact-byte manifests for explicitly selected research artifacts, and validate bounded offline replay declarations with retained input, source, environment, plan and output roles. Use for experiment handoffs or replay preparation; commands remain inert and byte agreement does not establish a reproduced experiment.
license: Apache-2.0
---

<!-- Modified 2026-09-30: original bounded replay-declaration extensions to
SZL source baseline 9668f1571315e93ca2059b9a44f12beef483532d. -->

# Reproducibility capsule

Select a declared project root and explicit files. Retain the experiment's inputs, source,
environment specification, analysis plan and reference outputs when replay preparation is
requested. Avoid credentials, personal records and unrelated workspace data. Metadata is
visible; credential-name guards do not inspect or redact the contents of selected files.

Call `szl_make_capsule(root, files, metadata=None, replay=None)`. Existing string file
lists remain supported. Role-tagged declarations add `path` and `role`. Canonical relative
POSIX paths, unique selections and hard count/byte bounds prevent accidental sweeps;
traversal, symlinks, junctions, reparse points and credential-like paths are refused.
Read [the manifest/replay contract](references/contracts.md) before authoring a replay.

Call `szl_verify_capsule(root, capsule)` on retained bytes. Report changed, missing and
unsafe/unreadable files separately. All matching bytes with a complete valid replay
declaration yield `replay_ready: true`, meaning preparation for a separate sandbox review.
`execution: NOT_RUN`, `capability_denial: DECLARED_ONLY` and `tolerance_application:
NOT_RUN` remain explicit. The helper never executes argv or source, installs dependencies,
launches processes, uses a model/provider or applies numerical comparisons to outputs.

Keep an independently retained manifest/digest when integrity across sessions matters.
An unsigned self-digest cannot detect an attacker replacing the entire manifest.
`authentic: false` remains false even if every byte matches; actual receipt signing needs
a real key and verifier. Do not relabel hashes as signatures or readiness as reproduction.

```bash
python scripts/run.py assets/example.json --root .
```

Creation accepts `files`, optional `metadata` and optional `replay`; verification accepts
the saved v1 capsule. Output files are created exclusively. The example and replay fixture
are synthetic and include no model, dataset, credentials or scientific results.

Compatibility: Python 3.10+ stdlib offline; at most 128 explicit files, 8 MiB per file and
32 MiB retained total. Replay declarations cover Python source, unsigned 32-bit seed,
bounded resource limits and predeclared exact-byte or numeric-tolerance policies. This
validator cannot prove source safety, enforce a sandbox or verify preregistration.
Scientific/agent performance remains `NOT_MEASURED`; sensitive use and rights decisions
require human review. Lambda remains Conjecture 1 (OPEN).

This is an original Apache-2.0 SZL Skills implementation following the existing unsigned
receipt boundary. [The contract](references/contracts.md) records source pins and tests.
