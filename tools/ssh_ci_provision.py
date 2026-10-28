#!/usr/bin/env python3
"""Owner-run root provisioning for the CPU build broker on the model VM.

``plan`` is read-only: preflight checks plus the exact file, user, and command
list. ``apply`` performs only that plan inside an owner-approved window and
writes a receipt. ``uninstall`` removes the SSH entry point and socket while
keeping claims, receipts, and permits. Nothing here touches the GPU, the model
process, the administrator account, or sshd settings for any other user; the
admin user's effective sshd configuration is compared before and after, and any
change rolls the drop-in back. The broker socket is enabled only after sshd has
accepted and reloaded the drop-in; from the first sshd write to the receipt,
Ctrl-C and a dropped session are ignored and any failure removes the SSH entry
point and disables the socket again. Provisioning is not qualification.
"""

import argparse
import base64
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ssh_ci_broker import CONFIG_SCHEMA, BrokerError, parse_config, stage_source
from tools.ssh_ci_contract import ContractError, canonical
from tools.ssh_ci_owner_signature import NAMESPACE

try:
    import pwd
except ImportError:  # Windows can render and test files; it can never apply them.
    pwd = None


MANAGED = "# Managed by szl-skills tools/ssh_ci_provision.py; do not edit by hand.\n"
CI_USER = "szl-ci"
BUILD_USER = "szl-build"
PYTHON = "/usr/bin/python3"
INSTALL_ROOT = "/opt/szl-cpu-broker"
CURRENT = INSTALL_ROOT + "/current"
CONFIG_PATH = "/etc/szl-cpu-broker/broker.json"
SOCKET_PATH = "/run/szl-cpu-broker.sock"
RELAY_COMMAND = PYTHON + " -I -B " + CURRENT + "/tools/ssh_ci_relay.py"
BROKER_COMMAND = (PYTHON + " -I -B " + CURRENT + "/tools/ssh_ci_broker.py --config "
                  + CONFIG_PATH)
SSHD = "/usr/sbin/sshd"
SYSTEMCTL = "/usr/bin/systemctl"
USERADD = "/usr/sbin/useradd"
KEY_TYPES = frozenset({"ssh-ed25519", "sk-ssh-ed25519@openssh.com", "ecdsa-sha2-nistp256",
                       "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa"})
REQUIRED_TOOLS = ("/usr/bin/systemd-run", SYSTEMCTL, "/usr/bin/git", "/usr/bin/mount",
                  "/usr/bin/umount", SSHD, "/usr/bin/ssh-keygen", USERADD, PYTHON)
DIRECTORIES = (
    ("/etc/szl-cpu-broker", 0o755), ("/etc/szl-cpu-broker/permits", 0o700),
    ("/etc/ssh/szl-ci", 0o755), (INSTALL_ROOT, 0o755), (INSTALL_ROOT + "/releases", 0o755),
    ("/var/lib/szl-cpu-broker", 0o755), ("/var/lib/szl-cpu-broker/claims", 0o700),
    ("/var/lib/szl-cpu-broker/receipts", 0o700), ("/var/lib/szl-cpu-broker/public", 0o755),
    ("/var/lib/szl-cpu-broker/qualification", 0o700), ("/run/szl-cpu-build", 0o700),
)
SSHD_FILES = ("/etc/ssh/sshd_config.d/60-szl-cpu-ci.conf", "/etc/ssh/szl-ci/authorized_keys")
# Ctrl-C over ``ssh -t`` and a dropped session (SIGHUP through sudo); Windows has no SIGHUP.
INTERRUPTS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGHUP") if hasattr(signal, name))
SSHD_EXPECTED = {
    "forcecommand": RELAY_COMMAND, "authorizedkeysfile": "/etc/ssh/szl-ci/authorized_keys",
    "authenticationmethods": "publickey", "passwordauthentication": "no",
    "kbdinteractiveauthentication": "no", "permittty": "no", "disableforwarding": "yes",
    "allowtcpforwarding": "no", "allowagentforwarding": "no", "x11forwarding": "no",
    "permittunnel": "no", "permituserrc": "no", "maxsessions": "1",
}


