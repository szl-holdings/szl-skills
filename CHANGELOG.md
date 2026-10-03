# Changelog

## Unreleased source candidate

- Add szl-experiment-contract as a separate bounded design family. It drafts an offline
  prospective experiment contract with declared units, controls, falsifier, harm limit and
  cheapest decisive measurement; it does not preregister, execute or validate an experiment.
- Add szl-measurement-harmonizer to the bounded replay/data import family. It aligns two to
  four byte-pinned CSV exports using explicitly declared specimen IDs and affine unit
  conversions; ambiguous, incomplete, changed or malformed inputs yield no usable rows.
  Mappings and conversions are declarations, not authenticated identities, calibration,
  uncertainty analysis or scientific agreement. No new tag or Claude Science registration
  is claimed.
- Add szl-research-change-impact as a separate bounded import family. It compares retained
  baseline and current dependency graphs, preserves withdrawn dependencies for impact tracing,
  keeps missing required claims visible, and gives a deterministic reassessment order.
  Supplied digests and synthetic examples do not establish scientific truth or host registration.
- Current source inventory is thirty-three skills: twenty-four core science tools, one design
  tool, two replay/data tools, one each in the paper, assay, multiplicity and change-impact
  families, and two evidence
  skills. Each family retains the 1 MB bundle limit; the published v0.5.0-rc.1 tag is unchanged.

## 0.5.0-rc.1

- Add szl-paper-evidence-audit as a separate, bounded paper import family. It matches complete
  extracted table-cell or figure-caption text to byte-pinned local inputs and separate PDF
  regions, rejects inverted boxes, and always requires human visual review. It does not perform
  OCR, prove extraction lineage or scientific truth, or authorize clinical use.
- Added szl-assay-measurement-audit, a stdlib-only offline check for one researcher-declared
  increasing linear assay run. It gates calibration residuals, a separately identified blank,
  QC recovery, sample range, quantification minimum and final-result uncertainty declarations.
  Measured-control failure withholds all sample concentrations. Synthetic fixtures and
  adversarial regression tests cover dilution-basis and numeric-precision edges.
- Add szl-multiplicity-audit as a separate import family: Holm FWER or declared-assumption
  BH FDR over a complete predeclared family; HOLD if any planned result is missing. Plan hash
  is not preregistration. It stays outside the 1 MB core bundle.
- Source inventory becomes twenty-four core science tools, one replay tool, one paper audit,
  one assay audit, one multiplicity audit and two evidence skills. This source release is not
  proof of Claude Science host registration, method validation or clinical use.
- Add szl-clustered-replication: distinguish observations from declared experimental units,
  equal-cluster paired effects, an exact conditional cluster sign-flip calculation and
  leave-one-cluster sensitivity. Missing commitments or undeclared assumptions stay
  DESCRIPTIVE_ONLY; independence and preregistration timing remain unverified.
- Correct source inventory, marketplace version, installer/profile descriptions and the
  distinction between the workbench's seven integrated core checks and standalone tools.
  Published v0.4.0 and earlier tags retain their original inventories.
- New numerical tests use an independent Cartesian sign-vector oracle and invariance to
  technical replication; test execution and app efficacy are reported separately.
- Preserve the separately developed experiment replay contract and keep paper and assay audits
  outside the core bundle. All four science families retain complete helpers, licenses and
  notices and the 1 MB SDK bundle limit; the shared installer can attach all twenty-eight tools
  across four separately verified calls.

## 0.4.0

- Five new science skills, all stdlib and offline: szl-refutation-ledger (append-only, hash-chained
  record of claims, replication attempts and withdrawals; per-claim REPLICATED / NOT_REPLICATED /
  CONTESTED / INCONCLUSIVE / UNTESTED / WITHDRAWN with a SOUND / SHAKEN / UNKNOWN foundation from the
  dependency trace; unreceipted attempts counted and flagged; `verify` finds the first broken link),
  szl-retrieval-eval (nDCG@k, MRR, MAP, precision@k, recall@k with judged queries missing from the
  run kept at zero, unjudged queries listed, duplicates dropped and counted; JSON or TREC files),
  szl-quantization-check (row-wise cosine, softmax KL at a declared temperature, top-1 agreement,
  worst rows named; WITHIN_TOLERANCE / DEGRADED / INCOMPARABLE against declared tolerances),
  szl-repo-pin (HEAD, clean/dirty, exact tag and configured origin per declared repository; the
  composite digest exists only when every tree is clean; MATCH / DRIFT / DIRTY / MISSING on verify),
  szl-result-fragility (exact Fisher two-sided p, Walsh fragility index and quotient against the
  number lost to follow-up, reverse fragility index for non-significant results).
- Not added: a separate calibration skill (szl-model-evaluation already reports ECE, MCE, Brier,
  log loss, AUROC and reliability bins) and a separate signed-capsule skill (szl-session-receipt
  already signs through szl-receipt-dsse when installed).
