#!/usr/bin/env python3
"""Pure validation for bounded CPU-build requests and DECLARED receipts.

No network, process, filesystem, signature, authorization, or isolation check is
performed here. A matching receipt establishes internal consistency only. The
caller must still enforce owner admission, single use, isolation, and a PASS
result. Archive bytes are returned unchanged and must never be auto-extracted.
"""

import base64
import binascii
import hashlib
import json
import math
import re
import uuid

REQUEST_SCHEMA = "szl.ssh-cpu-build.request.v1"
RESPONSE_SCHEMA = "szl.ssh-cpu-build.response.v1"
RECEIPT_SCHEMA = "szl.ssh-cpu-build.receipt.v1"
# v2 pins the whole observed GPU process set (possibly empty) instead of one PID.
RECEIPT_SCHEMA_V2 = "szl.ssh-cpu-build.receipt.v2"
MAX_PINNED_PROCESSES = 8
REPOSITORY = "szl-holdings/szl-skills"
WORKFLOW_REF = (
    REPOSITORY + "/.github/workflows/isolated-ssh-build.yml@refs/heads/main"
)
MAX_REQUEST_BYTES = 4096
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
MAX_JSON_DEPTH = 32
CPU_PROFILE = {
    "cpu_quota_percent": 100,
    "memory_max_bytes": 1_073_741_824,
    "memory_swap_max_bytes": 0,
    "tasks_max": 64,
    "wall_seconds": 420,
    "output_max_bytes": MAX_OUTPUT_BYTES,
    "network": "none",
    "gpu_devices": False,
}
STEP_NAMES = ("selfcheck", "sync", "unittest", "package")
REQUIRED_FILES = frozenset(
    name + suffix for name in STEP_NAMES for suffix in (".stdout", ".stderr")
) | {"packages.tar", "science-package-manifest.json"}
REQUEST_FIELDS = frozenset({
    "schema", "operation", "request_id", "permit_id", "repository",
    "source_sha", "controller_sha", "workflow_ref", "run_id", "run_attempt",
})
BINDING_FIELDS = REQUEST_FIELDS - {"schema", "operation"}
RECEIPT_FIELDS = BINDING_FIELDS | {
    "schema", "request_sha256", "result", "evidence_class", "signed",
    "independent_witness", "gpu_requests", "actual_cpu_profile",
    "isolation_verified", "runtime_before", "runtime_after", "steps", "files",
}


class ContractError(ValueError):
    """The supplied data does not satisfy the closed protocol."""


def _json_value(value, depth=0, active=None):
    if depth > MAX_JSON_DEPTH:
        raise ContractError("JSON nesting exceeds limit")
    kind = type(value)
    if value is None or kind in (str, int, bool):
        return
    if kind is float:
        if not math.isfinite(value):
            raise ContractError("non-finite JSON number")
        return
    if kind not in (dict, list):
        raise ContractError("value is not a JSON type")
    if len(value) > 1024:
        raise ContractError("JSON collection exceeds limit")
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ContractError("cyclic JSON value")
    active.add(identity)
    try:
        if kind is dict:
            if any(type(key) is not str for key in value):
                raise ContractError("JSON object keys must be strings")
            children = value.values()
        else:
            children = value
        for child in children:
            _json_value(child, depth + 1, active)
    finally:
        active.remove(identity)