class ProvisionError(RuntimeError):
    """Provisioning refused or failed; the message names the stage."""


def public_key(text, label):
    """Return one normalized ``type base64`` OpenSSH public key, or refuse."""
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] not in KEY_TYPES or "\n" in text.strip():
        raise ProvisionError(label + " must be one OpenSSH public key line")
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as error:
        raise ProvisionError(label + " has invalid base64") from error
    length = int.from_bytes(blob[:4], "big")
    if blob[4:4 + length].decode("ascii", "replace") != fields[0]:
        raise ProvisionError(label + " type does not match its encoded key")
    return fields[0] + " " + fields[1]


def render(options):
    """Return {absolute path: (bytes, mode)} for every managed file."""
    ci_key = public_key(options["ci_public_key"], "CI public key")
    owner_key = public_key(options["owner_public_key"], "owner public key")
    principal = options["owner_principal"]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}", principal) is None:
        raise ProvisionError("owner principal must be a plain identifier")
    if ci_key == owner_key:
        raise ProvisionError("the CI key must never be the owner signing key")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", options["admin_user"] or "") is None:
        raise ProvisionError("--admin-user (the owner's VM login, which may read receipts) "
                             "is required")
    config = {
        "schema": CONFIG_SCHEMA, "owner_principal": principal,
        "allowed_signers": "/etc/szl-cpu-broker/allowed_signers",
        "permit_dir": "/etc/szl-cpu-broker/permits",
        "claim_dir": "/var/lib/szl-cpu-broker/claims",
        "receipt_dir": "/var/lib/szl-cpu-broker/receipts",
        "public_receipt_dir": "/var/lib/szl-cpu-broker/public", "run_root": "/run/szl-cpu-build",
        "build_user": BUILD_USER, "receipt_reader": options["admin_user"],
        "build_python": options["build_python"],
        "runner": CURRENT + "/tools/ssh_ci_step_runner.py",
        "nvidia_smi": options["nvidia_smi"], "hidden_paths": sorted(options["hidden_paths"]),
    }
    parse_config(canonical(config))
    sshd = MANAGED + "Match User " + CI_USER + "\n" + "".join("    %s\n" % line for line in (
        "AuthorizedKeysFile /etc/ssh/szl-ci/authorized_keys", "AuthorizedKeysCommand none",
        "AuthorizedPrincipalsFile none", "ForceCommand " + RELAY_COMMAND,
        "AuthenticationMethods publickey", "PubkeyAuthentication yes",
        "PasswordAuthentication no", "KbdInteractiveAuthentication no",
        "DisableForwarding yes", "AllowTcpForwarding no", "AllowStreamLocalForwarding no",
        "AllowAgentForwarding no", "X11Forwarding no", "PermitTunnel no", "PermitTTY no",
        "PermitUserRC no", "PermitOpen none", "PermitListen none", "GatewayPorts no",
        "MaxSessions 1", "ClientAliveInterval 15", "ClientAliveCountMax 4")) + "Match all\n"
    socket_unit = MANAGED + (
        "[Unit]\nDescription=SZL CPU build broker socket (one request per connection)\n\n"
        "[Socket]\nListenStream=" + SOCKET_PATH + "\nSocketUser=root\nSocketGroup=" + CI_USER
        + "\nSocketMode=0660\nAccept=yes\nMaxConnections=1\nRemoveOnStop=yes\n\n"
        "[Install]\nWantedBy=sockets.target\n")
    service_unit = MANAGED + (
        "[Unit]\nDescription=SZL CPU build broker (one request)\nCollectMode=inactive-or-failed\n\n"
        "[Service]\nType=simple\nExecStart=" + BROKER_COMMAND + "\nStandardInput=socket\n"
        "StandardOutput=socket\nStandardError=journal\nUser=root\nNoNewPrivileges=yes\n"
        "RuntimeMaxSec=900\nKillMode=control-group\n"
        "# A host-wide OOM should take the broker before the model processes.\n"
        "OOMScoreAdjust=500\n"
        "# No PrivateTmp/Protect*/Private*: the broker must share the host mount\n"
        "# namespace so its per-run tmpfs is visible to the build unit it starts.\n")
    return {
        "/etc/ssh/sshd_config.d/60-szl-cpu-ci.conf": (sshd.encode("ascii"), 0o644),
        "/etc/ssh/szl-ci/authorized_keys": ((MANAGED + 'restrict,command="' + RELAY_COMMAND
                                             + '" ' + ci_key + " szl-cpu-ci\n").encode("ascii"), 0o644),
        "/etc/szl-cpu-broker/allowed_signers": ((MANAGED + principal + ' namespaces="' + NAMESPACE
                                                 + '" ' + owner_key + "\n").encode("ascii"), 0o644),
        CONFIG_PATH: (json.dumps(config, indent=2, sort_keys=True).encode("ascii") + b"\n", 0o644),
        "/etc/systemd/system/szl-cpu-broker.socket": (socket_unit.encode("ascii"), 0o644),
        "/etc/systemd/system/szl-cpu-broker@.service": (service_unit.encode("ascii"), 0o644),
    }


