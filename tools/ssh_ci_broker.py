#!/usr/bin/env python3
"""Root-run CPU build broker behind the ``szl-cpu-build-v1`` forced command.

One socket-activated instance handles one request: strict request parsing,
owner-signature permit loading, broker observation, durable single-use claim,
trusted source staging, the transient sandbox unit, bounded collection, and a
contract-checked v1 response. Every denial is BLOCKED with a reason code and no
retry. The response stays DECLARED remote evidence: this broker is not an
independent witness, and its receipt is not a model or science result.

Wire framing to the relay: one status byte, then canonical JSON. ``0`` precedes
a contract-checked v1 response envelope (whose receipt says PASS or BLOCKED);
``1`` precedes a BLOCKED notice carrying only reason codes.
"""

import argparse
import base64
import contextlib
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time

if __package__ in (None, ""):
    # Trusted root-owned installation root; never the payload or cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ssh_ci_claims import ClaimStore
from tools.ssh_ci_contract import (BINDING_FIELDS, CPU_PROFILE, MAX_OUTPUT_BYTES,
                                   MAX_REQUEST_BYTES, RECEIPT_SCHEMA, RECEIPT_SCHEMA_V2,
                                   REPOSITORY,
                                   RESPONSE_SCHEMA, STEP_NAMES, ContractError, canonical,
                                   sha256, strict_json_loads, validate_request,
                                   verify_response)
from tools.ssh_ci_executor import execute
from tools.ssh_ci_observer import observe
from tools.ssh_ci_owner_signature import make_permit_loader, read_protected
from tools import ssh_ci_sandbox as sandbox


CONFIG_SCHEMA = "szl.ssh-cpu-broker.config.v1"
BLOCKED_SCHEMA = "szl.ssh-cpu-build.broker-blocked.v1"
BROKER_RECEIPT_SCHEMA = "szl.ssh-cpu-build.broker-receipt.v1"
PUBLIC_RECEIPT_SCHEMA = "szl.ssh-cpu-build.public-receipt.v1"
DEFAULT_CONFIG = "/etc/szl-cpu-broker/broker.json"
SOURCE_URL = "https://github.com/" + REPOSITORY
GIT = "/usr/bin/git"
MOUNT = "/usr/bin/mount"
UMOUNT = "/usr/bin/umount"
# nr_inodes bounds entry counts the root broker must later list and collect.
RUN_TMPFS_OPTIONS = "size=268435456,nr_inodes=16384,mode=0755,nosuid,nodev,noexec"
MAX_PACKAGE_ENTRIES = 256
STAGE_TIMEOUT_SECONDS = 30  # Total for all staging commands, within the client deadline.
MAX_CONFIG_BYTES = 8192
MAX_PACKAGE_FILE_BYTES = 4 * 1024 * 1024
PACKAGE_NAME = r"[a-z0-9][a-z0-9.-]{0,95}\.zip"
MANIFEST_NAME = "science-package-manifest.json"
CONFIG_PATHS = ("allowed_signers", "permit_dir", "claim_dir", "receipt_dir",
                "public_receipt_dir", "run_root",
                "build_python", "runner", "nvidia_smi")
CONFIG_FIELDS = frozenset({"schema", "owner_principal", "build_user", "receipt_reader",
                           "hidden_paths", *CONFIG_PATHS})


