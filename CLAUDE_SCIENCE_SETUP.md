# Claude Science setup

The repository and individual ZIPs use the application's supported skill formats. The
candidate import line is in README. GitHub importing skills does not create a specialist.

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

The SDK procedure stages eight skills, accepts the real sidecar gate when available,
publishes through host.skills, reads every resource back, then creates SZL_SCIENCE with
exactly the eight selected skills and zero connectors on creation. It does not switch the
conversation, read tokens, edit application databases or bypass disabled custom skills.
`update=True` updates only these eight named SZL skills; protected bundled-name collisions
and a different existing specialist identity stop the setup. Existing matching profiles
retain their existing connectors/mode, which the receipt reports explicitly.

The receipt says PUBLISHED_AND_READ_BACK only after those application readbacks succeed.
A kernel gate rejection, unavailable SDK, publish refusal or mismatch is a visible failure.
An absent sidecar probe is recorded as PROBE_UNAVAILABLE. Publication is distinct from an
agent task evaluation; the receipt retains NOT_EXECUTED for that until a pilot is run.

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