def _managed(path, data):
    if path == CONFIG_PATH:
        try:
            return json.loads(data).get("schema") == CONFIG_SCHEMA
        except ValueError:
            return False
    return data.startswith(MANAGED.encode("ascii"))


def file_actions(files, root="/"):
    """Classify each managed file as create, unchanged, replace, or refuse."""
    def irregular(item):
        return item.is_symlink() or (item.exists() and not item.is_file())
    actions = []
    for path, (data, mode) in sorted(files.items()):
        target = Path(root) / path.lstrip("/")
        entry = {"path": path, "mode": oct(mode), "sha256": hashlib.sha256(data).hexdigest()}
        # A regular leftover temporary is replaced on write; anything else there is refused.
        if irregular(target) or irregular(Path(str(target) + ".szl-tmp")):
            entry["action"] = "refuse"
        elif not target.exists():
            entry["action"] = "create"
        else:
            current = target.read_bytes()
            entry["pre_sha256"] = hashlib.sha256(current).hexdigest()
            entry["action"] = ("unchanged" if current == data
                               else "replace" if _managed(path, current) else "refuse")
        actions.append(entry)
    return actions


def preflight(options, *, run=subprocess.run, root="/"):
    """Read-only host checks; return a list of failures (empty means ready)."""
    failures = []
    base = Path(root)
    if hasattr(os, "geteuid") and os.geteuid() != 0 and root == "/":
        failures.append("must run as root")
    def check(path, accept, message):
        try:
            if not accept((base / path).read_text()):
                failures.append(message)
        except OSError as error:
            failures.append(message + " (" + path + ": " + type(error).__name__ + ")")
    check("proc/1/comm", lambda text: text.strip() == "systemd", "PID 1 is not systemd")
    check("sys/fs/cgroup/cgroup.controllers",
          lambda text: {"cpu", "memory", "pids"} <= set(text.split()),
          "cgroup v2 cpu/memory/pids controllers are unavailable")
    check("etc/ssh/sshd_config",
          lambda text: re.search(r"^Include /etc/ssh/sshd_config\.d/\*\.conf\s*$", text, re.M),
          "sshd_config does not include sshd_config.d")
    for tool in (*REQUIRED_TOOLS, options["build_python"], options["nvidia_smi"]):
        if not (base / tool.lstrip("/")).exists():
            failures.append("missing " + tool)
    try:
        version = run([SYSTEMCTL, "--version"], capture_output=True, timeout=15, check=False)
        match = re.match(rb"systemd (\d+)", version.stdout)
        if not match or int(match.group(1)) < 248:
            failures.append("systemd 248 or newer is required (PrivateIPC, ProtectProc)")
        python = run([options["build_python"], "-I", "-c",
                      "import sys; print(sys.version_info >= (3, 10))"],
                     capture_output=True, timeout=15, check=False)
        if python.stdout.strip() != b"True":
            failures.append("build python must be 3.10 or newer")
    except (OSError, subprocess.SubprocessError) as error:
        failures.append("version probe failed: " + type(error).__name__)
    return failures


