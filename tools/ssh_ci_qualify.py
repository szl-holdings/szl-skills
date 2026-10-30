#!/usr/bin/env python3
"""Owner-run qualification of the installed CPU build sandbox on the model VM.

Run as root inside an owner-approved window after provisioning. Each control is
its own transient unit with the production properties and the same readback
gate: host-process, GPU-device, host /dev/shm, network, broker/model path,
namespace-creation, and privilege denial; task, memory, tmpfs, and deadline
limits; and, optionally, a rehearsal of the real four-step build. No permit is
used and no claim is written, but the broker's claim lock is held throughout,
so no build overlaps it; no GPU work runs. Model identity is observed before
and after. The receipt is MEASURED on this host only; it is not an
independent witness or a remote-CI success.
"""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ssh_ci_broker import (DEFAULT_CONFIG, Build, collect_outputs, load_config,
                                 parse_runner, stage_source)
from tools.ssh_ci_claims import ClaimError, ClaimStore
from tools.ssh_ci_contract import ContractError, canonical, sha256
from tools.ssh_ci_observer import observe
from tools import ssh_ci_sandbox as sandbox


QUALIFICATION_SCHEMA = "szl.ssh-cpu-build.qualification.v1"
ACTIVATION_STATE_SCHEMA = "szl.ssh-cpu-build.activation-state.v1"
RELEASE_ROOT = Path("/opt/szl-cpu-broker/releases")
CURRENT_RELEASE = Path("/opt/szl-cpu-broker/current")
MAX_ACTIVATION_STATE_BYTES = 16384
DEADLINE_SECONDS = 5
TMPFS_LIMIT = 536870912
SHM_ROOT = Path("/dev/shm")
# RestrictNamespaces= filters clone/unshare flags with EPERM; clone3 must fail
# with ENOSYS, the only error on which glibc falls back to clone for threads.
NAMESPACE_DENIALS = {"namespace:unshare": "DENIED:EPERM", "namespace:clone": "DENIED:EPERM",
                     "namespace:clone3": "DENIED:ENOSYS"}