class BrokerError(RuntimeError):
    """A broker stage failed closed; the reason code is safe to return."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def utc_now():
    return datetime.now(timezone.utc)


def _plain_path(value, label):
    if (type(value) is not str or not value.startswith("/") or ".." in Path(value).parts
            or re.fullmatch(r"/[A-Za-z0-9._/@+-]{0,254}", value) is None):
        raise ContractError(label + " must be a plain absolute path")
    return value


def parse_config(data):
    """Strictly parse the closed broker configuration."""
    config = strict_json_loads(data, MAX_CONFIG_BYTES)
    if type(config) is not dict or set(config) != CONFIG_FIELDS:
        raise ContractError("broker config has missing or unknown fields")
    if config["schema"] != CONFIG_SCHEMA:
        raise ContractError("broker config schema does not match")
    for field in CONFIG_PATHS:
        _plain_path(config[field], field)
    if (type(config["build_user"]) is not str
            or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", config["build_user"]) is None
            or config["build_user"] in {"root", "ubuntu"}):
        raise ContractError("build_user must be a dedicated unprivileged account")
    if (type(config["receipt_reader"]) is not str
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", config["receipt_reader"]) is None):
        raise ContractError("receipt_reader must be a plain account name")
    if type(config["owner_principal"]) is not str or not config["owner_principal"]:
        raise ContractError("owner_principal is required")
    hidden = config["hidden_paths"]
    if type(hidden) is not list or len(hidden) > 64:
        raise ContractError("hidden_paths must be a bounded list")
    for path in hidden:
        _plain_path(path, "hidden path")
    return config


def load_config(path=DEFAULT_CONFIG, reader=read_protected):
    return parse_config(reader(Path(path), MAX_CONFIG_BYTES))


def _git(arguments, cwd, run, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BrokerError("SOURCE_STAGING_DEADLINE")
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false"}
    completed = run([GIT, "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
                     "-c", "protocol.https.allow=always", "-c", "advice.detachedHead=false",
                     *arguments], cwd=cwd, env=environment, capture_output=True,
                    timeout=remaining, check=False, shell=False)
    if completed.returncode != 0:
        raise BrokerError("SOURCE_STAGING_FAILED")
    return completed.stdout.decode("ascii", "replace").strip()


def stage_source(directory, source_sha, run=subprocess.run, source_url=SOURCE_URL):
    """Fetch exactly one commit with hooks disabled and verify its object ID."""
    deadline = time.monotonic() + STAGE_TIMEOUT_SECONDS
    directory.mkdir(mode=0o755)
    _git(["init", "-q", str(directory)], directory, run, deadline)
    _git(["fetch", "-q", "--depth=1", "--no-tags", "--no-recurse-submodules",
          source_url, source_sha], directory, run, deadline)
    _git(["checkout", "-q", "--detach", source_sha], directory, run, deadline)
    if (_git(["rev-parse", "--verify", "HEAD^{commit}"], directory, run, deadline) != source_sha
            or _git(["cat-file", "-t", source_sha], directory, run, deadline) != "commit"):
        raise BrokerError("SOURCE_IDENTITY_MISMATCH")


def _read_regular_at(directory_fd, name, limit, budget=None):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        handle = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as error:  # Symlink (ELOOP), FIFO, or other non-regular entry.
        raise BrokerError("OUTPUT_FILE_REJECTED") from error
    try:
        metadata = os.fstat(handle)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise BrokerError("OUTPUT_FILE_REJECTED")
        if budget is not None:
            # Charge declared sizes before reading so the total stays bounded.
            budget[0] -= metadata.st_size
            if budget[0] < 0:
                raise BrokerError("OUTPUT_LIMIT_EXCEEDED")
        data = os.read(handle, limit + 1)
        if len(data) > limit:
            raise BrokerError("OUTPUT_FILE_REJECTED")
        return data
    finally:
        os.close(handle)


def deterministic_tar(members):
    """Return a USTAR archive of flat regular members with fixed metadata."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(members[name]), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(members[name]))
    return buffer.getvalue()


def collect_outputs(out_dir):
    """Collect the ten contract files without following links; return (files, problems)."""
    problems = []
    files = {}
    budget = [MAX_OUTPUT_BYTES]
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    out_fd = os.open(out_dir, flags)
    try:
        for name in STEP_NAMES:
            for suffix in (".stdout", ".stderr"):
                data = _read_regular_at(out_fd, name + suffix, 1024 * 1024, budget)
                if data is None:
                    problems.append("MISSING:" + name + suffix)
                files[name + suffix] = data or b""
        packages = {}
        manifest = b""
        try:
            packages_fd = os.open("packages", flags, dir_fd=out_fd)
        except FileNotFoundError:
            packages_fd = None
            problems.append("MISSING:packages")
        except OSError as error:
            raise BrokerError("OUTPUT_FILE_REJECTED") from error
        if packages_fd is not None:
            try:
                names = os.listdir(packages_fd)
                if len(names) > MAX_PACKAGE_ENTRIES + 1:
                    raise BrokerError("OUTPUT_FILE_REJECTED")
                # Leave room for the tar headers (512 bytes per member plus trailer).
                budget[0] -= 512 * (len(names) + 2)
                for entry in sorted(names):
                    if entry == MANIFEST_NAME:
                        manifest = _read_regular_at(packages_fd, entry, MAX_PACKAGE_FILE_BYTES,
                                                    budget) or b""
                    elif re.fullmatch(PACKAGE_NAME, entry):
                        packages[entry] = _read_regular_at(packages_fd, entry,
                                                           MAX_PACKAGE_FILE_BYTES, budget)
                    else:
                        problems.append("UNEXPECTED_PACKAGE_ENTRY")
            finally:
                os.close(packages_fd)
        if not manifest:
            problems.append("MISSING:" + MANIFEST_NAME)
        if not packages:
            problems.append("MISSING:package archives")
        files["packages.tar"] = deterministic_tar(packages) if packages else b""
        files[MANIFEST_NAME] = manifest
    finally:
        os.close(out_fd)
    if sum(len(data) for data in files.values()) > MAX_OUTPUT_BYTES:
        files["packages.tar"] = b""
        problems.append("OUTPUT_LIMIT_EXCEEDED")
    return files, problems


