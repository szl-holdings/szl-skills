---
name: szl-skill-update-review
description: "Offline review of two pinned local skill packages against inventories and a separately retained lock. Reports skill, declaration, file and helper changes plus evidence to rerun. Does not approve safety or scientific validity."
license: Apache-2.0
---

# Skill package update review

Compare two extracted, immutable local packages before an update. Supply complete SHA-256 inventories and a separately retained lock. The Python 3.10+ helper checks bytes, skills, references, declarations, licenses and helpers without network access or package execution.

## Run

From this skill directory:

```sh
python scripts/run.py OLD_INVENTORY NEW_INVENTORY --old-root OLD_PACKAGE --new-root NEW_PACKAGE --lock RETAINED_LOCK --output-dir NEW_REVIEW_DIR
```

Use a new output directory outside both roots. It receives UPDATE_REVIEW.json and UPDATE_REVIEW.md. `INCOMPLETE` exits 2; complete reviews exit 0. Prepare inputs using the [contract](references/contract.md). See [provenance](references/provenance.md).

Replay the synthetic example with a new output directory:

```sh
python scripts/run.py assets/example/old-inventory.json assets/example/new-inventory.json --old-root assets/example/old-package --new-root assets/example/new-package --lock assets/example/retained-lock.json --output-dir review-output
```

The [specimen](assets/example/UPDATE_REVIEW.md) shows the review. Its host, credential and helper changes are invented.

## Interpret the review

- Hosts, credentials and licenses are declarations. URL hosts and credential markers are partial static observations; values are omitted. Dynamic destinations remain `UNKNOWN`.
- Equal-byte moves are rename candidates. Changed helpers or referenced non-document files call for file-bound evidence and replay tests.
- Repair `INCOMPLETE` snapshots before review. Retain the lock independently; package and nearby inventory hashes alone cannot establish snapshot identity.

The tool does not fetch, execute, install, approve or update packages. `NO_RECORDED_CHANGE` establishes only equality of checked bytes and declarations, not safety, rights, installability or scientific validity.
