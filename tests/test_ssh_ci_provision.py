"""Offline provisioning tests; real sshd validates the drop-in when installed."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools import ssh_ci_broker as broker
from tools import ssh_ci_provision as provision


SSHD = shutil.which("sshd") or ("/usr/sbin/sshd" if Path("/usr/sbin/sshd").exists() else None)
SSH_KEYGEN = shutil.which("ssh-keygen")
CI_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g ci@example"
OWNER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGVmZ2hpamtsbW5vcHFyc3R1dnd4eXp7fH1+f4CBgoOE stephen"


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
            socket_path.write_text(provision.MANAGED + "[Socket]\n")
            sshd_path = Path(root) / "etc/ssh/sshd_config.d/60-szl-cpu-ci.conf"
            sshd_path.write_text("PermitRootLogin yes\n")
            signers = Path(root) / "etc/szl-cpu-broker/allowed_signers"
            signers.unlink()
            if os.name == "posix":
                os.symlink("/etc/passwd", signers)
            actions = {e["path"]: e["action"] for e in provision.file_actions(files, root)}
            self.assertEqual(actions["/etc/systemd/system/szl-cpu-broker.socket"], "replace")
            self.assertEqual(actions["/etc/ssh/sshd_config.d/60-szl-cpu-ci.conf"], "refuse")
            if os.name == "posix":
                self.assertEqual(actions["/etc/szl-cpu-broker/allowed_signers"], "refuse")

    def test_preflight_reports_every_missing_prerequisite(self):
        def run(argv, **_kwargs):
            output = b"systemd 245 (245.4)\n" if argv[-1] == "--version" else b"False\n"
            return subprocess.CompletedProcess(argv, 0, output, b"")
        with tempfile.TemporaryDirectory() as root:
            failures = provision.preflight(options(), run=run, root=root)
        joined = "\n".join(failures)
        for expected in ("host file unreadable", "missing /usr/bin/systemd-run",
                         "systemd 248", "python must be 3.10"):
            self.assertIn(expected, joined)


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
