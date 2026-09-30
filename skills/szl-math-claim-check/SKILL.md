---
name: szl-math-claim-check
description: Audit a mathematical claim's assumptions, supplied formal proof records and runtime scope, or check bounded numerical observations. Use when a theorem, formula or implementation is presented as evidence and its exact proposition, domain, numeric semantics and proof obligations need to remain distinct.
license: Apache-2.0
---

The numerical `szl_weighted_geomean` helper accepts axes in [0,1] and nonnegative
weights that sum to one; omitted weights are uniform. Normalize conventional
weights before calling it. A zero axis with positive weight pins the result to zero.

# Mathematical claim checking

Write the exact statement, quantifiers, domain and assumptions. Separate definitions,
cited theorems, conjectures, numerical observations and remaining proof obligations.

For a theorem-to-runtime claim, read [the scope contract](references/scope-contract.md).
Supply immutable source/toolchain/dependency observations, the actual theorem symbol,
proposition bytes, retained checker log and its transitive axiom observations. Review the
informal-to-formal and formal-to-runtime correspondence with evidence; a text search for
`sorry` or a repository's PROVEN label cannot establish this correspondence. Call
`szl_audit_math_scope(contract)` or run `python scripts/run.py assets/scope-example.json`.
The helper checks bindings, missing assumptions, interval extensions, one-way claims
presented as iff, unfinished proof observations, custom axioms and numeric boundary review.
It reads supplied records only; it never invokes Lean, evaluates formulas or executes a
source's command. `STRUCTURAL_CHECKS_PASSED` leaves `proof_discharged: false`.

For numerical cases, use the existing `szl_check_math_cases(claim, cases, relation, atol,
rtol)` API or `python scripts/run.py assets/example.json`. Fix tolerances and tested domain
before observations; include zeros, boundaries and extreme scales when permitted. Cases
supply reviewed `inputs`, `lhs`, `rhs`. Inspect exact arithmetic and rounding before treating
a discrepancy as mathematical refutation. Successful samples establish only
NO_COUNTEREXAMPLE_IN_TESTED_CASES. `szl_weighted_geomean` remains a strict [0,1] helper:
positive-weight zeros pin the result to zero; zero-weight axes are ignored. Do not silently
replace a production gate with it. Lambda remains **Conjecture 1 (OPEN)**.

Return the bound statement/proposition/source hashes, findings, tested domain and remaining
obligations. Retain failures. Unrun scientific or behavioral evaluation is NOT_MEASURED.
Sensitive scientific use and licensing decisions require human approval.

Runtime: Python 3.10+ stdlib, offline, keyless; CLI input capped at 8 MiB. Scope comparison
supports named real intervals, not arbitrary logical predicates or semantic proof checking.
No external service, model/provider/GPU call or dependency installation is bundled.
Read [provenance](references/provenance.md) for source pins and source-rights boundaries.

Modified 2026-09-30: added the original supplied-record scope audit and its boundaries.
