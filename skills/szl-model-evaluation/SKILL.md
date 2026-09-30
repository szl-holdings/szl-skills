---
name: szl-model-evaluation
description: Score supplied binary predictions or saved categorical outputs and audit a retained evaluation attempt plan, keeping failed, invalid, unavailable and missing attempts visible. Use when reporting held-out model quality or checking whether a success-only score conceals incomplete evaluation; this does not run or authenticate a model.
license: Apache-2.0
---

# Model evaluation with complete denominators

Establish the target population, retained labels, split construction and predeclared
threshold/bins before interpreting scores. Separate observed outputs from declarations
about model revision, tokenizer, inference settings and training overlap. Do not replace
the scientist's selected model or request inference merely to score saved predictions.

Use the existing `szl_evaluate_predictions(...)` for positive-class probabilities and
integer binary labels. It returns Brier, clipped log loss, bin-dependent ECE/MCE,
tie-aware AUROC, threshold accuracy, confusion and optional cohorts. Single-class AUROC
is null. Use `szl_evaluate_categories(...)` for saved categorical triage outputs joined
to retained target/family rows; it includes invalid outputs and ignores saved correctness
flags. Read [the scoring and ledger contract](references/contracts.md) for either mode.

When a claim covers multiple runs or attempted predictions, obtain the original attempt
plan and every outcome, including failures. Call `szl_audit_attempts(...)` with retained
binary labels, unique attempt ids and observed outcomes. Report planned, recorded,
attempted, completed and successful counts together. Missing records remain visible;
invalid outputs count as completed but unsuccessful. Never infer that absent runs passed.

The ledger reports planned accuracy with every non-success counted as incorrect and
separate successful-output probability metrics with their coverage. Do not report these
conditional metrics as whole-plan performance or silently impute probabilities. Repeated
row ids represent repeated attempts, not independent samples. A retained plan digest
detects changed plan bytes relative to that digest; it does not prove preregistration.

```bash
python scripts/run.py assets/attempts-example.json
```

Inputs containing `planned_attempts` route to the ledger; `records` route to categorical
scoring; other inputs route to binary scoring. The fixtures are synthetic, with no model
run. The helper has no provider calls, model loading, downloads, uploads or promotion.

Compatibility: Python 3.10+ stdlib offline; the ledger covers bounded binary prediction
attempts, not multiclass, regression, survival or population inference. It cannot verify
model origin, split independence or status declarations. Agent efficacy and scientific
performance remain `NOT_MEASURED`; energy is null without measurement. Sensitive
scientific use and source-rights decisions require human review.

The retained metric adaptation comes from Apache-2.0
[SZL calibration at b2e3178](https://github.com/szl-holdings/szl-calibration/blob/b2e317877abed98e70f9cf6730944a797837faf1/src/szl_calibration/metrics.py).
The attempt ledger is an original SZL Skills implementation. Source pins, limitations
and acceptance cases are recorded in [the contract](references/contracts.md).
