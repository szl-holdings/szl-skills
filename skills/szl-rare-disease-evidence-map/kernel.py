"""Offline synthetic evidence map. No real clinical records are accepted."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import sys

MAX_INPUT_BYTES = 256 * 1024
MAX_ANNOTATIONS = 256
MAX_RECORDS = 256
MAX_SUBMISSIONS = 16


class EvidenceMapError(ValueError):
    """A supplied synthetic exercise failed its declared contract."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceMapError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise EvidenceMapError("non-finite JSON number is not allowed")


def _parse_input(payload):
    if type(payload) is not bytes or len(payload) > MAX_INPUT_BYTES:
        raise EvidenceMapError("input must be bounded raw JSON bytes")
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except EvidenceMapError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise EvidenceMapError("input must be UTF-8 JSON") from error
    if not isinstance(parsed, dict):
        raise EvidenceMapError("input root must be an object")
    return parsed


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400)


def _identity(info, *, cross_api=False):
    clock = info.st_ctime_ns
    if cross_api and sys.platform == "win32":
        # Windows 3.12 lstat uses creation time for ctime; fstat uses ChangeTime.
        # Keep ctime stability within each API and compare birthtime across them.
        clock = getattr(info, "st_birthtime_ns", clock)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, clock)


def read_input(path):
    """Read bounded bytes from the admitted descriptor; reject observed changes."""
    target = pathlib.Path(path)
    observed = target.lstat()
    if not _regular(observed):
        raise EvidenceMapError("input must be a regular, non-reparse file")
    if observed.st_size > MAX_INPUT_BYTES:
        raise EvidenceMapError("input exceeds 256 KiB")
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    descriptor = os.open(target, flags)
    try:
        opened = os.fstat(descriptor)
        if (not _regular(opened)
                or _identity(opened, cross_api=True) != _identity(observed, cross_api=True)):
            raise EvidenceMapError("input changed before read")
        chunks = []
        remaining = observed.st_size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        finished = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = target.lstat()
    if (not _regular(after) or len(payload) != observed.st_size
            or _identity(opened) != _identity(finished)
            or _identity(observed) != _identity(after)):
        raise EvidenceMapError("input changed during read")
    _parse_input(payload)
    return payload


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise EvidenceMapError(label + " has missing or unknown fields")


def _matches(value, pattern, label):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise EvidenceMapError(label + " is not a synthetic token")
    return value


def _items(value, minimum, maximum, label):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise EvidenceMapError(label + " has invalid item count")
    return value


def _release(value, label):
    return _matches(value, r"SYNTHETIC-[A-Z0-9][A-Z0-9.-]{0,63}", label)


def _condition(value):
    return _matches(value, r"SYNTH-COND:[0-9]{4,6}", "condition")


def _versioned(value, kind):
    return _matches(value, rf"SYNTH-{kind}:[0-9]{{4,6}}\.[1-9][0-9]*", kind)


def _term(value):
    return _matches(value, r"SYNTH-HP:[0-9]{4,6}", "HPO term")


def _source_ref(value):
    return _matches(value, r"SYNTH-REF:[A-Z0-9][A-Z0-9-]{0,31}", "source_ref")


def _source_binding(manifest, kind, source, digest):
    declared = manifest["sources"][kind]
    _keys(declared, ("release", "sha256"), kind + " source binding")
    release = _release(declared["release"], kind + " release")
    declared_hash = _matches(declared["sha256"], r"[0-9a-f]{64}", kind + " sha256")
    if release != source["release"] or declared_hash != digest:
        raise EvidenceMapError(kind + " release or SHA-256 binding mismatch")
    return {"release": release, "sha256": digest, "binding": "EXACT_INPUT_BYTES"}


