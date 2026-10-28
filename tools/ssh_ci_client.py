"""Fail-closed GitHub controller for an owner-provisioned CPU-only SSH broker.

This client does not provision the broker, create permits, upload executable code,
or establish isolation. A verified response is a bound remote declaration, not an
independent attestation. Importing this module performs no I/O or network calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import uuid

try:
    from .ssh_ci_contract import (MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, canonical,
                                  sha256, strict_json_loads, validate_request,
                                  verify_response)
except ImportError:  # Direct script invocation from the trusted controller checkout.
    from ssh_ci_contract import (MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, canonical,
                                 sha256, strict_json_loads, validate_request,
                                 verify_response)


REPOSITORY = "szl-holdings/szl-skills"
WORKFLOW_REF = REPOSITORY + "/.github/workflows/isolated-ssh-build.yml@refs/heads/main"
REQUEST_NAMESPACE = uuid.UUID("0f50e2ba-c13c-5af1-8e77-e07149d9d0b4")
SSH_BINARY = "/usr/bin/ssh"
FORCED_COMMAND = "szl-cpu-build-v1"
# Staging (30 s) + readback + the 420 s unit + observations + a 24 MiB transfer.
TIMEOUT_SECONDS = 600
MAX_STDERR_BYTES = 1024 * 1024


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def build_request(environment, permit_id):
    """Bind the only admitted event to one deterministic single-use request."""
    if str(uuid.UUID(permit_id)) != permit_id:
        raise ValueError("permit_id must be a canonical lowercase UUID")
    required = {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_WORKFLOW_REF": WORKFLOW_REF}
    for key, expected in required.items():
        if environment.get(key) != expected:
            raise ValueError("Unapproved GitHub context: " + key)
    run_text = environment.get("GITHUB_RUN_ID", "")
    if re.fullmatch(r"[1-9][0-9]{0,19}", run_text) is None:
        raise ValueError("GITHUB_RUN_ID must be a canonical positive decimal integer")
    source_sha = environment.get("GITHUB_SHA", "")
    if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise ValueError("GITHUB_SHA must be an exact lowercase 40-character commit")
    request = {"schema": "szl.ssh-cpu-build.request.v1", "operation": "build",
               "request_id": str(uuid.uuid5(REQUEST_NAMESPACE, run_text + ":1:" + permit_id)),
               "permit_id": permit_id, "repository": REPOSITORY,
               "source_sha": source_sha, "controller_sha": source_sha,
               "workflow_ref": WORKFLOW_REF, "run_id": int(run_text), "run_attempt": 1}
    return validate_request(request)


def _regular_file(value, label, private=False):
    if not value:
        raise ValueError(label + " is required; no workstation credential fallback")
    path = Path(value)
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(label + " must be an absolute non-symlink file")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(label + " must be a regular file")
    if private and os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError(label + " must not be group/world accessible")
    return str(path)


def ssh_arguments(environment):
    """Return argv, never a shell command; secrets are referenced, never read."""
    host = environment.get("SZL_CPU_SSH_HOST", "")
    try:
        ipaddress.ip_address(host)
        if "%" in host:
            raise ValueError("IPv6 scope identifiers are not admitted")
    except ValueError:
        if len(host) > 253 or re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*", host) is None:
            raise ValueError("SZL_CPU_SSH_HOST is not a plain IP address or DNS name")
    user = environment.get("SZL_CPU_SSH_USER", "")
    if (re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) is None
            or user.lower() in {"ubuntu", "root"}):
        raise ValueError("A dedicated non-administrative SSH username is required")
    port = environment.get("SZL_CPU_SSH_PORT", "")
    if re.fullmatch(r"[1-9][0-9]{0,4}", port) is None or int(port) > 65535:
        raise ValueError("SZL_CPU_SSH_PORT must be an explicit valid port")
    key = _regular_file(environment.get("SZL_CPU_SSH_KEY_FILE"), "SSH key", private=True)
    known_hosts = _regular_file(environment.get("SZL_CPU_KNOWN_HOSTS_FILE"), "Known-hosts file")
    options = ["StrictHostKeyChecking=yes", "BatchMode=yes", "IdentitiesOnly=yes",
               "IdentityAgent=none", "ClearAllForwardings=yes", "RequestTTY=no",
               "PermitLocalCommand=no", "ProxyCommand=none", "ProxyJump=none",
               "GlobalKnownHostsFile=/dev/null", "UserKnownHostsFile=" + known_hosts,
               "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
               "PreferredAuthentications=publickey", "PubkeyAuthentication=yes",
               "NumberOfPasswordPrompts=0", "ConnectionAttempts=1", "ConnectTimeout=15",
               "ServerAliveInterval=15", "ServerAliveCountMax=2"]
    arguments = [SSH_BINARY, "-F", "/dev/null", "-T", "-a", "-x"]
    for option in options:
        arguments.extend(("-o", option))
    arguments.extend(("-i", key, "-p", port, "-l", user, host, FORCED_COMMAND))
    return arguments


def bounded_transport(arguments, request_bytes, stdout_path, stderr_path, *,
                      timeout=TIMEOUT_SECONDS, stdout_limit=MAX_RESPONSE_BYTES,
                      stderr_limit=MAX_STDERR_BYTES, popen=None):
    """One SSH process, bounded binary streams, no retry or remote cancellation.

    Only the created local SSH process is killed on error/deadline. Remote job
    termination is the owner's broker responsibility; transport failure therefore
    leaves remote completion UNKNOWN. Pipes use daemon workers so a maliciously
    inherited pipe cannot prevent the client deadline from completing.
    """
    if len(request_bytes) > MAX_REQUEST_BYTES:
        raise ValueError("Request exceeds the fixed stdin byte limit")
    if popen is None:
        popen = subprocess.Popen
    result = {"evidence_class": "MEASURED", "attempts": 1, "started": False,
              "exit_code": None, "timed_out": False, "stdout_truncated": False,
              "stderr_truncated": False, "stdout_bytes": 0, "stderr_bytes": 0,
              "remote_completion": "UNKNOWN", "failures": []}
    started = time.monotonic()
    stop = threading.Event()
    lock = threading.Lock()
    process = None
    workers = []

    def fail(message):
        with lock:
            result["failures"].append(message)
        stop.set()

    def reader(stream, destination, field, limit):
        try:
            with destination.open("xb") as output:
                while not stop.is_set():
                    chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                    if not chunk:
                        return
                    with lock:
                        # A blocked read may return after the process deadline.
                        # Do not append bytes after collection has been stopped.
                        if stop.is_set():
                            return
                        remaining = limit - result[field + "_bytes"]
                        kept = chunk[:remaining]
                        output.write(kept)
                        result[field + "_bytes"] += len(kept)
                    if len(chunk) > remaining:
                        with lock:
                            result[field + "_truncated"] = True
                        fail(field + " exceeded its byte cap; partial evidence preserved")
                        return
        except Exception as exc:
            fail(field + " collection failed: " + type(exc).__name__)

    def writer():
        try:
            process.stdin.write(request_bytes)
            process.stdin.flush()
            process.stdin.close()
        except Exception as exc:
            fail("stdin delivery failed: " + type(exc).__name__)

    try:
        process = popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, shell=False,
                        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        result["started"] = True
        workers = [threading.Thread(target=reader, args=(process.stdout, stdout_path, "stdout", stdout_limit), daemon=True),
                   threading.Thread(target=reader, args=(process.stderr, stderr_path, "stderr", stderr_limit), daemon=True),
                   threading.Thread(target=writer, daemon=True)]
        for worker in workers:
            worker.start()
        while True:
            if stop.is_set():
                break
            if process.poll() is not None and not any(worker.is_alive() for worker in workers):
                result["exit_code"] = process.returncode
                break
            if time.monotonic() - started >= timeout:
                result["timed_out"] = True
                fail("Local SSH deadline exceeded; remote completion UNKNOWN; no retry")
                break
            stop.wait(min(0.02, max(0.001, timeout - (time.monotonic() - started))))
    except Exception as exc:
        fail("Local SSH startup failed: " + type(exc).__name__)
    finally:
        stop.set()
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()  # ONLY this client; no process tree or remote commands.
                result["exit_code"] = process.wait(timeout=1)
            except Exception as exc:
                fail("Local SSH cleanup unavailable: " + type(exc).__name__)
        for worker in workers:
            worker.join(timeout=0.1)
        # Do not block closing a pipe held by a stalled worker. Process exit will
        # close it; evidence and the UNKNOWN remote outcome are retained.
        if any(worker.is_alive() for worker in workers):
            fail("Pipe worker did not finish; bounded evidence may be partial")
        elif process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        for path in (stdout_path, stderr_path):
            if not path.exists():
                path.open("xb").close()
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    with lock:
        # Detach the failure list from any late, daemonized pipe-worker error.
        return {**result, "failures": list(result["failures"])}


def run(output_dir, permit_id, environment=None):
    """Create an exclusive evidence directory and preserve every attempted run."""
    environment = os.environ if environment is None else environment
    output = Path(output_dir).absolute()
    if any(part.is_symlink() for part in (output, *output.parents)):
        raise ValueError("Output path must not traverse symlinks")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    receipt = {"schema": "szl.ssh-cpu-build.client-receipt.v1", "evidence_class": "MEASURED",
               "result": "BLOCKED", "started_at_utc": timestamp(), "completed_at_utc": None,
               "signed": False, "independent_witness": False, "publication_eligible": False,
               "inference_requests": 0, "request_sha256": None, "transport": None,
               "remote_observation_class": "DECLARED", "remote_receipt": None,
               "response_sha256": None, "verified_files": {}, "failures": []}
    stdout = output / "response.raw.json"
    stderr = output / "ssh.stderr"
    try:
        request = build_request(environment, permit_id)
        request_bytes = canonical(request)
        receipt["request_sha256"] = sha256(request_bytes)
        with (output / "request.json").open("xb") as handle:
            handle.write(request_bytes)
        arguments = ssh_arguments(environment)
        receipt["transport"] = bounded_transport(arguments, request_bytes, stdout, stderr)
        transport = receipt["transport"]
        if (not transport["started"] or transport["timed_out"] or transport["failures"]
                or transport["exit_code"] != 0):
            raise ValueError("SSH transport did not complete successfully; no retry")
        raw = stdout.read_bytes()  # Already hard-bounded while receiving.
        receipt["response_sha256"] = sha256(raw)
        envelope = strict_json_loads(raw)
        files = verify_response(request, envelope)
        receipt["remote_receipt"] = envelope["receipt"]
        artifacts = output / "artifacts"
        artifacts.mkdir(mode=0o700)
        for name, content in files.items():
            # The contract enforces the flat allowlist; defend this write boundary too.
            if Path(name).name != name or name in {"", ".", ".."} or "/" in name or "\\" in name:
                raise ValueError("Artifact path is not a flat filename")
            with (artifacts / name).open("xb") as handle:
                handle.write(content)
            receipt["verified_files"][name] = {"bytes": len(content), "sha256": sha256(content)}
        if envelope["receipt"]["result"] != "PASS":
            raise ValueError("Remote broker declared BLOCKED; artifacts retained")
        receipt["result"] = "PASS"
    except Exception as exc:
        receipt["failures"].append(type(exc).__name__ + ": " + str(exc))
    finally:
        for path in (stdout, stderr):
            if not path.exists():
                path.open("xb").close()
        receipt["completed_at_utc"] = timestamp()
        with (output / "client-receipt.json").open("xb") as handle:
            handle.write(canonical(receipt) + b"\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--permit-id", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = run(args.output_dir, args.permit_id)
    except (OSError, ValueError) as exc:
        parser.exit(2, "SSH CI BLOCKED: " + str(exc) + "\n")
    print("SSH CI " + receipt["result"] + "; evidence: " + str(Path(args.output_dir).absolute()))
    return 0 if receipt["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
