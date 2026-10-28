"""Offline provisioning tests; real sshd validates the drop-in when installed.

``apply`` runs with every user, file, sshd, and systemd step faked and recorded.
"""

import contextlib
import io
import os
from pathlib import Path
import posixpath
import shutil
import signal
import stat
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from tools import ssh_ci_broker as broker
from tools import ssh_ci_contract as contract
from tools import ssh_ci_provision as provision


SSHD = shutil.which("sshd") or ("/usr/sbin/sshd" if Path("/usr/sbin/sshd").exists() else None)
SSH_KEYGEN = shutil.which("ssh-keygen")
CI_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g ci@example"
OWNER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGVmZ2hpamtsbW5vcHFyc3R1dnd4eXp7fH1+f4CBgoOE stephen"
ADMIN_SSHD = b"passwordauthentication yes\npermitrootlogin no\n"
CI_SSHD = "".join("%s %s\n" % item for item in provision.SSHD_EXPECTED.items()).encode("ascii")
REMOVE = ["remove " + path for path in provision.SSHD_FILES]
DISABLE = ["systemctl disable --now szl-cpu-broker.socket"]
# Everything from the first sshd write on runs with Ctrl-C and SIGHUP ignored.
WINDOW = ["write " + path for path in provision.SSHD_FILES] + [
    "sshd -t", "sshd -T szl-ci", "sshd -T Ubuntu again", "systemctl is-active ssh.service",
    "systemctl reload ssh.service", "systemctl enable --now szl-cpu-broker.socket", "write receipt"]


def options(**overrides):
    value = {"release_sha": "a" * 40, "ci_public_key": CI_KEY, "owner_public_key": OWNER_KEY,
             "owner_principal": "stephen", "admin_user": "Ubuntu",
             "build_python": "/usr/bin/python3", "nvidia_smi": "/usr/bin/nvidia-smi",
             "hidden_paths": ["/data/models"], "owner_window_ref": "owner://window/1"}
    value.update(overrides)
    return value


