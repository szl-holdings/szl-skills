# Contributing

**Control before action. Evidence after.** Three doctrine rules govern every
contribution to this repository:

1. **Control before action.** State the invariant first — a failing test, a
   policy check, or a written assertion — then change behavior. No silent
   refactors of governed surfaces.
2. **Evidence after.** Every change updates its receipts: CI output, test
   runs, screenshots, audit artifacts. If something was not measured, say
   UNKNOWN — UNKNOWN is never claimed as PASS.
3. **Canonical surfaces only.** Product lives at a-11-oy.com, proof lives at
   a11oy.net. The legacy lookalike domain is forbidden and must never appear
   in code, docs, config, or fixtures. This repository has no automated
   forbidden-domain gate, so reviewers check for it.

## Branches

- Branch from `main`. One concern per branch; keep diffs reviewable.
- Never push to the default branch — every change lands via pull request.

## Commits

- [Conventional Commits](https://www.conventionalcommits.org):
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:` …
- Sign-off trailers are optional. The organization's solo-builder provenance
  policy ([szl-holdings/.github](https://github.com/szl-holdings/.github))
  does not require them.

## Checks

Run these from the repository root before you push (Python 3.12 or newer,
standard library only):

```bash
python -B tools/selfcheck.py
python -B tools/sync_workbench.py --check
python -B -m unittest discover -s tests -v
```

CI runs the same three checks on Linux and the evidence-map portability tests
on Windows (selfcheck workflow) for every pull request. The SSH build contract
runs when the SSH lane changes, and CodeQL runs for pull requests to `main`.

## Pull requests

- Fill in the organization's pull-request template — every section is a
  control or a receipt.
- CI must be green.
- If the change alters a governed surface, attach the receipt that proves it.