def runtime_identity(observation, mode="pinned"):
    """v1 identity for a single pinned PID; v2 identity for a pinned set."""
    processes = observation["gpu_compute_processes"]
    host = observation["host"]
    if mode == "pinned_set":
        return {"hostname": host["hostname"], "boot_id": host["boot_id"],
                "gpu_processes": sorted(({"pid": item["pid"], "start_ticks": item["start_ticks"]}
                                         for item in processes), key=lambda item: item["pid"])}
    if len(processes) != 1:
        raise BrokerError("RUNTIME_IDENTITY_UNAVAILABLE")
    return {"hostname": host["hostname"], "pid": processes[0]["pid"],
            "start_ticks": processes[0]["start_ticks"], "boot_id": host["boot_id"]}


def parse_runner(stdout):
    lines = stdout.splitlines()
    if len(lines) != 1:
        return None
    try:
        value = strict_json_loads(lines[0], 16384)
    except ContractError:
        return None
    if type(value) is not dict or value.get("schema") != "szl.ssh-cpu-build.runner.v1":
        return None
    steps = value.get("steps")
    if (type(steps) is not list or len(steps) != len(STEP_NAMES)
            or any(type(step) is not dict or step.get("name") != name
                   or type(step.get("returncode")) is not int
                   for step, name in zip(steps, STEP_NAMES))):
        return None
    return value


