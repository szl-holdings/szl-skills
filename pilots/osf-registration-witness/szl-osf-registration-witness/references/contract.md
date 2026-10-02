# Public registration witness contract

Input JSON has exactly `schema: szl.osf-registration-witness.v1`, `registration_id` (5–16
lowercase ASCII letters/digits), `file_id` (24 lowercase hex digits), `expected_path` (absolute
OSF materialized path, <=512 characters, no empty/dot/traversal segments), `expected_sha256`
(64 lowercase hex digits), and `artifact_kind` (`canonical_analysis_plan_v1` or `opaque_file`).
Keep this manifest in canonical source before asking the checker to read OSF. Do not paste tokens.

The standard-library checker uses public HTTPS GET only, without credentials. It accepts at
most 256 KiB per API response and 1 MiB of file bytes, with a 15-second timeout per request.
Only a file-byte read may follow up to two HTTPS redirects to a `storage.googleapis.com` object
under the observed `cos-osf-prod-files-us-east1` bucket with the exact pinned digest as object name. API redirects,
other storage paths or unknown hosts are `UNAVAILABLE`; inspect them before broadening the
allowlist. Signed redirect URLs are never put in the report. The file ID is read through the
registration-scoped OSF Storage detail route (without a trailing slash) and must resolve to a file whose OSF HTML link names
the same registration and whose path, size, current version and declared SHA-256 are present.
Identifier and file relationships must bind the same registration, accepting either the current
typed `data` form or the documented exact OSF `nodes/{id}/` related link, with contradictions blocked.
Downloaded bytes must match both the source-pinned and provider-declared SHA-256 and size.

For `canonical_analysis_plan_v1`, bytes must be a standalone JSON object with the seven
`szl-analysis-plan-audit` plan keys, encoded as UTF-8 canonical JSON using sorted keys,
comma/colon separators, `ensure_ascii=False`, and no NaN. This is a byte witness, not a
reimplementation of that skill's plan validation. Validate plan semantics with the original
skill separately. `opaque_file` checks bytes but leaves `plan_binding: NOT_EVALUATED`.

The OSF record must be the expected registration, public, active, non-embargoed and
nonwithdrawn, with a parseable `date_registered`. The date is retained verbatim; missing
timezone is flagged as `UNSPECIFIED`, and no pre-data chronology is inferred. Exactly one DOI
must be found on one complete OSF identifiers page (`next: null` and total equal to returned rows;
otherwise `UNAVAILABLE`), and DataCite must return a matching `findable` DOI pointing
to the public OSF registration URL. This cross-service read is metadata consistency, not
independent proof of plan timing or content validity. Schema-response history is not inspected.

Status and process exit: `PUBLIC_PLAN_BYTES_MATCHED` or `PUBLIC_FILE_BYTES_MATCHED` = 0;
`BLOCKED` (contradictory or malformed evidence) = 1; `UNAVAILABLE` (no bounded public read) = 2;
`INVALID_INPUT` = 3. Every result retains `expected_pin_provenance: CALLER_SUPPLIED_UNVERIFIED`,
`pre_data_timing: NOT_VERIFIED`,
`scientific_validity: NOT_EVALUATED`, and `provider_write_performed: false`.

Primary specifications: [OSF registration detail](https://github.com/CenterForOpenScience/developer.osf.io/blob/master/swagger-spec/registrations/detail.yaml),
[OSF file detail](https://github.com/CenterForOpenScience/developer.osf.io/blob/master/swagger-spec/registrations/file_detail.yaml),
[OSF file schema](https://github.com/CenterForOpenScience/developer.osf.io/blob/master/swagger-spec/files/definition.yaml),
and [DataCite public DOI retrieval](https://support.datacite.org/docs/api-get-doi).
