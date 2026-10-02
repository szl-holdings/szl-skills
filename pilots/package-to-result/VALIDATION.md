# Package-to-result continuity: offline pilot

This unregistered pilot asks a narrow question: do *supplied* records for one Python wheel agree
across a PyPI file listing, an extracted provenance identity, a supplied GitHub tag readback, pip's
installation report, a later `pip inspect` snapshot, and a declared analysis session? It neither
fetches nor installs a package and never executes a candidate script. The included `synthetic.json`
fixture names a nonexistent study and URL; all hashes and readbacks in it are synthetic assertions.

Run the focused tests from the repository root:

```bash
python -B -m unittest discover -s tests -p 'test_package_to_result_pilot.py' -v
python -B tools/selfcheck.py
python -B tools/sync_workbench.py --check
```

`kernel.check(record)` accepts schema `szl.package-result-pilot.v1` and returns a bounded receipt.
`CONSISTENT_SUPPLIED_EVIDENCE` means equality of the supplied project, version, wheel SHA-256,
publisher repository, source digest/ref, tag readback, pip report artifact hash, installed
distribution identity, and session links to the exact supplied pip reports. `GAP_OR_CONFLICT`,
`INCOMPLETE`, and `REFUSED` retain disagreement, absent evidence, and malformed or ambiguous
inputs separately. `kernel.loads_strict` rejects duplicate JSON keys, non-finite numbers and
oversized input. A record is capped at 128 KiB; pip report lists are capped at 1,000 entries.
Nested records are capped at 64 levels and 10,000 visited values, independent of platform-specific
JSON recursion behavior.

The receipt always says `attestation_cryptographically_verified: false`,
`actual_installation_observed: false`, `analysis_execution_observed: false`, and
`scientific_result_validity: NOT_EVALUATED`. The self-digest identifies the supplied record; it
does not authenticate it. A pip installation report can also be produced by `--dry-run`; the
supplied `install_invocation.dry_run` field is checked but is not an independent process witness.
`pip inspect` shows installed metadata, not the wheel bytes originally used. Matching fields do
not prove that the package was imported in a real analysis or that any scientific conclusion holds.

Before promotion to a registered skill, a separate opt-in live pilot would need to run PyPI's
[`pypi-attestations` verifier](https://docs.pypi.org/attestations/consuming-attestations/) against
the exact retained wheel, compare its certificate source digest/ref to a fresh GitHub tag readback,
and capture a controlled, hash-pinned installation plus the resulting interpreter's
[`pip inspect` report](https://pip.pypa.io/en/stable/reference/inspect-report/). Pip documents the
[`--report` format](https://pip.pypa.io/en/stable/reference/installation-report/) and
[`--require-hashes`](https://pip.pypa.io/en/stable/topics/secure-installs/). That live stage is not
performed here. This pilot contacts no outside service, needs no credential, and makes no release
or Claude Science registration claim.
