# Pilot validation — 2026-10-02 UTC

Source baseline: szl-holdings/szl-skills at
f65661ada5f1f7f124555857356468353f64f0e4 (23 science / two evidence skills).
The pilot is additive and excluded from that marketplace/installer release.

Executed in Windows with Python 3.11, no provider calls:

- Focused author tests: 19 completed, OK, one skip for unavailable Windows
  symlink privilege. Reparse-point rejection is also exercised with a test double.
- Full repository tests after the final fixes: 263 completed in 131.905 seconds,
  OK, two skips. This is software validation, not scientific/agent validation.
- Independent forward exercise: 17 cases, PASS, no unexpected verdicts;
  deliberately executable candidate code remained inert. An independent CLI
  invocation matched the offline checker and kept actual_host_invocation NOT_VERIFIED.
- Skill-authoring format validator: valid.
- Synthetic demonstration: CONSISTENT_SUPPLIED_EVIDENCE, NOT_VERIFIED actual host,
  NOT_EVALUATED scientific result; source fixture digest
  8d6f2242b6e00c03bbd981322c35b12882c7225d46a5a99f1aaa4463e1a65996.

The forward review found and the author repaired two fail-closed gaps before
publication: duplicate/quoted frontmatter identities and an empty explicit alias.
Current regression tests cover both, quoted keys, escaped identity keys and merge-key
syntax. The conservative frontmatter subset is documented; this is not a YAML parser.

Known boundaries: supplied records are not authenticated host execution; task relevance
and declared PASS outcomes require separate researcher/output checks. General importer
security, signatures, real-host registration, tool permissions, scientific truth,
cross-host efficacy, model quality, GPU performance and publication are not established
by these test results. Filesystem reads assume stable owner-controlled directories;
the checker is not a sandbox against concurrent malicious filesystem replacement.

Run `python -B pilots/skill-conformance/demo.py` and the focused unittest command in
README.md to reproduce the local behavior. An actual Claude Science pilot must retain
separate real-app evidence through documented interfaces. Do not alter app databases
or import states to manufacture that evidence.