PROBE = r"""
import errno, json, os, socket, sys, threading, time
if sys.stdin.buffer.read(3) != b"GO\n":
    print(json.dumps({"go": False})); sys.exit(125)
mode, args = sys.argv[1], json.loads(sys.argv[2])
r = {"go": True, "mode": mode}
def attempt(name, action):
    try:
        action(); r[name] = "ALLOWED"
    except FileNotFoundError:
        r[name] = "ABSENT"
    except Exception as error:
        r[name] = "DENIED:" + type(error).__name__
def emit():
    print(json.dumps(r, sort_keys=True)); sys.stdout.flush()
if mode == "isolation":
    r["proc_pids"] = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())
    r["model_pid_visible"] = [pid for pid in args["model_pids"] if os.path.exists("/proc/" + str(pid))]
    r["dev_shm_entries"] = len(os.listdir("/dev/shm"))
    attempt("dev_shm_marker", lambda: os.lstat("/dev/shm/" + args["shm_marker"]))
    r["gpu_devices"] = sorted(n for n in os.listdir("/dev") if n.startswith("nvidia"))
    attempt("inet_socket", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).close())
    for port in args["ports"]:
        attempt("tcp:" + str(port), lambda p=port: socket.create_connection(("127.0.0.1", p), 2).close())
    attempt("tcp:external", lambda: socket.create_connection(("1.1.1.1", 443), 2).close())
    for path in args["unix_sockets"]:
        def connect(p=path):
            s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(p); s.close()
        attempt("unix:" + path, connect)
    for path in args["hidden"]:
        attempt("list:" + path, lambda p=path: os.listdir(p))
    for path in ("/src/.probe", "/opt/.probe", "/etc/.probe", "/usr/.probe", "/var/.probe"):
        attempt("write:" + path, lambda p=path: open(p, "x").close())
    for path in ("/out/.probe", "/tmp/.probe"):
        attempt("write:" + path, lambda p=path: open(p, "x").close())
    attempt("setuid_root", lambda: os.setuid(0))
    for path in args["secret_files"]:
        attempt("read:" + path, lambda p=path: open(p, "rb").read(1))
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    for name, number, argv in (("io_uring_setup", 425, (1, None)), ("add_key", 248, (None, None, None, 0, 0))):
        result = libc.syscall(number, *argv)
        r["syscall:" + name] = "DENIED:EPERM" if result == -1 and ctypes.get_errno() == 1 else "ALLOWED:" + str(ctypes.get_errno())
    def reap(pid):
        # Bounded: a child still running after 5 seconds is killed; it is always reaped.
        for _ in range(500):
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                return status
            time.sleep(0.01)
        os.kill(pid, 9)
        return os.waitpid(pid, 0)[1]
    def denial(code):
        return "DENIED:" + errno.errorcode.get(code, str(code))
    if os.uname().machine == "x86_64":  # Raw x86_64 syscall numbers; elsewhere none is reported.
        # unshare changes its caller, so it runs in a forked child that exits with its errno.
        child = os.fork()
        if child == 0:
            os._exit(0 if libc.syscall(272, 0x10000000) == 0 else ctypes.get_errno() or 255)
        status = reap(child)
        code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
        r["namespace:unshare"] = "ALLOWED" if code == 0 else denial(code) if code > 0 else "UNKNOWN:killed"
        # Fork-like (no stack): CLONE_NEWUSER with SIGCHLD; clone_args is 88 bytes.
        clone_args = (ctypes.c_uint64 * 11)(0x10000000, 0, 0, 0, 17)
        for name, number, argv in (("clone", 56, (0x10000000 | 17, 0, 0, 0, 0)),
                                   ("clone3", 435, (ctypes.byref(clone_args), 88))):
            result = libc.syscall(number, *argv); code = ctypes.get_errno()
            if result == 0:
                os._exit(0)  # The child of an allowed call: never let it run on.
            if result > 0:
                reap(result)
            r["namespace:" + name] = "ALLOWED" if result > 0 else denial(code)
    def thread():
        worker = threading.Thread(target=int); worker.start(); worker.join(5)
        if worker.is_alive():
            raise RuntimeError("thread did not finish")
    attempt("thread_start", thread)  # Positive control: glibc's clone3 fallback works.
    reachable, seen = [], 0
    for top in ("/run", "/var", "/opt", "/etc", "/srv", "/mnt", "/usr/local", "/tmp", "/dev"):
        for directory, subdirectories, names in os.walk(top):
            for name in names:
                seen += 1
                path = os.path.join(directory, name)
                try:
                    if not __import__("stat").S_ISSOCK(os.lstat(path).st_mode):
                        continue
                    s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(path); s.close()
                    reachable.append(path)
                except OSError:
                    pass
            if seen > 50000:
                break
    r["reachable_unix_sockets"] = reachable
    status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
    r["cap_eff"] = status["CapEff"].strip(); r["no_new_privs"] = status["NoNewPrivs"].strip()
    r["uid"] = os.getuid()
elif mode == "tasks":
    gate, started = threading.Event(), 0
    try:
        while started < 512:
            threading.Thread(target=gate.wait, daemon=True).start(); started += 1
    except RuntimeError:
        pass
    r["threads_started"] = started; gate.set()
elif mode == "memory":
    held, total = [], 0
    while total < 2048:
        held.append(bytearray(b"\x01") * (64 << 20)); total += 64
        r["allocated_mib"] = total; emit()
elif mode == "tmpfs":
    written, chunk = 0, b"\x02" * (16 << 20)
    try:
        with open("/tmp/fill", "wb") as stream:
            while written < (768 << 20):
                stream.write(chunk); stream.flush(); written += len(chunk)
    except OSError as error:
        r["error"] = type(error).__name__
    r["bytes_written"] = written
elif mode == "deadline":
    r["sleeping"] = True; emit(); time.sleep(120)
emit()
"""


class QualificationError(ValueError):
    """Qualification or read-only activation binding was not established."""


class QualifyError(RuntimeError):
    """Qualification could not start safely; no receipt is written."""


