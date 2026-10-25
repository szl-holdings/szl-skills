#!/usr/bin/env python3
"""Pure, fail-closed consistency checks for an owner CPU-build permit.

The trusted broker must obtain permits and observations from its own protected
owner policy and inventory paths, not from the build payload. This module does
not authenticate the owner, clock, isolation, process inventory, or a signature.
It performs no I/O and neither claims a permit nor authorizes execution. A
successful return is only internal consistency at the supplied time; the broker
must independently enforce atomic single use and recheck runtime conditions.
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

PERMIT_SCHEMA = "szl.ssh-cpu-build.permit.v1"
OBSERVATION_SCHEMA = "szl.ssh-cpu-build.observation.v1"
MAX_PERMIT_BYTES = 4096
MAX_OBSERVATION_BYTES = 4096
MAX_WINDOW_SECONDS = 900
MAX_OBSERVATION_AGE_SECONDS = 5
CLAIM_MARGIN_SECONDS = 30
PERMIT_FIELDS = frozenset({
    "schema", "permit_id", "request_sha256", "not_before", "expires_at",
    "max_runs", "cpu_profile", "host", "workload",
})
OBSERVATION_FIELDS = frozenset({
    "schema", "observed_at", "host", "gpu_compute_processes",
    "competing_cpu_jobs",
})


def _object(value, limit, label):
    if type(value) in (bytes, str):
        value = strict_json_loads(value, limit)
    if type(value) is not dict:
        raise ContractError(label + " must be an object")
    encoded = canonical(value)
    if len(encoded) > limit:
        raise ContractError(label + " exceeds byte limit")
    # Detach nested containers, and apply the same strict representation rules
    # to wire and in-memory inputs. Duplicate wire members must not be decoded
    # by an ordinary JSON loader before calling this validator.
    return strict_json_loads(encoded, limit)


def _fields(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError(label + " has missing or unknown fields")


def _literal(value, expected, label):
    if type(value) is not type(expected) or value != expected:
        raise ContractError(label + " does not match the required value")


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractError(label + " is not an in-range integer")


def _uuid(value, label):
    if type(value) is not str:
        raise ContractError(label + " must be a canonical UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError) as error:
        raise ContractError(label + " must be a canonical UUID") from error


def _timestamp(value, label):
    if type(value) is not str or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise ContractError(label + " must be canonical whole-second UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as error:
        raise ContractError(label + " is not a valid UTC timestamp") from error


def _host(value, label):
    _fields(value, {"hostname", "boot_id"}, label)
    hostname = value["hostname"]
    if type(hostname) is not str or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
        raise ContractError(label + " has invalid hostname")
    _uuid(value["boot_id"], label + " boot_id")


def _process(value, label):
    _fields(value, {"pid", "start_ticks"}, label)
    _integer(value["pid"], 1, (1 << 31) - 1, label + " pid")
    _integer(value["start_ticks"], 1, (1 << 63) - 1, label + " start_ticks")


def validate_permit(request, permit, observation, now):
    """Return a deep-detached permit if all supplied declarations are consistent.

    Accept raw JSON bytes/text or dictionaries for the three protocol objects.
    ``now`` must be an aware UTC datetime from the broker's trusted clock. Raw
    JSON goes through duplicate-member rejection. A successful return does not
    establish authorization, owner authenticity, an enforced resource boundary,
    or prior non-consumption. The broker must enforce those separately.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ContractError("now must be a timezone-aware UTC datetime")
    try:
        if now.utcoffset() != timedelta(0):
            raise ContractError("now must be a timezone-aware UTC datetime")
    except (ValueError, OverflowError) as error:
        raise ContractError("now must be a timezone-aware UTC datetime") from error
    request = validate_request(request)
    if request["source_sha"] != request["controller_sha"]:
        raise ContractError("request source and controller revisions differ")
    permit = _object(permit, MAX_PERMIT_BYTES, "permit")
    observation = _object(observation, MAX_OBSERVATION_BYTES, "observation")
    _fields(permit, PERMIT_FIELDS, "permit")
    _fields(observation, OBSERVATION_FIELDS, "observation")
    _literal(permit["schema"], PERMIT_SCHEMA, "permit schema")
    _literal(observation["schema"], OBSERVATION_SCHEMA, "observation schema")
    _literal(permit["permit_id"], request["permit_id"], "permit_id")
    _literal(permit["request_sha256"], sha256(canonical(request)), "request_sha256")
    _literal(permit["max_runs"], 1, "max_runs")
    profile = permit["cpu_profile"]
    _fields(profile, CPU_PROFILE, "cpu_profile")
    for field, expected in CPU_PROFILE.items():
        _literal(profile[field], expected, "cpu_profile " + field)

    start = _timestamp(permit["not_before"], "not_before")
    end = _timestamp(permit["expires_at"], "expires_at")
    if not timedelta(0) < end - start <= timedelta(seconds=MAX_WINDOW_SECONDS):
        raise ContractError("permit window must be positive and at most 900 seconds")
    if now < start:
        raise ContractError("permit is not active yet")
    if end - now < timedelta(seconds=CPU_PROFILE["wall_seconds"] + CLAIM_MARGIN_SECONDS):
        raise ContractError("permit has insufficient time remaining before claim")
    observed_at = _timestamp(observation["observed_at"], "observed_at")
    age = now - observed_at
    if not timedelta(0) <= age <= timedelta(seconds=MAX_OBSERVATION_AGE_SECONDS):
        raise ContractError("observation is future-dated or stale")

    _host(permit["host"], "permit host")
    _host(observation["host"], "observation host")
    if permit["host"] != observation["host"]:
        raise ContractError("host identity changed")
    _literal(observation["competing_cpu_jobs"], 0, "competing_cpu_jobs")
    processes = observation["gpu_compute_processes"]
    if type(processes) is not list or len(processes) > 1:
        raise ContractError("observation must contain at most one GPU compute process")
    for process in processes:
        _process(process, "observed GPU process")
    workload = permit["workload"]
    if type(workload) is not dict:
        raise ContractError("workload must be an object")
    if workload.get("mode") == "idle":
        _fields(workload, {"mode"}, "idle workload")
        if processes:
            raise ContractError("idle permit requires no GPU compute processes")
    elif workload.get("mode") == "pinned":
        _fields(workload, {"mode", "pid", "start_ticks"}, "pinned workload")
        expected = {"pid": workload["pid"], "start_ticks": workload["start_ticks"]}
        _process(expected, "pinned GPU process")
        if processes != [expected]:
            raise ContractError("pinned GPU process identity changed")
    else:
        raise ContractError("workload mode must be idle or pinned")
    return permit
