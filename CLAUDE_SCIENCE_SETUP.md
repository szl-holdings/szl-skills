# Claude Science setup

The v0.5.0-rc.1 prerelease contains twenty-eight skills: twenty-six science tools and two evidence
skills. The default core family selects twenty-four science tools; the separate replay and assay
families select szl-experiment-replay and szl-assay-measurement-audit respectively. Each complete
family must fit the existing 1 MB resource limit. All three exclude szl-typesafe-ai and
szl-governed-decision. The stable v0.4.0 tag contains twenty-three science tools and two evidence
skills; the clustered-replication and assay audits were added in v0.5.0-rc.1.
The historical v0.2.0-rc.1 tag contains eight science skills. GitHub importing skills does not
create a specialist. The workbench integrates seven core checks; the other tools run separately.
SCIENCE_ACCEPTANCE.md retains the historical ten-package acceptance scope, which does not cover
all current science tools.

Use a clean, isolated checkout of the reviewed full source commit before staging resources.
bundle() reads the working tree; it does not resolve an immutable revision itself. Actual
application registration requires separate human review and authorization.

For supported SDK registration and specialist setup, select this repository as the Claude
Science project. In that application's **repl** control-plane tool (not its scientific
Python tool or a terminal), execute:

```python
import runpy
setup = runpy.run_path("tools/install_claude_science.py")
resources = setup["bundle"](".")
receipt = setup["install"](host, resources, "claude-science-install-receipt.json", update=True)
replay = setup["bundle"](".", family="replay")
replay_receipt = setup["install"](host, replay, "claude-science-replay-receipt.json",
                                  update=True, family="replay")
assay = setup["bundle"](".", family="assay")
assay_receipt = setup["install"](host, assay, "claude-science-assay-receipt.json",
                                 update=True, family="assay")
print({"core": receipt["status"], "replay": replay_receipt["status"],
       "assay": assay_receipt["status"]})
```

The SDK procedure stages twenty-four core science tools, checks returned sidecar gates for edited
kernel.py resources, publishes through host.skills and reads every resource back. A new
SZL_SCIENCE profile requests exactly the twenty-four selected core tools and zero connectors on
creation. It does not switch the
conversation, read tokens, edit application databases or bypass disabled custom skills.
`update=True` updates only these twenty-four named core SZL skills per core call; protected bundled-name collisions
and a different existing specialist identity stop the setup. Existing matching profiles
retain additional skills and their existing connectors/mode, which the receipt reports
explicitly. Creation and updating a matching profile therefore have different inventories.

The receipt says PUBLISHED_AND_READ_BACK only after those application readbacks succeed.
A kernel gate rejection, unavailable SDK, publish refusal or mismatch is a visible failure.
An absent sidecar probe is recorded as PROBE_UNAVAILABLE, even when publication/readback
succeeds. Unchanged kernels do not receive a fresh probe; CLI-only paired-science and
workbench resources do not enter the kernel gate path. Confirm all intended resources in
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
