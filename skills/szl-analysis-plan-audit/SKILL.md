---
name: szl-analysis-plan-audit
description: Compare a frozen computational analysis plan with a supplied run manifest and versioned deviation log. Use to audit prespecified hypotheses, outcomes, estimands, sampling/group units, splits, multiplicity, practical margins, exclusions, fixed stopping and complete attempts before calling an analysis confirmatory. Not a statistical test or efficacy estimator.
license: Apache-2.0
---

Read [the contract](references/contract.md), retain the original frozen plan, and run `scripts/run.py` on the supplied plan, run manifest and deviation log. The helper computes canonical plan/run digests and compares each declared setting by hypothesis or family identifier. It does not load datasets or execute experiments.

Audit the primary metric and direction, estimand, independent sampling/group units, split digest, family error allocation, practical margin, exclusion criteria and fixed attempt schedule together. Keep failures and aborts in the attempt ledger. A result digest names a declared artifact; this audit does not verify its underlying bytes or recompute its metric. Use the appropriate paired benchmark or model-evaluation helper for those separate checks.

Treat any deviation or incomplete attempt evidence as EXPLORATORY relative to this frozen plan, including a documented amendment. Log the original plan version, changed path, original/new value hashes, reason and declared time; retain the fixed original instead of silently rewriting it. A consistent report means the supplied run follows the declared plan. Precommit declarations, timestamps and hashes cannot prove that registration preceded access to outcome data; authenticated preregistration and independence need separate evidence.

This package never supplies a p value, power or efficacy conclusion, chooses scientific hypotheses, certifies causality, trains a model, calls a provider, uploads data, or grants scientific/licensing approval. Sensitive scientific use remains subject to human approval. Scientific/model performance is NOT_MEASURED.

Runtime: Python 3.9+ standard library, offline/keyless, 1 MiB JSON, 32 hypotheses/families and 256 scheduled attempts. Only fixed-attempt stopping is supported; sequential/adaptive plans need a different explicit contract. See [provenance](references/provenance.md). `assets/example.json` is a synthetic plan with a retained failed attempt.
