# Science pack validation

Observed on 2026-09-29 using Python 3.11 on Windows. These observations cover local
helpers and packaging, not successful import into Claude Science or measured agent efficacy.

- Six skill entrypoints passed the bundled skill-format validator.
- Repository selfcheck passed with zero failures: frontmatter, referenced resources,
  naming, size, license and secret/personal-path scanning.
- The behavioral suite ran 32 tests: 31 passed and one was skipped because Windows
  did not permit creating the symlink fixture. CI includes the same suite on Linux.
- Tests exercised transitive source invalidation, persisted correction history, missing
  evidence, graph cycles, duplicate ids, numerical counterexamples, invalid numbers,
  cross-split content and subject leakage, analytic probability metrics, tie-aware AUROC
  against an independent pairwise oracle, numerical mismatch, timing-context disagreement,
  arithmetic overflow, actual CPU timing, file/manifest tampering, path escape, duplicate
  JSON keys, refusal to overwrite, and unpacked renamed-directory CLI operation.
- The binary metric adaptation was compared with the inspected immutable calibration
  source at b2e317877abed98e70f9cf6730944a797837faf1 on 20 seeded synthetic trials and
  five metrics per trial. All 100 comparisons matched; maximum absolute difference 0.0.
- Individual ZIPs include their own entrypoints, helper, CLI, synthetic assets, LICENSE
  and NOTICE. Each is under 11 KB compressed and 25 KB uncompressed.

Remaining pilot: import the packaged skills into Claude Science, confirm actual sidecar
registration, and evaluate realistic scientist tasks against an unassisted baseline.
No weights were loaded, no model inference was performed, no research dataset was bundled,
and no quality, energy, accelerator speedup, deployed-service or scientific-proof claim
was qualified. Lambda remains Conjecture 1 (OPEN).
