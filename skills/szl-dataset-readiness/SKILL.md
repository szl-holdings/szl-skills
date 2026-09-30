---
name: szl-dataset-readiness
description: Audit selected scientific tables for exact feature and entity leakage, temporal availability and split-order faults, and preprocessing fit-scope leakage bound to actual row IDs. Use before research training or evaluation; missing evidence stays unknown and this audit does not approve scientific suitability or data rights.
license: Apache-2.0
---

# Dataset readiness

Modified 2026-09-30; original SZL additions. Python 3.10+ and stdlib, offline.

Establish the observation unit, prediction origin, entity boundaries and intended
split design before selecting columns. Call `szl_audit_dataset` in `kernel.py` on
explicitly bounded rows. Retain the existing exact-feature/group checks; provide
`leakage_spec` to inspect entity, temporal and preprocessing evidence. Read
[the contract](references/contract.md) when preparing that manifest or interpreting
missing evidence. Metadata records source revision and terms without verifying them.

Use content-defining features that exclude row ids and split names. Bind transform
fit row ids to selected evidence, and distinguish label availability from label
observation time. A TRAIN_ONLY declaration alone does not establish train-only fit.
Do not silently sample, impute missing timestamps, or infer timezone/column semantics.

Run `python scripts/run.py assets/leakage-clean.json` for a synthetic clean design;
`assets/example.json` retains the earlier synthetic duplicate/subject example.
Report findings and missing evidence together. NO_CHECKED_ISSUES applies only to
the supplied checks and selected rows; readiness remains REVIEW. Human review is
required for sensitive scientific use and licensing decisions. Digests are not
anonymization. Semantic duplicates and causal leakage need independent investigation.
No network, model calls, training, upload, or scientific-performance claim follows.
