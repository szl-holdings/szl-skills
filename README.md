# SZL Skills

Executable checks that make scientific claims carry their evidence. This source tree
contains forty skills for Claude Science, Claude Code and claude.ai: thirty-seven science
tools and three evidence skills. They use the Python standard library and report what they did not
verify. Normal checks are offline. Repository pins also use local Git; optional session-receipt
signing requires szl-receipt-dsse. The TypeSafe evidence skill declares its separate optional
service and credential requirements below.

The science tools have nine import families: twenty-seven core checks, one prospective experiment
draft, three bounded replay checks, one separate synthetic rare-disease evidence replay,
one paper evidence audit, one declared assay-run audit,
one multiplicity audit, one research change-impact check and one uncertainty-lineage check. The SDK stages the complete selection
in at most eight batches of at most 1 MB each, with a 200 KB limit per skill including its license
and notice. These are local staging bounds, not measured application import limits. All nine
families can attach to the same specialist. The stable v0.4.0 tag retains its original
twenty-three-tool science scope.

## Import

Claude Science: Settings > Skills > Add skill > Import from GitHub; select
`szl-holdings/szl-skills`. [Anthropic's current instructions](https://claude.com/docs/claude-science/connectors-and-skills)
document repository import and default-branch update checks, but not a `repo@tag`
pin. GitHub imports do not update automatically. Check the imported revision and contents
in the application before relying on them.
For the fixed prerelease, review and individually upload the skill-named ZIPs from the
[v0.5.0-rc.3 release](https://github.com/szl-holdings/szl-skills/releases/tag/v0.5.0-rc.3)
using Upload a skill. The signed tag points to commit
`baf0160e1acb2bee0de3c2324211d8d95e1b68b1`. The source-bound manifest
names and hashes all thirty-five ZIPs. Use that fixed revision when reproducing
the release; later default-branch changes do not rewrite its assets.

The v0.5.0-rc.3 prerelease publishes all thirty-five science tools, including outcome
preservation, release continuity, skill update review, figure data contract, research
change impact, prospective experiment design and uncertainty lineage. Its individual
science ZIPs use the enclosing-folder layout for manual claude.ai upload. MEASURED:
all 306 ZIP members were checked against immutable Git source, and all 39 public
assets were downloaded without authentication and hash-matched. The existing package
assertions passed for 34 SIMULATED CLI cases and paired-science entrypoint presence.
Actual Claude Science host import was NOT RUN; scientific usefulness remains UNKNOWN.
The release includes a verification summary and two replay scripts. Signing-key trust
remains REPO_DECLARED, without an external pin.

The older v0.5.0-rc.1 and v0.5.0-rc.2 tags and assets remain immutable.
The stable v0.4.0 tag retains twenty-three science tools and two evidence skills.
The fixed rc.3 source snapshot contains older marketplace metadata and historical
CHANGELOG counts, as disclosed in its release notes; use its source revision and
manifest rather than a version string to identify the published bytes.
The measurement-harmonizer, synthetic rare-disease evidence-replay and synthetic
rare-disease evidence-map skills are unreleased source candidates on this branch.

Claude Code: `/plugin marketplace add szl-holdings/szl-skills`, or copy a folder from `skills/`
into `.claude/skills/` or `~/.claude/skills/`.

Community index with pinned commits and automated checks: https://github.com/ai4science-skills/skills

## Start here

| If you want to... | Use | It tells you |
|---|---|---|
| run the nine integrated checks on one project and keep immutable runs | szl-science-workbench | what changed, what is stale, what has findings |
| know whether a claim in a paper or model card has a file behind it | szl-evidence-gate | PASS / FAIL / ABSTAIN per claim |
| replay a synthetic rare-disease evidence timeline without future leakage | szl-rare-disease-evidence-replay | selected HPO/ClinVar-shaped source snapshots, source/provenance ablations and a permanent HOLD readiness |
| check that a PDF table quote or figure caption has a pinned page and source region | szl-paper-evidence-audit | exact text locator or unresolved, always requiring human review |
| confirm an R rewrite matches the Python original | szl-cross-implementation-check | CONSISTENT / DIVERGENT / INCOMPARABLE per quantity |
| know whether your QC would catch a duplicated plate or a x1000 unit error | szl-analysis-mutation-test | which synthetic corruptions were caught or missed |
| write an honest energy or CO2e sentence for the methods section | szl-compute-energy-receipt | MEASURED / REPORTED / UNAVAILABLE with the sentence |
| record what an analysis session read, ran and produced, verifiably | szl-session-receipt | hashed receipt, Methods paragraph, MATCH / MISMATCH later |
| give a reviewer one page of what was checked and what is open | szl-reviewer-pack | REVIEW.md with every unresolved finding quoted |
| record a failed replication with the same weight as the original, and see what rests on it | szl-refutation-ledger | per-claim REPLICATED / NOT_REPLICATED / CONTESTED and SOUND / SHAKEN foundation, hash-chained |
| know whether the new embedding model found more of the relevant papers | szl-retrieval-eval | nDCG, MRR, MAP, recall with every judged query in the denominator |
| know whether the 4-bit or ported model still answers like the one you validated | szl-quantization-check | WITHIN_TOLERANCE / DEGRADED with the worst inputs named |
| name the exact commits of every repository an analysis used | szl-repo-pin | one composite digest, only when every tree is clean; MATCH / DRIFT later |
| know how many outcome changes stand between a result and p = 0.05 | szl-result-fragility | fragility index against the number lost to follow-up |
| distinguish many measurements from independent replications | szl-clustered-replication | declared unit counts, conditional cluster sign-flip result and leave-one-unit sensitivity |
| adjust a complete predeclared family without hiding missing tests | szl-multiplicity-audit | Holm FWER or declared-assumption BH FDR; HOLD if any planned result is missing |
| catch a scientific decision flip after a numerically close optimization | szl-outcome-preservation | complete-cohort regressions, inactive modes and withheld performance ratios |
| trace source identity through a wheel, Hub artifact and runtime | szl-release-continuity | missing bindings, conflicts and refused readiness |
| review a local skill package update before relying on its new bytes | szl-skill-update-review | added and changed skills, declarations, and evidence or tests to rerun |
| see which conclusions need reassessment when a dependency disappears | szl-research-change-impact | retained before/after graph impact, missing required claims and deterministic recheck order |
| see how corrected measurements and declared correlation change uncertainty | szl-uncertainty-lineage | local Jacobian, signed covariance contributions, standard uncertainty and retained input ancestry |

## Science checks

| Skill | Question it answers | Boundary |
|---|---|---|
| szl-dataset-readiness | Is the test set contaminated (duplicates, shared subjects, future labels, held-out fitting)? | Does not clean data or approve suitability |
| szl-model-evaluation | How good and how calibrated are these predictions, counting every attempt? | Does not run inference; binary and categorical only |
| szl-assay-measurement-audit | Did one declared linear assay run meet its calibration, blank, QC, range and uncertainty-disclosure checks? | Does not validate the method, verify uncertainty, or qualify clinical use |
| szl-math-claim-check | Does this formula or theorem claim hold on tested cases, and is the proof actually bound to the code? | Not a prover or CAS |
| szl-kernel-comparison | Does the fast implementation give the same numbers, and was the timing fair? | Does not measure energy |
| szl-paired-science | Does the paired before/after comparison bind to frozen inputs and survive its own control? | Does not verify authenticity or independence |
| szl-reproducibility-capsule | Are these exactly the files behind the result, and is a replay declaration complete? | Executes nothing; hashes are not signatures |
| szl-experiment-replay | Does a pinned, small CSV mean rerun match its frozen reference? | Fixed offline operation only; unsigned same-host receipt, not independent replication |
| szl-rare-disease-evidence-replay | What synthetic case-feature and HPO/ClinVar-shaped evidence was declared at a UTC cutoff, and what disappears under source/provenance ablation? | Synthetic source candidate only; readiness always HOLD; no diagnosis, ranking or model evaluation |
| szl-figure-data-contract | Does a declared CSV-to-SVG figure retain every point, axis unit and numeric caption assertion? | Bounded scatter/line replay; unsigned local receipt, not measurement authenticity or scientific truth |
| szl-measurement-harmonizer | Do two to four CSV exports align under declared specimen IDs and unit conversions? | Does not infer identity or validate conversion authority |
| szl-research-anatomy | Which conclusions depend on the input that just changed, expired or got contradicted? | Not a literature monitor |
| szl-artifact-lineage | Did every pipeline step consume the bytes the previous step produced? | Reads no artifacts, runs no transforms |
| szl-unit-invariants | Are dimensions, units, ranges and conservation checks consistent row by row? | No offset or log units, no uncertainty propagation |
| szl-negative-control-audit | Do the negative controls actually rule out the mechanism they claim to, and were outcomes retained? | Does not design interventions |
| szl-analysis-plan-audit | Did the analysis that ran match the frozen plan, or is the result exploratory now? | No p value, power or efficacy |
| szl-experiment-contract | Which design choices and assumptions are still missing before a prospective experiment can be reviewed? | Draft only; no preregistration, execution or scientific finding |
| szl-evidence-gate | Does each stated claim have an intact artifact behind it? | Does not judge scientific correctness |
| szl-cross-implementation-check | Do two independent implementations agree within declared tolerances on the same input? | Does not say which one is right |
| szl-analysis-mutation-test | Which classes of data corruption would the project's QC catch? | Never runs the pipeline; not a quality score |
| szl-compute-energy-receipt | What did this run cost in energy, and was that measured or estimated? | Not a hardware monitor or carbon standard |
| szl-session-receipt | What exactly did this session read, run and write, and is it still the same? | A MATCH is not correctness |
| szl-reviewer-pack | What was checked, what is still open, in one page? | Does not re-run checks or approve publication |
| szl-refutation-ledger | What has and has not replicated, and which conclusions rest on a shaken claim? | Not a verdict on truth; the chain proves order, not honesty |
| szl-retrieval-eval | How well does the retriever agree with the relevance judgments, with no query skipped? | Does not judge whether the judgments are right |
| szl-quantization-check | Does the quantized or ported model agree with the reference on these inputs? | Not accuracy; nothing about inputs not included |
| szl-repo-pin | Which exact commits, in which repositories, with nothing uncommitted? | Not publication or correctness of the code |
| szl-result-fragility | How many outcome flips remove significance, compared with the dropouts? | Not effect size, design or adjusted analyses |
| szl-paper-evidence-audit | Where did an extracted table cell or figure caption come from in a pinned PDF? | Does not perform OCR or verify values in pixels |
| szl-clustered-replication | Does a paired change survive equal experimental-unit weighting and removal of one cluster? | Independence and plan timing are declarations, not verified facts |
| szl-multiplicity-audit | Were all planned tests reported, and what are their Holm/BH adjusted p-values? | Supplied plan hash is not preregistration; raw p-values and BH dependence are unverified |
| szl-outcome-preservation | Did optimization preserve scientific decisions on every declared case? | Supplied measurements only; not GPU or scientific qualification |
| szl-release-continuity | Do registry artifacts and runtime bind to the intended source? | Supplied identity comparison; not attestation verification or release authority |
| szl-skill-update-review | What changed between two locally pinned skill packages, and which evidence or tests need another run? | Offline byte and declaration comparison; not safety, approval or scientific validity |
| szl-research-change-impact | Which conclusions still need review after a research dependency changes or disappears? | Uses declared retained graphs and supplied digests; does not verify scientific truth |
| szl-uncertainty-lineage | How does uncertainty propagate through a declared arithmetic graph? | First-order local approximation; input validity, units and distribution coverage require separate checks |
| szl-science-workbench | Nine integrated core checks on one project directory, with immutable runs and invalidation | Other science tools run separately; not an experiment runner |

Each science skill ships `SKILL.md`, a `kernel.py` or CLI, synthetic fixtures under `assets/`, a contract
under `references/` where the input format is non-trivial, and tests in `tests/`. The workbench
bundles its own copies of the check implementations (`tools/sync_workbench.py --check` keeps them
identical to the reviewed originals).

## Evidence skills

| Skill | What it does | What it does not do |
|---|---|---|
| szl-typesafe-ai | Uses TypeSafe Jev (Choice / Noul / Score) as an optional second reader for evidence-class triage. Fail-closed: any error gives UNAVAILABLE, never PASS. | Not a gate. Not TypeScript, Zod, Pydantic, mypy, or JSON Schema. Never marks anything LIVE. |
| szl-governed-decision | Wraps a classifier, policy engine, or System One model so each decision carries its own evidence. | Does not prove a model output is true. Receipts cover integrity and origin only. |
| szl-rare-disease-evidence-map | Reconciles synthetic phenotype and variant assertions into source-bound evidence with explicit HOLD results. | Not a diagnosis, clinical ranking, or validation of external source truth. |

## Try it in two minutes

```bash
python -B skills/szl-science-workbench/scripts/workbench.py init ../research-project
python -B skills/szl-science-workbench/scripts/workbench.py run ../research-project
python -B skills/szl-science-workbench/scripts/workbench.py check ../research-project
python -B skills/szl-reviewer-pack/scripts/run.py ../research-project --output ../research-project/REVIEW.md
```

The demo deliberately reports leakage, a counterexample and a rejected paired comparison; the
reviewer pack turns those into one page. `run` exit 0 means no checked findings, 1 means findings,
2 means an execution or input error. `check` exit 1 means retained evidence became stale.

For the pinned public triage study, `fetch-triage` downloads four small study files from Hugging
Face and records their hashes; no model weights are downloaded. See [VALIDATION.md](VALIDATION.md).
[MODEL_INTEGRATIONS.md](MODEL_INTEGRATIONS.md) describes optional SZL model roles;
[SCIENCE_SOURCES.json](SCIENCE_SOURCES.json) records inspected source and Hub revisions.

## Packaging and verification

```bash
python tools/package_science.py --output-dir ../science-skill-packages   # one ZIP per science skill
python -B tools/selfcheck.py
python -B tools/sync_workbench.py --check
python -B -m unittest discover -s tests -v
```

Each ZIP has one enclosing skill directory containing SKILL.md, helpers, CLI, fixtures and
license notices for manual claude.ai upload. This packaging is not evidence of a successful
Claude Science import or runtime check. Pass
`--revision <40-char commit>` to build deterministic archives from immutable Git blobs.
[CLAUDE_SCIENCE_SETUP.md](CLAUDE_SCIENCE_SETUP.md) covers the SDK installer and the curated
specialist. Actual registration and agent task performance in Claude Science remain unverified
until a pilot runs there; local tests do not establish them.

## Where these checks come from

The kernels descend from SZL Holdings' published, Apache-2.0 Python packages: calibration metrics
(szl-calibration), cross-implementation and verifier mutation testing (szl-crosscheck, szl-eclipse),
typed energy evidence (szl-energy-attest), the PASS/FAIL/ABSTAIN/ERROR evidence gate
(szl-evidence-mandate), signed receipts (szl-receipt-dsse, optional) and the weighted geometric mean
aggregator used by the reviewer pack. The skill versions are stdlib-only re-expressions for research
data rather than receipt chains; the package names are listed so the lineage can be inspected.

## Setup for szl-typesafe-ai (existing skill)

Use your own TypeSafe key. Save it as a credential (Customize > Credentials > Add Credential, generic token, name typesafe) or as the environment variable TYPESAFE_API_KEY. Never paste it into chat.

## Provenance

Built from szl-holdings/szl-typesafe-triage at commit 8d73dd23f640aace362172fff55d82ef7de63f98 (typesafe-ai source: GITHUB). License: Apache-2.0 (see LICENSE and NOTICE).
## Specialists

[specialists/szl-estate-auditor](specialists/szl-estate-auditor/SPECIALIST.md) describes a read-only Claude Science specialist that audits a GitHub org and a Hugging Face org against their public claims. Specialists are not imported with the skills; copy the fields into Customize > Specialists.

## Checks

Every push and pull request runs python tools/selfcheck.py. See [CHANGELOG.md](CHANGELOG.md).