class Build:
    """The executor callback; keeps file bytes outside the bounded executor result."""

    def __init__(self, config, *, run=subprocess.run, popen=subprocess.Popen,
                 observer=None, run_unit=sandbox.run_unit, scan=sandbox.host_hidden_paths):
        self.config, self.run, self.popen, self.run_unit = config, run, popen, run_unit
        self.scan = scan
        self.observer = observer
        self.files = None
        self.details = {}

    def __call__(self, request, permit, binding, observation):
        try:
            return self._build(request, permit, binding, observation)
        except BrokerError as error:
            self.details.setdefault("failure", error.reason)
            raise
        except Exception as error:
            self.details.setdefault("failure", "BUILD_ERROR:" + type(error).__name__)
            raise

    @contextlib.contextmanager
    def workspace(self, request_id):
        """Yield (source, out) on a per-run tmpfs; always unmount and remove it."""
        run_root = Path(self.config["run_root"])
        root = run_root / request_id
        run_root.mkdir(mode=0o700, exist_ok=True)  # /run is per boot.
        # The claim lock is held and no build unit runs, so anything left here is
        # from a killed broker: unmount and remove it before starting.
        for stale in sorted(run_root.iterdir()):
            if not stale.is_symlink():
                self.run([UMOUNT, str(stale)], capture_output=True, timeout=30, check=False)
            shutil.rmtree(stale, ignore_errors=True)
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        mounted = False
        try:
            self._checked([MOUNT, "-t", "tmpfs", "-o", RUN_TMPFS_OPTIONS, "tmpfs", str(root)],
                          "RUN_TMPFS_UNAVAILABLE")
            mounted = True
            out = root / "out"
            out.mkdir(mode=0o700)
            os.chown(out, *self._build_ids())
            yield root / "src", out
        finally:
            if mounted:
                self.run([UMOUNT, str(root)], capture_output=True, timeout=30, check=False)
            # rmtree never follows payload symlinks; a still-mounted root stays visible.
            shutil.rmtree(root, ignore_errors=True)

    def _build(self, request, permit, binding, observation):
        mode = permit["workload"]["mode"]
        runtime_before = runtime_identity(observation, mode)
        with self.workspace(request["request_id"]) as (source, out):
            stage_source(source, request["source_sha"], self.run)
            scanned, problems = self.scan()
            if problems:
                raise BrokerError("UNHIDEABLE_HOST_PATH")
            properties = sandbox.unit_properties(
                user=self.config["build_user"], source_dir=source, output_dir=out,
                hidden_paths=[*self.config["hidden_paths"], *scanned])
            argv = sandbox.launch_argv(request["request_id"], properties,
                                       self.config["build_python"], self.config["runner"],
                                       request["source_sha"])
            outcome = self.run_unit(request["request_id"], argv, self.config["build_user"],
                                    popen=self.popen, run=self.run)
            runner = parse_runner(outcome["runner_stdout"])
            files, problems = collect_outputs(out)
            runtime_after = runtime_identity(self.observer(), mode)
        verified = bool(outcome["readback"] and outcome["readback"].get("verified"))
        self.details = {
            "readback": outcome["readback"], "go_sent": outcome["go_sent"],
            "timed_out": outcome["timed_out"], "exit_code": outcome["exit_code"],
            "unit_gone": outcome["unit_gone"], "runner": runner,
            "runner_stderr_sha256": sha256(outcome["runner_stderr"]),
            "collection_problems": problems, "runtime_before": runtime_before,
            "runtime_after": runtime_after,
        }
        if not verified or not outcome["go_sent"]:
            raise BrokerError("ISOLATION_READBACK_FAILED")
        if not outcome["unit_gone"]:
            raise BrokerError("BUILD_UNIT_NOT_STOPPED")
        if runtime_after != runtime_before:
            raise BrokerError("MODEL_IDENTITY_CHANGED")
        steps = runner["steps"] if runner else [
            {"name": name, "returncode": -1} for name in STEP_NAMES]
        passed = (runner is not None and runner.get("started") is True
                  and runner.get("prepared") is True and not runner.get("truncated")
                  and all(step["returncode"] == 0 for step in steps)
                  and outcome["exit_code"] == 0 and not outcome["timed_out"]
                  and not outcome["truncated"] and not problems)
        self.files = files
        receipt = {field: request[field] for field in BINDING_FIELDS}
        receipt.update({
            "schema": RECEIPT_SCHEMA_V2 if mode == "pinned_set" else RECEIPT_SCHEMA,
            "request_sha256": binding["request_sha256"],
            "result": "PASS" if passed else "BLOCKED", "evidence_class": "DECLARED",
            "signed": False, "independent_witness": False, "gpu_requests": 0,
            "actual_cpu_profile": dict(CPU_PROFILE), "isolation_verified": True,
            "runtime_before": runtime_before, "runtime_after": runtime_after,
            "steps": [{"name": step["name"], "returncode": step["returncode"]} for step in steps],
            "files": {name: {"sha256": sha256(data), "bytes": len(data)}
                      for name, data in sorted(files.items())},
        })
        return receipt

    def _checked(self, argv, reason):
        completed = self.run(argv, capture_output=True, timeout=30, check=False, shell=False)
        if completed.returncode != 0:
            raise BrokerError(reason)

    def _build_ids(self):
        import pwd
        entry = pwd.getpwnam(self.config["build_user"])
        if entry.pw_uid == 0 or entry.pw_gid == 0:
            raise BrokerError("BUILD_USER_PRIVILEGED")
        return entry.pw_uid, entry.pw_gid


