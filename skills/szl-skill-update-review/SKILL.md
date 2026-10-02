---
name: szl-skill-update-review
description: "Compares two pinned, local skill-package inventories against independently retained hashes before an update. Reports added, removed, changed and equal-byte moved skills, declaration changes, literal strings, and affected evidence or tests to rerun. Use for an offline old/new package review; not a safety or scientific-validity approval."
license: Apache-2.0
---

# Skill package update review

Use this when a collaborator offers a new skill package and you need a bounded account of what changed from a retained local version. The helper reads two complete extracted package directories, two inventories, and a separately retained lock. It hashes package bytes and compares skill entrypoints, referenced files, declarations, licenses and helper files. Python 3.10+, standard library, offline.

## Run

From this skill directory:

```sh
python scripts/run.py OLD_INVENTORY NEW_INVENTORY --old-root OLD_PACKAGE --new-root NEW_PACKAGE --lock RETAINED_LOCK --output-dir NEW_REVIEW_DIR
```

The output directory must be new and outside both package roots. It contains UPDATE_REVIEW.json and a readable UPDATE_REVIEW.md. An incomplete input produces `INCOMPLETE` and exit 2; a complete comparison exits 0 with `NO_RECORDED_CHANGE` or `CHANGES_REVIEW_REQUIRED`. Read the detailed [inventory and lock contract](references/contract.md) before preparing inputs. The [source note](references/provenance.md) records the implementation boundary.

The bundled synthetic example can be replayed from this skill directory with a new output directory:

```sh
python scripts/run.py assets/example/old-inventory.json assets/example/new-inventory.json --old-root assets/example/old-package --new-root assets/example/new-package --lock assets/example/retained-lock.json --output-dir review-output
```

The committed readable specimen at assets/example/UPDATE_REVIEW.md shows the resulting review; running the example also emits its structured JSON. The example declares one new host and credential and changes a helper's bytes; these are invented inputs, not a real package assessment.

## Interpret the review

- Treat external hosts, credentials and licenses as declarations, even when repeated in package text. Literal URL hosts and credential markers are observations of strings; they do not prove the package's reachable destinations or credential needs. Dynamic destinations remain `UNKNOWN`.
- An equal-byte directory move is only a rename candidate. A changed helper invalidates prior helper-bound receipts and calls for affected evidence and replay tests, even when SKILL.md bytes did not change.
- If either manifest, lock or package is incomplete or contradictory, repair the snapshot and rerun. Keep the lock independently retained: recomputing both package hashes and its nearby inventory cannot establish the original snapshot identity.

The tool does not fetch, execute package code, install, approve or update anything. `NO_RECORDED_CHANGE` means only that the checked package bytes and declarations did not differ; it does not establish code safety, rights, installability or scientific validity.