class RenderTests(unittest.TestCase):
    def test_public_keys_are_single_validated_lines(self):
        self.assertEqual(provision.public_key(CI_KEY, "k"), " ".join(CI_KEY.split()[:2]))
        for bad in ("", "ssh-ed25519", "ssh-dss AAAA", "ssh-ed25519 !!!", CI_KEY + "\n" + CI_KEY,
                    "ssh-rsa " + CI_KEY.split()[1]):
            with self.subTest(bad=bad[:30]), self.assertRaises(provision.ProvisionError):
                provision.public_key(bad, "k")

    def test_rendered_files_bind_forced_command_signer_namespace_and_config(self):
        files = provision.render(options())
        keys = files["/etc/ssh/szl-ci/authorized_keys"][0].decode()
        self.assertIn('restrict,command="' + provision.RELAY_COMMAND + '" ssh-ed25519 ', keys)
        signers = files["/etc/szl-cpu-broker/allowed_signers"][0].decode()
        self.assertIn('stephen namespaces="szl-cpu-permit-v1" ssh-ed25519 ', signers)
        config = broker.parse_config(files[provision.CONFIG_PATH][0])
        self.assertEqual(config["build_user"], "szl-build")
        self.assertEqual(config["hidden_paths"], ["/data/models"])
        dropin = files["/etc/ssh/sshd_config.d/60-szl-cpu-ci.conf"][0].decode()
        self.assertTrue(dropin.startswith(provision.MANAGED + "Match User szl-ci\n"))
        self.assertTrue(dropin.endswith("Match all\n"))
        service = files["/etc/systemd/system/szl-cpu-broker@.service"][0].decode()
        self.assertIn("StandardInput=socket", service)
        self.assertIn("\nOOMScoreAdjust=500\n", service)
        for namespacing in ("\nPrivateTmp", "\nProtectSystem", "\nPrivateDevices"):
            self.assertNotIn(namespacing, service)
        socket_unit = files["/etc/systemd/system/szl-cpu-broker.socket"][0].decode()
        for line in ("SocketGroup=szl-ci", "SocketMode=0660", "Accept=yes", "MaxConnections=1"):
            self.assertIn(line, socket_unit)

    def test_owner_key_reuse_and_bad_principal_are_refused(self):
        for change in ({"ci_public_key": OWNER_KEY}, {"owner_principal": "a b"},
                       {"build_python": "python3"}, {"hidden_paths": ["relative"]}):
            with self.subTest(change=change), self.assertRaises(Exception):
                provision.render(options(**change))

    def test_file_actions_never_overwrite_unmanaged_or_symlinked_files(self):
        files = provision.render(options())
        with tempfile.TemporaryDirectory() as root:
            first = {entry["path"]: entry["action"] for entry in provision.file_actions(files, root)}
            self.assertEqual(set(first.values()), {"create"})
            for path, (data, _mode) in files.items():
                target = Path(root) / path.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            unchanged = {e["action"] for e in provision.file_actions(files, root)}
            self.assertEqual(unchanged, {"unchanged"})
            socket_path = Path(root) / "etc/systemd/system/szl-cpu-broker.socket"
            socket_path.write_bytes((provision.MANAGED + "[Socket]\n").encode("ascii"))
            sshd_path = Path(root) / "etc/ssh/sshd_config.d/60-szl-cpu-ci.conf"
            sshd_path.write_bytes(b"PermitRootLogin yes\n")
            signers = Path(root) / "etc/szl-cpu-broker/allowed_signers"
            signers.unlink()
            if os.name == "posix":
                os.symlink("/etc/passwd", signers)
            actions = {e["path"]: e["action"] for e in provision.file_actions(files, root)}
            self.assertEqual(actions["/etc/systemd/system/szl-cpu-broker.socket"], "replace")
            self.assertEqual(actions["/etc/ssh/sshd_config.d/60-szl-cpu-ci.conf"], "refuse")
            if os.name == "posix":
                self.assertEqual(actions["/etc/szl-cpu-broker/allowed_signers"], "refuse")

    def test_file_actions_refuse_a_leftover_temporary_that_is_not_a_regular_file(self):
        files = provision.render(options())
        with tempfile.TemporaryDirectory() as root:
            keys = Path(root) / "etc/ssh/szl-ci/authorized_keys.szl-tmp"
            keys.mkdir(parents=True)
            signers = Path(root) / "etc/szl-cpu-broker/allowed_signers.szl-tmp"
            signers.parent.mkdir(parents=True)
            signers.write_bytes(b"partial")  # A regular leftover is replaced by _write.
            if os.name == "posix":
                os.symlink("/etc/passwd", Path(root) / "etc/szl-cpu-broker/broker.json.szl-tmp")
            actions = {e["path"]: e["action"] for e in provision.file_actions(files, root)}
        self.assertEqual(actions["/etc/ssh/szl-ci/authorized_keys"], "refuse")
        self.assertEqual(actions["/etc/szl-cpu-broker/allowed_signers"], "create")
        if os.name == "posix":
            self.assertEqual(actions[provision.CONFIG_PATH], "refuse")

    def test_preflight_reports_every_missing_prerequisite(self):
        def run(argv, **_kwargs):
            output = b"systemd 245 (245.4)\n" if argv[-1] == "--version" else b"False\n"
            return subprocess.CompletedProcess(argv, 0, output, b"")
        with tempfile.TemporaryDirectory() as root:
            failures = provision.preflight(options(), run=run, root=root)
        joined = "\n".join(failures)
        for expected in ("PID 1 is not systemd", "cgroup v2", "sshd_config does not include",
                         "missing /usr/bin/systemd-run",
                         "systemd 248", "python must be 3.10"):
            self.assertIn(expected, joined)


class FileWriteTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX writes only")
    def test_write_replaces_a_regular_leftover_temporary_and_refuses_anything_else(self):
        with tempfile.TemporaryDirectory() as scratch, mock.patch.object(provision.os, "chown"):
            target = Path(scratch) / "authorized_keys"
            leftover = Path(str(target) + ".szl-tmp")
            leftover.write_bytes(b"half")
            provision._write(str(target), b"whole\n", 0o600)
            self.assertEqual(target.read_bytes(), b"whole\n")
            self.assertFalse(os.path.lexists(leftover))
            decoy = Path(scratch) / "decoy"
            decoy.write_bytes(b"keep")
            for make, undo in ((leftover.mkdir, leftover.rmdir),
                               (lambda: os.symlink(decoy, leftover), leftover.unlink)):
                make()
                with self.assertRaisesRegex(provision.ProvisionError, "not a regular file"):
                    provision._write(str(target), b"other\n", 0o600)
                self.assertEqual(target.read_bytes(), b"whole\n")
                self.assertEqual(decoy.read_bytes(), b"keep")
                undo()

    def test_managed_files_are_root_only_except_the_ci_key_file(self):
        modes = {path: mode for path, (_data, mode) in provision.render(options()).items()}
        keys = "/etc/ssh/szl-ci/authorized_keys"
        self.assertEqual(modes.pop(keys), 0o640)
        self.assertEqual(set(modes.values()), {0o600})
        self.assertEqual(provision.FILE_GROUPS, {keys: provision.CI_USER})

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX writes only")
    def test_ci_key_file_is_group_owned_by_szl_ci_and_a_missing_group_writes_nothing(self):
        keys = "/etc/ssh/szl-ci/authorized_keys"
        with tempfile.TemporaryDirectory() as scratch:
            target = Path(scratch) / "authorized_keys"
            owners = []
            group = mock.Mock(gr_gid=4321)
            with mock.patch.dict(provision.FILE_GROUPS, {str(target): provision.CI_USER}), \
                    mock.patch("grp.getgrnam", return_value=group) as lookup, \
                    mock.patch.object(provision.os, "chown",
                                      side_effect=lambda _path, uid, gid: owners.append((uid, gid))):
                provision._write(str(target), b"key\n", 0o640)
                lookup.assert_called_once_with(provision.CI_USER)
                self.assertEqual(owners, [(0, 4321)])
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
                missing = Path(scratch) / "missing"
                with mock.patch.dict(provision.FILE_GROUPS, {str(missing): "no-such-group"}), \
                        mock.patch("grp.getgrnam", side_effect=KeyError("no-such-group")), \
                        self.assertRaisesRegex(provision.ProvisionError, "group no-such-group"):
                    provision._write(str(missing), b"key\n", 0o640)
                self.assertFalse(os.path.lexists(missing))
                self.assertFalse(os.path.lexists(str(missing) + ".szl-tmp"))
            self.assertIn(keys, provision.FILE_GROUPS)
            self.assertEqual(provision._group_id(str(Path(scratch) / "other")), 0)

    def test_remove_clears_the_file_and_its_leftover_temporary(self):
        with tempfile.TemporaryDirectory() as scratch:
            target = Path(scratch) / "60-szl-cpu-ci.conf"
            target.write_bytes(b"drop-in")
            Path(str(target) + ".szl-tmp").write_bytes(b"half")
            provision._remove(str(target))
            self.assertEqual(list(Path(scratch).iterdir()), [])
            provision._remove(str(target))


