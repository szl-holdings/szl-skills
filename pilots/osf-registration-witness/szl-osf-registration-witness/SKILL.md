---
name: szl-osf-registration-witness
description: "Checks a public OSF registration, its attached plan bytes and DataCite DOI with bounded keyless reads. Use when a frozen analysis plan is claimed to have an OSF public registration and a reviewer needs exact file-hash and DOI evidence. This pilot does not prove that registration preceded access to outcomes or that the science is valid."
license: Apache-2.0
---

# OSF registration witness pilot

Use this pilot only for a public, unwithdrawn OSF registration with a specific OSF Storage file.
Read [the contract](references/contract.md) before interpreting the report. It needs a pinned
registration ID, file ID, materialized path and SHA-256 from the canonical source; do not take
those expected values from the same live API response that you are checking.

Run with Python 3.9+ and no API key:

```text
python -B scripts/run.py witness.json
```

For a plan created by `szl-analysis-plan-audit`, publish the standalone **canonical JSON plan
object** as the OSF registration file, and use that skill's `plan_sha256` as `expected_sha256`.
Choose `artifact_kind: canonical_analysis_plan_v1`; the helper checks downloaded bytes against
the source-pinned digest and canonical serialization. For a non-JSON attachment, choose
`opaque_file` and report only public file-byte matching, not plan binding.

The checker makes bounded GETs to the public OSF registration, identifiers and file endpoints,
and DataCite. It never uses a token, uploads, edits, registers or withdraws anything, follows
unapproved redirects, or executes attached files. A signed storage redirect is accepted only
when its HTTPS host and object path contain the pinned SHA-256. A private, pending, withdrawn, missing,
oversized, malformed or mismatched record is `BLOCKED` or `UNAVAILABLE`, not PASS.

Even `PUBLIC_PLAN_BYTES_MATCHED` means only that current public provider evidence matches the
pinned bytes and DOI. It is **not** proof of pre-data registration: the date may lack a timezone,
the outcome-data access time is not witnessed, and OSF schema responses may have approved later
revisions. It is not a statistical, causal, scientific-performance, or Claude Science
registration evaluation. Preserve exact provider revisions and independent research records.

This is an isolated pilot, not included in the released marketplace or installer.
