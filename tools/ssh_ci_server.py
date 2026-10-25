#!/usr/bin/env python3
"""Draft forced-command decision sequence for the CPU broker.

This module accepts one strict request, validates an owner permit, and can
consume that permit through an already entered ClaimStore. It does not install
packages, open a socket, load a model, sandbox a process, or execute a build.
EXECUTION_AUTHORIZED is a fixed false constant. A caller-supplied command is
not read, because the request schema is closed.

A transport loss after a durable claim is UNKNOWN. That is not a failed exit
and it does not release the permit for retry. Host isolation remains NOT_RUN
until a qualified POSIX ClaimStore and a reviewed command binding exist.
"""

import os
from pathlib import Path
import stat
import sys

from tools import ssh_ci_admission as admission
from tools import ssh_ci_claims as claims
from tools import ssh_ci_contract as contract


FORCED_COMMAND = "szl-cpu-build-v1"
EXECUTION_AUTHORIZED = False
REVIEWED_ARGV = None
DECISION_SCHEMA = "szl.ssh-cpu-build.broker-decision.v1"
INSTRUCTION_SCHEMA = "szl.ssh-cpu-build.deployment-instructions.v1"
STAGE_SCHEMA = "szl.ssh-cpu-build.stage.v1"
MAX_STAGE_FILES = 32
NETWORK_INSTALL_TOKENS = frozenset({
    "pip", "pip3", "curl", "wget", "apt", "apt-get", "npm", "uv",
})


class BrokerError(ValueError):
    """The draft broker refused the operation. Refusal is not execution."""


def _decision(reason):
    """Closed receipt. result stays BLOCKED for every path in this draft."""
    return {
        "schema": DECISION_SCHEMA,
        "evidence_class": "DECLARED",
        "result": "BLOCKED",
        "reason": reason,
        "execution_attempted": False,
        "execution_outcome": "NOT_RUN",
        "isolation_verified": False,
        "host_claim_qualified": False,
        "installation_performed": False,
        "automatic_install": False,
        "retry_allowed": False,
        "claim_preserved": False,
        "signed": False,
        "independent_witness": False,
        "gpu_access": False,
        "network_access": False,
        "cgroup_enforced": False,
        "scratch_limit": "PROPOSE_AND_REVIEW",
        "log_limit": "PROPOSE_AND_REVIEW",
    }


def _qualified_claim_store(store):
    """True only for a ClaimStore context this process has already entered."""
    return (type(store) is claims.ClaimStore
            and getattr(store, "_lock_fd", None) is not None
            and getattr(store, "_pid", None) == os.getpid())


def parse_request(request):
    """Return a validated request, or raise BrokerError before any claim."""
    if type(request) is str:
        request = request.encode("utf-8")
    if type(request) is not bytes:
        if type(request) is not dict:
            raise BrokerError("malformed_request")
        encoded = contract.canonical(request)
        if len(encoded) > contract.MAX_REQUEST_BYTES:
            raise BrokerError("malformed_request")
        try:
            return contract.validate_request(request)
        except contract.ContractError as error:
            raise BrokerError("malformed_request") from error
    if len(request) > contract.MAX_REQUEST_BYTES:
        raise BrokerError("malformed_request")
    try:
        return contract.validate_request(request)
    except contract.ContractError as error:
        raise BrokerError("malformed_request") from error


def accept_forced_command(argv):
    """Admit only the literal forced command. Extra arguments are a mismatch."""
    if tuple(argv) != (FORCED_COMMAND,):
        raise BrokerError("forced_command_mismatch")


def reject_unreviewed_command(argv):
    """Refuse every argv. This draft has no digest-pinned command binding."""
    if argv is None:
        raise BrokerError("reviewed command is not bound")
    if type(argv) is not tuple or not argv or any(type(part) is not str for part in argv):
        raise BrokerError("command must be a tuple of strings")
    for part in argv:
        if (part in NETWORK_INSTALL_TOKENS or "/" in part or "\\" in part
                or part.startswith("-")):
            raise BrokerError("network install or path command is not admitted")
    raise BrokerError("no reviewed command digest is admitted by this draft")