class ApplyOrderTests(unittest.TestCase):
    """The socket comes last, and every failure after the first sshd write rolls back."""

    def apply(self, faults=None):
        faults = faults or {}
        events, quiet = [], []

        def step(label):
            label += " again" if label in events else ""
            events.append(label)
            quiet.append(all(signal.getsignal(number) == signal.SIG_IGN
                             for number in provision.INTERRUPTS))
            fault = faults.get(label)
            fault = fault() if callable(fault) else fault
            if isinstance(fault, BaseException):
                raise fault
            return fault

        def run(argv, **_kwargs):
            words = [posixpath.basename(argv[0]), *argv[1:]]
            output = b"active\n" if "is-active" in words else b""
            label = " ".join(words)
            if words[:2] == ["sshd", "-T"]:
                user = argv[3].split(",")[0].split("=", 1)[1]
                label, output = "sshd -T " + user, CI_SSHD if user == provision.CI_USER else ADMIN_SSHD
            elif words[0] == "useradd":
                label = "useradd " + argv[-1]
            fault = step(label)
            return subprocess.CompletedProcess(argv, fault if type(fault) is int else 0,
                                               fault if type(fault) is bytes else output, b"boom")

        def write(path, _data, _mode):
            receipt = path.startswith("/var/lib/szl-cpu-broker/provision-")
            step("write receipt" if receipt else "write " + path)

        def missing(_name):
            raise KeyError(_name)
        fakes = {"pwd": types.SimpleNamespace(getpwnam=missing),
                 "preflight": lambda *_args, **_kwargs: [], "DIRECTORIES": (),
                 "file_actions": lambda files, root="/": [{"path": p, "action": "create"}
                                                          for p in sorted(files)],
                 "install_release": lambda sha, _run: step("install release") or "/r/" + sha,
                 "_write": write, "_remove": lambda path: step("remove " + path)}
        before = [signal.getsignal(number) for number in provision.INTERRUPTS]
        with contextlib.ExitStack() as stack:
            for name, value in fakes.items():
                stack.enter_context(mock.patch.object(provision, name, value))
            try:
                outcome = provision.apply(options(), run=run)
            except (Exception, KeyboardInterrupt) as error:
                outcome = error
        self.assertEqual([signal.getsignal(number) for number in provision.INTERRUPTS], before)
        return outcome, events, quiet

    @staticmethod
    def prefix():
        files = provision.render(options())
        return (["sshd -T Ubuntu", "useradd szl-ci", "useradd szl-build", "install release"]
                + ["write " + path for path in sorted(files) if path not in provision.SSHD_FILES]
                + ["systemctl daemon-reload"])

    def test_socket_is_enabled_only_after_sshd_validated_and_reloaded_the_drop_in(self):
        outcome, events, quiet = self.apply()
        self.assertEqual(outcome["qualification"], "NOT_RUN")
        self.assertEqual(events, self.prefix() + WINDOW)
        self.assertEqual(quiet, [False] * len(self.prefix()) + [True] * len(WINDOW))
        positions = [next(i for i, event in enumerate(events) if event.startswith(command))
                     for command in outcome["commands"]]
        self.assertEqual(positions, sorted(positions))

    def test_a_real_interrupt_during_validation_is_ignored(self):
        outcome, events, _quiet = self.apply({"sshd -t": lambda: signal.raise_signal(signal.SIGINT)})
        self.assertIsInstance(outcome, dict, outcome)
        self.assertEqual(events, self.prefix() + WINDOW)

    def test_every_failure_after_the_first_sshd_write_rolls_back(self):
        cases = (
            ("write /etc/ssh/szl-ci/authorized_keys", provision.ProvisionError("write failed"), 2,
             False, provision.ProvisionError),
            ("sshd -t", 1, 3, False, provision.ProvisionError),
            ("sshd -T szl-ci", CI_SSHD.replace(b"permittty no\n", b""), 4, False,
             provision.ProvisionError),
            ("sshd -T szl-ci", KeyboardInterrupt(), 4, False, KeyboardInterrupt),
            ("sshd -T Ubuntu again", b"passwordauthentication no\n", 5, False,
             provision.ProvisionError),
            ("systemctl is-active ssh.service", subprocess.TimeoutExpired(["systemctl"], 15), 6,
             False, provision.ProvisionError),
            ("systemctl reload ssh.service", subprocess.TimeoutExpired(["systemctl"], 120), 7,
             False, provision.ProvisionError),
            ("systemctl enable --now szl-cpu-broker.socket", 1, 8, True, provision.ProvisionError),
            ("systemctl enable --now szl-cpu-broker.socket", KeyboardInterrupt(), 8, True,
             KeyboardInterrupt),
            ("write receipt", OSError("disk full"), 9, True, OSError),
        )
        for label, fault, reached, disabled, kind in cases:
            with self.subTest(label=label, fault=type(fault).__name__):
                outcome, events, quiet = self.apply({label: fault})
                self.assertIsInstance(outcome, kind)
                after = WINDOW[:reached] + REMOVE + (DISABLE if disabled else [])
                self.assertEqual(events, self.prefix() + after)
                self.assertEqual(quiet, [False] * len(self.prefix()) + [True] * len(after))

    def test_incomplete_rollback_is_reported_after_every_undo_step_ran(self):
        outcome, events, _quiet = self.apply({"systemctl enable --now szl-cpu-broker.socket": 1,
                                             REMOVE[0]: PermissionError(13, "denied"),
                                             DISABLE[0]: 1})
        self.assertIsInstance(outcome, provision.ProvisionError)
        self.assertIsInstance(outcome.__cause__, provision.ProvisionError)
        for text in ("enable socket failed", "rollback incomplete",
                     "remove /etc/ssh/sshd_config.d/60-szl-cpu-ci.conf: PermissionError",
                     "disable socket failed"):
            self.assertIn(text, str(outcome))
        self.assertEqual(events[-3:], REMOVE + DISABLE)

    def test_failures_before_the_sshd_window_name_their_stage_and_touch_no_sshd_file(self):
        for label, fault, message in (
                ("install release", broker.BrokerError("SOURCE_STAGING_FAILED"),
                 "install release failed: BrokerError: SOURCE_STAGING_FAILED"),
                ("install release", subprocess.TimeoutExpired(["git"], 30),
                 "install release failed: TimeoutExpired"),
                ("sshd -T Ubuntu", subprocess.TimeoutExpired(["sshd"], 120),
                 "sshd -T failed: TimeoutExpired"),
                ("useradd szl-ci", 1, "useradd szl-ci failed"),
                ("systemctl daemon-reload", FileNotFoundError(2, "missing"),
                 "daemon-reload failed: FileNotFoundError")):
            with self.subTest(label=label, fault=type(fault).__name__):
                outcome, events, quiet = self.apply({label: fault})
                self.assertIsInstance(outcome, provision.ProvisionError)
                self.assertIn(message, str(outcome))
                self.assertEqual(events[-1], label)
                self.assertFalse(any(quiet))
                self.assertFalse([event for event in events if event in WINDOW + REMOVE + DISABLE])


