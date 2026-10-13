# Replay v2 contract (`scripts/engine_ops.py`; v1 unchanged)

- Declaration `szl.experiment-replay.declaration.v2`: the v1 fields, plus `group_column` only
  for `csv_grouped_mean_v1`. Even-count median averages the middle pair; variance uses n - 1.
- Reference `szl.experiment-replay.reference.v2`: exactly `schema`, the same `operation` and
  `values`, a decimal string per label (the column name, or each group).
- Pin `szl.experiment-replay.pin.v2`: the v1 fields plus `engine_ops_sha256` (and `group_column`).
- Receipt `szl.experiment-replay.receipt.v2`: `schema`, `status`, `reason`, `execution`,
  `pin_sha256`, `evidence_scope`, `signed`, `independent_witness`, `scientific_claims_verified`,
  `receipt_sha256`, and `observed_hashes` or `*_sha256_observed` as in v1. `COMPLETED` adds
  `operation` and `row_count`; `MATCH`/`DIVERGED` add `arithmetic_paths`, `absolute_tolerance`
  and per-label `results` (`label`, `row_count`, `observed`, `expected` as referenced,
  `absolute_error`, `within`; exact, `p/q` if non-terminating). No top-level `observed_mean`,
  `expected_mean` or `absolute_error`.

Decimal (one-pass variance) and Fraction (two-pass) accumulation must agree, else `INCOMPLETE`/
`INTERNAL_DISAGREEMENT` (`COMPLETED`, no `results`). `MATCH` (exit 0) needs every label within
tolerance, else `DIVERGED` (1). `INCOMPLETE` (2) adds `ENGINE_OPS_` reasons to v1's; `REFUSED`
(2) adds `UNSUPPORTED_OPERATION`. `prepare` pins only after a trial replay completes.
