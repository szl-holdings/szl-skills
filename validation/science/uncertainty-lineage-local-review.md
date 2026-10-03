# Uncertainty lineage: local synthetic review

The source candidate implements bounded first-order uncertainty propagation for
declared arithmetic graphs. It uses 80-digit Decimal arithmetic for point values,
Jacobians and the standard-uncertainty square root, exact Fraction LDL factorization
for correlation positive-semidefiniteness, and exact rational covariance accumulation
on the computed decimal Jacobian values.
The formulas and bounds are documented in the skill's contract. The implementation
is independently authored for this repository, uses the standard library, and
contacts no services. No novelty or scientific-truth claim is made.

An independent local agent reviewed the prototype on 2026-10-02. The original
`scripts/run.py` SHA-256 was
`0D21DDF69AC0B251822AC238D719BD74E414300FF3158765750BD719927ABDA5`;
that prototype hash is retained here as historical review provenance. A separate
integration review found that Decimal covariance accumulation could erase a small
positive residual for valid correlation 1 - 1e-80, that fixed precision could lose
a representable point in `(1e100 + 1) - 1e100`, and that a malformed giant numeric
exponent could escape the typed CLI error boundary. The source successor uses exact
rational covariance accumulation and an actual CLI regression for variance 2e-80
and standard uncertainty sqrt(2) * 1e-40. Inexact point/Jacobian operations now
fail closed; their intermediate exponent is bounded before Fraction conversion.
The CLI normalizes extreme numeric parse failures and overflow errors. Original
prototype tests SHA-256:
`6E9C7D3A08A79117D04872A34CB19EDBB5851E2E0A6330C4F508F21583FAD974`.
Integrated successor `scripts/run.py` SHA-256:
`EC32495247281219DBD076A7D45112A378EA9686AB2BAE5BED3BF10A357C4F29`.
Repository tests relocate the prototype tests to the standard test discovery
directory and add a regression for the independent corrected-measurement example.
The agent review is local development evidence, not an external scientific review.

The independent synthetic reference used `(raw - offset) * gain`, with raw
10 +/- 0.2, offset 1 +/- 0.1, gain 2 +/- 0.05 and correlation(raw, offset) = 0.5.
The point is 18, Jacobian is (2, -2, 9), variance is 0.3225 and standard
uncertainty is sqrt(0.3225). Quotient cases at scales 1e155, 1e-155 and 1e-200
returned value 1 and standard uncertainty 0.1 without collapsing to JSON zero.
An 80-decimal-place correlation matrix with off-diagonal -0.5 - 1e-80 was
rejected as non-PSD rather than rounded to a valid matrix.

The original 12 numerical/contract tests passed locally. A representative
32-input, 1024-entry, 80-decimal-place positive equicorrelation model completed
in 0.367 seconds on the review host. This is one resource observation;
adversarial worst-case exact-rational runtime was not profiled.

The integrated successor's 17 numerical/contract regressions passed locally,
including actual CLI rejection of inexact cancellation, nonterminating decimal
division, and malformed giant numeric exponents. Inventory and installer-family
tests, standalone archive execution, the unchanged 1 MB source guard, and shared
workbench synchronization also passed locally. A broader pre-repair suite was
stopped when disk space fell; it is superseded and is not a complete-suite pass.
Complete repository validation remains a separate hosted CI requirement.

A second independent local agent rechecked the successor implementation and
recreated all three reported CLI edge cases. It found no remaining actionable
finding in this bounded source integration. It also used the actual package
builder on a small isolated single-family snapshot, extracted the skill beneath
a renamed parent, and ran the synthetic example with exact script-byte and
input-hash readback. Its observed archive/installer resource total was 30,388
uncompressed bytes, below the unchanged 200 KB skill limit. That small snapshot
test is not immutable candidate-commit archive verification or full-suite CI.

The packaged example is synthetic. Local integrity, arithmetic agreement,
inventory checks, archive execution and SDK test doubles do not establish valid
input uncertainties, physical units, distribution coverage, scientific truth,
Claude Science host import, deployment or scientific efficacy. This is a separate
source candidate; prior signed release tags and their assets remain unchanged.
