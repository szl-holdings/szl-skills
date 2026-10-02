# Change-impact contract

The document has exactly `schema`, `baseline`, `current`, `observed_digests`, `required_claims` and
`as_of`. Schema is `szl.research-change-impact.v1`. Both snapshots use `szl.research-anatomy.v1`:
nodes require unique `id`, valid `kind`, nonempty `title`; `depends_on` and `evidence_links` create
directed dependencies. Every declared parent must exist in that snapshot. Model feedback as a new
run id; cycles are rejected. Evidence links target claim/proof/decision nodes and have relation
supports/contradicts/qualifies. Existing workbench metadata and retained history are accepted as JSON.

`observed_digests` maps current pinned node ids to actual SHA-256 readbacks, or null when unavailable.
Unknown and unpinned observation ids are rejected. A missing readback stays NOT_OBSERVED. Paper,
dataset, code, model, kernel, run and proof nodes without a digest are UNPINNED. Matching bytes never
clear a change in an ancestor. Caller-entered digests remain assertions until separately verified.

`required_claims` contains 1..1000 unique claim/proof/decision ids. A removed or absent required claim
returns MISSING_REQUIRED_CLAIM rather than silently falling out of the review denominator. `as_of`
and optional observed_at/expires_at fields use UTC YYYY-MM-DDTHH:MM:SSZ. An expired, corrected,
retracted or future-dated observation requires rechecking. A persistent needs_recheck flag is retained.

Added/removed nodes, any changed declared node field, added/removed dependencies, and changed evidence
relations seed impact. Dependencies are combined across baseline and current only for impact
reachability, so removing an old edge cannot hide its effect. A reversal can make the combined graph
cyclic even when each snapshot is valid; finite visited-node traversal still works. Recheck order
uses the current DAG. Removed affected nodes appear separately in retired_affected_nodes.

The report binds canonical input and snapshot JSON with SHA-256, orders ids deterministically and
gives one shortest witness path per required claim. A path is one sufficient declared reason, not
an enumeration of every possible cause. Recheck order includes all impacted current nodes, including
artifacts to reacquire and claims to review; it is not an executable or globally minimal experiment
plan. Unrelated branches are omitted on the supplied graph, subject to missing readbacks remaining
explicit. NO_DECLARED_IMPACT says nothing about omitted dependencies, authenticity or truth.

Bounds: 2 MiB input, JSON depth 64, 100,000 JSON values, 1000 nodes and 10,000 declared dependencies
per snapshot. Invalid JSON, duplicate JSON keys, nonfinite values, duplicate ids, dangling parents,
duplicate edges, invalid digests and per-snapshot cycles are rejected. CLI exit 0 means the report
was computed, including REVIEW_REQUIRED; exit 2 means invalid input or I/O failure. Output creation
is exclusive; no existing report is overwritten. Graph data is never evaluated or executed.

Original implementation for SZL Holdings under Apache-2.0; it adapts the repository's existing
research-anatomy schema. This is a useful comparison design, with no priority or first-ever claim.
