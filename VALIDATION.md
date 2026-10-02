# Science pack validation

The observations below describe the earlier eight-science-skill prerelease. They are
historical evidence, not measurements of the expanded source candidate or a new run.
For the later six-upgrade, four-audit candidate and its notice follow-up, see
[SCIENCE_ACCEPTANCE.md](SCIENCE_ACCEPTANCE.md). That candidate routed fourteen packages:
twelve science skills selected by its installer and two separate evidence skills.
Current source routing is twenty-eight total packages: twenty-four core science tools, one
separate replay-family tool, one separate paper-evidence-family tool, and two excluded evidence
skills. This count is not a new behavioral acceptance result.
The [source inventory](SKILL_INVENTORY.json) is generated from the current SKILL.md files
and marketplace entries; run `python -B tools/skill_inventory.py` to check it. These
historical evidence sets establish neither application import nor measured agent efficacy.

## Checks

The behavioral/packaging/SDK-contract suite ran 46 tests on Windows Python 3.11: 45 passed
and one symlink-creation test was skipped because the host denied symlink creation.
The existing paired-comparison suite passed all 12 tests. Repository selfcheck and the
generated-workbench drift check passed. CI runs the same suites on Linux Python 3.12.

Tests cover graph history and transitive invalidation, changed/missing inputs, safe paths,
duplicate JSON, simultaneous-writer rejection, completion-record tampering, field projection,
numerical counterexamples, probability metrics and AUROC oracle, invalid numbers, leakage,
kernel correctness/timing context, actual CPU timing, capsules, SDK collisions/gate rejection,
and ZIP extraction into renamed directories. The workbench runs from its individual ZIP
without any sibling skill folders. SDK tests use a test double, not Claude Science.

The binary adaptation matched five metrics from the inspected immutable calibration source
at b2e317877abed98e70f9cf6730944a797837faf1 on 20 seeded synthetic trials: 100 comparisons,
maximum absolute difference 0.0. The workbench also executes that reference and the adapted
helper on its demo input, checks agreement and measures actual CPU durations. This is not
a general acceleration, GPU or energy result.

## Connected runs

The synthetic demo completed with expected findings: feature/group leakage, a computed
geometric-mean counterexample and a rejected, unregistered paired identity demonstration.
The retained capsule matched. Source-change tests require invalidation of dependent model,
kernel, paired, overall-run and conclusion nodes. A new run preserves old records and does
not automatically clear the scientist's conclusion flag.

The explicit public-study fetch read four files at Hub revision
eb79a26a2934d5eaa667984720feacdcb90dcc28 from
[the SZL five-seed study](https://huggingface.co/SZLHOLDINGS/szl-triage-qwen3.5-0.8b-lora-study5/tree/eb79a26a2934d5eaa667984720feacdcb90dcc28).
Training row.input and held-out prompt were projected to the declared prompt feature.
628 rows (515 train, 113 held) had no checked missing, exact-feature or family-leakage
issues. This does not establish semantic separation or scientific suitability.

Recomputed against all 113 retained held-out targets:

| Saved outputs | Label accuracy | State accuracy | Joint label/state/ordered-evidence accuracy | Invalid outputs |
|---|---:|---:|---:|---:|
| Baseline | 0/113 | 0/113 | 0/113 | 113 |
| Seed 011 | 113/113 | 113/113 | 112/113 | 0 |

The capsule and completion record matched, with no changed sources. Stored correctness flags
were ignored; missing outputs stay in the denominator. Inputs are synthetic and the model
origin/held-out construction are study declarations. No new inference or independent model
binding was performed, and this is not an external-data benchmark or production admission.

## Distribution and application boundary

In that earlier prerelease, eight individual ZIPs included SKILL.md, explicit resources,
LICENSE and NOTICE. The largest
workbench ZIP was approximately 34 KB compressed and 93 KB uncompressed; all eight total
about 104 KB compressed. These sizes were not measured for the later twelve-skill
candidate. No weights, private second-brain records or downloaded study data
are included. Selected artifact downloads are explicit and their byte receipts are unsigned.

Claude Science's own control-plane SDK is unavailable in the authoring Codex session.
CLAUDE_SCIENCE_SETUP.md supplies a supported SDK installer with real-sidecar-gate handling,
publication/readback checks and a curated specialist. Actual application registration,
sidecar injection and agent performance against a baseline remain unverified until that
procedure and pilot execute in the application. No cache/database edit substitutes for it.
Lambda remains Conjecture 1 (OPEN).
