---
name: szl-unit-invariants
description: Check declared SI dimensions, allowlisted unit conversions, ranges, and fixed pointwise numeric invariants in selected scientific measurements or simulation outputs. Use to find incompatible quantities, scale mistakes, or broken conservation/equality checks; excludes symbolic proof and offset, logarithmic, or context-dependent units.
license: Apache-2.0
---

# Unit invariants

Select a bounded set of measurement or simulation columns and declare their units, SI dimensions, expected ranges, and prespecified invariants. Read [the contract](references/contract.md) for the unit table, dimension order, and fixed operation grammar. Use decimal strings when input precision matters. Preserve original values; do not silently guess units, fix scale, or change tolerances after seeing a failure.

Call `szl_audit_unit_invariants(record)` in `kernel.py`, or run:

```bash
python scripts/run.py assets/example.json --output unit-report.json
```

Inspect dimension failures before interpreting numeric agreement. Review range violations, numerical discrepancies, undefined ratios, and flagged factors of at least 1000. Retain the record and report, and ask the scientist to resolve ambiguous units or tolerances. A corrected declaration is a new audit, not evidence that the original passed.

The helper normalizes a restricted table of linear SI conversions and checks `equal`, `sum`, `difference`, `product`, or `ratio` pointwise. It never evaluates expressions, reads datasets, runs experiments, loads models, or contacts services. Success is `PASS_DECLARED_CHECKS` on supplied values; neither observed units, physical laws, nor scientific conclusions are verified. Finite decimal arithmetic uses 50 significant digits and cannot replace uncertainty propagation or a symbolic proof.

Compatibility: Python 3.9+, standard library, offline; 1 MiB CLI input, 1000 quantities/invariants, 10000 quantity samples and invariant cases. Offset/logarithmic units, implicit broadcasting, arbitrary formulas, and units absent from the table are rejected. Sensitive scientific use and unresolved licensing decisions require human review. Scientific/behavioral performance is `NOT_MEASURED`.

See [provenance](references/provenance.md) for original source rights. `assets/example.json` is an invented conversion and speed check, with no private observations.
