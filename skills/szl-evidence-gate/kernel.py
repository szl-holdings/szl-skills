"""szl-evidence-gate — does each claim have a checkable artifact behind it?

Stdlib only. Offline. Reads only the files the claims file names.

Verdicts per claim (same vocabulary as the published szl-evidence-mandate gate):
  PASS    every declared evidence item exists and matches its declared digest
  FAIL    a declared evidence item is missing, or its bytes differ from the
          declared digest, or an expected text snippet is absent
  ABSTAIN the claim declares no checkable evidence (nothing to verify)
  ERROR   the claim or evidence record is malformed (also REQUIRED_NOT_BOOLEAN,
          PATH_OUTSIDE_ROOT, ARTIFACT_TOO_LARGE)

Aggregate: FAIL if any required claim FAILs or ERRORs; otherwise ABSTAIN if any
claim is ABSTAIN (or there are no claims); otherwise PASS. Repeated claim ids are
ERROR DUPLICATE_CLAIM_ID. A PASS proves integrity and presence of the
declared artifacts. It does not prove the claim is true.
Opt-in strict_numeric_binding: see references/contract.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

SCHEMA = "szl.evidence-gate-report.v1"
SCHEMA_V2 = "szl.evidence-gate-report.v2"
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NUMBER = re.compile(r"(?<![\w.+-])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\.?\d)")
_CODED = {"PATH_OUTSIDE_ROOT", "ARTIFACT_TOO_LARGE"}


def szl_canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def szl_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def szl_number_tokens(text: str) -> list:
    """Unique decimal number tokens in order of first appearance."""
    return list(dict.fromkeys(_NUMBER.findall(text)))


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
        raise ValueError("evidence path must be relative and may not escape the root: %r" % (relative,))
    if ":" in relative.split("/")[0] and os.name == "nt":
        raise ValueError("evidence path must be relative: %r" % (relative,))
    return (root / relative).resolve()


def _check_item(root: Path, item, numeric=False):  # -> (record, bound numbers)
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        return {"path": item.get("path") if isinstance(item, dict) else None, "status": "ERROR",
                "reason": "evidence item must be an object with a string path"}, set()
    record = {"path": item["path"], "status": "PASS", "reason": None, "observed_sha256": None,
              "declared_sha256": item.get("sha256"), "size_bytes": None}
    try:
        target = _safe_path(root, item["path"])
    except ValueError as exc:
        record.update(status="ERROR", reason=str(exc))
        return record, set()
    if not target.is_relative_to(root.resolve()):
        record.update(status="ERROR", reason="PATH_OUTSIDE_ROOT")
        return record, set()
    if not target.is_file():
        record.update(status="FAIL", reason="MISSING_ARTIFACT")
        return record, set()
    size = target.stat().st_size
    if size <= MAX_ARTIFACT_BYTES:
        with target.open("rb") as handle:
            data = handle.read(MAX_ARTIFACT_BYTES + 1)
        size = len(data)
    if size > MAX_ARTIFACT_BYTES:
        record.update(status="ERROR", reason="ARTIFACT_TOO_LARGE", size_bytes=size)
        return record, set()
    record["observed_sha256"] = szl_sha256_bytes(data)
    record["size_bytes"] = len(data)
    declared = item.get("sha256")
    if declared is not None:
        if not isinstance(declared, str) or not _HEX64.match(declared):
            record.update(status="ERROR", reason="declared sha256 must be 64 lowercase hex characters")
            return record, set()
        if declared != record["observed_sha256"]:
            record.update(status="FAIL", reason="DIGEST_MISMATCH")
            return record, set()
    text = data.decode("utf-8", errors="replace")
    snippets = item.get("must_contain")
    if snippets is not None:
        if not isinstance(snippets, list) or not all(isinstance(s, str) and s for s in snippets):
            record.update(status="ERROR", reason="must_contain must be a list of non-empty strings")
            return record, set()
        missing = [s for s in snippets if s not in text]
        if missing:
            record.update(status="FAIL", reason="TEXT_NOT_FOUND", missing_text=missing)
            return record, set()
    values = item.get("values") if numeric else None
    bound = set()
    if numeric:
        tokens = set(_NUMBER.findall(text))
        if values is not None:
            if not isinstance(values, list) or not all(isinstance(v, str) and _NUMBER.fullmatch(v) for v in values):
                record.update(status="ERROR", reason="values must be a list of decimal number strings")
                return record, set()
            missing = [v for v in values if v not in tokens]
            if missing:
                record.update(status="FAIL", reason="TEXT_NOT_FOUND", missing_values=missing)
                return record, set()
            bound.update(values)
        for snippet in snippets or []:
            bound.update(t for t in _NUMBER.findall(snippet) if t in tokens)
    if record["declared_sha256"] is None and snippets is None and values is None:
        record["reason"] = "PRESENCE_ONLY"
    return record, bound


def szl_gate_claim(root: Path, claim, strict=None) -> dict:  # strict None: v1 shape
    if not isinstance(claim, dict) or not isinstance(claim.get("id"), str) or not isinstance(claim.get("text"), str):
        return {"id": claim.get("id") if isinstance(claim, dict) else None, "status": "ERROR",
                "reason": "claim must be an object with string id and text", "evidence": []}
    required = claim.get("required", True)
    if not isinstance(required, bool):
        return {"id": claim["id"], "text": claim["text"], "status": "ERROR",
                "reason": "REQUIRED_NOT_BOOLEAN", "evidence": []}
    evidence = claim.get("evidence", [])
    base = {"id": claim["id"], "text": claim["text"], "required": required}
    bound = set()
    if evidence is None or evidence == []:
        result = dict(base, status="ABSTAIN", reason="NO_CHECKABLE_EVIDENCE_DECLARED", evidence=[])
    elif not isinstance(evidence, list):
        result = dict(base, status="ERROR", reason="evidence must be a list", evidence=[])
    else:
        checked = [_check_item(root, item, strict is not None) for item in evidence]
        items = [record for record, _ in checked]
        bound = set().union(*(numbers for _, numbers in checked))
        statuses = {i["status"] for i in items}
        if "ERROR" in statuses:
            codes = sorted({i["reason"] for i in items if i["status"] == "ERROR"})
            status, reason = "ERROR", ",".join(codes) if set(codes) <= _CODED else "MALFORMED_EVIDENCE"
        elif "FAIL" in statuses:
            status, reason = "FAIL", ",".join(sorted({i["reason"] for i in items if i["status"] == "FAIL"}))
        else:
            status, reason = "PASS", "ALL_DECLARED_EVIDENCE_PRESENT"
            if all(i.get("reason") == "PRESENCE_ONLY" for i in items):
                reason = "PRESENCE_ONLY_NO_DIGESTS_DECLARED"
        result = dict(base, status=status, reason=reason, evidence=items)
    if strict is not None:
        numbers = szl_number_tokens(claim["text"])
        unbound = [n for n in numbers if n not in bound]
        result["numeric_binding"] = {"numbers_in_text": numbers, "bound": [n for n in numbers if n in bound],
                                     "unbound": unbound}
        if strict and unbound and result["status"] == "PASS":
            result.update(status="ABSTAIN", reason="NUMBERS_NOT_BOUND")
    return result


def szl_evidence_gate(root, claims_document) -> dict:
    root = Path(root)
    if not isinstance(claims_document, dict) or not isinstance(claims_document.get("claims"), list):
        return {"schema": SCHEMA, "status": "ERROR", "reason": "document must contain a claims list",
                "claims": [], "counts": {}}
    numeric = "strict_numeric_binding" in claims_document
    strict = claims_document.get("strict_numeric_binding") if numeric else None
    schema = SCHEMA_V2 if numeric else SCHEMA
    if numeric and not isinstance(strict, bool):
        return {"schema": schema, "status": "ERROR", "reason": "strict_numeric_binding must be true or false",
                "claims": [], "counts": {}}
    claims = claims_document["claims"]
    claims_sha256 = szl_sha256_bytes(szl_canonical(claims_document).encode())
    ids = Counter(c["id"] for c in claims if isinstance(c, dict) and isinstance(c.get("id"), str))
    duplicates = sorted(i for i, n in ids.items() if n > 1)
    if duplicates:
        return {"schema": schema, "status": "ERROR", "reason": "DUPLICATE_CLAIM_ID", "duplicate_claim_ids": duplicates,
                "subject": claims_document.get("subject"), "claims_sha256": claims_sha256, "claims": [], "counts": {}}
    results = [szl_gate_claim(root, c, strict) for c in claims]
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("PASS", "FAIL", "ABSTAIN", "ERROR")}
    required_bad = [r["id"] for r in results if r.get("required", True) and r["status"] in ("FAIL", "ERROR")]
    reason = None
    if not results:
        status, reason = "ABSTAIN", "NO_CLAIMS_DECLARED"
    elif required_bad:
        status = "FAIL"
    elif counts["ABSTAIN"]:
        status = "ABSTAIN"
    elif counts["ERROR"] or counts["FAIL"]:
        status = "FAIL_OPTIONAL_ONLY"
    else:
        status = "PASS"
    report = {"schema": schema, "status": status}
    if reason:
        report["reason"] = reason
    report.update({
        "subject": claims_document.get("subject"),
        "claims_sha256": claims_sha256,
        "counts": counts,
        "required_failures": required_bad,
        "claims": results,
        "scope": "Presence, byte digests and declared text of named artifacts under the root. "
                 "A PASS does not establish that any claim is scientifically true.",
    })
    return report
