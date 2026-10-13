---
name: szl-evidence-gate
description: "Checks whether each claim in a paper, model card, README or grant report has a real artifact behind it (file present, bytes match a declared digest, required text present) and returns PASS / FAIL / ABSTAIN / ERROR per claim. Use when the user asks to verify a results section, audit a model card or dataset card, check supplementary files against the text, or prepare a reviewer response; also when someone says 'is this claim backed up'. Not for judging whether a claim is scientifically correct."
license: Apache-2.0
---

# Evidence gate

Answers one question per claim: is there a checkable artifact behind it? Each claim names
the files that are supposed to support it; the gate confirms presence, byte digest and
required text, and says ABSTAIN when a claim declares nothing checkable at all. Stdlib only,
offline, reads only the files the claims file names.

## Use when

- A PI asks "does every number in the results section point at a file in the repo?"
- A model card says "ECE < 0.05" or "no template leakage" and you need to show which file proves it.
- A reviewer asks for the artifact behind a specific sentence.
- Preparing supplementary material: the gate's report is the artifact index.

## Quick start

Write a claims file (see `assets/example.json`) and run it against the directory that holds the artifacts:

```bash
python scripts/run.py assets/example.json
```

The synthetic example returns `"status": "ABSTAIN"` with counts `{"PASS": 2, "FAIL": 1, "ABSTAIN": 1, "ERROR": 0}`:
the accuracy claim PASSes (digest matches `assets/artifacts/held_out_metrics.csv`), the leakage claim PASSes
(`assets/artifacts/eval_protocol.txt` contains the declared sentence), the calibration claim ABSTAINs (no evidence
declared), and the optional energy claim FAILs with `MISSING_ARTIFACT`. The exit code is 0 whenever the gate ran; 2 on malformed input. Read the status, not the exit code.

## Claims file

```json
{"subject": "what is being audited",
 "claims": [{"id": "held-out-accuracy", "text": "the sentence as written", "required": true,
             "evidence": [{"path": "artifacts/metrics.csv", "sha256": "<64 hex>", "must_contain": ["0.912"]}]}]}
```

`sha256` and `must_contain` are optional; with neither, the item is checked for presence only and the
claim is labelled `PRESENCE_ONLY_NO_DIGESTS_DECLARED`. `required: false` keeps a failing claim out of the
aggregate FAIL. Paths are relative to `--root` (default: the claims file's directory) and may not escape it.

Input checks fail closed: no claims is ABSTAIN `NO_CLAIMS_DECLARED`; repeated claim ids are report ERROR
`DUPLICATE_CLAIM_ID`; a non-boolean `required`, a resolved path (symlinks included) outside the root, or
an artifact over 32 MiB is ERROR `REQUIRED_NOT_BOOLEAN`, `PATH_OUTSIDE_ROOT` or `ARTIFACT_TOO_LARGE`.
The CLI exits 2 on duplicate JSON keys or NaN/Infinity.

## Numeric binding (opt-in)

Add `"strict_numeric_binding": true` (`false`: report only), as in `assets/strict-example.json`:

```bash
python scripts/run.py assets/strict-example.json
```

Each evaluated claim (not a malformed or `REQUIRED_NOT_BOOLEAN` one) gets `numeric_binding`
`{numbers_in_text, bound, unbound, unparsed}` (schema v2). A claim number is bound only by a passing
item's `values` (number strings, each a whole token in the artifact) or by a `must_contain` snippet
whose number is a whole artifact token at the place the snippet matched. Strict mode turns a PASS claim
with an unbound number, or with a digit it cannot read (`unparsed`), into ABSTAIN `NUMBERS_NOT_BOUND`:
here the seed-11 claim binds `11` and `0.912`, the digest-only mean claim leaves `0.912` unbound.

A token keeps its sign (`-`, `+`, U+2212 read as `-`, an en dash kept as written), `1,234` grouping,
a leading-dot decimal (`.05`) and an `e`, `^`, superscript or `×10` exponent (`1.2×10⁻⁵` is one token).
A dash between two numbers is a range, and both ends must bind. `unparsed` catches what the grammar
does not read: a digit outside every token and identifier (a spaced superscript, `x−0.3`, `10₂`) or a
comma that is not 3-digit grouping (`0,91`, `7,11`).

## Reading the result

| Status | Meaning |
|---|---|
| PASS | every declared artifact present, digests and text match |
| FAIL | an artifact is missing, its bytes differ, or a required snippet is absent |
| ABSTAIN | the claim declares no evidence (or, strict, has an unbound or unreadable number) |
| ERROR | the claim or evidence record is malformed or refused |

The aggregate is FAIL if any required claim fails, otherwise ABSTAIN if any claim abstains or none
exist, otherwise PASS.

## What it does not do

It does not read the claim text for meaning, does not open PDFs or notebooks, does not recompute
numbers, and does not decide whether the artifact actually supports the sentence. A PASS says the
evidence exists and is intact; a human still has to read it. Numeric binding is exact token matching
(`0.9120` does not bind `0.912`, `.05` does not bind `0.05`); units, rounding and arithmetic are not
checked, so a bound number can still be the wrong value or row. Digits attached to a word, directly or
by `-`, `.` or `^` (`R07`, `SHA-256`, `GPT-3.5`, `R^2`, and also `x-0.3`), and dotted versions are
identifiers, not numbers. A sign separated from its digits by a space is not read. Superscripts right
after a number are read as its exponent, footnote marks included. In an artifact, comma-joined digits
also count as separate CSV fields, so `values: ["234"]` binds inside `1,234`.
Contract: `references/contract.md`; provenance: `references/provenance.md`.
