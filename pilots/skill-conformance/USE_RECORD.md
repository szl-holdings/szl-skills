# Supplied skill-use record v1

This optional record joins one source skill bundle to one retained result artifact and,
when available, a supplied host trace file. The machine-readable shape is in
`pilots/skill-conformance/use-record.schema.json`; `skills/szl-skill-conformance/scripts/use_record.py` is the standard-library
validator. The existing positive/irrelevant-task conformance check remains separate.

```text
python -B skills/szl-skill-conformance/scripts/use_record.py --source SKILL_DIRECTORY --record use-record.json --retained RETAINED_DIRECTORY
```

The exact fields are `schema_version: 1`, `evidence_kind` (`synthetic_fixture` or
`operator_supplied_record`), `source`, `run_id`, `artifact`, and
`reported_selection`. Unknown and duplicate JSON keys fail closed. `source` contains
`repository_url`, full immutable `revision`, relative `skill_path`, source `name`,
host display `imported_name`, and `bundle_sha256`. The last digest is computed from
the complete local skill directory's sorted file manifest, as in the existing
conformance check. The URL, revision and path are **operator declarations**: this
offline checker does not fetch Git or attest that the bytes came from that commit.

`artifact` has an `id`, safe relative `path`, and SHA-256 of the retained file. The
validator reads and hashes that file. `run_id` and artifact ID form a declared join.
If no host trace is available, set `reported_selection` to JSON `null`. It returns
`INCOMPLETE_HOST_TRACE` (exit 2), `host_trace_binding: UNAVAILABLE`, and
`actual_host_invocation: UNKNOWN`, while retaining successful source/artifact byte
bindings. Missing evidence must not be filled with an invented trace.

When a trace is supplied, `reported_selection` contains `host`, `trace_id`,
`trace_path`, `trace_sha256`, `run_id`, `skill_name`, `source_revision`,
`source_bundle_sha256`, and `artifact_id`. These declarations must match the source,
run and artifact fields; the retained trace file must match its SHA-256. A consistent
record returns `CONSISTENT_SUPPLIED_RECORD` (exit 0) and
`host_trace_binding: MATCHED_SUPPLIED_BYTES`, but
`actual_host_invocation: NOT_VERIFIED`. The checker does **not** parse an arbitrary
host's trace semantics, authenticate its origin, or prove that the host selected the
skill. A researcher must inspect a trace produced by the actual host, using its
documented export interface, before making a host-invocation claim. The validator
never evaluates scientific correctness.

The source and retained roots must be stable, regular directories. This reuses the
existing conservative path and byte-reader rules: no symlinks/reparse points,
traversal, Windows reserved names, nonportable aliases or changed files during a
read; at most 1 MiB per retained file and 4 MiB per source bundle. No candidate
code is imported or run. Neither the record nor the report is a signature or
authorization. Retain original host exports and review privacy before sharing.