def _write_exclusive(path, data, owner=None):
    """Create a 0600 file once; ``owner`` (uid, gid) lets one named reader see it."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path, flags, 0o600)
    if owner is not None:
        os.fchown(descriptor, *owner)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def public_summary(record):
    """Admin-readable, secret-free subset the owner compares with GitHub evidence."""
    build = record.get("build") or {}
    readback = build.get("readback") or {}
    runner = build.get("runner") or {}
    return {
        "schema": PUBLIC_RECEIPT_SCHEMA, "evidence_class": "DECLARED",
        "request_id": record["request_id"], "request_sha256": record["request_sha256"],
        "response_sha256": record["response_sha256"], "execution": record["execution"],
        "build_failure": build.get("failure"), "isolation_verified": readback.get("verified"),
        "go_sent": build.get("go_sent"), "unit_gone": build.get("unit_gone"),
        "steps": runner.get("steps"), "runtime_before": build.get("runtime_before"),
        "runtime_after": build.get("runtime_after"), "signed": False,
        "independent_witness": False,
    }


def write_receipts(config, record):
    """Write the root-only receipt and, for a valid request, the public summary."""
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    request_id = record["request_id"]
    _write_exclusive(Path(config["receipt_dir"]) / (stamp + "-" + (request_id or "invalid-request")
                                                    + ".json"), canonical(record) + b"\n")
    if request_id is not None:
        import pwd
        reader = pwd.getpwnam(config["receipt_reader"])
        public = Path(config["public_receipt_dir"])
        data = canonical(public_summary(record)) + b"\n"
        owner = (reader.pw_uid, reader.pw_gid)
        try:
            _write_exclusive(public / (request_id + ".json"), data, owner)
        except FileExistsError:  # A replayed request never overwrites the first record.
            _write_exclusive(public / (request_id + "." + stamp + ".json"), data, owner)


def handle(request_bytes, config, *, clock=utc_now, run=subprocess.run,
           popen=subprocess.Popen, observer=None, claim_store_factory=None,
           permit_loader=None, run_unit=sandbox.run_unit):
    """Return (status_byte, body_bytes, broker_record) for one request."""
    build, status, body = None, b"1", None
    record = {"schema": BROKER_RECEIPT_SCHEMA, "evidence_class": "DECLARED",
              "received_at": clock().isoformat(), "request_sha256": None,
              "request_id": None, "execution": None, "build": None,
              "response_sha256": None, "signed": False, "independent_witness": False}
    try:
        request = validate_request(strict_json_loads(request_bytes, MAX_REQUEST_BYTES))
    except (ContractError, TypeError, ValueError):
        request = None
    if request is None:
        execution = {"result": "BLOCKED", "stage": "REQUEST", "reason": "INVALID_REQUEST",
                     "claim_status": "NOT_ATTEMPTED"}
    else:
        record["request_sha256"] = sha256(canonical(request))
        record["request_id"] = request["request_id"]
        if observer is None:
            def observer():
                return observe(clock, nvidia_smi=config["nvidia_smi"], run=run)
        build = Build(config, run=run, popen=popen, observer=observer, run_unit=run_unit)
        execution = execute(
            request,
            permit_loader=permit_loader or make_permit_loader(
                config["permit_dir"], config["allowed_signers"], config["owner_principal"]),
            observation_reader=observer,
            claim_store_factory=claim_store_factory or (
                lambda: ClaimStore(Path(config["claim_dir"]))),
            executor=build, clock=clock)
        record["build"] = build.details or None
    record["execution"] = {key: execution.get(key) for key in
                           ("result", "stage", "reason", "claim_status", "executor_called")}
    if execution.get("result") == "PASS" and request is not None and build is not None:
        receipt = execution["receipt"]
        files = build.files
        envelope = {"schema": RESPONSE_SCHEMA, "request_sha256": record["request_sha256"],
                    "receipt": receipt,
                    "files": {name: base64.b64encode(data).decode("ascii")
                              for name, data in sorted(files.items())}}
        try:
            verify_response(request, envelope)  # Never emit a self-inconsistent envelope.
            body = canonical(envelope)
            # 0 means "a contract envelope follows" (PASS or BLOCKED result), so
            # the client verifies and retains its evidence; 1 is a broker notice.
            status = b"0"
        except ContractError:
            execution = {"result": "BLOCKED", "stage": "RESPONSE",
                         "reason": "RESPONSE_SELF_CHECK_FAILED",
                         "claim_status": execution.get("claim_status")}
            record["execution"].update(execution)
            body = None
    if body is None:
        status = b"1"
        body = canonical({"schema": BLOCKED_SCHEMA, "request_sha256": record["request_sha256"],
                          "result": "BLOCKED", "stage": record["execution"]["stage"],
                          "reason": record["execution"]["reason"],
                          "claim_status": record["execution"]["claim_status"],
                          "detail": (record["build"] or {}).get("failure"),
                          "retry": False})
    record["response_sha256"] = sha256(body)
    return status, body, record


def main(argv=None, stdin=None, stdout=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    request_bytes = stdin.read(MAX_REQUEST_BYTES + 1)
    try:
        config = load_config(args.config)
    except (ContractError, OSError):
        stdout.write(b"1" + canonical({"schema": BLOCKED_SCHEMA, "request_sha256": None,
                                        "result": "BLOCKED", "stage": "CONFIG",
                                        "reason": "BROKER_CONFIG_UNAVAILABLE",
                                        "claim_status": "NOT_ATTEMPTED", "detail": None,
                                        "retry": False}))
        stdout.flush()
        return 1
    status, body, record = handle(request_bytes, config)
    try:
        write_receipts(config, record)
    except OSError:
        # No receipt, no envelope: the claim stays consumed and nothing retries.
        status = b"1"
        body = canonical({"schema": BLOCKED_SCHEMA, "request_sha256": record["request_sha256"],
                          "result": "BLOCKED", "stage": "RECEIPT",
                          "reason": "BROKER_RECEIPT_UNAVAILABLE",
                          "claim_status": record["execution"]["claim_status"],
                          "detail": None, "retry": False})
    stdout.write(status + body)
    stdout.flush()
    return 0 if status == b"0" else 1


if __name__ == "__main__":
    raise SystemExit(main())
