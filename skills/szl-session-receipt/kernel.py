"""szl-session-receipt — a methods section that can be re-verified.

Stdlib only. Offline. Optional signing through the separately installed
szl-receipt-dsse package (never required; absence is recorded, not hidden).

Input: a session manifest the analyst or agent writes while working:

  {
    "project": "...", "session_id": "...",
    "started_at": "...Z", "finished_at": "...Z",
    "actor": {"kind": "agent"|"human", "name": "..."},
    "inputs":  ["data/raw.csv", ...],          # files read
    "code":    ["analysis.py", ...],           # scripts/notebooks executed
    "outputs": ["results/table1.csv", ...],    # files produced
    "commands": ["python analysis.py --seed 7", ...],
    "environment": {"python": "3.12.1", "platform": "...", "packages": {"numpy": "2.1.0"}},
    "notes": "free text",
    "receipt_schema": "szl.session-receipt.v2"   # optional: adds root_sha256_v2
  }

Output: a receipt that records size and SHA-256 of every listed file by role,
a root digest over the whole receipt body, the commands and environment as
declared, and a plain-language Methods paragraph. `verify` re-hashes every
file and reports MATCH / MISMATCH / MISSING per file. Unsafe paths and
symlinks are refused (ERROR PATH_OUTSIDE_ROOT); see references/contract.md.

What a MATCH means: the files on disk are byte-identical to the ones recorded.
It does not mean the analysis was correct, that the commands were the only
ones run, or that the declared environment was the real one.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

SCHEMA = "szl.session-receipt.v1"
SCHEMA_V2 = "szl.session-receipt.v2"
ROLES = ("inputs", "code", "outputs")
MAX_FILE_BYTES = 256 * 1024 * 1024


def szl_canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _outside(relative, why="not a canonical relative POSIX path"):
    return ValueError("PATH_OUTSIDE_ROOT", "%s: %r" % (why, relative))


def _locate(root: Path, relative):
    """(path, lstat) of a contained regular file, or None (MISSING)."""
    if not isinstance(relative, str) or "\\" in relative or ":" in relative or any(ord(c) < 32 for c in relative):
        raise _outside(relative)
    pure = PurePosixPath(relative)
    if pure.is_absolute() or str(pure) != relative or not pure.parts or any(p.endswith((".", " ")) for p in pure.parts):
        raise _outside(relative)
    chain = [root] + [root.joinpath(*pure.parts[:i + 1]) for i in range(len(pure.parts))]
    try:
        for node in chain:
            info = node.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise _outside(relative, "symlink or reparse point")
        if not stat.S_ISREG(info.st_mode):
            return None
        if not chain[-1].resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise _outside(relative, "resolves outside the root")
    except OSError:
        return None
    return chain[-1], info


def _file_record(root: Path, relative: str, max_file_bytes: int = MAX_FILE_BYTES) -> dict:
    found = _locate(root, relative)
    if found is None:
        return {"path": relative, "state": "MISSING", "size_bytes": None, "sha256": None}
    target, info = found
    too_large = ValueError("FILE_TOO_LARGE", "%r exceeds %d bytes" % (relative, max_file_bytes))
    if info.st_size > max_file_bytes:
        raise too_large
    digest, size = hashlib.sha256(), 0
    with target.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise _outside(relative, "changed after the containment check")
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            size += len(chunk)
            if size > max_file_bytes:
                raise too_large
            digest.update(chunk)
    return {"path": relative, "state": "RECORDED", "size_bytes": size, "sha256": digest.hexdigest()}


def szl_root_sha256_v2(files: dict) -> str:
    """SHA-256 of canonical [[role, path, sha256|null], ...] in role, then path order."""
    rows = [[role, f["path"], f.get("sha256")] for role in ROLES for f in sorted(files.get(role, []), key=lambda f: f["path"])]
    return hashlib.sha256(szl_canonical(rows).encode()).hexdigest()


def szl_methods_paragraph(receipt: dict) -> str:
    files = receipt["files"]
    n = {r: len(files[r]) for r in ROLES}
    env = receipt.get("environment") or {}
    actor = receipt.get("actor") or {}
    who = {"agent": "an AI agent", "human": "the analyst"}.get(actor.get("kind"), "the analyst")
    if actor.get("name"):
        who += f" ({actor['name']})"
    pk = env.get("packages") or {}
    pk_txt = ", ".join(f"{k} {v}" for k, v in sorted(pk.items())[:8]) + (" and others" if len(pk) > 8 else "")
    py = f"Python {env['python']}" if env.get("python") else "an undeclared Python version"
    roots = f"root digest {receipt['root_sha256'][:16]}"
    if receipt.get("root_sha256_v2"):
        roots += f", path-bound root digest {receipt['root_sha256_v2'][:16]}"
    parts = [
        f"Analysis for {receipt.get('project') or 'this project'} was performed by {who} between "
        f"{receipt.get('started_at') or 'an unrecorded start'} and {receipt.get('finished_at') or 'an unrecorded end'}.",
        f"It read {n['inputs']} input file(s), executed {n['code']} script(s) via {len(receipt.get('commands') or [])} recorded command(s), "
        f"and produced {n['outputs']} output file(s); the SHA-256 digest of every file is recorded in session receipt "
        f"{receipt['receipt_sha256'][:16]} ({roots}).",
        f"The environment was declared as {py}" + (f" with {pk_txt}" if pk_txt else "") + ".",
    ]
    missing = [f["path"] for r in ROLES for f in files[r] if f["state"] == "MISSING"]
    if missing:
        parts.append(f"{len(missing)} declared file(s) were missing when the receipt was written and are recorded as MISSING.")
    sig = receipt.get("signature", {})
    parts.append("The receipt is signed (DSSE)." if sig.get("state") == "SIGNED"
                 else "The receipt is unsigned; integrity can be re-verified by re-hashing the listed files, authenticity is not established.")
    return " ".join(parts)


def szl_make_session_receipt(root, manifest: dict, max_file_bytes: int = MAX_FILE_BYTES) -> dict:
    root = Path(root)
    if not isinstance(manifest, dict):
        return {"schema": SCHEMA, "status": "ERROR", "reason": "manifest must be an object"}
    schema = manifest.get("receipt_schema", SCHEMA)
    if schema not in (SCHEMA, SCHEMA_V2):
        return {"schema": SCHEMA, "status": "ERROR", "reason": f"receipt_schema must be {SCHEMA} or {SCHEMA_V2}"}
    files = {}
    try:
        for role in ROLES:
            listed = manifest.get(role, []) or []
            if not isinstance(listed, list) or not all(isinstance(p, str) for p in listed):
                return {"schema": schema, "status": "ERROR", "reason": f"{role} must be a list of relative paths"}
            files[role] = [_file_record(root, p, max_file_bytes) for p in sorted(set(listed))]
    except ValueError as exc:
        return {"schema": schema, "status": "ERROR", "reason": exc.args[0], "detail": exc.args[-1]}
    commands = manifest.get("commands", []) or []
    if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
        return {"schema": schema, "status": "ERROR", "reason": "commands must be a list of strings"}
    body = {
        "schema": schema,
        "project": manifest.get("project"),
        "session_id": manifest.get("session_id"),
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "actor": manifest.get("actor"),
        "files": files,
        "commands": commands,
        "environment": manifest.get("environment"),
        "notes": manifest.get("notes"),
        "declared_only": ["commands", "environment", "actor", "started_at", "finished_at"],
    }
    digests = sorted(f["sha256"] for r in ROLES for f in files[r] if f["sha256"])
    body["root_sha256"] = hashlib.sha256("\n".join(digests).encode()).hexdigest()
    if schema == SCHEMA_V2:
        body["root_sha256_v2"] = szl_root_sha256_v2(files)
    body["receipt_sha256"] = hashlib.sha256(szl_canonical(body).encode()).hexdigest()
    body["signature"] = {"state": "UNSIGNED", "note": "no signer supplied; integrity only"}
    body["methods_paragraph"] = szl_methods_paragraph(body)
    missing = sum(1 for r in ROLES for f in files[r] if f["state"] == "MISSING")
    body["status"] = "RECORDED_WITH_MISSING_FILES" if missing else "RECORDED"
    body["scope"] = ("Byte identity of listed files plus declared commands and environment. "
                     "Not evidence that the analysis is correct or complete.")
    return body


def szl_sign_session_receipt(receipt: dict, private_key_pem) -> dict:
    """Optional. Uses szl-receipt-dsse (pip install szl-receipt-dsse) if present.
    Returns the receipt with a signature block; UNSIGNED with a reason otherwise."""
    try:
        from szl_receipt._sign import sign_dsse  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional package
        receipt["signature"] = {"state": "UNSIGNED", "note": f"szl-receipt-dsse not importable: {type(exc).__name__}"}
        return receipt
    signable = {k: v for k, v in receipt.items() if k not in ("signature", "methods_paragraph", "status", "scope")}
    try:
        payload_b64, sig_b64 = sign_dsse(signable, private_key_pem)
        receipt["signature"] = {"state": "SIGNED", "algo": "ECDSA-P256-SHA256", "envelope": "DSSE",
                                "payload_b64": payload_b64, "sig_b64": sig_b64}
    except Exception as exc:  # pragma: no cover
        receipt["signature"] = {"state": "UNSIGNED", "note": f"signing failed: {type(exc).__name__}"}
    receipt["methods_paragraph"] = szl_methods_paragraph(receipt)
    return receipt


def szl_scan_extra_files(root, recorded) -> dict:
    """Unrecorded entries directly inside each recorded path's directory (not recursive)."""
    root, recorded = Path(root), set(recorded)
    ancestors = {p.rsplit("/", k)[0] for p in recorded for k in range(1, p.count("/") + 1)}
    directories = sorted({p.rpartition("/")[0] for p in recorded})
    extra = []
    for folder in directories:
        try:
            names = sorted(os.listdir(root.joinpath(*folder.split("/")) if folder else root))
        except OSError:
            continue
        for name in names:
            rel = f"{folder}/{name}" if folder else name
            node = root.joinpath(*rel.split("/"))
            is_dir = node.is_dir() and not node.is_symlink()
            if rel not in recorded and not (is_dir and rel in ancestors):
                extra.append(rel + "/" if is_dir else rel)
    return {"directories": [d or "." for d in directories], "extra_files": extra}


