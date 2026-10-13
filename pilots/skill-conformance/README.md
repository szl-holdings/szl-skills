# Import-to-invocation conformance

An additive, standard-library-only skill for reviewing retained evidence after sharing
scientific skills. Existing importer security checks remain separate and authoritative.
The reviewed pilot now lives in `skills/szl-skill-conformance` and is selected by a
separate source-candidate marketplace and installer family. This does not change
the immutable v0.4.0 or v0.5.0-rc.3 releases or any community-index entry.

From the repository root, Python 3.10+:

```text
python -B pilots/skill-conformance/demo.py
python -B -m unittest discover -s tests -p test_skill_conformance_pilot.py -v
python -B skills/szl-skill-conformance/scripts/run.py --source SKILL_DIRECTORY
python -B skills/szl-skill-conformance/scripts/run.py --source skills/szl-skill-conformance --host-export EXPORTED_SKILL_DIRECTORY --legal-root .
```

The first command runs two synthetic tasks in a disposable directory and prints
CONSISTENT_SUPPLIED_EVIDENCE together with actual_host_invocation NOT_VERIFIED.
The third reports INCOMPLETE (exit 2), because a source directory alone does not
prove an import or trial. The fourth compares this repository's raw source with an
export staged by the installer or a ZIP from this package tool, both of which add the root
LICENSE and NOTICE; run it from a checkout at the staged revision. A matching export
without trials still reports INCOMPLETE (exit 2); any other missing, changed or extra
file is REJECTED (exit 1). Complete retained-evidence usage and bounds are in
[SKILL.md](../../skills/szl-skill-conformance/SKILL.md) and
[contract.md](../../skills/szl-skill-conformance/references/contract.md).

The checker reads candidate resources as data. It never runs their scripts, contacts
providers, authenticates, trains, downloads models, mutates an application, or updates
imports. Only the demo/tests create and remove their own temporary fixtures. File hashes
establish integrity of supplied bytes, not authenticity of a host, signature validity,
task relevance, scientific correctness, or agent effectiveness.

## Proposed Claude Science study

Compare the same pinned skill across independently retained task runs inside the actual
Claude Science app and another documented host. Preregister task IDs, intended triggers,
irrelevant-task controls, required output checks and missing-evidence handling. Keep raw
task/output exports and the source revision. Report attempted runs, imports, invocations,
correct results and unavailable traces with their complete denominators. Never replace
missing host evidence with a local fixture. Researcher inspection and actual-host exports
are necessary; this pilot itself cannot certify host invocation.

For Anthropic: request a minimal documented export format for selected-skill identity,
resource readbacks, task/result artifacts and invocation trace IDs. Begin with a small
synthetic nonclinical pilot, user-approved updates, explicit external-service disclosures
and a collision/rename case. No credential values or proprietary research data need to
be shared in the community topic.

Primary references reviewed 2026-10-02 UTC:

- [Claude Science announcement](https://www.anthropic.com/news/claude-science-ai-workbench)
- [Agent Skills structure and security](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Community skills index](https://github.com/ai4science-skills/skills)

Those references inform the proposal; they do not endorse or validate this pilot.
