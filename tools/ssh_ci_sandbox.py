"""Transient systemd unit sandbox for the fixed CPU build profile.

One build runs as one transient service: one aggregate cgroup (CPU quota,
memory, zero swap, task count, runtime), private network/devices/IPC/users,
tmpfs /tmp charged to that cgroup, a read-only system, and hidden home, model,
broker, and container-runtime paths. The in-unit runner waits for ``GO``; the
broker sends it only after kernel cgroup files and systemd properties read back
exactly. Only this unit's cgroup is ever killed. This is systemd/kernel
namespace isolation inside the model VM, not a separate kernel or VM.
"""

from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from tools.ssh_ci_contract import CPU_PROFILE


SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
CGROUP_ROOT = Path("/sys/fs/cgroup")
UNIT_PREFIX = "szl-cpu-build-"
GO = b"GO\n"
TMPFS_OPTIONS = "mode=1777,size=536870912,nosuid,nodev"
READBACK_SECONDS = 10
STOP_SECONDS = 20
MAX_RUNNER_STDOUT = 64 * 1024
MAX_RUNNER_STDERR = 64 * 1024
HIDDEN_ALWAYS = (
    "/etc/szl-cpu-broker", "/var/lib/szl-cpu-broker", "/etc/ssh", "/run/szl-cpu-broker.sock",
    "/run/docker.sock", "/run/containerd", "/var/lib/docker", "/var/lib/containerd",
    "/var/log", "/boot", "/mnt", "/media", "/srv", "/run/dbus", "/run/systemd",
)
SHM_OPTIONS = "mode=1777,size=67108864,nosuid,nodev,noexec"
EXPECTED_CGROUP = {
    "cpu.max": "100000 100000",
    "memory.max": str(CPU_PROFILE["memory_max_bytes"]),
    "memory.swap.max": str(CPU_PROFILE["memory_swap_max_bytes"]),
    "pids.max": str(CPU_PROFILE["tasks_max"]),
}
EXPECTED_PROPERTIES = {
    "MemoryMax": str(CPU_PROFILE["memory_max_bytes"]),
    "MemorySwapMax": str(CPU_PROFILE["memory_swap_max_bytes"]),
    "TasksMax": str(CPU_PROFILE["tasks_max"]),
    "CPUQuotaPerSecUSec": "1s",
    "PrivateNetwork": "yes",
    "PrivateDevices": "yes",
    "DevicePolicy": "closed",
    "PrivateUsers": "yes",
    "PrivateIPC": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "ProtectProc": "invisible",
    "NoNewPrivileges": "yes",
    "KillMode": "control-group",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
}


def format_timespan(seconds):
    """Render whole seconds the way ``systemctl show`` prints a timespan."""
    parts = []
    for suffix, size in (("d", 86400), ("h", 3600), ("min", 60), ("s", 1)):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append("%d%s" % (count, suffix))
    return " ".join(parts) or "0"


class SandboxError(RuntimeError):
    """The sandbox could not be launched or verified; never send GO."""


def unit_name(request_id):
    if type(request_id) is not str or str(uuid.UUID(request_id)) != request_id:
        raise SandboxError("request_id must be a canonical UUID")
    return UNIT_PREFIX + request_id + ".service"


def _absolute(path, label):
    text = str(path)
    if (not text.startswith("/") or ".." in Path(text).parts
            or re.fullmatch(r"/[A-Za-z0-9._/@+-]{0,254}", text) is None):
        raise SandboxError(label + " must be a plain absolute path")
    return text


