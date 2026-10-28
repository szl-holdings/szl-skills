"""Pure validation for an externally authenticated CPU provisioning window.

The owner receipt is an input to a trusted broker, not an owner signature. This
module never authenticates a signer, reads a provider, checks a budget ledger,
or creates a permit. It only rejects malformed or incomplete window descriptors
before they can reach the executor state machine.
"""

from datetime import datetime, timedelta, timezone
import re
import uuid

from tools.ssh_ci_contract import (
    CPU_PROFILE,
    ContractError,
    canonical,
    sha256,
    strict_json_loads,
    validate_request,
)


WINDOW_SCHEMA = "szl.ssh-cpu-build.owner-window.v1"
MAX_WINDOW_BYTES = 8192
MAX_WINDOW_SECONDS = 900
MIN_REMAINING_SECONDS = CPU_PROFILE["wall_seconds"] + 30
MAX_CAMPAIGN_SECONDS = 180_000
MAX_CAMPAIGN_USD = 2_000
WINDOW_FIELDS = frozenset({
    "schema", "window_id", "permit_id", "request_sha256", "not_before",
    "expires_at", "provider", "host", "execution", "source", "cpu_profile",
    "budget", "workload", "checkpoint_manifest_sha256", "owner_receipt_ref",
})
OWNER_ATTESTATION_FIELDS = frozenset({
    "provider_instance_uuid", "hf_revision", "budget", "checkpoint_manifest_sha256",
    "owner_receipt_ref",
})


def _object(value):
    if type(value) in (bytes, str):
        value = strict_json_loads(value, MAX_WINDOW_BYTES)
    if type(value) is not dict or len(canonical(value)) > MAX_WINDOW_BYTES:
        raise ContractError("owner window must be a bounded object")
    return strict_json_loads(canonical(value), MAX_WINDOW_BYTES)


