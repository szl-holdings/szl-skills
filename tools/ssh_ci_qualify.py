#!/usr/bin/env python3
"""Owner-run qualification of the installed CPU build sandbox on the model VM.

Run as root inside an owner-approved window after provisioning. Each control is
its own transient unit with the production properties and the same readback
gate: host-process, GPU-device, network, broker/model path, and privilege
denial; task, memory, tmpfs, and deadline limits; and, optionally, a rehearsal
of the real four-step build. No permit or claim is used and no GPU work runs.
Model identity is observed before and after. The receipt is MEASURED on this
host only; it is not an independent witness or a remote-CI success.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ssh_ci_broker import (DEFAULT_CONFIG, Build, collect_outputs, load_config,
                                 parse_runner, stage_source)
from tools.ssh_ci_contract import canonical
from tools.ssh_ci_observer import observe
from tools import ssh_ci_sandbox as sandbox


QUALIFICATION_SCHEMA = "szl.ssh-cpu-build.qualification.v1"
DEADLINE_SECONDS = 5
TMPFS_LIMIT = 536870912
PROBE = r"""
import json, os, socket, sys, threading, time
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
            pass
        try:
            for line in (base / "maps").read_text(errors="replace").splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and fields[5].endswith(MODEL_SUFFIXES):
                    directories.add(str(Path(fields[5]).parent))
        except OSError:
            pass
        try:
            for argument in (base / "cmdline").read_bytes().split(b"\0"):
                text = argument.decode("utf-8", "replace").split("=", 1)[-1]
                if text.startswith("/") and not text.startswith(("/usr/", "/proc", "/dev")):
                    target = Path(text)
                    (directories if not target.suffix else files).add(text)
        except OSError:
            pass
        try:
            for entry in (base / "environ").read_bytes().split(b"\0"):
                key, _, value = entry.decode("utf-8", "replace").partition("=")
                if key in MODEL_ENV and value.startswith("/"):
                    directories.add(value)
        except OSError:
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


def qualify(config, *, rehearse_sha=None, owner_window_ref=None, run=subprocess.run,
            popen=subprocess.Popen, clock=lambda: datetime.now(timezone.utc),
            run_unit=sandbox.run_unit, proc=Path("/proc"), build_uid=None, observer=None,
            build=None):
    build = build or Build(config, run=run, popen=popen)
    if observer is None:
        def observer():
            return observe(clock, nvidia_smi=config["nvidia_smi"], run=run)
    before = observer()
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
    controls = []
    for mode, runtime in (("isolation", 60), ("tasks", 60), ("memory", 60), ("tmpfs", 60),
                          ("deadline", DEADLINE_SECONDS)):
        outcome = run_control(build, config, mode, arguments, runtime_seconds=runtime,
                              run_unit=run_unit)
        probe = last_json(outcome["runner_stdout"])
        controls.append({"control": mode, "probe": probe, "elapsed": outcome["elapsed"],
                         "unit_result": unit_result(outcome["runner_stderr"]),
                         "readback": outcome["readback"],
                         "failures": evaluate(mode, probe, outcome, context)})
    rehearsal = rehearse(build, config, rehearse_sha, run_unit=run_unit) if rehearse_sha else None
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
    parser.add_argument("--owner-window-ref", required=True)
    args = parser.parse_args(argv)
    if args.rehearse_build and re.fullmatch(r"[0-9a-f]{40}", args.rehearse_build) is None:
        parser.error("--rehearse-build must be a full lowercase commit")
    config = load_config(args.config)
    receipt = qualify(config, rehearse_sha=args.rehearse_build,
                      owner_window_ref=args.owner_window_ref)
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