def unit_properties(*, user, source_dir, output_dir, hidden_paths=(),
                    runtime_seconds=CPU_PROFILE["wall_seconds"]):
    """Return the fixed property list; only paths and the build user vary."""
    if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user or "") is None or user in {"root", "ubuntu"}:
        raise SandboxError("build user must be a dedicated unprivileged account")
    if type(runtime_seconds) is not int or not 1 <= runtime_seconds <= CPU_PROFILE["wall_seconds"]:
        raise SandboxError("runtime must be within the fixed wall-clock profile")
    source = _absolute(source_dir, "source_dir")
    output = _absolute(output_dir, "output_dir")
    hidden = sorted({_absolute(path, "hidden path") for path in (*HIDDEN_ALWAYS, *hidden_paths)})
    properties = [
        "Type=exec", "User=" + user, "Group=" + user,
        "CPUAccounting=yes", "CPUQuota=100%",
        "MemoryAccounting=yes", "MemoryMax=%d" % CPU_PROFILE["memory_max_bytes"],
        "MemorySwapMax=%d" % CPU_PROFILE["memory_swap_max_bytes"],
        "TasksAccounting=yes", "TasksMax=%d" % CPU_PROFILE["tasks_max"],
        "RuntimeMaxSec=%d" % runtime_seconds,
        "KillMode=control-group", "OOMPolicy=kill", "SendSIGKILL=yes", "TimeoutStopSec=10",
        "PrivateNetwork=yes", "IPAddressDeny=any", "RestrictAddressFamilies=AF_UNIX",
        "PrivateDevices=yes", "DevicePolicy=closed", "PrivateIPC=yes", "PrivateUsers=yes",
        "ProtectSystem=strict", "ProtectHome=yes", "ProtectProc=invisible",
        "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes", "ProtectClock=yes", "ProtectHostname=yes",
        "NoNewPrivileges=yes", "CapabilityBoundingSet=", "AmbientCapabilities=",
        "RestrictSUIDSGID=yes", "RestrictRealtime=yes", "RestrictNamespaces=yes",
        "LockPersonality=yes", "SystemCallArchitectures=native",
        "SystemCallFilter=@system-service", "SystemCallFilter=~@privileged @resources",
        "SystemCallErrorNumber=EPERM", "UMask=0077",
        "TemporaryFileSystem=/tmp:" + TMPFS_OPTIONS,
        "TemporaryFileSystem=/var/tmp:" + TMPFS_OPTIONS,
        # PrivateDevices= binds the host /dev/shm; replace it with a private tmpfs.
        "TemporaryFileSystem=/dev/shm:" + SHM_OPTIONS,
        "BindReadOnlyPaths=" + source + ":/src",
        "BindPaths=" + output + ":/out",
        "WorkingDirectory=/tmp",
    ]
    properties.extend("InaccessiblePaths=-" + path for path in hidden)
    return properties


def systemd_run_argv(request_id, properties, command, *, quiet=True):
    """Return the fixed transient-unit launcher; ``command`` is trusted argv."""
    argv = [SYSTEMD_RUN, "--unit=" + unit_name(request_id),
            "--description=SZL CPU build " + request_id, "--service-type=exec",
            "--pipe", "--wait", "--collect", "--no-ask-password"]
    if quiet:
        argv.append("--quiet")
    argv.extend("--property=" + item for item in properties)
    return argv + ["--", *command]


def launch_argv(request_id, properties, python, runner, source_sha):
    if re.fullmatch(r"[0-9a-f]{40}", source_sha or "") is None:
        raise SandboxError("source_sha must be a full lowercase commit")
    return systemd_run_argv(request_id, properties, [
        _absolute(python, "python"), "-I", "-B", _absolute(runner, "runner"),
        "--source-sha", source_sha])


def _read_cgroup(unit, cgroup_root):
    base = Path(cgroup_root) / "system.slice" / unit
    values = {}
    for name in EXPECTED_CGROUP:
        with open(base / name, "rb") as stream:
            values[name] = stream.read(256).decode("ascii").strip()
    with open(base / "cgroup.procs", "rb") as stream:
        values["procs"] = len(stream.read(65536).split())
    return values


def _show(unit, run):
    completed = run([SYSTEMCTL, "show", unit, "--no-pager",
                     "--property=" + ",".join(sorted(EXPECTED_PROPERTIES)
                                              + ["ControlGroup", "RuntimeMaxUSec", "User"])],
                    capture_output=True, timeout=15, check=False, shell=False)
    if completed.returncode != 0:
        raise SandboxError("systemctl show failed")
    values = {}
    for line in completed.stdout.decode("ascii", "replace").splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def readback(unit, user, *, run=subprocess.run, cgroup_root=CGROUP_ROOT,
             clock=time.monotonic, sleep=time.sleep, seconds=READBACK_SECONDS,
             runtime_seconds=CPU_PROFILE["wall_seconds"]):
    """Wait for the unit's cgroup, then return measured values and a verdict."""
    deadline = clock() + seconds
    cgroup = None
    while clock() < deadline:
        try:
            cgroup = _read_cgroup(unit, cgroup_root)
            if cgroup["procs"]:
                break
        except (OSError, UnicodeDecodeError):
            cgroup = None
        sleep(0.05)
    if not cgroup or not cgroup["procs"]:
        return {"verified": False, "failure": "CGROUP_UNAVAILABLE", "cgroup": cgroup,
                "properties": None}
    properties = _show(unit, run)
    expected = dict(EXPECTED_PROPERTIES, ControlGroup="/system.slice/" + unit, User=user,
                    RuntimeMaxUSec=format_timespan(runtime_seconds))
    mismatched = sorted(key for key, value in expected.items() if properties.get(key) != value)
    mismatched += sorted(key for key, value in EXPECTED_CGROUP.items() if cgroup.get(key) != value)
    return {"verified": not mismatched, "failure": "READBACK_MISMATCH" if mismatched else None,
            "mismatched": mismatched, "cgroup": cgroup,
            "properties": {key: properties.get(key) for key in sorted(expected)}}


