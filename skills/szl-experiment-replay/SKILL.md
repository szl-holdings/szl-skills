---
name: szl-experiment-replay
description: "Replays a pinned, bounded CSV column mean, or one allowlisted v2 column operation, against a frozen reference result and writes an explicit match, divergence or incomplete receipt. Use when a researcher needs to rerun a small tabular result offline and distinguish numeric disagreement from changed evidence."
license: Apache-2.0
---

# Experiment replay

Use Python 3.10+ and the standard library to replay one fixed operation on local CSV data. This
executes the skill's built-in `csv_column_mean_v1` or one allowlisted v2 operation; it does not
execute a supplied script, shell command, notebook, plugin or arbitrary Python. It uses no network
or credentials.

## Run

From this skill directory, use a writable copy of the synthetic assets or a study root of your own:

```bash
python -I -B scripts/run.py prepare assets/declaration.json --root . --output replay-pin.json
python -I -B scripts/run.py replay replay-pin.json --root . --receipt replay-receipt.json
```

Each output path must be new and relative to `--root`; the runner refuses overwrite. The
declaration names a CSV input, JSON reference result, column and absolute decimal tolerance.
`prepare` freezes the exact input, reference and built-in `scripts/engine.py` hashes. The CLI
loads that engine before `replay` compares its hash, then checks the input and reference hashes
before calculating the mean. This detects drift after loading a trusted package; it is not a
pre-execution integrity, authenticity or sandbox gate. `MATCH` means the observed mean
fell within the declared tolerance; `DIVERGED` means it did not. Changed or missing files yield
`INCOMPLETE`; invalid pins yield `REFUSED`. Exit codes: 0 match, 1 divergence, 2 incomplete or
refused. With a valid root and a safe, new receipt path, a missing, unreadable or malformed
on-disk pin also yields a retained `REFUSED` receipt. An invalid pin is not identified by its
unvalidated claimed digest: the receipt's `pin_sha256` is null. If the root or receipt path is
unsafe or unwritable, the CLI exits 2 but cannot retain a receipt. A reference with a different
value must be deliberately prepared as a new pin to test divergence; editing a previously pinned
reference yields `INCOMPLETE`.

Retain the pin, receipt and input/reference bytes together. The self-digests catch accidental
changes but are not signatures. Receipts explicitly say `SAME_HOST_LOCAL`, `signed: false`,
`independent_witness: false` and `scientific_claims_verified: false`. The runner does not prove
the experiment design, data collection, author identity or another machine's reproduction.

## v2 operations

A v2 declaration (`assets/ops-declaration.json`) selects column sum, count, median, sample
variance or grouped mean from `scripts/engine_ops.py`, pinned separately. Exact Decimal and
Fraction paths must agree, else `INCOMPLETE`. `assets/ops-unsupported.json` is refused. See
`references/contract.md`.

## Bounds and relationship to other skills

Input CSV: at most 256 KiB, 10,000 rows, 64 columns; reference JSON: at most 4 KiB. Paths are
canonical relative paths under the selected root. Symlinks, junctions, other reparse points,
special files, traversal, credential-like names and existing output files are refused. This is
for a cooperative local filesystem, not an adversarial race-safe sandbox.

This is a CLI-only skill; `scripts/engine.py` is not a Claude Science sidecar.
Source tests and an SDK test double do not verify that the real Claude Science host can run
the CLI or attach this skill. Keep host registration and task evaluation `NOT_VERIFIED` until
that control plane supplies a gate, readback and pilot evidence.

Use `szl-reproducibility-capsule` to inventory broader source, environment and analysis plan
files. Its replay declaration remains inert; this skill independently executes only its fixed
CSV operations. For publication, arrange independent retention and a separate witness.

## What it does not do

It does not run unlisted operations or choose tolerances. v2 results are exact; v1 displays mean
and error to 80 significant digits, with an exact tolerance test. Path agreement is a same-host
software check, not independent replication; v1 pins are never upgraded.