class MainTests(unittest.TestCase):
    def test_broker_subprocess_contract_and_os_errors_print_provision_blocked(self):
        with tempfile.TemporaryDirectory() as scratch:
            ci, owner = Path(scratch) / "ci.pub", Path(scratch) / "owner.pub"
            ci.write_text(CI_KEY + "\n", encoding="ascii")
            owner.write_text(OWNER_KEY + "\n", encoding="ascii")
            argv = ["apply", "--release-sha", "a" * 40, "--ci-public-key", str(ci),
                    "--owner-public-key", str(owner), "--admin-user", "Ubuntu",
                    "--owner-window-ref", "owner://window/1"]
            for error, expected in (
                    (broker.BrokerError("SOURCE_STAGING_FAILED"),
                     "apply: BrokerError: SOURCE_STAGING_FAILED"),
                    (subprocess.TimeoutExpired(["git"], 30), "apply: TimeoutExpired: "),
                    (PermissionError(13, "denied"), "apply: PermissionError: "),
                    (contract.ContractError("bad path"), "apply: ContractError: bad path"),
                    (provision.ProvisionError("sshd -t failed: x"), "sshd -t failed: x")):
                with self.subTest(error=type(error).__name__), \
                        mock.patch.object(provision, "apply", side_effect=error), \
                        contextlib.redirect_stderr(io.StringIO()) as stderr, \
                        contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(provision.main(argv), 1)
                self.assertTrue(stderr.getvalue().startswith("PROVISION BLOCKED: " + expected),
                                stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")
            ci.unlink()
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(provision.main(argv), 1)
            self.assertIn("PROVISION BLOCKED: apply: FileNotFoundError: ", stderr.getvalue())


@unittest.skipUnless(os.name == "posix" and SSHD and SSH_KEYGEN, "OpenSSH server is not installed")
class RealSshdTests(unittest.TestCase):
    def test_dropin_enforces_restrictions_for_ci_user_only(self):
        files = provision.render(options())
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "conf.d").mkdir()
            subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(root / "hostkey")],
                           check=True, capture_output=True, timeout=30)
            main = root / "sshd_config"
            main.write_text("Include %s/conf.d/*.conf\nHostKey %s\nPasswordAuthentication yes\n"
                            % (root, root / "hostkey"))
            baseline = subprocess.run([SSHD, "-t", "-f", str(main)], capture_output=True, timeout=30)
            if baseline.returncode != 0:
                self.skipTest("sshd cannot validate here: " + baseline.stderr.decode()[-120:])

            def effective(user):
                output = subprocess.run(
                    [SSHD, "-T", "-f", str(main), "-C", "user=%s,host=localhost,addr=127.0.0.1" % user],
                    check=True, capture_output=True, text=True, timeout=30).stdout
                return dict(line.split(" ", 1) for line in output.splitlines() if " " in line)
            admin_before = effective("Ubuntu")
            (root / "conf.d" / "60-szl-cpu-ci.conf").write_bytes(
                files["/etc/ssh/sshd_config.d/60-szl-cpu-ci.conf"][0])
            subprocess.run([SSHD, "-t", "-f", str(main)], check=True, capture_output=True, timeout=30)
            ci = effective("szl-ci")
            for key, value in provision.SSHD_EXPECTED.items():
                self.assertEqual(ci.get(key), value, key)
            self.assertEqual(effective("Ubuntu"), admin_before)


if __name__ == "__main__":
    unittest.main()
