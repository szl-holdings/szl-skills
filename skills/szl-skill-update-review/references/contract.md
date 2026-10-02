# Contract: szl.skill-update-review.v1

The caller supplies two **local immutable extracted package roots**, one JSON inventory per root, and a lock retained separately from both packages and inventories. The inventory and lock paths must be outside the package roots. Do not change a snapshot while the review is running. A revision is a full lowercase 40- or 64-hex pin supplied by the caller; this offline helper does not contact a host to establish where that revision came from.

## Inventory

Each inventory is UTF-8 JSON with exactly these top-level keys:

```json
{
  "schema": "szl.skill-package-inventory.v1",
  "package": "study-skills",
  "revision": "1111111111111111111111111111111111111111",
  "complete": true,
  "files": [
    {"path": "skills/demo/SKILL.md", "sha256": "<64 lowercase hex digits>", "size": 72},
    {"path": "skills/demo/scripts/run.py", "sha256": "<64 lowercase hex digits>", "size": 56}
  ],
  "skills": [
    {
      "path": "skills/demo",
      "name": "demo",
      "referenced_files": ["scripts/run.py"],
      "declared_external_hosts": [],
      "declared_credentials": [],
      "declared_license": "Apache-2.0",
      "declared_dynamic_destinations": false
    }
  ]
}
```

The shown hashes and sizes are placeholders; calculate SHA-256 over exact file bytes. Include **every** file in the root, including tests, licenses, documentation and helpers. File entries must be sorted by canonical relative POSIX path; skill entries by skill path. The skill name and license must agree with `SKILL.md` frontmatter. The reviewer accepts a bounded frontmatter subset: one plain top-level scalar key and value per line; no nested or quoted keys. Other YAML forms are incomplete rather than guessed. A skill's `referenced_files` list consists of sorted, unique skill-relative paths that exist in the inventory. Include paths used by that skill even when they are not written in its entrypoint; all visible local Markdown links and code-quoted paths under `assets/`, `docs/`, `references/` or `scripts/` must also be declared. The three declaration fields are statements supplied with the package; the reviewer compares them, not their truth.

Path traversal, absolute or noncanonical paths, symlinks, Windows reparse points, duplicate/case-colliding paths or skill names, duplicate JSON keys, missing/extra package files, mismatched hashes, inconsistent frontmatter and unlisted visible references yield `INCOMPLETE`. Limits: 2 MiB inventory, 64 KiB lock, 4,096 package files, 256 skills, 8 MiB per file, 32 MiB total. A snapshot can be complete for this check while still omitting undeclared real-world capabilities.

## Separately retained lock

The single lock is UTF-8 JSON with exactly these keys:

```json
{
  "schema": "szl.skill-update-lock.v1",
  "snapshots": {
    "old": {"package": "study-skills", "revision": "1111111111111111111111111111111111111111", "inventory_sha256": "<SHA-256 of exact old inventory bytes>"},
    "new": {"package": "study-skills", "revision": "2222222222222222222222222222222222222222", "inventory_sha256": "<SHA-256 of exact new inventory bytes>"}
  }
}
```

Retain the lock outside the update package and under independent change control. If an attacker can replace both inventory and lock, matching hashes do not establish authenticity. The report records the lock's own SHA-256 for reference; it is not a signature.

## Output and reruns

`UPDATE_REVIEW.json` uses `szl.skill-update-review.v1`. Complete reports contain `status`, `package`, old/new revisions and inventory SHA-256 values, lock SHA-256, `changes`, `rerun`, `observations`, and `limits`. `changes` separates added and removed skills, exact-byte directory move candidates, modified skills, and package file changes. Modified skills identify declared host/credential/reference/license changes, helper-byte changes, and documentation-only changes. `rerun` lists affected skill tests, package integration checks and evidence/replay work to revisit. An invalid input returns `INCOMPLETE` with errors and no change conclusion. The Markdown output conveys the same review in readable form.

`NO_RECORDED_CHANGE` applies when file bytes and declarations are the same, even if the two supplied revision labels differ; the labels are still reported. This outcome is only a byte/declaration comparison against the lock. The helper does not execute the old or new package, inspect runtime behavior, verify license rights, approve installation, or determine whether scientific results remain valid. URL and credential observations are literal strings and can miss constructed destinations; dynamic destinations are always `UNKNOWN`.