def classify_outcome(claimed, disconnected, returncode):
    """Separate a lost transport from a completed non-zero exit.

    Neither outcome is PASS. A claim that already happened stays consumed.
    """
    if claimed is not True:
        raise BrokerError("no outcome without a preserved claim")
    if type(disconnected) is not bool:
        raise BrokerError("disconnected must be a boolean")
    if disconnected or returncode is None:
        outcome = "UNKNOWN"
    elif type(returncode) is not int:
        raise BrokerError("returncode must be an int or absent")
    elif returncode == 0:
        outcome = "EXIT_ZERO"
    else:
        outcome = "FAILED"
    return {
        "result": "BLOCKED",
        "execution_outcome": outcome,
        "retry_allowed": False,
        "claim_preserved": True,
        "isolation_verified": False,
        "pass_declared": False,
    }


def decide(request, permit, observation, now, claim_store):
    """Validate, optionally consume one permit, and still refuse execution.

    ``claim_store`` must already be an entered ClaimStore for a qualified host
    claim. Any other object can demonstrate single-use call order in a unit
    test, and the receipt marks that claim unqualified. This function never
    deletes a claim and never starts a process.
    """
    if EXECUTION_AUTHORIZED or REVIEWED_ARGV is not None:
        return _decision("execution_constant_must_stay_false")
    try:
        parsed = parse_request(request)
    except BrokerError:
        return _decision("malformed_request")
    try:
        admission.validate_permit(parsed, permit, observation, now)
    except contract.ContractError:
        return _decision("permit_denied")
    if claim_store is None:
        return _decision("claim_store_required")
    digest = contract.sha256(contract.canonical(parsed))
    try:
        claim_store.claim(parsed["request_id"], parsed["permit_id"], digest)
    except claims.ClaimError as error:
        text = str(error)
        reason = "replay_denied" if text.startswith("permit already claimed") else "claim_denied"
        decision = _decision(reason)
        decision["claim_preserved"] = reason == "replay_denied"
        return decision
    decision = _decision("execution_not_qualified")
    decision["claim_preserved"] = True
    decision["host_claim_qualified"] = _qualified_claim_store(claim_store)
    return decision


def _artifact_name(name):
    if type(name) is not str or "\x00" in name or "/" in name or "\\" in name:
        raise BrokerError("artifact name is not a single segment")
    if name != Path(name).name or name in {"", ".", ".."}:
        raise BrokerError("artifact name is not a single segment")


def write_regular_artifact(directory, name, payload, remaining):
    """Create one new regular file. Existing names and links are refused.

    The byte check happens before a write. A refusal leaves an existing target
    unchanged. This does not extract archives or follow a symlink.
    """
    _artifact_name(name)
    if type(payload) is not bytes or type(remaining) is not int or remaining < 0:
        raise BrokerError("artifact arguments have the wrong type")
    if len(payload) > contract.MAX_OUTPUT_BYTES or len(payload) > remaining:
        raise BrokerError("artifact exceeds the byte budget")
    root = Path(directory)
    if not root.is_absolute():
        raise BrokerError("artifact directory must be absolute")
    destination = root / name
    if destination.is_symlink() or destination.exists():
        raise BrokerError("artifact path exists or is a link")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(destination, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BrokerError("artifact is not a new regular file")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise BrokerError("artifact write was incomplete")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return len(payload)


def _contained_source(root, name):
    """Return one regular file that stays inside ``root``.

    The check is component-wise and does not resolve links. A link, a parent
    segment, or a network-install basename is refused before any copy.
    """
    if type(name) is not str or not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise BrokerError("stage path is not contained")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BrokerError("stage path is not contained")
    if any(part in NETWORK_INSTALL_TOKENS for part in parts):
        raise BrokerError("network install is not staged")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise BrokerError("stage path is a link")
    if not current.is_file():
        raise BrokerError("stage path is not a regular file")
    info = current.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size < 0:
        raise BrokerError("stage path is not a regular file")
    return current, info.st_size


def _read_regular(path, size):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size != size):
            raise BrokerError("stage source is not a stable regular file")
        payload = os.read(descriptor, size + 1)
        if len(payload) != size:
            raise BrokerError("stage source changed")
        return payload
    finally:
        os.close(descriptor)


def _mark_read_only(path):
    """Return whether a following write open is refused by the host."""
    os.chmod(path, stat.S_IREAD)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError:
        return True
    os.close(descriptor)
    return False


