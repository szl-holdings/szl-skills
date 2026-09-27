# Fix queue format

Ask the auditor to write a folder named fix_queue containing:

1. manifest.json: a JSON array, one object per fix, with fields id (F001), platform (github or hf), repo (owner/name), repo_type (hf only: model, dataset, or space), class (BLOCK or HOLD), kind (secret, license, reference, claim, or other), title, evidence (URL, commit, or revision), and patch (file name, or null when the fix needs human judgment).
2. One unified git diff per fix (F001.patch), made with git diff against the current default branch, paths relative to the repo root.

Rules: change only what the finding requires. For secrets, never include the value; rotate the credential at its provider first, because a patch cannot remove it from git history.
