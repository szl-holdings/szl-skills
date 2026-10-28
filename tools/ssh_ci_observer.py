"""Broker-side runtime observation for the CPU admission contract.

The trusted broker reads host identity from ``/proc``, GPU compute processes
from ``nvidia-smi``, and running build units from systemd. The result is a
broker DECLARATION in ``szl.ssh-cpu-build.observation.v1`` form, not an
independent witness: an attacker with root on the VM could forge it. Any read
failure raises, so the executor denies execution instead of guessing.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ssh_ci_admission import OBSERVATION_SCHEMA


NVIDIA_SMI = "/usr/bin/nvidia-smi"
SYSTEMCTL = "/usr/bin/systemctl"
UNIT_PATTERN = "szl-cpu-build-*.service"
HOSTNAME = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
COMMAND_TIMEOUT_SECONDS = 15
MAX_COMMAND_BYTES = 65536
RUNNING_STATES = frozenset({"active", "activating", "deactivating", "reloading", "refreshing"})


class ObservationError(RuntimeError):
    """The runtime could not be observed; execution must be denied."""


def _read_text(path, limit=4096):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ObservationError("runtime file exceeds limit: " + str(path))
    return data.decode("ascii").strip()


def host_identity(proc=Path("/proc")):
    """Return the exact hostname and boot ID; never normalize case."""
    hostname = _read_text(Path(proc) / "sys/kernel/hostname")
    boot_id = _read_text(Path(proc) / "sys/kernel/random/boot_id")
    if re.fullmatch(HOSTNAME, hostname) is None:
        raise ObservationError("hostname is outside the receipt contract")
    try:
        if str(uuid.UUID(boot_id)) != boot_id:
            raise ValueError("noncanonical")
    except ValueError as error:
        raise ObservationError("boot_id is not a canonical UUID") from error
    return {"hostname": hostname, "boot_id": boot_id}


def start_ticks(pid, proc=Path("/proc")):
    """Return field 22 (starttime) of ``/proc/<pid>/stat`` as an integer."""
    if type(pid) is not int or pid < 1:
        raise ObservationError("invalid pid")
    with open(Path(proc) / str(pid) / "stat", "rb") as stream:
        data = stream.read(4096)
    try:
        fields = data[data.rindex(b")") + 2:].split()
        value = int(fields[19])
    except (ValueError, IndexError) as error:
        raise ObservationError("cannot parse process start time") from error
    if value < 1:
        raise ObservationError("process start time is not positive")
    return value


def _command(argv, run):
    try:
        completed = run(argv, capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS,
                        check=False, shell=False,
                        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as error:
        raise ObservationError("observer command unavailable: " + argv[0]) from error
    if completed.returncode != 0 or len(completed.stdout) > MAX_COMMAND_BYTES:
        raise ObservationError("observer command failed: " + argv[0])
    try:
        return completed.stdout.decode("ascii")
    except UnicodeDecodeError as error:
        raise ObservationError("observer command output is not ASCII") from error


def gpu_compute_pids(nvidia_smi=NVIDIA_SMI, run=subprocess.run):
    """Return sorted distinct GPU compute PIDs; a failed query is not 'no GPU work'."""
    output = _command([nvidia_smi, "--query-compute-apps=pid",
                       "--format=csv,noheader,nounits"], run)
    pids = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[1-9][0-9]{0,9}", line) is None:
            raise ObservationError("unexpected nvidia-smi process line")
        pids.add(int(line))
    return sorted(pids)


def running_build_units(systemctl=SYSTEMCTL, run=subprocess.run):
    """Return names of build units systemd reports as running or transitioning."""
    output = _command([systemctl, "list-units", "--all", "--plain", "--no-legend",
                       "--no-pager", "--type=service", UNIT_PATTERN], run)
    units = []
    for line in output.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) < 4 or not fields[0].startswith("szl-cpu-build-"):
            raise ObservationError("unexpected systemctl unit line")
        if fields[2] in RUNNING_STATES:
            units.append(fields[0])
    return units


def observe(clock, *, proc=Path("/proc"), nvidia_smi=NVIDIA_SMI, systemctl=SYSTEMCTL,
            run=subprocess.run):
    """Return one fresh observation, stamped after every read completed."""
    host = host_identity(proc)
    processes = [{"pid": pid, "start_ticks": start_ticks(pid, proc)}
                 for pid in gpu_compute_pids(nvidia_smi, run)]
    competing = len(running_build_units(systemctl, run))
    if host_identity(proc) != host:
        raise ObservationError("host identity changed during observation")
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None or now.utcoffset().total_seconds() != 0:
        raise ObservationError("observer clock must be aware UTC")
    return {
        "schema": OBSERVATION_SCHEMA,
        "observed_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": host,
        "gpu_compute_processes": processes,
        "competing_cpu_jobs": competing,
    }


def main():
    """Print one observation; read-only and runnable without root."""
    try:
        value = observe(lambda: datetime.now(timezone.utc))
    except (ObservationError, OSError) as error:
        print("OBSERVATION UNAVAILABLE: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