def szl_verify_session_receipt(root, receipt: dict, scan: bool = False, max_file_bytes: int = MAX_FILE_BYTES) -> dict:
    root = Path(root)
    schema = receipt.get("schema") if isinstance(receipt, dict) else None
    files = receipt.get("files") if isinstance(receipt, dict) else None
    lists = [files.get(r, []) for r in ROLES] if isinstance(files, dict) else [None]
    if schema not in (SCHEMA, SCHEMA_V2) or not all(isinstance(x, list) and all(isinstance(f, dict) and isinstance(f.get("path"), str) for f in x) for x in lists):
        return {"schema": SCHEMA + ".verification", "status": "ERROR", "reason": "not a szl.session-receipt.v1 or v2 document"}
    rows, mismatch, missing, errors = [], 0, 0, []
    for role in ROLES:
        for rec in files.get(role, []):
            try:
                now = _file_record(root, rec["path"], max_file_bytes)
            except ValueError as exc:
                rows.append({"role": role, "path": rec["path"], "result": "ERROR", "reason": exc.args[0], "detail": exc.args[-1]})
                mismatch += 1; errors.append(exc.args[0]); continue
            if now["state"] == "MISSING":
                result = "MISSING"; missing += 1
            elif rec.get("sha256") is None:
                result = "RECORDED_AS_MISSING_NOW_PRESENT"; mismatch += 1
            elif now["sha256"] == rec["sha256"]:
                result = "MATCH"
            else:
                result = "MISMATCH"; mismatch += 1
            rows.append({"role": role, "path": rec["path"], "result": result, "recorded_sha256": rec.get("sha256"), "observed_sha256": now["sha256"]})
    check = {k: v for k, v in receipt.items() if k not in ("receipt_sha256", "signature", "methods_paragraph", "status", "scope")}
    receipt_ok = hashlib.sha256(szl_canonical(check).encode()).hexdigest() == receipt.get("receipt_sha256")
    if schema == SCHEMA_V2:
        receipt_ok = receipt_ok and receipt.get("root_sha256_v2") == szl_root_sha256_v2(files)
    found = szl_scan_extra_files(root, [r["path"] for r in rows if r["result"] != "ERROR"]) if scan else None
    if errors:
        status = "ERROR"
    elif not receipt_ok:
        status = "RECEIPT_TAMPERED"
    elif mismatch or missing:
        status = "MISMATCH"
    elif not rows:
        status = "EMPTY_RECEIPT"
    else:
        status = "EXTRA_FILES_PRESENT" if found and found["extra_files"] else "MATCH"
    out = {"schema": schema + ".verification", "status": status}
    if errors:
        out["reason"] = errors[0]
    out.update({"receipt_digest_intact": receipt_ok,
                "files_checked": len(rows), "mismatches": mismatch, "missing": missing, "rows": rows,
                "signature_state": (receipt.get("signature") or {}).get("state", "UNSIGNED"),
                "scope": "Byte identity against the recorded receipt. A MATCH is not evidence of correctness."})
    if schema == SCHEMA_V2:
        out["observed_root_sha256_v2"] = szl_root_sha256_v2(
            {r: [{"path": x["path"], "sha256": x.get("observed_sha256")} for x in rows if x["role"] == r] for r in ROLES})
    if found is not None:
        out["scan"] = found
    return out