def map_evidence(manifest_bytes, hpo_bytes, clinvar_bytes):
    """Parse and hash raw synthetic inputs before mapping any assertion."""
    manifest = _parse_input(manifest_bytes)
    hpo = _parse_input(hpo_bytes)
    clinvar = _parse_input(clinvar_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    hpo_sha256 = hashlib.sha256(hpo_bytes).hexdigest()
    clinvar_sha256 = hashlib.sha256(clinvar_bytes).hexdigest()
    _keys(manifest, ("schema", "synthetic", "exercise_id", "sources"), "manifest")
    if manifest["schema"] != "szl.rare-disease-evidence-map.v1" or manifest["synthetic"] is not True:
        raise EvidenceMapError("manifest is not a supported synthetic exercise")
    exercise_id = _matches(manifest["exercise_id"], r"SYNTH-EXERCISE:[A-Z0-9][A-Z0-9-]{0,31}", "exercise_id")
    _keys(manifest["sources"], ("hpo", "clinvar"), "source list")

    _keys(hpo, ("schema", "synthetic", "rights", "release", "annotations"), "HPO-shaped source")
    if hpo["schema"] != "szl.synthetic-hpo.v1" or hpo["synthetic"] is not True or hpo["rights"] != "SYNTHETIC_ONLY":
        raise EvidenceMapError("HPO-shaped source must be synthetic-only")
    _release(hpo["release"], "HPO-shaped release")

    _keys(clinvar, ("schema", "synthetic", "rights", "release", "records"), "ClinVar-shaped source")
    if clinvar["schema"] != "szl.synthetic-clinvar.v1" or clinvar["synthetic"] is not True or clinvar["rights"] != "SYNTHETIC_ONLY":
        raise EvidenceMapError("ClinVar-shaped source must be synthetic-only")
    _release(clinvar["release"], "ClinVar-shaped release")

    bindings = {
        "hpo": _source_binding(manifest, "hpo", hpo, hpo_sha256),
        "clinvar": _source_binding(manifest, "clinvar", clinvar, clinvar_sha256),
    }

    annotations = {}
    seen_annotations = set()
    for entry in _items(hpo["annotations"], 1, MAX_ANNOTATIONS, "annotations"):
        _keys(entry, ("condition", "term", "qualifier", "source_ref"), "annotation")
        condition = _condition(entry["condition"])
        term = _term(entry["term"])
        qualifier = entry["qualifier"]
        if qualifier not in ("ANNOTATED", "EXPLICIT_NEGATIVE_ANNOTATION"):
            raise EvidenceMapError("annotation qualifier is unsupported")
        source_ref = _source_ref(entry["source_ref"])
        identity = (condition, term, qualifier, source_ref)
        if identity in seen_annotations:
            raise EvidenceMapError("duplicate annotation")
        seen_annotations.add(identity)
        annotations.setdefault(condition, []).append({
            "term": term, "qualifier": qualifier, "source_ref": source_ref,
        })

    records = {}
    seen_rcvs = set()
    seen_scvs = set()
    for entry in _items(clinvar["records"], 1, MAX_RECORDS, "records"):
        _keys(entry, ("condition", "vcv", "rcv", "submissions"), "ClinVar-shaped record")
        condition = _condition(entry["condition"])
        vcv = _versioned(entry["vcv"], "VCV")
        rcv = _versioned(entry["rcv"], "RCV")
        if rcv in seen_rcvs:
            raise EvidenceMapError("duplicate RCV reference")
        seen_rcvs.add(rcv)
        submissions = []
        for submitted in _items(entry["submissions"], 1, MAX_SUBMISSIONS, "submissions"):
            _keys(submitted, ("scv", "assertion_token", "source_ref"), "submission")
            scv = _versioned(submitted["scv"], "SCV")
            if scv in seen_scvs:
                raise EvidenceMapError("duplicate SCV reference")
            seen_scvs.add(scv)
            submissions.append({
                "scv": scv,
                "assertion_token": _matches(submitted["assertion_token"], r"SYNTH-ASSERT:[0-9]{4,6}", "assertion_token"),
                "source_ref": _source_ref(submitted["source_ref"]),
            })
        tokens = sorted({item["assertion_token"] for item in submissions})
        records.setdefault(condition, []).append({
            "vcv": vcv,
            "rcv": rcv,
            "submissions": sorted(submissions, key=lambda item: item["scv"]),
            "distinct_supplied_assertion_tokens": tokens,
            "review_flags": ["DIVERGENT_DECLARED_TOKENS"] if len(tokens) > 1 else [],
        })

    conditions = []
    for condition in sorted(set(annotations) | set(records)):
        linked_annotations = sorted(annotations.get(condition, []), key=lambda item: (item["term"], item["qualifier"], item["source_ref"]))
        linked_records = sorted(records.get(condition, []), key=lambda item: item["rcv"])
        flags = []
        if not linked_annotations:
            flags.append("MISSING_HPO_ANNOTATION")
        if not linked_records:
            flags.append("MISSING_CLINVAR_RECORD")
        qualifiers = {}
        for item in linked_annotations:
            qualifiers.setdefault(item["term"], set()).add(item["qualifier"])
        if any(len(values) > 1 for values in qualifiers.values()):
            flags.append("DIVERGENT_HPO_QUALIFIERS")
        if any(item["review_flags"] for item in linked_records):
            flags.append("DIVERGENT_DECLARED_TOKENS")
        conditions.append({
            "condition": condition,
            "hpo_annotations": linked_annotations,
            "clinvar_records": linked_records,
            "review_flags": flags,
        })

    return {
        "schema": "szl.rare-disease-evidence-map.report.v1",
        "exercise_id": exercise_id,
        "synthetic": True,
        "status": "SYNTHETIC_EVIDENCE_MAPPED",
        "readiness": "HOLD",
        "manifest_sha256": manifest_sha256,
        "sources": bindings,
        "conditions": conditions,
        "limits": ["NOT_AUTHENTICATED_PROVIDER_DATA", "NOT_CLINICALLY_VALIDATED", "NO_PATIENT_OR_VARIANT_INTERPRETATION"],
    }
