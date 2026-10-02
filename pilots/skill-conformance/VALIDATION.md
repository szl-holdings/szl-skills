# Pilot validation — 2026-10-02 UTC

Source baseline: szl-holdings/szl-skills at
f65661ada5f1f7f124555857356468353f64f0e4 (23 science / two evidence skills).
The pilot is additive and excluded from that marketplace/installer release.

Original pilot validation in Windows with Python 3.11, no provider calls:

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

## Post-merge identity repair — 2026-10-02 UTC

Repair baseline: protected main ec0930d1a15a9c45640da775f46f79ca034c3a70,
after PR #20. Review found that Python `splitlines()` and multiline regular
expressions disagreed about a bare CR. A duplicate `name` field could therefore
escape the identity count. The new bare-CR regression failed before the repair
(`INCOMPLETE` instead of `REJECTED`); it passes after the repair.

All structural frontmatter checks now share one CRLF/CR/LF-normalized parsing view.
File bytes and hashes are unchanged. VT, FF, NEL, LS and PS are rejected inside
frontmatter but permitted in the opaque body. The parser remains a conservative
subset, not a complete YAML implementation or importer security certification.

Independent forward testing then found six phantom-identity cases: a `name:` line
inside a multiline quoted, anchored, tagged or flow value could be mistaken for
an actual field. All six author regressions failed before the additional repair.
The supported subset is now a flat mapping with unique literal keys and single-line
scalar values. Multiline/nested/block/flow/anchored/tagged constructs fail closed.
Names must start with a letter; ambiguous implicit boolean/null tokens are rejected,
and identity trimming uses only ASCII space/tab. The complete subset is in the contract.

- Focused author tests: 25 completed in 9.359 seconds, OK, one Windows symlink
  privilege skip. Tests cover duplicate identities under all three ASCII line
  endings, distinct raw-byte hashes, the bare-CR regression, and unsupported
  separators confined to frontmatter, phantom identities, valid single-line quotes,
  structured values, duplicate fields and implicit-type names.
- Synthetic demonstration remains CONSISTENT_SUPPLIED_EVIDENCE, with the same
  fixture digest listed above; actual host invocation stays NOT_VERIFIED and
  scientific result validity stays NOT_EVALUATED.
- The historical full-suite and 17-case forward results above are not new
  executions against this repair. Local validation is scoped to this isolated
  pilot; GitHub CI runs the repository-wide checks on the published head.
- Before publication the owned branch fast-forwarded to current signed main
  074aeb54b29e6546f94161fe53b393a6aaf95234. That update adds the experiment-replay
  skill and does not change this pilot. Repository selfcheck and workbench byte-sync
  checks passed on the refreshed base; their results are separate from host efficacy.