def plan(options, *, run=subprocess.run, root="/"):
    files = render(options)
    actions = file_actions(files, root)
    users = []
    # The CI home is "/" only so sshd can chdir quietly; the account owns nothing.
    for name, shell, home in ((CI_USER, "/bin/sh", "/"),
                              (BUILD_USER, "/usr/sbin/nologin", "/nonexistent")):
        if pwd is None:
            raise ProvisionError("provisioning requires a POSIX host")
        try:
            entry = pwd.getpwnam(name)
            state = "exists" if entry.pw_uid not in (0,) and entry.pw_shell == shell else "refuse"
        except KeyError:
            state = "create"
        users.append({"user": name, "shell": shell, "home": home, "action": state})
    failures = preflight(options, run=run, root=root)
    failures += ["refuse " + entry["path"] for entry in actions if entry["action"] == "refuse"]
    failures += ["refuse user " + entry["user"] for entry in users if entry["action"] == "refuse"]
    return {
        "schema": "szl.ssh-cpu-build.provision-plan.v1", "evidence_class": "MEASURED",
        "release_sha": options["release_sha"], "ready": not failures, "failures": failures,
        "users": users, "directories": [{"path": p, "mode": oct(m)} for p, m in DIRECTORIES],
        "files": actions, "release": INSTALL_ROOT + "/releases/" + options["release_sha"],
        "commands": ["systemctl daemon-reload", "sshd -t", "systemctl reload ssh",
                     "systemctl enable --now szl-cpu-broker.socket"],
        "untouched": ["GPU and model processes", "administrator account",
                      "sshd settings for other users", "existing services"],
    }


@contextlib.contextmanager
def _stage(name):
    """Turn an OS, subprocess, or broker failure into a refusal that names its stage."""
    try:
        yield
    except (BrokerError, ContractError, subprocess.SubprocessError, OSError) as error:
        raise ProvisionError("%s failed: %s: %s" % (name, type(error).__name__, error)) from error


def _write(path, data, mode):
    temporary = Path(str(path) + ".szl-tmp")
    with _stage("write " + str(path)):
        try:
            leftover = temporary.lstat()
        except FileNotFoundError:
            leftover = None
        if leftover is not None:  # An interrupted earlier write; never follow or reuse it.
            if not stat.S_ISREG(leftover.st_mode):
                raise ProvisionError("refuse " + str(temporary) + ": not a regular file")
            temporary.unlink()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, mode)
        os.replace(temporary, path)


def _remove(path):
    """Remove a managed file and any temporary an interrupted write left beside it."""
    for item in (Path(path), Path(str(path) + ".szl-tmp")):
        item.unlink(missing_ok=True)


def _checked(run, argv, stage):
    with _stage(stage):
        completed = run(argv, capture_output=True, timeout=120, check=False)
    if completed.returncode != 0:
        raise ProvisionError(stage + " failed: " + completed.stderr.decode("utf-8", "replace")[-400:])
    return completed.stdout.decode("utf-8", "replace")


def _ssh_active(run):
    with _stage("systemctl is-active ssh"):
        active = run([SYSTEMCTL, "is-active", "ssh.service"], capture_output=True, timeout=15,
                     check=False)
    return active.stdout.strip() == b"active"


def effective_sshd(run, user):
    output = _checked(run, [SSHD, "-T", "-C", "user=%s,host=localhost,addr=127.0.0.1" % user],
                      "sshd -T")
    return dict(line.split(" ", 1) for line in output.splitlines() if " " in line)


