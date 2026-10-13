# Measurement harmonizer contract

`schema` is `szl.measurement-harmonizer.v1`. The manifest has exactly five fields:
`schema`, `sources`, `expected_ids`, `target_unit`, and `conversions`. It is at most 64 KiB.
Duplicate JSON keys and nonfinite JSON constants are rejected.

`expected_ids` contains 1–1000 distinct canonical IDs. `sources` contains 2–4 objects
selecting distinct physical files; repeated paths or file identities are blocked.
Each source has exactly `id`, `path`, `sha256`, `id_column`, `value_column`, `unit`, and `id_map`.
The source path is POSIX-style, relative to `--root`; absolute paths, traversal, backslashes,
drive designators, symlinks, and Windows reparse points are rejected. Source files are at most
1 MiB each. Only two-column UTF-8 CSV is accepted: the header must exactly equal the two
declared column names in that order; at most 1000 data rows per file are read.

`id_map` maps every observed source ID to one expected canonical ID. It must cover exactly
the observed IDs, map no two source IDs to the same canonical ID, and cover every expected
canonical ID in each source. There is no name matching or inferred join.

`conversions` must contain exactly one entry for each observed source unit and the target
unit. Each entry has decimal-string `scale` and `offset`; normalized value is
`raw_value * scale + offset`, calculated with 50-digit Decimal precision. Scale must be
positive. The target unit's conversion must be scale `1`, offset `0`. Conversion labels
and coefficients are **user declarations**, not a trusted registry. Finite decimal input
has a bounded length and exponent; binary floating point is not used. No rounding rule,
uncertainty, logarithmic unit, or unit composition is inferred.

The report always records `mapping_authority: DECLARED_ONLY`,
`scientific_validity: NOT_EVALUATED`, `network: NOT_USED`, and `execution: NOT_RUN`.
`input_sha256` gives the observed digest of each readable source. `HARMONIZED` (exit 0)
contains sorted row-level values. `BLOCKED` (exit 2) means invalid declarations, changed
bytes, malformed rows, or another failed check. `UNAVAILABLE` (exit 3) applies only when
the checked files are missing and no other failure was found. Failed reports have empty
`rows`, even if another source was valid. The CLI writes a fresh report outside the input
root; it does not overwrite an existing report.

The report is an unsigned local observation. SHA-256 binds the bytes read to the manifest,
but does not establish source authenticity, custody, or a defensible scientific conclusion.

## Version 2 data contract

`szl.measurement-harmonizer.v2` takes the v1 fields plus exactly `as_of`, `max_age_days`,
`missing_tokens` and `max_missing_fraction`; each source adds exactly `provenance` with
`origin`, `collected_at`, `rights_declaration` and `instrument` (text up to 200 characters,
echoed as unverified declarations). Other `schema` values follow v1 unchanged.

`as_of` and `collected_at` are `YYYY-MM-DDTHH:MM:SSZ` UTC instants; `max_age_days` is an
integer 0-36500. Age is `as_of - collected_at` in whole seconds, never the host clock; above
`max_age_days` days gives `STALE`. `missing_tokens` lists 0-8 distinct exact value-cell
strings of at most 32 characters (`""` allowed; no edge whitespace, no character that
`str.isprintable` rejects, and nothing Python `Decimal` reads as finite, such as `1e100`).
A matching cell keeps `raw_value`, gets `normalized_value: null` and is never imputed; other
non-decimal cells still block. `max_missing_fraction` is a decimal string in [0, 1]; a
source whose missing cells exceed that fraction of its rows gives `MISSINGNESS_EXCEEDED`.

Both findings block (`BLOCKED`, exit 2, empty `rows`). Malformed declarations, including
`collected_at` after `as_of`, give `INVALID_DATA_CONTRACT` (detail names the field),
`INVALID_SOURCE_FIELDS` or `INVALID_TEXT`. The report adds `data_contract`: `schema_checked`,
`provenance_declared`, `staleness` (`age_seconds`, `stale`) and `missingness` (`per_source`
missing/rows, `per_specimen` sources with a missing-token cell for it, `exceeded`). Parts not
computed, and sources not fully read and parsed, are `NOT_EVALUATED`; `per_specimen` and
`exceeded` are `NOT_EVALUATED` unless every source was parsed. Each source reports its first
failed check, and its missing fraction is checked before `id_map` coverage. Unit dimensions
belong to `szl-unit-invariants`.
