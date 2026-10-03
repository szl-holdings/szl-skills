# Claude Science setup

This source tree contains thirty-eight skills: thirty-six science tools and two evidence
skills. The core family selects twenty-seven science tools; separate design, replay, paper,
assay, multiplicity, change-impact and uncertainty families select szl-experiment-contract,
szl-experiment-replay, szl-figure-data-contract and szl-measurement-harmonizer,
szl-paper-evidence-audit, szl-assay-measurement-audit,
szl-multiplicity-audit, szl-research-change-impact and szl-uncertainty-lineage. The SDK stages all thirty-six science
tools in at most eight batches, each at most 1 MB, with a 200 KB limit per skill including its
license and notice. These are local staging bounds, not measured application import limits.
All eight families exclude szl-typesafe-ai and szl-governed-decision. The published
v0.5.0-rc.3 prerelease has thirty-five science ZIPs at immutable source
`baf0160e1acb2bee0de3c2324211d8d95e1b68b1`, including outcome preservation,
release continuity, skill update review, figure data contract, change impact,
prospective experiment design and uncertainty lineage. The earlier v0.5.0-rc.2
tag retains twenty-eight science tools and two evidence skills. The stable v0.4.0 tag
contains twenty-three science tools and two evidence skills. The historical v0.2.0-rc.1 tag
contains eight science skills. GitHub importing skills does not create a specialist. The
workbench integrates nine checks; other tools run separately.
SCIENCE_ACCEPTANCE.md retains the historical ten-package acceptance scope, which does not cover
all current science tools.

For the native Claude Science GitHub import, use Settings > Skills > Add skill >
Import from GitHub and select `szl-holdings/szl-skills`. The
[published instructions](https://claude.com/docs/claude-science/connectors-and-skills)
do not document a `repo@tag` pin; their update check compares with the latest default-branch
commit, and imported skills do not update automatically. Check installed revisions and
contents in the application. To use the fixed
v0.5.0-rc.3 prerelease, review the individual skill-named ZIPs from its
[release](https://github.com/szl-holdings/szl-skills/releases/tag/v0.5.0-rc.3)
and use Upload a skill. The signed tag points to
`baf0160e1acb2bee0de3c2324211d8d95e1b68b1`. MEASURED package checks cover
all 306 ZIP members, public asset hashes, 34 SIMULATED CLI cases and paired-science
entrypoint presence. Actual host import and specialist activation were NOT RUN;
scientific usefulness remains UNKNOWN. The release's fixed source snapshot retains
older marketplace metadata, disclosed in its notes; the source-bound manifest
identifies the exact ZIP bytes.

Use a clean, isolated checkout of the reviewed full source commit before staging resources.
bundle_batches() reads the working tree; it does not resolve an immutable revision itself. Actual
application registration requires separate human review and authorization.
The older one-bundle `bundle(".", family="core")` call exceeds the unchanged 1 MB limit for
this source tree; use the bounded batch path below.

For supported SDK registration and specialist setup, select this repository as the Claude
Science project. In that application's **repl** control-plane tool (not its scientific
Python tool or a terminal), execute:

```python
import runpy
setup = runpy.run_path("tools/install_claude_science.py")
resources = setup["bundle_batches"](".", family="all")
receipt = setup["install"](host, resources, "claude-science-install-receipt.json",
                           update=True, family="all")
print({"status": receipt["status"], "agent": receipt["agent"],
       "staging": receipt["staging"]})
```

The SDK procedure preflights all thirty-six selected science tools and batch limits before any
write, checks returned sidecar gates for edited kernel.py resources, publishes through
host.skills and reads every staged resource path back. A new SZL_SCIENCE profile requests
exactly the thirty-six selected tools and zero connectors on creation. It does not switch the
conversation, read tokens, edit application databases or bypass disabled custom skills.
`update=True` updates staged paths for the selected science skills; protected bundled-name collisions
and a different existing specialist identity stop the setup. Existing matching profiles
retain additional skills and their existing connectors/mode, which the receipt reports
explicitly. Creation and updating a matching profile therefore have different inventories.

The receipt says PUBLISHED_AND_READ_BACK only after those staged-path readbacks succeed.
It reports `resource_set_completeness: UNVERIFIED` and
`readback_scope: STAGED_RESOURCES_ONLY`: the host SDK cannot enumerate or remove extra files
already present inside a skill, so a prior executable resource may remain after an update.
A kernel gate rejection, unavailable SDK, publish refusal or mismatch is a visible failure.
An absent sidecar probe is recorded as PROBE_UNAVAILABLE, even when publication/readback
succeeds. Unchanged kernels do not receive a fresh probe; CLI-only uncertainty-lineage, paired-science,
measurement-harmonizer and workbench resources do not enter the kernel gate path. Confirm all intended resources in
the actual application's supported import/runtime and retain unresolved checks separately.
Publication/readback is distinct from real sidecar validation and agent task evaluation;
the receipt retains NOT_EXECUTED for task evaluation until a pilot is run.

Select the new specialist using the application's normal conversation picker. Pilot:

1. Initialize/run/check a synthetic workbench project. Expect deliberate leakage and math
   counterexample findings and an unqualified paired demonstration. Check retained files.
2. Change the prediction input; check again and require downstream invalidation.
3. Run again; confirm old snapshots persist and the scientist's conclusion needs review.
4. If wanted, explicitly fetch the pinned public triage study and compare saved outputs.
5. Exercise the assay audit with its two invented plates: the passing declaration and the
   deliberately failed QC. Confirm the failed run withholds every sample concentration.

This host SDK is documented by the installed Claude Science customize skill. It is not
available as a tool in the Codex session that authored this pack; actual application
registration and this agent pilot have not been claimed as completed here. SDK contract
tests use a test double and are not application-import evidence.