def verify_release(config, expected_release, *, source_file=None, release_root=None,
                   current_path=None):
    """Bind this executable, the configured runner and current link to one release.

    This checks installed paths and the strict configuration digest, not a
    cryptographic attestation of installed file contents. Root ownership and
    configuration authentication remain the provisioner/loader's boundaries.
    """
    if type(expected_release) is not str or re.fullmatch(r"[0-9a-f]{40}", expected_release) is None:
        raise QualificationError("expected release must be a full lowercase commit")
    try:
        root = Path(release_root if release_root is not None else RELEASE_ROOT).resolve(strict=True)
        release = root / expected_release
        if release.is_symlink() or not release.is_dir():
            raise QualificationError("expected release is not an immutable release directory")
        source = Path(source_file if source_file is not None else __file__).resolve(strict=True)
        if source != release / "tools" / "ssh_ci_qualify.py" or not source.is_file():
            raise QualificationError("qualifier source is not the expected installed release")
        current = Path(current_path if current_path is not None else CURRENT_RELEASE)
        if current.resolve(strict=True) != release:
            raise QualificationError("current installed release does not match expected release")
        runner = Path(config["runner"]).resolve(strict=True)
        if runner != release / "tools" / "ssh_ci_step_runner.py" or not runner.is_file():
            raise QualificationError("configured runner is not the expected installed release")
    except (OSError, KeyError, TypeError) as error:
        raise QualificationError("installed release binding is unavailable") from error
    return {"release_sha": expected_release, "config_sha256": sha256(canonical(config))}


def activation_state(config_path, expected_release, *, loader=None, verifier=None,
                     observer=None, clock=lambda: datetime.now(timezone.utc)):
    """Read current release/configuration and workload state without probes or writes."""
    loader = loader or load_config
    verifier = verifier or verify_release
    config = loader(config_path)
    binding = verifier(config, expected_release)
    observation = (observer() if observer is not None else
                   observe(clock, nvidia_smi=config["nvidia_smi"]))
    after = verifier(loader(config_path), expected_release)
    if after != binding:
        raise QualificationError("release or configuration changed during readiness read")
    state = {"schema": ACTIVATION_STATE_SCHEMA, **binding, "observation": observation}
    if len(canonical(state)) > MAX_ACTIVATION_STATE_BYTES:
        raise QualificationError("activation state exceeds output bound")
    return state


def listening_ports(proc=Path("/proc")):
    ports = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = (Path(proc) / "net" / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 3 and fields[3] == "0A":
                ports.add(int(fields[1].rsplit(":", 1)[1], 16))
    return sorted(ports)[:64]


MODEL_SUFFIXES = (".safetensors", ".bin", ".gguf", ".pt", ".pth")
MODEL_ENV = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
             "VLLM_CACHE_ROOT", "XDG_CACHE_HOME", "TORCH_HOME")


def model_paths(pids, proc=Path("/proc"), run=subprocess.run):
    """Read-only: directories and files the model process uses, for probing.

    Sources: cwd, mapped weight files, absolute paths on its command line,
    Hugging Face/vLLM cache variables in its environment, and its systemd unit
    file (which may carry tokens in Environment=). Returns (dirs, files).
    """
    directories, files = set(), set()
    for pid in pids:
        base = Path(proc) / str(pid)
        try:
            directories.add(str(Path(base / "cwd").resolve()))
        except OSError:
            # An unreadable field of a live process is simply not probed.
            pass
        try:
            for line in (base / "maps").read_text(errors="replace").splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and fields[5].endswith(MODEL_SUFFIXES):
                    directories.add(posixpath.dirname(fields[5]))  # VM paths are POSIX.
        except OSError:
            # An unreadable field of a live process is simply not probed.
            pass
        try:
            for argument in (base / "cmdline").read_bytes().split(b"\0"):
                text = argument.decode("utf-8", "replace").split("=", 1)[-1]
                if text.startswith("/") and not text.startswith(("/usr/", "/proc", "/dev")):
                    has_suffix = "." in posixpath.basename(text)
                    (files if has_suffix else directories).add(text)
        except OSError:
            # An unreadable field of a live process is simply not probed.
            pass
        try:
            for entry in (base / "environ").read_bytes().split(b"\0"):
                key, _, value = entry.decode("utf-8", "replace").partition("=")
                if key in MODEL_ENV and value.startswith("/"):
                    directories.add(value)
        except OSError:
            # An unreadable field of a live process is simply not probed.
            pass
        try:
            cgroup = (base / "cgroup").read_text().strip().rsplit("/", 1)[-1]
            if cgroup.endswith(".service"):
                shown = run([sandbox.SYSTEMCTL, "show", cgroup, "--property=FragmentPath",
                             "--value"], capture_output=True, timeout=15, check=False)
                fragment = shown.stdout.decode("utf-8", "replace").strip()
                if fragment.startswith("/"):
                    files.add(fragment)
        except (OSError, subprocess.SubprocessError):
            # No readable unit file means there is nothing extra to probe.
            pass
    keep = sorted(path for path in directories if path not in ("/", "/proc"))[:32]
    return keep, sorted(files)[:32]


