# Mutation test contract

`generate` input: `{"rows": [object], "label_column": str?, "k": int = 2, "seed": int = 11, "mutations": [name]?}`.
Rows must be objects; numeric columns are those whose non-null values are all int/float (bools excluded).

Plan (`szl.mutation-plan.v1`): `baseline_sha256`, `expected_detection` (mutations that produced bytes
different from the baseline), and per variant `{mutation, status: GENERATED|SKIPPED|NO_OP, detail, sha256, row_count}`.
A SKIPPED variant records why (no label column, no numeric column, fewer than two numeric columns).

`score` input: `{"baseline_flagged": bool?, "variants": {mutation: {"flagged": bool, "check": str?, "variant_sha256": str?}}}`.
If `variant_sha256` is given and differs from the plan's digest, the outcome is NOT_TESTED (wrong file).

Report (`szl.mutation-coverage-report.v1`): `caught`, `missed`, `not_tested`, `coverage` as `caught/applicable`,
status SCORED, BASELINE_FLAGGED, NO_OUTCOMES_RECORDED or BASELINE_NOT_TESTED.

## Upgrade: baseline, binding, strict input, property classes

- `k` (>= 0) and `seed` must be JSON integers, `label_column` a string or null; `mutations` a list of
  distinct known names ([] = default ten).
- Opt-in classes, never default: `scale_column_inverse`, `negate_column`, `extreme_value`,
  `conflicting_duplicate_id` (needs `id_column`), `all_missing_column`, `single_row`. `columns` and
  `id_column` must name row keys and need one of them. Targets: `columns` minus `id_column`, else the
  first numeric column by name that is not the label or id (an all-null column counts as numeric;
  list `columns`); no target: SKIPPED. One non-numeric target SKIPs the whole variant, except for
  `all_missing_column` (any column) and `single_row` (no targets). NO_OP: output equals input.
  - `negate_column` (sign flip), `scale_column_inverse` (x0.001): change only nonzero int/float cells
    with abs <= the largest finite double (zeros, nulls, bools stay); none changed: NO_OP.
  - `extreme_value`: per target, one seeded cell (any row with the key, zero or null too) becomes the
    largest finite double; NO_OP only if every picked cell already held it.
  - `conflicting_duplicate_id`: appends a copy of one seeded row with a non-null id and all targets
    int/float that change under +1, each target +1; no such row: SKIPPED.
  - `all_missing_column`: every non-null target cell becomes null; an all-null target: NO_OP.
  - `single_row`: keeps the first row; a one-row dataset: NO_OP.
- Such plans, or `require_variant_binding: true`, are `szl.mutation-plan.v2` (adds
  `require_variant_binding`, `columns`, `id_column`); every other plan stays v1, byte-identical to
  base commit b8e259c.
- `score` takes v1/v2 plans; ERROR on missing or duplicate variant names or a non-boolean
  `require_variant_binding`. When the plan or outcomes require binding, an outcome lacking
  `variant_sha256` is NOT_TESTED.
- Status order: BASELINE_FLAGGED, NO_OUTCOMES_RECORDED, BASELINE_NOT_TESTED (`baseline_flagged` not
  `false`; adds `reason`), SCORED. A SCORED report with coverage `0/0` tested nothing.
- CLI: bad JSON, duplicate keys, NaN/Infinity, input or report nested too deeply, an unreadable
  input or an unwritable output exit 2 with a JSON ERROR. Exit-1 tracebacks kept from base include
  `precision_drift` on an integer beyond the double range and a hand-edited plan variant without
  `status`, `sha256` or `detail`.