def _fields(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError(label + " has missing or unknown fields")


def _literal(value, expected, label):
    if type(value) is not type(expected) or value != expected:
        raise ContractError(label + " does not match the required value")


def _uuid(value, label):
    if type(value) is not str:
        raise ContractError(label + " must be a canonical UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError) as error:
        raise ContractError(label + " must be a canonical UUID") from error


def _digest(value, length, label):
    if type(value) is not str or not re.fullmatch(r"[a-f0-9]{%d}" % length, value):
        raise ContractError(label + " must be a lowercase digest")


def _timestamp(value, label):
    if type(value) is not str or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise ContractError(label + " must be canonical whole-second UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as error:
        raise ContractError(label + " is not a valid UTC timestamp") from error


def _host(value):
    _fields(value, {"hostname", "boot_id"}, "window host")
    if type(value["hostname"]) is not str or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value["hostname"]):
        raise ContractError("window hostname is invalid")
    _uuid(value["boot_id"], "window boot_id")


def _nonempty(value, label, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum \
            or any(ord(char) < 0x20 for char in value):
        raise ContractError(label + " must be a bounded nonempty text value")


def validate_owner_attestations(owner):
    """Check the owner-attested values; the same checks run before dispatch and on the broker.

    Template placeholders (any text starting with FILL) and uppercase digests are
    refused, never normalized.
    """
    _fields(owner, OWNER_ATTESTATION_FIELDS, "owner attestations")
    budget = owner["budget"]
    _fields(budget, {"ledger_ref", "remaining_seconds", "remaining_usd"}, "budget")
    texts = [(field, owner[field]) for field in sorted(OWNER_ATTESTATION_FIELDS - {"budget"})]
    texts += [("budget " + field, budget[field]) for field in sorted(budget)]
    for label, text in texts:
        if type(text) is str and text.lstrip().upper().startswith("FILL"):
            raise ContractError(label + " is still a template placeholder")
    _uuid(owner["provider_instance_uuid"], "provider instance_uuid")
    _nonempty(owner["hf_revision"], "source hf_revision", 128)
    if owner["hf_revision"] in {"UNKNOWN", "UNAVAILABLE"}:
        raise ContractError("source hf_revision must be admitted")
    _nonempty(budget["ledger_ref"], "budget ledger_ref", 256)
    if type(budget["remaining_seconds"]) is not int \
            or not MIN_REMAINING_SECONDS <= budget["remaining_seconds"] <= MAX_CAMPAIGN_SECONDS:
        raise ContractError("budget remaining_seconds is outside the admitted bound")
    if type(budget["remaining_usd"]) not in (int, float) \
            or isinstance(budget["remaining_usd"], bool) \
            or not 0 < budget["remaining_usd"] <= MAX_CAMPAIGN_USD:
        raise ContractError("budget remaining_usd is outside the admitted bound")
    checkpoint = owner["checkpoint_manifest_sha256"]
    if type(checkpoint) is str and re.fullmatch(r"[A-Fa-f0-9]{64}", checkpoint) \
            and checkpoint != checkpoint.lower():
        raise ContractError("checkpoint_manifest_sha256 has uppercase hex; it is refused, "
                            "not normalized (Get-FileHash prints uppercase)")
    _digest(checkpoint, 64, "checkpoint_manifest_sha256")
    _nonempty(owner["owner_receipt_ref"], "owner_receipt_ref", 256)


def validate_owner_window(request, window, now):
    """Return a detached window if it is internally complete and request-bound.

    This is consistency checking only. A trusted loader must separately verify
    the owner identity, signature, freshness of the budget ledger, provider
    lease, and receipt reference before passing the result to the executor.
    """
    if not isinstance(now, datetime) or now.tzinfo is None \
            or now.utcoffset() != timedelta(0):
        raise ContractError("now must be an aware UTC datetime")
    request = validate_request(request)
    window = _object(window)
    _fields(window, WINDOW_FIELDS, "owner window")
    _literal(window["schema"], WINDOW_SCHEMA, "window schema")
    _uuid(window["window_id"], "window_id")
    _uuid(window["permit_id"], "permit_id")
    _literal(window["permit_id"], request["permit_id"], "window permit_id")
    _literal(window["request_sha256"], sha256(canonical(request)),
             "window request_sha256")
    start = _timestamp(window["not_before"], "not_before")
    end = _timestamp(window["expires_at"], "expires_at")
    if not timedelta(0) < end - start <= timedelta(seconds=MAX_WINDOW_SECONDS):
        raise ContractError("owner window must be positive and at most 900 seconds")
    if now < start:
        raise ContractError("owner window is not active yet")
    if end - now < timedelta(seconds=MIN_REMAINING_SECONDS):
        raise ContractError("owner window has insufficient time remaining")

    provider = window["provider"]
    _fields(provider, {"name", "instance_uuid"}, "provider")
    _literal(provider["name"], "Massed Compute", "provider name")
    _host(window["host"])

    execution = window["execution"]
    _fields(execution, {"execution_id", "run_nonce"}, "execution")
    _uuid(execution["execution_id"], "execution_id")
    _uuid(execution["run_nonce"], "run_nonce")
    if execution["execution_id"] == execution["run_nonce"]:
        raise ContractError("execution_id and run_nonce must be distinct")

    source = window["source"]
    _fields(source, {"github_sha", "hf_revision"}, "source")
    _digest(source["github_sha"], 40, "source github_sha")
    _literal(source["github_sha"], request["source_sha"], "source github_sha")

    profile = window["cpu_profile"]
    _fields(profile, CPU_PROFILE, "cpu_profile")
    for field, expected in CPU_PROFILE.items():
        _literal(profile[field], expected, "cpu_profile " + field)

    workload = window["workload"]
    _fields(workload, {"mode", "gpu_devices", "model_interaction", "training", "inference"},
            "workload")
    _literal(workload["mode"], "cpu_build", "workload mode")
    _literal(workload["gpu_devices"], False, "workload gpu_devices")
    _literal(workload["model_interaction"], "none", "workload model_interaction")
    _literal(workload["training"], False, "workload training")
    _literal(workload["inference"], False, "workload inference")
    validate_owner_attestations({
        "provider_instance_uuid": provider["instance_uuid"], "hf_revision": source["hf_revision"],
        "budget": window["budget"],
        "checkpoint_manifest_sha256": window["checkpoint_manifest_sha256"],
        "owner_receipt_ref": window["owner_receipt_ref"]})
    return window