def last_json(stdout):
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def unit_result(stderr):
    match = re.search(rb"Finished with result: ([a-z-]+)", stderr)
    return match.group(1).decode("ascii") if match else None


def evaluate(mode, probe, outcome, context):
    """Return failure strings for one control; empty means the control held."""
    failures = []
    if not (outcome["readback"] or {}).get("verified") or not outcome["go_sent"]:
        return ["readback not verified: %s" % (outcome["readback"] or {}).get("mismatched")]
    if not outcome["unit_gone"]:
        failures.append("unit still present after completion")
    if mode == "memory":
        if unit_result(outcome["runner_stderr"]) != "oom-kill":
            failures.append("memory control was not OOM-killed by its own cgroup")
        if probe and probe.get("allocated_mib", 0) > 1024:
            failures.append("allocated beyond 1 GiB")
        return failures
    if mode == "deadline":
        if unit_result(outcome["runner_stderr"]) != "timeout":
            failures.append("deadline control did not end with result timeout")
        if outcome["elapsed"] > DEADLINE_SECONDS + 20:
            failures.append("deadline kill was late")
        return failures
    if probe is None:
        return failures + ["probe produced no result"]
    if mode == "tasks" and not 0 < probe["threads_started"] < 64:
        failures.append("task limit not enforced: %s" % probe["threads_started"])
    if mode == "tmpfs" and probe["bytes_written"] > TMPFS_LIMIT:
        failures.append("tmpfs exceeded its size")
    if mode == "isolation":
        if probe["model_pid_visible"] or len(probe["proc_pids"]) > 8:
            failures.append("host processes visible: %d pids" % len(probe["proc_pids"]))
        if probe["gpu_devices"]:
            failures.append("GPU devices visible")
        if probe["dev_shm_entries"]:
            failures.append("host /dev/shm visible")
        if probe.get("dev_shm_marker") != "ABSENT":
            failures.append("host /dev/shm marker not hidden: %s" % probe.get("dev_shm_marker"))
        for key, expected in NAMESPACE_DENIALS.items():
            if probe.get(key) != expected:
                failures.append("%s is %s, not %s" % (key, probe.get(key, "missing"), expected))
        if probe.get("thread_start") != "ALLOWED":
            failures.append("threads do not start: %s" % probe.get("thread_start"))
        if probe.get("reachable_unix_sockets"):
            failures.append("reachable unix sockets: " + ", ".join(probe["reachable_unix_sockets"][:8]))
        for key, value in probe.items():
            if key.startswith(("tcp:", "unix:", "list:", "read:")) or key in ("inet_socket",
                                                                              "setuid_root"):
                if value == "ALLOWED":
                    failures.append(key + " allowed")
            if key.startswith("syscall:") and value != "DENIED:EPERM":
                failures.append(key + " not filtered: " + value)
            if key.startswith("write:"):
                expected_allowed = key in ("write:/out/.probe", "write:/tmp/.probe")
                if (value == "ALLOWED") != expected_allowed:
                    failures.append(key + " " + value)
        if int(probe["cap_eff"], 16) != 0 or probe["no_new_privs"] != "1":
            failures.append("capabilities or no_new_privs not dropped")
        if probe["uid"] != context["build_uid"]:
            failures.append("probe did not run as the build user")
    return failures


def run_control(build, config, mode, arguments, *, runtime_seconds, run_unit=sandbox.run_unit):
    request_id = str(uuid.uuid4())
    scanned, _problems = build.scan()  # Same per-run hiding as a real build.
    with build.workspace(request_id) as (source, out):
        source.mkdir(mode=0o755)
        properties = sandbox.unit_properties(
            user=config["build_user"], source_dir=source, output_dir=out,
            hidden_paths=[*config["hidden_paths"], *scanned], runtime_seconds=runtime_seconds)
        argv = sandbox.systemd_run_argv(request_id, properties, [
            config["build_python"], "-I", "-B", "-c", PROBE, mode, json.dumps(arguments)],
            quiet=False)
        started = time.monotonic()
        outcome = run_unit(request_id, argv, config["build_user"], run=build.run,
                           popen=build.popen, wall_seconds=runtime_seconds)
        outcome["elapsed"] = round(time.monotonic() - started, 3)
    return outcome


