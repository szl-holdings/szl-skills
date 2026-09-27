# Changelog

## v0.1.3
- szl-typesafe-ai: every jev-plane reference is now a pinned link to szl-typesafe-triage, or marked as not published. The szl-governed-decision reference now names it as a separate skill. Found by the SZL Estate Auditor; the index check had caught only one of these.
- Added tools/selfcheck.py and a selfcheck GitHub Actions job: frontmatter, a "does NOT do" statement, referenced files exist, size, license, secret and personal-path scan. Required before merging to main.
- Added the SZL Estate Auditor specialist definition (specialists/szl-estate-auditor/) and the fix queue format. Specialists are documentation to copy into Claude Science; they are not imported as skills.
- Added .gitattributes (LF line endings), SECURITY.md, and this changelog.

## v0.1.2
- szl-typesafe-ai: replaced a reference to compose_overclaim.py (a file that does not exist) with a pinned link. Caught by the ai4science-skills index checks.

## v0.1.1
- First public release: szl-typesafe-ai and szl-governed-decision.
