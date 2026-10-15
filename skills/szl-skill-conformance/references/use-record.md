# Optional supplied skill-use record

Run `python -B scripts/use_record.py --source SKILL_DIRECTORY --record use-record.json --retained RETAINED_DIRECTORY`.
The record declares one source bundle (URL, full revision, path, source/imported names,
bundle SHA-256), run ID and retained artifact (ID, path, SHA-256). Set
`reported_selection` to JSON `null` without a host trace. Otherwise include a
supplied trace file digest and matching run, skill, revision, bundle and artifact IDs.
The full v1 schema and examples are in the repository's
`pilots/skill-conformance/USE_RECORD.md`. This offline byte check never verifies the
declared Git origin, host invocation, trace authenticity or scientific result.