def rehearse(build, config, source_sha, *, run_unit=sandbox.run_unit):
    """Run the real staged four-step build in the sandbox without any permit."""
    request_id = str(uuid.uuid4())
    scanned, _problems = build.scan()
    with build.workspace(request_id) as (source, out):
        stage_source(source, source_sha, build.run)
        properties = sandbox.unit_properties(user=config["build_user"], source_dir=source,
                                             output_dir=out,
                                             hidden_paths=[*config["hidden_paths"], *scanned])
        argv = sandbox.launch_argv(request_id, properties, config["build_python"],
                                   config["runner"], source_sha)
        outcome = run_unit(request_id, argv, config["build_user"], run=build.run, popen=build.popen)
        runner = parse_runner(outcome["runner_stdout"])
        files, problems = collect_outputs(out)
    failures = []
    if not (outcome["readback"] or {}).get("verified"):
        failures.append("rehearsal readback not verified")
    if runner is None or any(step["returncode"] != 0 for step in runner["steps"]):
        failures.append("rehearsal steps did not all pass")
    if problems or outcome["truncated"]:
        failures.append("rehearsal output problems: %s" % (problems + outcome["truncated"]))
    return {"source_sha": source_sha, "runner": runner, "collection_problems": problems,
            "file_bytes": {name: len(data) for name, data in sorted(files.items())},
            "failures": failures}


def shm_marker(root=SHM_ROOT):
    """Create a fresh 0600 marker in host /dev/shm that the isolation probe must not see."""
    path = Path(root) / ("szl-qualify-" + uuid.uuid4().hex)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    except OSError as error:
        raise QualifyError("cannot create a fresh host /dev/shm marker: "
                           + type(error).__name__) from error
    return path


