---
name: szl-reviewer-pack
description: "Renders one REVIEW.md a reviewer can read in five minutes from a szl-science-workbench project (latest run, every check's status, report integrity, each unresolved finding quoted) plus any standalone reports (evidence gate, cross-implementation check, mutation coverage, energy receipt, session receipt), with an optional reviewer-declared non-compensatory aggregate. Use when the user asks for a pre-review, a PI summary of what was checked, a response-to-reviewers appendix, or 'what is still open before we submit'. Not an approval to publish."
license: Apache-2.0
---

# Reviewer pack

The workbench and the standalone checks each write JSON. A reviewer wants one page. This skill
reads the latest retained run, confirms each report's bytes still match the digest the run
recorded, lists every unresolved finding with the exact field that raised it, and renders
Markdown. Stdlib only, offline; it never re-runs a check.

## Use when

- "Summarize what we checked and what is still open" before a submission or a lab meeting.
- Building a response-to-reviewers appendix from retained evidence rather than memory.
- A PI wants the finding list without opening five JSON files.

## Quick start

```bash
python scripts/run.py assets/project --config assets/review-config.json --output REVIEW.md
python scripts/run.py assets/project --extra reports/evidence-gate.json --extra reports/session-receipt.json
python scripts/run.py assets/status-probe --extra mutation-scored.json --extra cross-incomparable.json --extra gate-abstain.json
```

The bundled synthetic project renders `Verdict: REVIEW_REQUIRED` with a five-row check table
(dataset ISSUES_FOUND, math NUMERICAL_COUNTEREXAMPLE, paired REJECTED_LOCAL_COMPARISON, two clean),
the capsule digest over 16 files, and three quoted unresolved findings such as
`issues (3): ["EXACT_FEATURE_DUPLICATES", "CROSS_SPLIT_FEATURE_LEAKAGE", "CROSS_SPLIT_GROUP_LEAKAGE"]`.
Exit 0 whenever the pack rendered; 2 on an unreadable project, report or config, including
duplicate JSON keys and NaN/Infinity.

## Statuses that stay open

A standalone report is unresolved when its status is a known non-pass name (ABSTAIN, INCOMPARABLE,
NO_OUTCOMES_RECORDED, HOLD, BLOCKED, UNKNOWN, REFUSED, DIVERGED and the rest of the set in
`references/contract.md`) or when it carries finding fields such as `missed`, `not_tested`,
`incomparable` or `inference_blockers`. A mutation report SCORED 6/10 is listed through its four
missed variants, and a clustered report with an unbound plan through its inference blockers. The
`assets/status-probe/` reports are the negative fixture (third command: three unresolved) and, with
`--extra cross-consistent.json` alone, the positive one.

## The declared aggregate

If you want a single number, declare it. `assets/review-config.json` assigns a score in [0,1] and a weight
per check (default: 1.0 for a check without findings, 0.0 with findings). The pack reports the
weighted geometric mean

    Lambda(x) = prod_i x_i^w_i,  sum(w_i) = 1,  x_i in [0,1]

which is non-compensatory: one zero-scored check gives 0, so a clean kernel benchmark cannot offset
a leaking split. It is printed with its inputs, labelled ADVISORY, and never changes the categorical
verdict. It is an aggregation rule the reviewer chose, not a validated quality score.

## What it does not do

It does not re-run checks, verify scientific truth, read checks that were never run, or approve
publication. Reports whose bytes no longer match the run summary are flagged, not repaired.
It does not interpret a status it does not know: an unlisted status without finding fields is
shown in the table but not listed as unresolved.
The input layout it expects is described in `references/contract.md`.