def _bounded_reader(stream, limit, sink, flags, name):
    kept = bytearray()
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        room = limit - len(kept)
        kept.extend(chunk[:max(room, 0)])
        if len(chunk) > room:
            flags.append(name)
    sink[name] = bytes(kept)


def stop_unit(unit, run=subprocess.run):
    """Kill and stop only this build unit; never touch any other process."""
    for argv in ([SYSTEMCTL, "kill", "--kill-whom=all", "--signal=SIGKILL", unit],
                 [SYSTEMCTL, "stop", unit]):
        try:
            run(argv, capture_output=True, timeout=STOP_SECONDS, check=False, shell=False)
        except (OSError, subprocess.SubprocessError):
            pass


def unit_is_gone(unit, run=subprocess.run, cgroup_root=CGROUP_ROOT):
    try:
        completed = run([SYSTEMCTL, "is-active", unit], capture_output=True,
                        timeout=15, check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        return False
    state = completed.stdout.decode("ascii", "replace").strip()
    return state in {"inactive", "failed", "unknown"} and not (
        Path(cgroup_root) / "system.slice" / unit).exists()


def run_unit(request_id, argv, user, *, popen=subprocess.Popen, run=subprocess.run,
             cgroup_root=CGROUP_ROOT, wall_seconds=CPU_PROFILE["wall_seconds"],
             readback_fn=readback):
    """Start the unit, gate it on readback, and wait for its bounded completion."""
    unit = unit_name(request_id)
    outcome = {"unit": unit, "readback": None, "go_sent": False, "timed_out": False,
               "exit_code": None, "runner_stdout": b"", "runner_stderr": b"",
               "truncated": [], "unit_gone": False}
    started = time.monotonic()
    process = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, shell=False)
    sink, flags = {}, []
    readers = [threading.Thread(target=_bounded_reader, daemon=True,
                                args=(process.stdout, MAX_RUNNER_STDOUT, sink, flags, "stdout")),
               threading.Thread(target=_bounded_reader, daemon=True,
                                args=(process.stderr, MAX_RUNNER_STDERR, sink, flags, "stderr"))]
    for reader in readers:
        reader.start()
    try:
        try:
            outcome["readback"] = readback_fn(unit, user, run=run, cgroup_root=cgroup_root,
                                              runtime_seconds=max(wall_seconds, 1))
        except Exception as error:
            outcome["readback"] = {"verified": False, "failure": "READBACK_ERROR:" + type(error).__name__}
        try:
            if outcome["readback"].get("verified") is True:
                process.stdin.write(GO)
                outcome["go_sent"] = True
            process.stdin.close()
        except OSError:
            outcome["go_sent"] = False
        # Unit RuntimeMaxSec enforces the build deadline; this only bounds waiting.
        remaining = wall_seconds + 10 - (time.monotonic() - started)
        try:
            outcome["exit_code"] = process.wait(timeout=max(remaining, 1))
        except subprocess.TimeoutExpired:
            outcome["timed_out"] = True
    finally:
        if outcome["timed_out"] or outcome["exit_code"] is None or not outcome["go_sent"]:
            stop_unit(unit, run)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=STOP_SECONDS)
        for reader in readers:
            reader.join(timeout=5)
        if not any(reader.is_alive() for reader in readers):
            process.stdout.close()
            process.stderr.close()
        else:
            flags.append("reader-stalled")  # Fails the build closed; never a PASS.
        outcome["runner_stdout"] = sink.get("stdout", b"")
        outcome["runner_stderr"] = sink.get("stderr", b"")
        outcome["truncated"] = sorted(set(flags))
        outcome["unit_gone"] = unit_is_gone(unit, run, cgroup_root)
    return outcome
