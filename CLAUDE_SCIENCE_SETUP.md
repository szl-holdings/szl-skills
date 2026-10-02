# Claude Science setup

This post-v0.4.0 source contains twenty-six skills: twenty-four science skills and two evidence
skills. The SDK installer selects only the twenty-four science skills; it deliberately
excludes szl-typesafe-ai and szl-governed-decision. The marketplace lists the same inventory.
SCIENCE_ACCEPTANCE.md retains the separate, historical ten-package acceptance scope; its
counts do not cover all twenty-four current science skills. The v0.4.0 tag contains twenty-three
science skills; the historical v0.2.0-rc.1 tag
contains eight science skills. GitHub importing skills does not create a specialist.

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
print({"status": receipt["status"], "agent": receipt["agent"]})
```

The SDK procedure stages twenty-four science skills, checks returned sidecar gates for edited
kernel.py resources, publishes through host.skills and reads every resource back. A new
SZL_SCIENCE profile requests exactly the twenty-four selected skills and zero connectors on
creation. It does not switch the
conversation, read tokens, edit application databases or bypass disabled custom skills.
`update=True` updates only these twenty-four named SZL skills; protected bundled-name collisions
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

This host SDK is documented by the installed Claude Science customize skill. It is not
available as a tool in the Codex session that authored this pack; actual application
registration and this agent pilot have not been claimed as completed here. SDK contract
tests use a test double and are not application-import evidence.