def qualify(config, *, rehearse_sha=None, owner_window_ref=None, run=subprocess.run,
            popen=subprocess.Popen, clock=lambda: datetime.now(timezone.utc),
            run_unit=sandbox.run_unit, proc=Path("/proc"), build_uid=None, observer=None,
            build=None, claim_store_factory=None, shm_root=SHM_ROOT):
    build = build or Build(config, run=run, popen=popen)
    if observer is None:
        def observer():
            return observe(clock, nvidia_smi=config["nvidia_smi"], run=run)
    store = (claim_store_factory or (lambda: ClaimStore(Path(config["claim_dir"]))))()
    with contextlib.ExitStack() as stack:
        try:
            # The broker's own lock: no build starts, and its stale-run cleanup never
            # removes these units' run directories, until qualification ends.
            stack.enter_context(store)
        except ClaimError as error:
            raise QualifyError("the broker claim lock is held or unavailable (a build may be "
                               "running); qualification not started") from error
        before = observer()
        competing = before.get("competing_cpu_jobs")
        if type(competing) is not int or competing != 0:
            raise QualifyError("build units are already running (%r); qualification not started"
                               % (competing,))
        model_pids = [item["pid"] for item in before["gpu_compute_processes"]]
        context = {"build_uid": build_uid if build_uid is not None else build._build_ids()[0]}
        model_dirs, model_files = model_paths(model_pids, proc, run)
        scanned, scan_problems = build.scan()
        sockets = [path for path in scanned if path.startswith("/run/")]
        hidden = sorted({*sandbox.HIDDEN_ALWAYS, *config["hidden_paths"], "/home", "/root",
                         *model_dirs, *(path for path in scanned if path not in sockets)})
        arguments = {"model_pids": model_pids, "ports": listening_ports(proc), "hidden": hidden,
                     "secret_files": model_files,
                     "unix_sockets": sorted({"/run/szl-cpu-broker.sock",
                                             "/run/dbus/system_bus_socket", *sockets})}
        marker = shm_marker(shm_root)
        try:
            arguments["shm_marker"] = marker.name
            controls = []
            for mode, runtime in (("isolation", 60), ("tasks", 60), ("memory", 60),
                                  ("tmpfs", 60), ("deadline", DEADLINE_SECONDS)):
                outcome = run_control(build, config, mode, arguments, runtime_seconds=runtime,
                                      run_unit=run_unit)
                probe = last_json(outcome["runner_stdout"])
                controls.append({"control": mode, "probe": probe, "elapsed": outcome["elapsed"],
                                 "unit_result": unit_result(outcome["runner_stderr"]),
                                 "readback": outcome["readback"],
                                 "failures": evaluate(mode, probe, outcome, context)})
        finally:
            os.unlink(marker)
        rehearsal = (rehearse(build, config, rehearse_sha, run_unit=run_unit)
                     if rehearse_sha else None)
        after = observer()
    failures = [control["control"] + ": " + failure for control in controls
                for failure in control["failures"]]
    failures += ["host scan: " + problem for problem in scan_problems]
    if rehearsal:
        failures += ["rehearsal: " + failure for failure in rehearsal["failures"]]
    if (before["host"], before["gpu_compute_processes"]) != (after["host"], after["gpu_compute_processes"]):
        failures.append("model identity changed during qualification")
    if after["competing_cpu_jobs"]:
        failures.append("build units still running after qualification")
    return {
        "schema": QUALIFICATION_SCHEMA, "evidence_class": "MEASURED",
        "result": "QUALIFIED" if not failures else "BLOCKED", "failures": failures,
        "completed_at": clock().isoformat(), "owner_window_ref": owner_window_ref,
        "host": before["host"], "model_before": before["gpu_compute_processes"],
        "model_after": after["gpu_compute_processes"], "controls": controls,
        "rehearsal": rehearsal, "signed": False, "independent_witness": False,
        "scope": "this host, this release, the fixed CPU profile; not model or science quality",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--rehearse-build", help="full commit to rehearse with the real runner")
    parser.add_argument("--owner-window-ref")
    parser.add_argument("--expected-release", help="full installed release commit to bind")
    parser.add_argument("--readiness", action="store_true",
                        help="read release/config/workload identity only; no probes or writes")
    args = parser.parse_args(argv)
    if args.rehearse_build and re.fullmatch(r"[0-9a-f]{40}", args.rehearse_build) is None:
        parser.error("--rehearse-build must be a full lowercase commit")
    if args.expected_release is not None and re.fullmatch(r"[0-9a-f]{40}", args.expected_release) is None:
        parser.error("--expected-release must be a full lowercase commit")
    if args.readiness and (not args.expected_release or args.rehearse_build):
        parser.error("--readiness requires --expected-release and forbids --rehearse-build")
    if not args.readiness and not args.owner_window_ref:
        parser.error("qualification requires --owner-window-ref")
    if args.expected_release and args.rehearse_build and args.expected_release != args.rehearse_build:
        parser.error("rehearsal source must match --expected-release")
    try:
        if args.readiness:
            print(canonical(activation_state(args.config, args.expected_release)).decode("utf-8"))
            return 0
        config = load_config(args.config)
        binding = (verify_release(config, args.expected_release)
                   if args.expected_release is not None else None)
        try:
            receipt = qualify(config, rehearse_sha=args.rehearse_build,
                              owner_window_ref=args.owner_window_ref)
        except QualifyError as error:
            # The lock, a running build, or the marker stopped the run before a receipt exists.
            print("QUALIFICATION REFUSED: " + str(error), file=sys.stderr)
            return 1
        if binding is not None:
            receipt.update(binding)
            try:
                after = verify_release(load_config(args.config), args.expected_release)
                if binding != after:
                    raise QualificationError("release or configuration changed during qualification")
            except (QualificationError, ContractError, OSError, ValueError) as error:
                # Preserve the failed run's evidence rather than dropping its
                # receipt after the controls have already executed.
                receipt["failures"].append("activation binding: " + str(error))
                receipt["result"] = "BLOCKED"
    except (QualificationError, ContractError, OSError, ValueError) as error:
        print("QUALIFICATION BLOCKED: " + str(error), file=sys.stderr)
        return 1
    data = canonical(receipt) + b"\n"
    name = "qualification-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    path = Path("/var/lib/szl-cpu-broker/qualification") / name
    with open(path, "xb") as stream:
        stream.write(data)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    print("qualification receipt: " + str(path), file=sys.stderr)
    return 0 if receipt["result"] == "QUALIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
