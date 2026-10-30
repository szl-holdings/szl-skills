"""Offline sandbox tests: fixed unit properties, readback gate, GO ordering."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from tools import ssh_ci_contract as contract
from tools import ssh_ci_sandbox as sandbox


REQUEST_ID = "b58224fe-b0a7-4d81-ac3c-509e3ac46b53"
UNIT = sandbox.unit_name(REQUEST_ID)
SHA = "a" * 40


def properties(**overrides):
    arguments = {"user": "szl-build", "source_dir": "/run/szl-cpu-build/x/src",
                 "output_dir": "/run/szl-cpu-build/x/out"}
    arguments.update(overrides)
    return sandbox.unit_properties(**arguments)


class PropertyTests(unittest.TestCase):
    def test_fixed_profile_and_isolation_properties_are_present(self):
        values = properties(hidden_paths=["/opt/models"])
        for required in ("CPUQuota=100%", "MemoryMax=1073741824", "MemorySwapMax=0",
                         "TasksMax=64", "RuntimeMaxSec=420", "PrivateNetwork=yes",
                         "IPAddressDeny=any", "RestrictAddressFamilies=AF_UNIX",
                         "PrivateDevices=yes", "DevicePolicy=closed", "PrivateIPC=yes",
                         "PrivateUsers=yes", "ProtectSystem=strict", "ProtectHome=yes",
                         "ProtectProc=invisible", "NoNewPrivileges=yes",
                         "CapabilityBoundingSet=", "RestrictNamespaces=yes",
                         "KillMode=control-group", "OOMPolicy=kill",
                         "BindReadOnlyPaths=/run/szl-cpu-build/x/src:/src",
                         "BindPaths=/run/szl-cpu-build/x/out:/out",
                         "InaccessiblePaths=-/opt/models",
                         "InaccessiblePaths=-/etc/szl-cpu-broker",
                         "InaccessiblePaths=-/var/lib", "InaccessiblePaths=-/etc/systemd"):
            self.assertIn(required, values)
        self.assertTrue(any(value.startswith("TemporaryFileSystem=/tmp:") for value in values))
        self.assertFalse(any("PrivateTmp" in value for value in values))
        # Hiding a directory that holds mount sources would break namespace setup.
        for source_parent in ("/run", "/run/systemd", "/run/szl-cpu-build"):
            self.assertNotIn("InaccessiblePaths=-" + source_parent, values)
        deny = values.index("SystemCallFilter=~@privileged @resources @keyring io_uring_setup "
                            "io_uring_enter io_uring_register")
        # clone3 must fail with ENOSYS (glibc then falls back to clone), not EPERM.
        self.assertEqual(values[deny + 1], "SystemCallFilter=~clone3:ENOSYS")
        self.assertIn("SystemCallErrorNumber=EPERM", values)
        self.assertIn("OOMScoreAdjust=1000", values)
        self.assertEqual(sandbox.EXPECTED_PROPERTIES["OOMScoreAdjust"], "1000")

    def test_unit_mount_points_are_never_hidden_and_never_covered(self):
        covered = ["/src", "/src/", "/out/x", "/tmp", "/var/tmp/a", "/dev/shm", "/./out", "//src"]
        values = properties(hidden_paths=[*covered, "/opt/models"])
        self.assertIn("InaccessiblePaths=-/opt/models", values)
        for path in covered:
            with self.subTest(path=path):
                self.assertNotIn("InaccessiblePaths=-" + path, values)
        self.assertEqual(len([value for value in values if value.startswith("InaccessiblePaths=")]),
                         len(sandbox.HIDDEN_ALWAYS) + 1)
        # Hiding an ancestor would mask the unit's own bind or tmpfs: refuse.
        for ancestor in ("/", "/var", "/var/", "/dev", "//dev", "/./var"):
            with self.subTest(ancestor=ancestor), self.assertRaises(sandbox.SandboxError):
                properties(hidden_paths=[ancestor])

    def test_privileged_users_bad_paths_and_longer_runtime_are_rejected(self):
        for overrides in ({"user": "root"}, {"user": "ubuntu"}, {"user": "Bad"},
                          {"source_dir": "relative"}, {"output_dir": "/x/../y"},
                          {"hidden_paths": ["/a b"]}, {"runtime_seconds": 421},
                          {"source_dir": "/x:/etc"}):
            with self.subTest(overrides=overrides), self.assertRaises(sandbox.SandboxError):
                properties(**overrides)

    def test_launch_argv_is_fixed_systemd_run_without_a_shell(self):
        argv = sandbox.launch_argv(REQUEST_ID, properties(), "/usr/bin/python3",
                                   "/opt/szl-cpu-broker/current/tools/ssh_ci_step_runner.py", SHA)
        self.assertEqual(argv[0], sandbox.SYSTEMD_RUN)
        self.assertIn("--unit=" + UNIT, argv)
        for flag in ("--pipe", "--wait", "--collect", "--service-type=exec"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-6:], ["/usr/bin/python3", "-I", "-B",
                                     "/opt/szl-cpu-broker/current/tools/ssh_ci_step_runner.py",
                                     "--source-sha", SHA])
        for bad in ("A" * 40, "a" * 39, "main"):
            with self.subTest(bad=bad), self.assertRaises(sandbox.SandboxError):
                sandbox.launch_argv(REQUEST_ID, properties(), "/usr/bin/python3", "/r.py", bad)
        with self.assertRaises((sandbox.SandboxError, ValueError)):
            sandbox.unit_name("../escape")


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.cgroup = Path(scratch.name)
        base = self.cgroup / "system.slice" / UNIT
        base.mkdir(parents=True)
        for name, value in sandbox.EXPECTED_CGROUP.items():
            (base / name).write_text(value + "\n")
        (base / "cgroup.procs").write_text("4242\n")
        self.proc = self.cgroup / "proc"
        self.write_process()
        self.shown = dict(sandbox.EXPECTED_PROPERTIES, ControlGroup="/system.slice/" + UNIT,
                          User="szl-build", RuntimeMaxUSec="7min", ActiveState="active",
                          SubState="running", MainPID="4242")
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def write_process(self, seccomp="2", caps=sandbox.ZERO_CAPS, shared=(), oom="1000"):
        if os.name != "posix":
            patcher = unittest.mock.patch.object(sandbox, "process_readback",
                                        return_value={"failures": []})
            patcher.start()
            self.addCleanup(patcher.stop)
            return
        shutil.rmtree(self.proc, ignore_errors=True)
        for pid in ("4242", "self"):
            (self.proc / pid / "ns").mkdir(parents=True)
            for index, name in enumerate(sandbox.NAMESPACES):
                identity = "host" if pid == "self" or name in shared else "unit"
                os.symlink("%s:[%s-%d]" % (name, identity, index), self.proc / pid / "ns" / name)
        (self.proc / "4242" / "status").write_text(
            "Name:\tpython3\nSeccomp:\t%s\nNoNewPrivs:\t1\nCapEff:\t%s\nCapPrm:\t%s\n"
            "CapBnd:\t%s\nCapAmb:\t%s\n" % (seccomp, caps, caps, caps, caps))
        (self.proc / "4242" / "oom_score_adj").write_text(oom + "\n")

    def fake_run(self, argv, **_kwargs):
        self.assertEqual(argv[:3], [sandbox.SYSTEMCTL, "show", UNIT])
        for key in sandbox.UNIT_STATE:
            self.assertIn(key, argv[-1].split("=", 1)[1].split(","))
        text = "".join("%s=%s\n" % item for item in self.shown.items())
        return subprocess.CompletedProcess(argv, 0, text.encode("ascii"), b"")

    def readback(self, run=None):
        return sandbox.readback(UNIT, "szl-build", run=run or self.fake_run,
                                cgroup_root=self.cgroup, proc_root=self.proc,
                                clock=self.clock, sleep=self.sleep)

    def test_exact_kernel_and_systemd_values_verify(self):
        value = self.readback()
        self.assertIs(value["verified"], True)
        self.assertEqual(value["mismatched"], [])

    def test_any_mismatch_blocks_go(self):
        for key, value in (("MemoryMax", "infinity"), ("PrivateNetwork", "no"),
                           ("RuntimeMaxUSec", "infinity"),
                           ("User", "root"), ("ProtectProc", "default")):
            with self.subTest(key=key):
                self.setUp()
                self.shown[key] = value
                result = self.readback()
                self.assertIs(result["verified"], False)
                self.assertIn(key, result["mismatched"])
        self.setUp()
        (self.cgroup / "system.slice" / UNIT / "pids.max").write_text("max\n")
        self.assertIn("pids.max", self.readback()["mismatched"])

    @unittest.skipUnless(os.name == "posix", "namespace links need POSIX symlinks")
    def test_kernel_process_view_must_show_seccomp_no_caps_and_private_namespaces(self):
        value = self.readback()
        self.assertEqual(value["process"]["separate_namespaces"], sorted(sandbox.NAMESPACES))
        for kwargs, expected in (({"seccomp": "0"}, "seccomp"),
                                 ({"caps": "000001ffffffffff"}, "capabilities"),
                                 ({"shared": ("net",)}, "host namespace"),
                                 ({"oom": "0"}, "oom_score_adj")):
            with self.subTest(kwargs=kwargs):
                self.write_process(**kwargs)
                self.now = 0.0
                result = self.readback()
                self.assertIs(result["verified"], False)
                self.assertTrue(any(expected in item for item in result["mismatched"]))
                # Retried until the deadline, never accepted on a partial view.
                self.assertGreaterEqual(self.now, sandbox.READBACK_SECONDS)

    def test_process_is_read_only_after_execve_and_retried_until_it_holds(self):
        states = iter([("activating", "start", "0"), ("activating", "start", "4242"),
                       ("active", "running", "4242"), ("active", "running", "4242"),
                       ("active", "running", "4242")])
        shows = []

        def run(argv, **kwargs):
            self.shown["ActiveState"], self.shown["SubState"], self.shown["MainPID"] = next(states)
            shows.append(self.shown["ActiveState"])
            return self.fake_run(argv, **kwargs)
        # Nothing is read while activating; a failed read or a failing view is retried.
        reads = [OSError("gone"), {"failures": ["seccomp filter not active"]}, {"failures": []}]
        with unittest.mock.patch.object(sandbox, "process_readback", side_effect=reads) as check:
            value = self.readback(run)
        self.assertIs(value["verified"], True)
        self.assertEqual(value["mismatched"], [])
        self.assertEqual(shows, ["activating", "activating", "active", "active", "active"])
        self.assertEqual(check.call_args_list, [unittest.mock.call(4242, self.proc)] * 3)
        self.assertEqual(value["unit_state"], {"ActiveState": "active", "MainPID": "4242",
                                               "SubState": "running"})

    def test_unit_never_running_is_a_mismatch_without_reading_the_process(self):
        self.shown.update(ActiveState="activating", SubState="start")
        with unittest.mock.patch.object(sandbox, "process_readback",
                                        return_value={"failures": []}) as check:
            value = self.readback()
        self.assertIs(value["verified"], False)
        self.assertEqual(value["failure"], "READBACK_MISMATCH")
        self.assertIn("unit not active before deadline", value["mismatched"])
        self.assertIsNone(value["process"])
        self.assertEqual(value["unit_state"]["ActiveState"], "activating")
        self.assertGreaterEqual(self.now, sandbox.READBACK_SECONDS)
        check.assert_not_called()

    def test_main_pid_outside_the_unit_cgroup_never_gets_go(self):
        commands = []

        def run(argv, **kwargs):
            commands.append(argv)
            if argv[1:2] == ["show"]:
                return self.fake_run(argv, **kwargs)
            output = b"inactive\n" if argv[1:2] == ["is-active"] else b""
            return subprocess.CompletedProcess(argv, 3 if output else 0, output, b"")

        def readback(unit, user, **kwargs):
            return sandbox.readback(unit, user, proc_root=self.proc, clock=self.clock,
                                    sleep=self.sleep, **kwargs)
        for main_pid in ("999", "0", "-4242", "4242x"):
            with self.subTest(main_pid=main_pid):
                self.shown["MainPID"], self.now = main_pid, 0.0
                with unittest.mock.patch.object(sandbox, "process_readback",
                                                return_value={"failures": []}) as check:
                    outcome = sandbox.run_unit(
                        REQUEST_ID, ["unused"], "szl-build", run=run, cgroup_root=self.cgroup,
                        popen=lambda _argv, **kwargs: subprocess.Popen(
                            [sys.executable, "-c", CHILD], **kwargs),
                        readback_fn=readback)
                self.assertFalse(outcome["go_sent"])
                self.assertEqual(outcome["runner_stdout"].strip(), b"EOF")
                self.assertIn("main process not running in unit cgroup",
                              outcome["readback"]["mismatched"])
                check.assert_not_called()
        self.assertIn([sandbox.SYSTEMCTL, "kill", "--kill-who=all", "--signal=SIGKILL", UNIT],
                      commands)

    def test_missing_cgroup_is_unavailable(self):
        ticks = iter(range(1000))
        value = sandbox.readback("szl-cpu-build-none.service", "szl-build", run=self.fake_run,
                                 cgroup_root=self.cgroup, clock=lambda: next(ticks),
                                 sleep=lambda _seconds: None, seconds=3)
        self.assertEqual(value["failure"], "CGROUP_UNAVAILABLE")
        self.assertIs(value["verified"], False)


CHILD = r"""
import sys
data = sys.stdin.buffer.read(3)
sys.stdout.write("GOT:" + data.decode() + "\n" if data else "EOF\n")
"""


class RunUnitTests(unittest.TestCase):
    def setUp(self):
        self.commands = []

    def popen(self, _argv, **kwargs):
        return subprocess.Popen([sys.executable, "-c", CHILD], **kwargs)

    def fake_run(self, argv, **_kwargs):
        self.commands.append(argv)
        output = b"inactive\n" if argv[1:2] == ["is-active"] else b""
        return subprocess.CompletedProcess(argv, 3 if output else 0, output, b"")

    def run_unit(self, verified):
        return sandbox.run_unit(
            REQUEST_ID, ["unused"], "szl-build", popen=self.popen, run=self.fake_run,
            cgroup_root=Path(tempfile.gettempdir()) / "no-such-cgroup-root",
            readback_fn=lambda *_args, **_kwargs: {"verified": verified})

    def test_go_is_sent_only_after_verified_readback(self):
        outcome = self.run_unit(True)
        self.assertTrue(outcome["go_sent"])
        self.assertEqual(outcome["runner_stdout"].strip(), b"GOT:GO")
        self.assertEqual(outcome["exit_code"], 0)
        self.assertTrue(outcome["unit_gone"])
        self.assertFalse(any(argv[1:2] in (["kill"], ["stop"]) for argv in self.commands))

    def test_unverified_readback_closes_stdin_and_stops_only_this_unit(self):
        outcome = self.run_unit(False)
        self.assertFalse(outcome["go_sent"])
        self.assertEqual(outcome["runner_stdout"].strip(), b"EOF")
        stops = [argv for argv in self.commands if argv[1:2] in (["kill"], ["stop"])]
        self.assertEqual(len(stops), 2)
        self.assertTrue(all(argv[-1] == UNIT for argv in stops))

    def test_wait_deadline_kills_only_this_unit(self):
        def sleeper(_argv, **kwargs):
            return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        outcome = sandbox.run_unit(
            REQUEST_ID, ["unused"], "szl-build", popen=sleeper, run=self.fake_run,
            wall_seconds=-9, readback_fn=lambda *_args, **_kwargs: {"verified": True})
        self.assertTrue(outcome["timed_out"])
        self.assertIn([sandbox.SYSTEMCTL, "kill", "--kill-who=all", "--signal=SIGKILL", UNIT],
                      self.commands)


@unittest.skipUnless(os.name == "posix", "host scan uses POSIX sockets and symlinks")
class HostScanTests(unittest.TestCase):
    def test_unknown_top_level_opt_var_and_run_sockets_are_hidden(self):
        import socket
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            for directory in ("usr/bin", "etc", "data/models", "opt/szl-cpu-broker", "opt/vllm",
                              "var/lib/secrets", "var/tmp", "run/snapd", "run/systemd/inaccessible",
                              "run/szl-cpu-build/x"):
                (root / directory).mkdir(parents=True)
            os.symlink("usr/bin", root / "bin")
            os.symlink("/run", root / "var" / "run")
            sockets = []
            for path in ("run/snapd.socket", "run/systemd/inaccessible/sock",
                         "run/szl-cpu-build/x/s"):
                server = socket.socket(socket.AF_UNIX)
                server.bind(str(root / path))
                sockets.append(server)
            try:
                hidden, problems = sandbox.host_hidden_paths(root)
            finally:
                for server in sockets:
                    server.close()
        self.assertEqual(problems, [])
        self.assertEqual(hidden, ["/data", "/opt/vllm", "/run/snapd.socket", "/var/lib"])

    def test_unit_mount_points_left_on_the_host_root_stay_visible(self):
        # systemd 249 creates missing bind destinations on the host root and never
        # removes them; hiding them would mask the next unit's /src and /out binds.
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            for directory in ("usr", "etc", "var/tmp", "run", "cdrom", "persistent", "src", "out"):
                (root / directory).mkdir(parents=True)
            hidden, problems = sandbox.host_hidden_paths(root)
        self.assertEqual(problems, [])
        self.assertEqual(hidden, ["/cdrom", "/persistent"])
        values = properties(hidden_paths=hidden)
        self.assertIn("InaccessiblePaths=-/cdrom", values)
        for mount in ("/src", "/out"):
            self.assertNotIn("InaccessiblePaths=-" + mount, values)
        self.assertIn("BindReadOnlyPaths=/run/szl-cpu-build/x/src:/src", values)
        self.assertIn("BindPaths=/run/szl-cpu-build/x/out:/out", values)

    def test_run_scan_counts_only_directories_and_sockets(self):
        import socket
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            for directory in ("run/udev/data", "run/containerd/io", "run/dbus", "run/lock"):
                (root / directory).mkdir(parents=True)
            # More regular files than the limit: the old scan refused this host.
            for index in range(sandbox.SCAN_LIMIT + 1):
                (root / "run" / "udev" / "data" / ("c%d" % index)).touch()
            for index in range(8):
                (root / "run" / "containerd" / "io" / ("task%d" % index)).mkdir()
            sockets = []
            for path in ("run/containerd/containerd.sock", "run/dbus/system_bus_socket",
                         "run/snapd.socket"):
                server = socket.socket(socket.AF_UNIX)
                server.bind(str(root / path))
                sockets.append(server)
            try:
                hidden, problems = sandbox.host_hidden_paths(root)
                # Counted: run/{containerd,dbus,lock,snapd.socket,udev,udev/data}. The
                # hidden-as-a-whole containerd and dbus trees would add 11 if walked.
                with unittest.mock.patch.object(sandbox, "SCAN_LIMIT", 6):
                    limited = sandbox.host_hidden_paths(root)
                    (root / "run" / "lock" / "extra").mkdir()
                    overflow = sandbox.host_hidden_paths(root)
            finally:
                for server in sockets:
                    server.close()
        self.assertEqual((hidden, problems), (["/run/snapd.socket"], []))
        self.assertEqual(limited, (["/run/snapd.socket"], []))
        # Directories still count, so a real overflow still refuses.
        self.assertEqual(overflow[1], ["socket scan limit reached under /run"])

    def test_unsafe_names_are_problems_not_silently_visible(self):
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / "model weights").mkdir()
            hidden, problems = sandbox.host_hidden_paths(Path(scratch))
        self.assertEqual(hidden, [])
        self.assertEqual(problems, ["unhideable host path name"])


class ProfileAgreementTests(unittest.TestCase):
    def test_expected_readback_matches_contract_profile(self):
        self.assertEqual(sandbox.EXPECTED_CGROUP["memory.max"],
                         str(contract.CPU_PROFILE["memory_max_bytes"]))
        self.assertEqual(sandbox.EXPECTED_CGROUP["pids.max"], str(contract.CPU_PROFILE["tasks_max"]))
        self.assertEqual(contract.CPU_PROFILE["wall_seconds"], 420)
        self.assertEqual(sandbox.format_timespan(420), "7min")
        self.assertEqual(sandbox.format_timespan(5), "5s")
        self.assertEqual(sandbox.format_timespan(90), "1min 30s")


if __name__ == "__main__":
    unittest.main()