- Marketplace plugin szl-science-skills now lists twenty-three skills; installer and packaging tests
  updated; 15 new behavioral tests (244 total). Trigger evaluation sets added for the five skills
  (rates NOT_MEASURED until executed).

## 0.3.0

- Six new science skills, all stdlib and offline: szl-evidence-gate (PASS/FAIL/ABSTAIN/ERROR per
  claim against named artifacts), szl-cross-implementation-check (CONSISTENT/DIVERGENT/INCOMPARABLE
  per quantity under declared tolerances with input-digest binding), szl-analysis-mutation-test
  (ten synthetic corruption classes, two-phase generate/score, pipeline never executed),
  szl-compute-energy-receipt (MEASURED/REPORTED/UNAVAILABLE typing, trapezoidal integration,
  methods sentence), szl-session-receipt (per-role file digests, root digest, Methods paragraph,
  verify, optional DSSE signing via szl-receipt-dsse), szl-reviewer-pack (REVIEW.md from retained
  workbench runs and standalone reports, with an advisory reviewer-declared weighted geometric mean).
- Rewrote every science SKILL.md for working scientists: trigger-oriented descriptions, a quick
  start with the fixture's real output, domain examples, and one consolidated boundary section.
  Kernels, contracts and fixtures are unchanged; the 208 existing tests still pass.
- Removed SZL-internal vocabulary from the science pack prose; the open-conjecture status of the
  demo aggregator is still recorded in reports, now explained in plain language.
- Converted the two evidence skills' folded YAML descriptions to single-line strings.
- Marketplace plugin szl-science-skills now lists eighteen skills; installer and packaging tests
  cover the new CLIs. 229 tests; selfcheck PASS; skills/ under 1 MB.
- Scientific and agent performance remain NOT_MEASURED until a pilot runs in Claude Science.

## Historical science acceptance candidate (before 0.3.0)

- Substantially upgrade six existing science packages and add four original offline audits.
  Source inventory is fourteen packages: twelve science and two separate evidence skills.
  The SDK installer selects only the twelve science skills; ten upgraded/new packages have
  bounded synthetic acceptance evidence. Workbench and kernel-comparison retain their scopes.
- Add precise modification notices to six model/capsule files, synchronize mirrors and
  correct install/profile inventory and helper-format documentation. Preserve license terms,
  calibration/MIT attribution, initial failure evidence and the previous acceptance receipts.
- Existing release tags remain unchanged. Actual host/sidecar registration and scientific
  performance remain NOT_RUN and NOT_MEASURED, respectively.

## 0.2.0-rc.1

- Connected the science pack through a self-contained workbench with immutable run directories,
  observed input/implementation hashes, retained capsules, persistent anatomy and transitive
  invalidation for changed, missing or unsafe files. Concurrent writers are rejected.
- Added categorical evaluation that joins retained targets and ignores stored correctness
  flags; integrated the pinned public triage study without downloading model weights.
- Execute the reviewed SZL geometric-mean formula and calibration source; numerical kernel
  agreement precedes actual local CPU timing. Integrated paired comparison and negative controls.
- Generate helper copies through tools/sync_workbench.py and enforce drift checks in CI.
- Package eight self-contained science skills, plus supported Claude Science SDK registration
  and a curated specialist. SDK contract tests are separate from actual application import.
- Retained stable two-skill v0.1.3 import; publish this pack as an explicit prerelease and submit
  its exact source pin to the community index. No automatic updates or owner merge.

## Initial 0.2.0 candidate

- Added six offline scientific skills: living research anatomy, mathematical claim checks,
  dataset readiness, binary model evaluation, numerical kernel comparison and reproducibility
  capsules. Each includes a sidecar helper, portable CLI, synthetic example and service disclosure.
- Adapted real SZL calibration formulas and pinned software, math, dataset and model references.
  Model weights and private second-brain records are not included. Lambda is Conjecture 1 (OPEN).
- Added behavioral and packaging tests to CI and individual ZIP packaging with explicit file
  allowlists, license notices and archive digests. The stable community index stays on 0.1.3.

## v0.1.3
- szl-typesafe-ai: every jev-plane reference is now a pinned link to szl-typesafe-triage, or marked as not published. The szl-governed-decision reference now names it as a separate skill. Found by the SZL Estate Auditor; the index check had caught only one of these.
- Added tools/selfcheck.py and a selfcheck GitHub Actions job: frontmatter, a "does NOT do" statement, referenced files exist, size, license, secret and personal-path scan. Required before merging to main.
- Added the SZL Estate Auditor specialist definition (specialists/szl-estate-auditor/) and the fix queue format. Specialists are documentation to copy into Claude Science; they are not imported as skills.
- Added .gitattributes (LF line endings), SECURITY.md, and this changelog.

## v0.1.2
- szl-typesafe-ai: replaced a reference to compose_overclaim.py (a file that does not exist) with a pinned link. Caught by the ai4science-skills index checks.

## v0.1.1
- First public release: szl-typesafe-ai and szl-governed-decision.