def install_release(sha, run):
    release = Path(INSTALL_ROOT) / "releases" / sha
    if not release.exists():
        staging = Path(str(release) + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        stage_source(staging, sha, run)
        shutil.rmtree(staging / ".git")
        for directory, subdirectories, names in os.walk(staging):
            for name in [*subdirectories, *names]:
                item = Path(directory) / name
                os.lchown(item, 0, 0)
                if not item.is_symlink():  # Keep checkout modes; drop group/other write.
                    os.chmod(item, stat.S_IMODE(item.lstat().st_mode) & ~0o022)
        os.chown(staging, 0, 0)
        os.chmod(staging, stat.S_IMODE(staging.lstat().st_mode) & ~0o022)
        staging.rename(release)
    link = Path(CURRENT + ".szl-tmp")
    if link.is_symlink():
        link.unlink()
    os.symlink("releases/" + sha, link)
    os.replace(link, CURRENT)
    return str(release)


def _rollback(run, socket_enabled):
    """Remove the SSH entry point, then the socket once enabled; return what was not undone."""
    incomplete = []
    for path in SSHD_FILES:
        try:
            _remove(path)
        except OSError as error:
            incomplete.append("remove %s: %s" % (path, type(error).__name__))
    if socket_enabled:
        try:
            _checked(run, [SYSTEMCTL, "disable", "--now", "szl-cpu-broker.socket"], "disable socket")
        except ProvisionError as error:
            incomplete.append(str(error))
    return incomplete


def apply(options, *, run=subprocess.run):
    report = plan(options, run=run)
    if not report["ready"]:
        raise ProvisionError("plan is not ready: " + "; ".join(report["failures"]))
    files = render(options)
    admin_before = effective_sshd(run, options["admin_user"])
    for entry in report["users"]:
        if entry["action"] == "create":
            _checked(run, [USERADD, "--system", "--user-group", "--shell", entry["shell"],
                           "--home-dir", entry["home"], "--no-create-home", entry["user"]],
                     "useradd " + entry["user"])
    with _stage("create directories"):
        for path, mode in DIRECTORIES:
            os.makedirs(path, mode=mode, exist_ok=True)
            os.chown(path, 0, 0)
            os.chmod(path, mode)
    with _stage("install release"):
        release = install_release(options["release_sha"], run)
    for path, (data, mode) in sorted(files.items()):
        if path not in SSHD_FILES:
            _write(path, data, mode)
    _checked(run, [SYSTEMCTL, "daemon-reload"], "daemon-reload")
    # From the first sshd write to the receipt, neither Ctrl-C nor a dropped ``ssh -t``
    # session can stop apply halfway, and any failure (even an interrupt raised as an
    # exception) removes the SSH entry point and, once enabled, the socket. The socket is
    # enabled only after sshd accepted, enforced, and reloaded the drop-in.
    enabled = False
    previous = {number: signal.signal(number, signal.SIG_IGN) for number in INTERRUPTS}
    try:
        for path in SSHD_FILES:
            _write(path, *files[path])
        _checked(run, [SSHD, "-t"], "sshd -t")
        ci = effective_sshd(run, CI_USER)
        wrong = sorted(key for key, value in SSHD_EXPECTED.items() if ci.get(key) != value)
        if wrong:
            raise ProvisionError("szl-ci sshd settings are not enforced: " + ", ".join(wrong))
        if effective_sshd(run, options["admin_user"]) != admin_before:
            raise ProvisionError("drop-in changed the administrator's sshd settings")
        if _ssh_active(run):
            _checked(run, [SYSTEMCTL, "reload", "ssh.service"], "reload ssh")
        enabled = True  # Set first: a failed or timed-out enable may still have enabled it.
        _checked(run, [SYSTEMCTL, "enable", "--now", "szl-cpu-broker.socket"], "enable socket")
        receipt = dict(report, schema="szl.ssh-cpu-build.provision-receipt.v1",
                       applied_at=datetime.now(timezone.utc).isoformat(), release=release,
                       owner_window_ref=options["owner_window_ref"],
                       socket=SOCKET_PATH, qualification="NOT_RUN", signed=False)
        name = "provision-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
        _write("/var/lib/szl-cpu-broker/" + name, canonical(receipt) + b"\n", 0o600)
    except BaseException as error:
        incomplete = _rollback(run, enabled)
        if incomplete:
            raise ProvisionError("%s (%s); rollback incomplete: %s" % (
                type(error).__name__, error, "; ".join(incomplete))) from error
        raise
    finally:
        for number, handler in previous.items():
            if handler is not None:  # None: set outside Python; leave it ignored.
                signal.signal(number, handler)
    return receipt


def uninstall(*, run=subprocess.run):
    """Remove the SSH entry point and socket; keep claims, receipts, and permits."""
    for path in SSHD_FILES:
        _remove(path)
    _checked(run, [SSHD, "-t"], "sshd -t")
    if _ssh_active(run):
        _checked(run, [SYSTEMCTL, "reload", "ssh.service"], "reload ssh")
    run([SYSTEMCTL, "disable", "--now", "szl-cpu-broker.socket"], capture_output=True,
        timeout=60, check=False)
    for path in ("/etc/systemd/system/szl-cpu-broker.socket",
                 "/etc/systemd/system/szl-cpu-broker@.service"):
        Path(path).unlink(missing_ok=True)
    _checked(run, [SYSTEMCTL, "daemon-reload"], "daemon-reload")
    return {"removed": ["sshd drop-in", "authorized key", "broker socket and units"],
            "kept": ["/var/lib/szl-cpu-broker (claims, receipts)", "/etc/szl-cpu-broker",
                     INSTALL_ROOT, "users " + CI_USER + " and " + BUILD_USER]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "uninstall"))
    parser.add_argument("--release-sha")
    parser.add_argument("--ci-public-key", type=Path, help="public half of the dedicated CI key")
    parser.add_argument("--owner-public-key", type=Path, help="owner permit-signing public key")
    parser.add_argument("--owner-principal", default="stephen")
    parser.add_argument("--admin-user", default=os.environ.get("SUDO_USER", ""))
    parser.add_argument("--build-python", default=PYTHON)
    parser.add_argument("--nvidia-smi", default="/usr/bin/nvidia-smi")
    parser.add_argument("--hidden-path", action="append", default=[],
                        help="extra model/data path to hide from builds (repeatable)")
    parser.add_argument("--owner-window-ref", help="owner approval reference recorded in the receipt")
    args = parser.parse_args(argv)
    try:
        if args.action == "uninstall":
            result = uninstall()
        else:
            if re.fullmatch(r"[0-9a-f]{40}", args.release_sha or "") is None:
                raise ProvisionError("--release-sha must be a full lowercase commit")
            if not args.ci_public_key or not args.owner_public_key:
                raise ProvisionError("--ci-public-key and --owner-public-key are required")
            options = {"release_sha": args.release_sha,
                       "ci_public_key": args.ci_public_key.read_text(encoding="ascii"),
                       "owner_public_key": args.owner_public_key.read_text(encoding="ascii"),
                       "owner_principal": args.owner_principal, "admin_user": args.admin_user,
                       "build_python": args.build_python, "nvidia_smi": args.nvidia_smi,
                       "hidden_paths": args.hidden_path, "owner_window_ref": args.owner_window_ref}
            if args.action == "plan":
                result = plan(options)
            else:
                if not args.admin_user or not args.owner_window_ref:
                    raise ProvisionError("apply needs --admin-user and --owner-window-ref")
                result = apply(options)
    except (ProvisionError, BrokerError, ContractError, subprocess.SubprocessError,
            OSError) as error:
        stage = "" if isinstance(error, ProvisionError) else "%s: %s: " % (
            args.action, type(error).__name__)
        print("PROVISION BLOCKED: " + stage + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ready", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
