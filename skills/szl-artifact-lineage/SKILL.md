---
name: szl-artifact-lineage
description: Audit digest continuity across a declared scientific transformation DAG, including required stages and inputs, unique producers, and supplied readback bindings. Use when checking whether a result consumed the recorded version of each parent artifact; use a reproducibility capsule separately to hash selected files.
license: Apache-2.0
---

# Artifact lineage

Build an explicit artifact and transformation manifest from the scientist's selected run records. Read [the contract](references/contract.md) before constructing it. Preserve each source, intermediate, and result as a separate versioned id; record every required stage/input, consumed and produced digest, and exact code/config digest pin. A descriptive operation is data, never an executable command.

Obtain readback digests independently for the selected artifacts and place them in `observed_digests`. Do not treat ids, filenames, timestamps, or an absent observation as matching bytes. Call `szl_audit_lineage(manifest)` in `kernel.py`, or run:

```bash
python scripts/run.py assets/example.json --output lineage-report.json
```

Review every finding and retain the original manifest and report. Syntax failures reject the audit; missing edges, substituted versions, absent stages, and digest disagreements return `REVIEW_REQUIRED`. A clean result means `CONTINUITY_ON_SUPPLIED_DIGESTS` only. It does not establish that the declared transform ran, that a code/config pin was checked, that an unsigned record is authentic, or that the result is scientifically correct.

Use Python 3.9+ and the standard library, offline. The helper reads no artifact files, executes no transformations, loads no models, and contacts no services. Limits: 1 MiB CLI input, 1000 artifacts/stages, 10000 total IO entries. Supplied readbacks remain caller assertions; use a separately trusted retained record for adversarial integrity. Sensitive scientific use and unresolved source-rights decisions require human review. Scientific/behavioral performance is `NOT_MEASURED`.

See [provenance](references/provenance.md) for source scope and original implementation rights. `assets/example.json` contains only synthetic digest labels.
