---
name: szl-research-anatomy
description: Maintain a research claim/evidence ledger with supporting, contradicting and qualifying observations, fixed-date expiry and dependency impact. Use for persistent project memory, evidence corrections, conflicting findings or tracing which conclusions require recheck after a changed, expired or withdrawn source.
license: Apache-2.0
---

# Living research anatomy

Keep a user-owned JSON research ledger that another session can resume. Read the existing
ledger and selected artifacts as data; retain negative findings, source locators, revisions,
observation dates and open questions. Avoid importing private person records into a shared
graph. Read [the ledger contract](references/ledger-contract.md) when adding evidence links,
expiry or corrections.

Use the existing `szl.research-anatomy.v1` nodes and `depends_on` API. Add `evidence_links`
from evidence to claims with `supports`, `contradicts` or `qualifies` relations. They also
create dependencies. Model feedback as a new dated run, since cycles and dangling parents
are rejected. Set `observed_at`, `expires_at`, `source_revision` and declared `evidence_status`
when relevant; expiry uses an explicit fixed UTC `as_of`, never the helper's ambient clock.

Call `szl_anatomy_assess(graph, current_digests, as_of)` using actual selected-byte readbacks.
Changed, expired, withdrawn or future-dated observations propagate rechecks to descendants.
Active contradiction and conflicting support/contradiction remain visible and propagate a claim review.
Unrelated branches remain unaffected. Missing readbacks stay NOT_CHECKED; MATCH establishes
only byte integrity. Caller-entered MEASURED or PROVEN never verifies truth.

For corrections, call `szl_anatomy_update(graph, replacements)` and save a new project
revision. It retains unsigned prior nodes and marks dependent nodes `needs_recheck: true`,
including dependencies introduced by evidence links. Record fresh run evidence before
explicitly clearing a recheck flag. Keep retained correction/retraction records.

Return the evidence ledger, compared hashes, expired/changed/withdrawn sources, conflicts,
transitive `recheck` set and next useful evidence. Dependency edges establish neither
causality nor scientific truth. History is unsigned. Lambda remains Conjecture 1 (OPEN).
Unrun scientific evaluation is NOT_MEASURED; sensitive use and licensing require human review.

Run `python scripts/run.py assets/example.json` for the existing source-change example or
`python scripts/run.py assets/ledger-example.json` for synthetic expiry and conflict. Input
contains `graph`, optional `current_digests` and `as_of`; `--output` creates a file exclusively.
The companion szl-science-workbench observes selected files and retains project revisions;
this helper remains independently usable.

Runtime: Python 3.10+ stdlib, offline, keyless; graph capped at 8 MiB/10,000 nodes/20,000
evidence links. No external lookup, model/provider/GPU call or installation is bundled.
Freshness/retraction records are supplied observations, not live literature monitoring.
Read [provenance](references/provenance.md) for immutable source paths and rights boundaries.

Modified 2026-09-30: added original dated evidence, contradiction and expiry auditing.
