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

- `k` (>= 0) and `seed` must be JSON integers; `mutations` a list of distinct known names ([] = default ten).
- Opt-in classes, never default: `scale_column_inverse`, `negate_column`, `extreme_value`,
  `conflicting_duplicate_id` (needs `id_column`), `all_missing_column`, `single_row`. `columns` and
  `id_column` must name row keys and need one of them. `negate_column` and `scale_column_inverse`
  SKIP non-numeric targets and leave zero cells and cells beyond the largest finite double alone
  (nothing left to change: NO_OP). `extreme_value` and `conflicting_duplicate_id` need numeric
  targets and change zeros too; the twin needs a row with a non-null id whose targets change under +1.
  `all_missing_column` accepts any listed column. `single_row` ignores `columns`. An all-null column counts as numeric, so the default target can be one; list `columns`.
- Such plans, or `require_variant_binding: true`, are `szl.mutation-plan.v2` (adds
  `require_variant_binding`, `columns`, `id_column`); every other plan stays v1, byte-identical to
  base commit b8e259c.
- `score` takes v1/v2 plans; ERROR on missing or duplicate variant names or a non-boolean
  `require_variant_binding`. When the plan or outcomes require binding, an outcome lacking
  `variant_sha256` is NOT_TESTED.
- Status order: BASELINE_FLAGGED, NO_OUTCOMES_RECORDED, BASELINE_NOT_TESTED (`baseline_flagged` not
  `false`; adds `reason`), SCORED. A SCORED report with coverage `0/0` tested nothing.
- CLI: bad JSON, duplicate keys, NaN/Infinity or input nested too deeply exit 2.
