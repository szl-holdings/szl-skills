# SZL Skills

Fail-closed evidence and decision skills for Claude (Claude Science, Claude Code, claude.ai).

## Import

Claude Science: Skills > Import from GitHub, paste:

    szl-holdings/szl-skills@v0.1.3

Claude Code: copy a folder from skills/ into .claude/skills/ or ~/.claude/skills/.

## Skills

| Skill | What it does | What it does not do |
|---|---|---|
| szl-typesafe-ai | Uses TypeSafe Jev (Choice / Noul / Score) as an optional second reader for evidence-class triage. Fail-closed: any error gives UNAVAILABLE, never PASS. | Not a gate. Not TypeScript, Zod, Pydantic, mypy, or JSON Schema. Never marks anything LIVE. |
| szl-governed-decision | Wraps a classifier, policy engine, or System One model so each decision carries its own evidence. | Does not prove a model output is true. Receipts cover integrity and origin only. |
| szl-paired-science | Checks complete paired measurements, training-only normalization, identity controls, exact input hashes, and corrected paired comparisons offline. | Does not verify researcher declarations, certify science, or admit a model to production. |

## Science pack (0.2.0-rc.1)

Eight science skills form a connected scientific workflow. They are offline-first, stdlib-only
and prefixed with szl to avoid personal-skill name collisions. Each includes a SKILL.md,
a Claude Science kernel.py helper, a standalone command-line alternative and a synthetic
example. No weight downloads, private second-brain data, service calls or keys are bundled.

- **szl-research-anatomy** maintains persistent project memory and propagates source changes
  to dependent claims, proofs and runs without erasing previous records.
- **szl-math-claim-check** records numerical counterexamples and formal proof obligations;
  Lambda uniqueness remains Conjecture 1 (OPEN).
- **szl-dataset-readiness** detects exact feature and subject leakage across splits and
  retains missing provenance/reuse findings.
- **szl-model-evaluation** adapts the SZL calibration implementation for binary probability
  metrics and cohorts, without equating supplied predictions with verified model quality.
- **szl-kernel-comparison** compares finite numerical outputs before considering a timing
  ratio; incompatible or incomplete timing context suppresses the ratio.
- **szl-reproducibility-capsule** hashes explicitly selected files and detects changes
  relative to a retained unsigned manifest. Integrity is not authenticity or scientific truth.
- **szl-paired-science** applies a declared paired comparison with complete trials and an
  identity negative control; declarations are distinct from independent verification.
- **szl-science-workbench** runs the selected checks together, observes actual input/code
  bytes, saves immutable results and propagates changed or missing sources through anatomy.
  Its helper copies are generated and checked against the reviewed originals, not hand-maintained.

Candidate GitHub import:

    szl-holdings/szl-skills@v0.2.0-rc.1

The science plugin contains exactly these eight skills; the evidence plugin contains the
two existing skills. No duplicated skill names occur across the plugins. This is a
prerelease at a signed source revision; Stephen merges source and community-index PRs.

The workbench is also self-contained if imported alone. Start a synthetic project:

```bash
python -B skills/szl-science-workbench/scripts/workbench.py init ../research-project
python -B skills/szl-science-workbench/scripts/workbench.py run ../research-project
python -B skills/szl-science-workbench/scripts/workbench.py check ../research-project
```

The demo deliberately reports leakage, a counterexample and a rejected paired comparison.
Run exit 0 means execution completed without checked findings, 1 means completed with
findings, and 2 means an execution/input error. Check exit 1 means retained evidence became
stale or mismatched. Neither exit 0 nor matching files verifies scientific truth.

For the pinned public triage study, fetch-triage explicitly downloads four small study
files from Hugging Face and records their hashes; no model weights are downloaded. Then
run and check that project. The categorical evaluator recomputes outputs against retained
targets rather than trusting stored correctness flags. See [VALIDATION.md](VALIDATION.md).

[MODEL_INTEGRATIONS.md](MODEL_INTEGRATIONS.md) describes optional SZL model roles and evidence
to check before using them. [SCIENCE_SOURCES.json](SCIENCE_SOURCES.json) records inspected source
and Hub revisions. These references are not certifications of those assets.

For individual upload packages (no installs), run:

```bash
python tools/package_science.py --output-dir ../science-skill-packages
```

Each ZIP has SKILL.md and kernel.py at its root plus its CLI, examples and license notices.
Use the import mechanism available in your Claude Science version. [CLAUDE_SCIENCE_SETUP.md](CLAUDE_SCIENCE_SETUP.md)
provides the supported SDK installer and curated SZL_SCIENCE specialist with application
readbacks. Actual application registration and agent task performance remain unverified
until that procedure and pilot run in Claude Science's own control-plane repl. This Codex
session has no access to that SDK; local tests and SDK test doubles do not establish import.
The stable two-skill import remains v0.1.3; the science pack is an explicit prerelease.

All tests are offline:

```bash
python -B tools/selfcheck.py
python -B tools/sync_workbench.py --check
python -B -m unittest discover -s tests -v
```

## Setup for szl-typesafe-ai (existing skill)

Use your own TypeSafe key. Save it as a credential (Customize > Credentials > Add Credential, generic token, name typesafe) or as the environment variable TYPESAFE_API_KEY. Never paste it into chat.

## Provenance

Built from szl-holdings/szl-typesafe-triage at commit 8d73dd23f640aace362172fff55d82ef7de63f98 (typesafe-ai source: GITHUB). License: Apache-2.0 (see LICENSE and NOTICE).
## Specialists

[specialists/szl-estate-auditor](specialists/szl-estate-auditor/SPECIALIST.md) describes a read-only Claude Science specialist that audits a GitHub org and a Hugging Face org against their public claims. Specialists are not imported with the skills; copy the fields into Customize > Specialists.

## Checks

Every push and pull request runs python tools/selfcheck.py. See [CHANGELOG.md](CHANGELOG.md).
