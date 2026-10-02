# Science integrity acceptance

Historical candidate receipt: the package counts and routing below describe this bounded
acceptance candidate. See [SKILL_INVENTORY.json](SKILL_INVENTORY.json) for the current source tree.

This candidate extends source main `9668f1571315e93ca2059b9a44f12beef483532d`.
Six existing packages receive substantial additive contracts; four original packages
cover separate handoff failures. Kernel comparison retains its numerical/timing scope.
Generated workbench helpers remain byte copies of their reviewed sources.

| Package | Change | New acceptance boundary |
| --- | --- | --- |
| szl-dataset-readiness | Upgraded | Entity and timestamp evidence; actual transform fit rows |
| szl-paired-science | Upgraded | Separately frozen input/target/corpus/scorer/normalization bytes |
| szl-model-evaluation | Upgraded | Planned attempts, failures, missing outputs and explicit denominators |
| szl-reproducibility-capsule | Upgraded | Role-tagged bounded replay specification; commands remain inert |
| szl-math-claim-check | Upgraded | Proposition, theorem/log/pin binding and formal/runtime scope |
| szl-research-anatomy | Upgraded | Evidence contradictions, fixed-date expiry and descendant invalidation |
| szl-artifact-lineage | New | Required transformations and producer/consumer digest continuity |
| szl-unit-invariants | New | Restricted SI dimension/conversion/range/invariant grammar |
| szl-negative-control-audit | New | Computational control registry, declared graph and retained outcomes |
| szl-analysis-plan-audit | New | Frozen analysis settings, attempt schedule and versioned deviations |

## Reproduce the bounded acceptance run

The initial Python 3.12.3 snapshot passed **132 author tests and 13 independent forward
scenarios**, with no failures, errors or skips. The initial combined run found an
incomplete-fit status bug and an overbroad capsule credential guard; both were corrected
with regressions. Two independent fixture assumptions were aligned with the documented
weight and computational-control contracts. Full input hashes, final logs, denial probes
and the unchanged initial failure record are retained under `validation/science/`.

Use an existing non-root Linux Python 3.12 runtime with Landlock and libseccomp.
The runner installs no software. It fails closed if OS controls or probes fail.

```bash
python -I -B tools/science_acceptance.py --source . --probe-only
python -I -B tools/science_acceptance.py --source . --scope upgrades
python -I -B tools/science_acceptance.py --source .
python -I -B tools/science_acceptance.py --source . --tests test_science_forward.py
python -I -B tools/check_science_contracts.py
```

Only the ten explicit packages and selected newly authored acceptance tests enter
a temporary snapshot. The environment is cleared. Landlock permits files only
in that snapshot and the standard library; seccomp denies sockets, child processes,
process inspection and privileged operations. Synthetic probes must observe denied
access to an outside fake secret, a host-mounted file, a socket and a child process.
Limits: 512 MiB address space, 60 CPU seconds, zero child processes, 64 descriptors,
2 MiB per written file and a 4 MiB input snapshot. The explicit input inventory is
limited to 256 regular files. This is bounded synthetic helper validation, not a
general hostile-code sandbox certification or a total filesystem quota.

Every package has a usable success fixture and more than five adversarial cases.
Tests assess observable findings, byte continuity, numerical invariants, coverage
and failure handling. Package syntax/frontmatter and mirror checks inspect code
as data; they do not establish scientific or behavioral safety. Existing CI runs
the repository's broader regression, packaging and SDK-test-double checks; its
results are distinct from this OS-isolated acceptance evidence.

The 2026-09-30 notice/documentation follow-up reran all **132 author tests and 13
independent scenarios**, with no failures, errors or skips. Its exact input inventories
and logs are `validation/science/notice-author-suite.json` and
`notice-forward-review.json`; notice/routing and structural/mirror checks are
`notice-inventory.json` and `notice-structural.json`. All prior reports, including the
initial failures, remain unchanged. Comments/notices and install documentation changed;
the scientific helper algorithms and workbench dispatch scope did not.

The P2 review follow-up first reproduced all four reported defects against the retained
source: three failed assertions and one UTC-normalization exception in 105 selected tests.
That failure receipt is `validation/science/p2-reproduction.json`; it is dated pre-fix
evidence, not the current result. Exact integer interval bounds, fail-closed UTC conversion,
option-shaped replay entrypoint rejection and exact unit JSON numeric parsing now have
17 new regression methods. All **149 author tests and the existing 13 independent forward
scenarios** pass with the same OS-denial probes, without executing any supplied replay argv.
The 82-test upgrade and 36-test lineage/unit subsets also pass. Current source inventories,
logs and static notice/routing/mirror checks are retained as `validation/science/p2-*`.
Unit input digests preserve numeric/string types and the CLI binds exact raw input bytes.
No host registration, scientific benchmark, provider call or release gate is exercised.

Source routing is fourteen packages: twelve science skills selected by the SDK installer,
and two evidence skills intentionally excluded from it. Acceptance covers the ten
upgraded/new packages; workbench and kernel-comparison retain separate existing checks.
Historical eight-skill tags, test counts and archive sizes are not new candidate results.

The independent forward reviewer receives the packages and invented research task,
without author test expectations. Its fixture outcomes cover one synthetic handoff
per package. That exercise establishes limited usability evidence, not measured
scientist productivity, model behavior or scientific utility.

## Rights and evidence boundaries

New helpers are original contributions to this Apache-2.0 source repository.
Existing SZL calibration attribution is retained. No platform proprietary source,
K-Dense custom-license subtree, CC-BY lake/papers/trust text, third-party skill,
model weights or dataset rows is incorporated. Source observations and Library
specifications guide requirements; they do not authorize execution or establish
legal clearance. Each package records its own pinned source/path provenance.

Scientific performance and model/agent effectiveness: **NOT_MEASURED**.
Novelty: **NOT_ESTABLISHED**. Formal proof, semantic correspondence, prediction
consumption, authenticated preregistration and artifact authenticity require their
own evidence. Supplied commands are never replayed. Claude Science registration and
its real sidecar gate remain **NOT_RUN**; a local AST check or SDK double is not
application registration. Sensitive scientific use and licensing decisions remain
human-owned. Draft PRs require review; this candidate is neither merged nor released.
