# Session receipt contract

Schema `szl.session-receipt.v1`. Files are recorded per role (`inputs`, `code`, `outputs`) as
`{path, state: RECORDED|MISSING, size_bytes, sha256}`, sorted and de-duplicated by path.

`root_sha256` = SHA-256 over the newline-joined, sorted list of file digests.
`receipt_sha256` = SHA-256 of the canonical JSON of the body before `signature`, `methods_paragraph`,
`status` and `scope` are attached; `verify` recomputes it and reports `RECEIPT_TAMPERED` on mismatch.

Verification results per file: MATCH, MISMATCH, MISSING, RECORDED_AS_MISSING_NOW_PRESENT, ERROR (bad path).
Overall: MATCH only when the receipt digest is intact and every file matches.

Signing: `szl_sign_session_receipt(receipt, private_key_pem)` imports `szl_receipt._sign.sign_dsse` lazily.
If the package is absent or signing fails, `signature.state` is UNSIGNED with the reason. Keys are never
read by the kernel; the CLI passes the PEM bytes you point it at.

## Paths, size cap, restore scan and v2 (2026-10-06)

Paths are canonical relative POSIX: no backslash, colon (`C:/`, `C:x`, streams), control character,
leading `/`, empty, `.`, `..` or trailing-dot/space segment. Each component from the root down is
`lstat`ed; symlinks and reparse points (the root included) are refused and the resolved file must stay
inside the resolved root. Violations give ERROR `PATH_OUTSIDE_ROOT` (with `detail`) and nothing is read;
a file above `max_file_bytes` (default 268435456, CLI `--max-file-bytes`) gives ERROR `FILE_TOO_LARGE`.
In `verify` those rows are `result: ERROR` and the overall status is ERROR (exit 2). Precedence: ERROR,
RECEIPT_TAMPERED, MISMATCH, EMPTY_RECEIPT (intact receipt with zero files), EXTRA_FILES_PRESENT, MATCH.

`scan=True` (`verify --scan`) adds `scan: {directories, extra_files}`: entries directly inside each
recorded path's directory that are neither recorded nor a directory leading to one (directories end
in `/`; not recursive, not hashed).

Schema v2 (`receipt_schema: "szl.session-receipt.v2"`) adds `root_sha256_v2` = SHA-256 of canonical
JSON `[[role, path, sha256|null], ...]` in role then path order, before `receipt_sha256`; `root_sha256`
keeps v1. v2 verification recomputes it from the recorded rows (difference: RECEIPT_TAMPERED) and
reports `observed_root_sha256_v2`. v1 manifests give byte-identical v1 receipts. The CLI rejects
duplicate JSON keys and NaN/Infinity with exit 2.