def _fsync_directory(path):
    """Best-effort directory durability. A host that cannot fsync reports false."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def stage_immutable(source_root, destination, names, remaining):
    """Copy an explicit manifest into a new directory and do not execute it.

    Validation finishes before the destination is created. A later copy error
    leaves the partial directory in place. This function never deletes it and
    never starts a process or installer.
    """
    if type(names) is not tuple or not names or len(names) > MAX_STAGE_FILES:
        raise BrokerError("stage manifest is closed")
    if len(set(names)) != len(names):
        raise BrokerError("stage manifest repeats a path")
    if type(remaining) is not int or remaining < 0:
        raise BrokerError("stage budget has the wrong type")
    root = Path(source_root)
    dest = Path(destination)
    if not root.is_absolute() or not dest.is_absolute():
        raise BrokerError("stage directories must be absolute")
    if root.is_symlink() or dest.exists() or dest.is_symlink():
        raise BrokerError("stage destination exists or is a link")
    selected = []
    total = 0
    for name in names:
        path, size = _contained_source(root, name)
        total += size
        if total > remaining or total > contract.MAX_OUTPUT_BYTES:
            raise BrokerError("stage exceeds the byte budget")
        selected.append((name, path, size))
    os.mkdir(dest)
    files = {}
    write_blocked = True
    for name, path, size in selected:
        payload = _read_regular(path, size)
        parent = dest.joinpath(*name.split("/")[:-1])
        parent.mkdir(parents=True, exist_ok=True)
        write_regular_artifact(parent, name.split("/")[-1], payload, remaining)
        written = parent / name.split("/")[-1]
        write_blocked = write_blocked and _mark_read_only(written)
        files[name] = {"sha256": contract.sha256(payload), "bytes": len(payload)}
    return {
        "schema": STAGE_SCHEMA,
        "executed": False,
        "network_install": False,
        "immutable": write_blocked,
        "directory_fsync": _fsync_directory(dest),
        "files": files,
        "total_bytes": total,
    }


def deployment_instructions():
    """Owner text only. Nothing in this structure is executed."""
    return {
        "schema": INSTRUCTION_SCHEMA,
        "automatic_install": False,
        "executed": False,
        "deletes_claims": False,
        "stops_model_workload": False,
        "enables_workflow": False,
        "cpu_profile": dict(contract.CPU_PROFILE),
        "scratch_limit": "PROPOSE_AND_REVIEW",
        "log_limit": "PROPOSE_AND_REVIEW",
        "steps": [
            "Provision a dedicated non-admin account whose forced command is szl-cpu-build-v1.",
            "Refuse sudo, Docker, a shell, a PTY, forwarding, SCP, and SFTP.",
            "Bind one exact CPU window and a fresh permit to the source, run, and boot.",
            "Qualify namespace, cgroup, device, and network denial before any build.",
            "Leave scratch and log ceilings as PROPOSE_AND_REVIEW until a reviewed limit exists.",
        ],
        "rollback": [
            "Leave every claim file in place.",
            "Keep SZL_CPU_CI_ENABLED unset and do not approve the protected environment.",
            "Do not stop a model workload or another cgroup.",
            "Remove only the owner-provisioned forced-command key after the window.",
        ],
    }


def entry(argv, stdin_bytes):
    """Classify one stdin payload and refuse to claim or execute.

    The qualified claim path is ``decide`` with an entered ClaimStore. This
    entrypoint is the uninstalled forced command: it must not consume a permit
    on a host that has not been qualified.
    """
    try:
        accept_forced_command(argv)
    except BrokerError:
        return 2, _decision("forced_command_mismatch")
    if type(stdin_bytes) is not bytes:
        return 2, _decision("malformed_request")
    try:
        parse_request(stdin_bytes)
    except BrokerError:
        return 2, _decision("malformed_request")
    decision = _decision("broker_not_installed")
    decision["request_syntax"] = "valid"
    return 2, decision


def main(argv=None, stdin=None, stdout=None):
    """Read at most 4097 stdin bytes, emit one BLOCKED decision, and exit 2."""
    if argv is None:
        argv = sys.argv[1:]
    if stdin is None:
        stdin = sys.stdin.buffer
    if stdout is None:
        stdout = sys.stdout.buffer
    payload = stdin.read(contract.MAX_REQUEST_BYTES + 1)
    code, decision = entry(tuple(argv), payload)
    stdout.write(contract.canonical(decision) + b"\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
