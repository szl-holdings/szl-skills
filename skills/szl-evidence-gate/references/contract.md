# Evidence gate contract

Input document: `{"subject": str?, "claims": [Claim]}`.

Claim: `{"id": str, "text": str, "required": bool = true, "evidence": [Item] | [] | null}`.

Item: `{"path": str (relative, no `..`), "sha256": 64 lowercase hex?, "must_contain": [str]?}`.

Per-item result: `PASS`, `FAIL` (`MISSING_ARTIFACT`, `DIGEST_MISMATCH`, `TEXT_NOT_FOUND`), or `ERROR`.
Per-claim status is ERROR if any item errors, else FAIL if any item fails, else PASS; empty or null
evidence gives ABSTAIN with reason `NO_CHECKABLE_EVIDENCE_DECLARED`.

Aggregate: FAIL when a required claim is FAIL or ERROR; ABSTAIN when no required failure but at
least one ABSTAIN; FAIL_OPTIONAL_ONLY when only optional claims failed; PASS otherwise.

The report records `claims_sha256` (canonical JSON digest of the claims file) so a reviewer can
confirm which claims file produced it. Text matching is exact substring on UTF-8 decoded bytes.

## Input strictness and numeric binding (v2)

All documents: `claims: []` is aggregate ABSTAIN, top-level `reason: NO_CLAIMS_DECLARED`. Repeated
string claim ids are report ERROR `DUPLICATE_CLAIM_ID` with sorted `duplicate_claim_ids`, no claim
results. Non-boolean `required` is claim ERROR `REQUIRED_NOT_BOOLEAN`, counted as required. A resolved
item path failing `Path.is_relative_to(root.resolve())` is item and claim ERROR `PATH_OUTSIDE_ROOT`;
lexical rejections (`..`, absolute, drive) keep their messages and claim reason `MALFORMED_EVIDENCE`.
Artifacts over `MAX_ARTIFACT_BYTES` (33,554,432) are not read: ERROR `ARTIFACT_TOO_LARGE`.
`scripts/run.py` exits 2 on duplicate JSON keys, NaN, Infinity or overflowing floats.

Opt-in: a document containing `strict_numeric_binding` (`true`/`false`, else report ERROR) gets
report schema `szl.evidence-gate-report.v2`, item field `values: [str]` and, per evaluated claim,
`numeric_binding: {numbers_in_text, bound, unbound}` (unique, first-appearance order). Tokens match
`(?<![\w.+-])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\.?\d)` and compare as exact strings. Each
`values` entry must be one token (else item ERROR) found in the decoded artifact (else item FAIL
`TEXT_NOT_FOUND`, `missing_values`). Only PASS items bind: their `values` and the `must_contain`
snippet tokens that are whole artifact tokens. With `true`, a would-be PASS claim with an unbound
number is ABSTAIN `NUMBERS_NOT_BOUND`. Without the key the v1 shape is kept and `values` is ignored.
