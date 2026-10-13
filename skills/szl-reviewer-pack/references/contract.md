# Reviewer pack contract

Project directory: `project.json` (any of `name`, `project`, `title`, `question` is used as the title) and
`runs/<YYYYMMDDTHHMMSSffffffZ>-<12 hex>/summary.json` as written by szl-science-workbench
(`szl.science-run.v1`: `checks: {id: {type, path, sha256, findings}}`). The newest run is selected by the
UTC timestamp in its directory name. Per-check reports named in the summary are re-hashed and compared
with the recorded `sha256`; `capsule.json` and `report.json` are summarized when present.

Extra reports: paths relative to the project directory; their `schema`, `status`/`readiness` and finding
fields (`issues`, `findings`, `failures`, `mismatch_indexes`, `missing`, `required_failures`, `missed`,
`not_tested`, `inference_blockers`, `counterexample_count`, `invalid_outputs`, `mismatches`, `divergent`,
`incomparable`) are summarized. A non-empty list or object, or a positive integer, counts as a finding.

Unresolved = any check with findings, a report digest mismatch or a missing report, plus any extra report
whose status (`readiness` when it has no `status`) is in the unresolved set below, or that carries finding
fields.

Verdict: NOTHING_TO_REVIEW, REVIEW_REQUIRED, or NO_UNRESOLVED_CHECKED_FINDINGS. `pack_sha256` is the digest of
the pack before rendering.

Aggregate config: `{"scores": {check_id: float in [0,1]}, "weights": {check_id: float >= 0}}`. Weights are
normalized to sum to 1; a zero score with positive weight gives 0.

## Unresolved status set

`UNRESOLVED_STATUSES` in `kernel.py`. Base set: FAIL, DIVERGENT, MISMATCH, MISSING, RECEIPT_TAMPERED,
ERROR, BASELINE_FLAGGED. Added so upgraded checks are not hidden: ABSTAIN, FAIL_OPTIONAL_ONLY,
INCOMPARABLE, NO_OUTCOMES_RECORDED, RECORDED_WITH_MISSING_FILES, BASELINE_NOT_TESTED, EMPTY_RECEIPT,
EXTRA_FILES_PRESENT, UNSTABLE, PLAN_INCOMPLETE, HOLD, BLOCKED, UNKNOWN, PARITY_ISSUES, EXTRA_MEMBERS,
INCOMPLETE, REFUSED, DIVERGED, STALE. UNKNOWN also covers a report with neither `status` nor `readiness`.
Pass statuses (PASS, CONSISTENT, MATCH, RECORDED, SCORED, STABLE, PARITY_OK, ANALYZED, ...) stay resolved
unless finding fields are present, so SCORED 6/10 is unresolved through `missed`, and a DESCRIPTIVE_ONLY
clustered report through `inference_blockers` (an ablation ANALYZED report also lists them in `findings`).
The set is a list of known names: any other status without finding fields (for example BROKEN,
INPUT_ERROR, INVALID_INPUT) is shown in the table but not listed as unresolved. The set applies to
standalone reports only; workbench checks still use the run summary's `findings` flag.

Fixtures in `assets/status-probe/` are trimmed copies of real kernel outputs. The mutation and
evidence-gate ones come from the bundled examples of szl-analysis-mutation-test and szl-evidence-gate.
The cross-implementation ones come from the bundled szl-cross-implementation-check example with side b's
results set to side a's, plus a one-sided `aic` quantity for INCOMPARABLE. Negative: the three
`mutation-scored`, `cross-incomparable`, `gate-abstain` give REVIEW_REQUIRED with three unresolved.
Positive: `cross-consistent` alone gives NO_UNRESOLVED_CHECKED_FINDINGS.
