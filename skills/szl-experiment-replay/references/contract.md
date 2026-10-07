# Replay v2 contract

`scripts/engine_ops.py` adds v2; `scripts/engine.py` and the v1 schemas are unchanged.

- Declaration `szl.experiment-replay.declaration.v2`: the v1 fields, plus `group_column` only
  for `csv_grouped_mean_v1`. Even-count median averages the middle pair; variance uses n - 1.
- Reference `szl.experiment-replay.reference.v2`: exactly `schema`, the same `operation` and
  `values`, a decimal string per label (the column name, or each group).
- Pin `szl.experiment-replay.pin.v2`: the v1 fields plus `engine_ops_sha256` (and `group_column`).
- Receipt `szl.experiment-replay.receipt.v2`: the v1 fields plus per-label `results` with exact
  `observed`, `expected`, `absolute_error` (`p/q` if non-terminating) and `within`.

Decimal accumulation (one-pass variance) and Fraction accumulation (two-pass) must agree.
`MATCH` (exit 0) needs every label within tolerance, else `DIVERGED` (1). `INCOMPLETE` (2) adds
`INTERNAL_DISAGREEMENT` and `ENGINE_OPS_` reasons to v1's; `REFUSED` (2) adds
`UNSUPPORTED_OPERATION`. `prepare` pins only after a trial replay completes.