def canonical(value):
    """Sorted-key compact UTF-8 JSON bytes; rejects non-JSON/non-finite data."""
    _json_value(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ContractError("cannot encode canonical JSON") from error


def sha256(data):
    if type(data) is not bytes:
        raise ContractError("SHA-256 input must be bytes")
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON object member")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ContractError("non-finite JSON number")


def strict_json_loads(data, max_bytes=MAX_RESPONSE_BYTES):
    """Parse bounded UTF-8 JSON without duplicate members or non-finite numbers.

    Call this on wire bytes rather than using json.loads before validation;
    duplicate members cannot be recovered from an already-decoded dictionary.
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ContractError("invalid JSON byte limit")
    if type(data) not in (bytes, str):
        raise ContractError("JSON input must be bytes or text")
    try:
        if len(data) > max_bytes:
            raise ContractError("JSON input exceeds byte limit")
        encoded = data.encode("utf-8") if type(data) is str else data
        if len(encoded) > max_bytes:
            raise ContractError("JSON input exceeds byte limit")
        value = json.loads(encoded.decode("utf-8"),
                           object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        _json_value(value)
        # Also refuses escaped lone surrogates that Python's parser permits.
        canonical(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        if isinstance(error, ContractError):
            raise
        raise ContractError("invalid JSON input") from error


def _object(value, limit, label):
    if type(value) in (bytes, str):
        value = strict_json_loads(value, limit)
    if type(value) is not dict:
        raise ContractError(label + " must be an object")
    if len(canonical(value)) > limit:
        raise ContractError(label + " exceeds byte limit")
    return value


def _fields(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError(label + " has missing or unknown fields")


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractError(label + " is not an in-range integer")


def _literal(value, expected, label):
    if type(value) is not type(expected) or value != expected:
        raise ContractError(label + " does not match the required value")


def _digest(value, length, label):
    if type(value) is not str or not re.fullmatch(r"[a-f0-9]{%d}" % length, value):
        raise ContractError(label + " must be a lowercase full digest")


def _uuid(value, label):
    if type(value) is not str:
        raise ContractError(label + " must be a canonical UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError) as error:
        raise ContractError(label + " must be a canonical UUID") from error


def validate_request(request):
    """Validate a request object or raw JSON; return a detached, flat dictionary.

    This checks syntax and fixed operation scope, not permit authenticity,
    deadline, availability, prior consumption, or authorization to execute.
    """
    request = _object(request, MAX_REQUEST_BYTES, "request")
    _fields(request, REQUEST_FIELDS, "request")
    for field, expected in (("schema", REQUEST_SCHEMA), ("operation", "build"),
                            ("repository", REPOSITORY), ("workflow_ref", WORKFLOW_REF)):
        _literal(request[field], expected, field)
    for field in ("request_id", "permit_id"):
        _uuid(request[field], field)
    for field in ("source_sha", "controller_sha"):
        _digest(request[field], 40, field)
    _integer(request["run_id"], 1, (1 << 63) - 1, "run_id")
    _literal(request["run_attempt"], 1, "run_attempt")
    return dict(request)


def _hostname(hostname, label):
    if type(hostname) is not str or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
        raise ContractError(label + " has invalid hostname")


def _runtime(identity, label):
    _fields(identity, {"hostname", "pid", "start_ticks", "boot_id"}, label)
    _hostname(identity["hostname"], label)
    _integer(identity["pid"], 1, (1 << 31) - 1, label + " pid")
    _integer(identity["start_ticks"], 1, (1 << 63) - 1, label + " start_ticks")
    _uuid(identity["boot_id"], label + " boot_id")


def validate_pinned_processes(processes, label):
    """A pinned GPU process set: at most 8 {pid, start_ticks}, unique, sorted by pid."""
    if type(processes) is not list or len(processes) > MAX_PINNED_PROCESSES:
        raise ContractError(label + " must list at most %d GPU processes" % MAX_PINNED_PROCESSES)
    for process in processes:
        _fields(process, {"pid", "start_ticks"}, label + " process")
        _integer(process["pid"], 1, (1 << 31) - 1, label + " pid")
        _integer(process["start_ticks"], 1, (1 << 63) - 1, label + " start_ticks")
    pids = [process["pid"] for process in processes]
    if pids != sorted(set(pids)):
        raise ContractError(label + " processes must be unique and sorted by pid")


def _runtime_v2(identity, label):
    _fields(identity, {"hostname", "boot_id", "gpu_processes"}, label)
    _hostname(identity["hostname"], label)
    _uuid(identity["boot_id"], label + " boot_id")
    validate_pinned_processes(identity["gpu_processes"], label)


def verify_response(request, envelope):
    """Return ten verified opaque file byte strings, or raise ContractError.

    PASS and BLOCKED are producer-declared results. A valid BLOCKED response is
    retained, not promoted; callers must check result before reporting success.
    No signature, external witness, remote isolation, archive contents, or real
    execution is authenticated by successful consistency verification.
    """
    request = validate_request(request)
    request_hash = sha256(canonical(request))
    envelope = _object(envelope, MAX_RESPONSE_BYTES, "response")
    _fields(envelope, {"schema", "request_sha256", "receipt", "files"}, "response")
    _literal(envelope["schema"], RESPONSE_SCHEMA, "response schema")
    _literal(envelope["request_sha256"], request_hash, "response request_sha256")
    receipt = envelope["receipt"]
    _fields(receipt, RECEIPT_FIELDS, "receipt")
    runtime = {RECEIPT_SCHEMA: _runtime, RECEIPT_SCHEMA_V2: _runtime_v2}.get(
        receipt["schema"] if type(receipt["schema"]) is str else None)
    if runtime is None:
        raise ContractError("receipt schema does not match the required value")
    _literal(receipt["request_sha256"], request_hash, "receipt request_sha256")
    for field in BINDING_FIELDS:
        _literal(receipt[field], request[field], "receipt " + field)
    for field, expected in (("evidence_class", "DECLARED"), ("signed", False),
                            ("independent_witness", False), ("gpu_requests", 0)):
        _literal(receipt[field], expected, "receipt " + field)
    if type(receipt["result"]) is not str or receipt["result"] not in {"PASS", "BLOCKED"}:
        raise ContractError("receipt result must be PASS or BLOCKED")
    if type(receipt["isolation_verified"]) is not bool:
        raise ContractError("isolation_verified must be a boolean declaration")
    if receipt["result"] == "PASS" and receipt["isolation_verified"] is not True:
        raise ContractError("PASS requires the isolation declaration")
    profile = receipt["actual_cpu_profile"]
    _fields(profile, CPU_PROFILE, "actual_cpu_profile")
    for field, expected in CPU_PROFILE.items():
        _literal(profile[field], expected, "actual_cpu_profile " + field)
    runtime(receipt["runtime_before"], "runtime_before")
    runtime(receipt["runtime_after"], "runtime_after")
    if receipt["runtime_before"] != receipt["runtime_after"]:
        raise ContractError("runtime identity changed")
    steps = receipt["steps"]
    if type(steps) is not list or len(steps) != len(STEP_NAMES):
        raise ContractError("receipt requires all four ordered steps")
    for step, name in zip(steps, STEP_NAMES):
        _fields(step, {"name", "returncode"}, "step")
        _literal(step["name"], name, "step name")
        _integer(step["returncode"], -(1 << 31), (1 << 31) - 1, "step returncode")
        if receipt["result"] == "PASS" and step["returncode"] != 0:
            raise ContractError("PASS requires zero returncodes")
    _fields(receipt["files"], REQUIRED_FILES, "receipt files")
    _fields(envelope["files"], REQUIRED_FILES, "response files")
    total = 0
    for name in sorted(REQUIRED_FILES):
        metadata = receipt["files"][name]
        _fields(metadata, {"sha256", "bytes"}, "file metadata")
        _digest(metadata["sha256"], 64, "file sha256")
        _integer(metadata["bytes"], 0, MAX_OUTPUT_BYTES, "file bytes")
        total += metadata["bytes"]
        if total > MAX_OUTPUT_BYTES:
            raise ContractError("aggregate decoded output exceeds limit")
    decoded = {}
    for name in sorted(REQUIRED_FILES):
        encoded = envelope["files"][name]
        metadata = receipt["files"][name]
        # Check the declared length before allocation, then exact decoded bytes.
        expected_encoded_length = 4 * ((metadata["bytes"] + 2) // 3)
        if type(encoded) is not str or len(encoded) != expected_encoded_length:
            raise ContractError("file base64 length mismatch")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ContractError("invalid file base64") from error
        if base64.b64encode(data).decode("ascii") != encoded:
            raise ContractError("file base64 must be canonical")
        if len(data) != metadata["bytes"] or sha256(data) != metadata["sha256"]:
            raise ContractError("file hash or size mismatch")
        decoded[name] = data
    return decoded
