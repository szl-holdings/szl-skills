"""Offline qualification tests: probe parsing and pass/fail evaluation only."""

from datetime import datetime, timezone
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import ssh_ci_qualify as qualify


CONFINED_ISOLATION = {
    "go": True, "mode": "isolation", "proc_pids": [1], "model_pid_visible": [],
    "dev_shm_entries": 0, "gpu_devices": [], "inet_socket": "DENIED:OSError",
    "tcp:8000": "DENIED:OSError", "tcp:external": "DENIED:OSError",
    "unix:/run/szl-cpu-broker.sock": "DENIED:PermissionError",
    "list:/home": "DENIED:PermissionError", "list:/data/models": "ABSENT",
    "write:/src/.probe": "DENIED:OSError", "write:/etc/.probe": "DENIED:OSError",
    "write:/out/.probe": "ALLOWED", "write:/tmp/.probe": "ALLOWED",
    "setuid_root": "DENIED:PermissionError", "cap_eff": "0000000000000000",
    "no_new_privs": "1", "uid": 999, "read:/etc/systemd/system/vllm.service": "DENIED:PermissionError",
    "syscall:io_uring_setup": "DENIED:EPERM", "syscall:add_key": "DENIED:EPERM",
    "reachable_unix_sockets": [],
}


def outcome(stderr=b"Finished with result: success\n", verified=True, elapsed=1.0):
    return {"readback": {"verified": verified, "mismatched": []}, "go_sent": verified,
            "unit_gone": True, "runner_stderr": stderr, "elapsed": elapsed}


class EvaluationTests(unittest.TestCase):
    context = {"build_uid": 999}

    def test_confined_controls_pass(self):
        cases = (("isolation", CONFINED_ISOLATION, outcome()),
                 ("tasks", {"threads_started": 62}, outcome()),
                 ("tmpfs", {"bytes_written": qualify.TMPFS_LIMIT}, outcome()),
                 ("memory", {"allocated_mib": 960}, outcome(b"Finished with result: oom-kill\n")),
                 ("deadline", {"sleeping": True}, outcome(b"Finished with result: timeout\n", elapsed=6)))
        for mode, probe, result in cases:
            with self.subTest(mode=mode):
                self.assertEqual(qualify.evaluate(mode, probe, result, self.context), [])

    def test_unconfined_results_fail(self):
        leaky = dict(CONFINED_ISOLATION, inet_socket="ALLOWED", **{
            "tcp:8000": "ALLOWED", "write:/etc/.probe": "ALLOWED", "list:/home": "ALLOWED"},
            gpu_devices=["nvidia0"], dev_shm_entries=3, model_pid_visible=[445607],
            cap_eff="00000000a80425fb", uid=0, reachable_unix_sockets=["/run/snapd.socket"],
            **{"syscall:io_uring_setup": "ALLOWED:14",
               "read:/etc/systemd/system/vllm.service": "ALLOWED"})
        failures = "\n".join(qualify.evaluate("isolation", leaky, outcome(), self.context))
        for expected in ("inet_socket allowed", "tcp:8000 allowed", "write:/etc/.probe",
                         "list:/home allowed", "GPU devices", "/dev/shm", "host processes",
                         "capabilities", "build user", "/run/snapd.socket",
                         "syscall:io_uring_setup not filtered", "read:/etc/systemd/system/vllm.service"):
            self.assertIn(expected, failures)
        self.assertTrue(qualify.evaluate("tasks", {"threads_started": 512}, outcome(), self.context))
        self.assertTrue(qualify.evaluate("tmpfs", {"bytes_written": 805306368}, outcome(), self.context))
        self.assertTrue(qualify.evaluate("memory", {"allocated_mib": 2048}, outcome(), self.context))
        self.assertTrue(qualify.evaluate("deadline", {}, outcome(elapsed=60), self.context))

    def test_unverified_readback_fails_every_control(self):
        for mode in ("isolation", "tasks", "memory", "tmpfs", "deadline"):
            with self.subTest(mode=mode):
                self.assertTrue(qualify.evaluate(mode, {}, outcome(verified=False), self.context))

    def test_probe_has_no_systemd_specifier_or_variable_characters(self):
        self.assertNotIn("%", qualify.PROBE)
        self.assertNotIn("$", qualify.PROBE)
        compile(qualify.PROBE, "probe", "exec")


class HostReadTests(unittest.TestCase):
    def test_listening_ports_and_model_paths_are_read_only_parses(self):
        with tempfile.TemporaryDirectory() as scratch:
            proc = Path(scratch)
            (proc / "net").mkdir()
            (proc / "net/tcp").write_text(
                "  sl  local_address rem_address   st\n"
                "   0: 0100007F:1F40 00000000:0000 0A 0\n"
                "   1: 0100007F:0016 0100007F:9C40 01 0\n")
            (proc / "net/tcp6").write_text("header\n   0: 00000000000000000000000000000000:0050 0 0A\n")
            self.assertEqual(qualify.listening_ports(proc), [80, 8000])
            pid = proc / "4242"
            pid.mkdir()
            (pid / "maps").write_text(
                "7f00-7f01 r--s 00000000 08:01 1 /data/models/llm/model-00001.safetensors\n"
                "7f02-7f03 r-xp 00000000 08:01 2 /usr/lib/libc.so.6\n")
            if os.name == "posix":
                os.symlink("/srv/vllm", pid / "cwd")
            (pid / "cmdline").write_bytes(b"python3\0-m\0vllm.entrypoints\0--model\0"
                                          b"/models/Nemotron\0--download-dir=/cache/hf\0"
                                          b"--chat-template\0/opt/t/template.jinja\0")
            (pid / "environ").write_bytes(b"PATH=/usr/bin\0HF_HOME=/var/hf\0HF_TOKEN=secret\0")
            (pid / "cgroup").write_text("0::/system.slice/vllm.service\n")

            def run(argv, **_kwargs):
                return subprocess.CompletedProcess(argv, 0, b"/etc/systemd/system/vllm.service\n", b"")
            directories, files = qualify.model_paths([4242], proc, run)
            for expected in ("/data/models/llm", "/models/Nemotron", "/cache/hf", "/var/hf"):
                self.assertIn(expected, directories)
            self.assertNotIn("/usr/lib", directories)
            self.assertIn("/opt/t/template.jinja", files)
            self.assertIn("/etc/systemd/system/vllm.service", files)
            self.assertFalse(any("secret" in path for path in directories + files))

    def test_unit_result_and_last_json(self):
        self.assertEqual(qualify.unit_result(b"x\nFinished with result: oom-kill\n"), "oom-kill")
        self.assertIsNone(qualify.unit_result(b""))
        self.assertEqual(qualify.last_json(b'{"a":1}\n{"a":2}\nnoise\n'), {"a": 2})


class QualifyFlowTests(unittest.TestCase):
    def test_model_identity_change_blocks_qualification(self):
        host = {"hostname": "h", "boot_id": "90941c8a-32c2-45c3-a526-381429617329"}
        observations = iter([
            {"host": host, "gpu_compute_processes": [{"pid": 1, "start_ticks": 2}], "competing_cpu_jobs": 0},
            {"host": host, "gpu_compute_processes": [], "competing_cpu_jobs": 0}])

        class FakeBuild:
            run = popen = None

            @staticmethod
            def scan():
                return ["/data", "/run/snapd.socket"], []

        def fake_control(_build, _config, mode, _arguments, **_kwargs):
            probes = {"isolation": CONFINED_ISOLATION, "tasks": {"threads_started": 3},
                      "tmpfs": {"bytes_written": 1}, "memory": {"allocated_mib": 64},
                      "deadline": {"sleeping": True}}
            stderr = {"memory": b"Finished with result: oom-kill\n",
                      "deadline": b"Finished with result: timeout\n"}.get(mode, b"")
            import json
            return dict(outcome(stderr), runner_stdout=json.dumps(probes[mode]).encode())
        original = qualify.run_control
        qualify.run_control = fake_control
        try:
            receipt = qualify.qualify({"build_user": "szl-build", "hidden_paths": [],
                                       "nvidia_smi": "/usr/bin/nvidia-smi"},
                                      build=FakeBuild(), build_uid=999,
                                      observer=lambda: next(observations),
                                      clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
                                      proc=Path(tempfile.gettempdir()) / "no-proc")
        finally:
            qualify.run_control = original
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertEqual(receipt["failures"], ["model identity changed during qualification"])
        self.assertFalse(receipt["independent_witness"])


class ReleaseBindingTests(unittest.TestCase):
    sha = "a" * 40

    def fixture(self, scratch, sha=None):
        release = Path(scratch) / (sha or self.sha)
        tools = release / "tools"
        tools.mkdir(parents=True)
        source = tools / "ssh_ci_qualify.py"
        runner = tools / "ssh_ci_step_runner.py"
        source.write_text("# Synthetic qualification fixture\n")
        runner.write_text("# Synthetic runner fixture\n")
        return release, source, {"runner": str(runner), "nvidia_smi": "/usr/bin/nvidia-smi"}

    def test_binding_requires_exact_source_current_and_runner_release(self):
        with tempfile.TemporaryDirectory() as scratch:
            release, source, config = self.fixture(scratch)
            other, other_source, other_config = self.fixture(scratch, "b" * 40)
            kwargs = {"source_file": source, "release_root": scratch, "current_path": release}
            binding = qualify.verify_release(config, self.sha, **kwargs)
            self.assertEqual(binding, {"release_sha": self.sha,
                                      "config_sha256": qualify.sha256(qualify.canonical(config))})
            cases = (
                (config, dict(kwargs, source_file=other_source), "qualifier source"),
                (config, dict(kwargs, current_path=other), "current installed release"),
                (other_config, kwargs, "configured runner"),
            )
            for value, paths, reason in cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(qualify.QualificationError, reason):
                    qualify.verify_release(value, self.sha, **paths)

    def test_invalid_or_missing_release_refuses(self):
        for sha in (None, "main", "A" * 40, "../" + "a" * 40):
            with self.subTest(sha=sha), self.assertRaises(qualify.QualificationError):
                qualify.verify_release({}, sha)
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaisesRegex(qualify.QualificationError, "immutable release directory"):
                qualify.verify_release({}, self.sha, release_root=scratch)

    def test_readiness_is_bounded_and_refuses_configuration_change(self):
        observation = {"schema": "szl.ssh-cpu-build.observation.v1",
                       "observed_at": "2030-01-01T00:00:00Z",
                       "host": {"hostname": "fixture", "boot_id": "90941c8a-32c2-45c3-a526-381429617329"},
                       "gpu_compute_processes": [], "competing_cpu_jobs": 0}
        with tempfile.TemporaryDirectory() as scratch:
            release, source, config = self.fixture(scratch)

            def verifier(value, expected):
                return qualify.verify_release(value, expected, source_file=source,
                                               release_root=scratch, current_path=release)

            loader = mock.Mock(return_value=config)
            observer = mock.Mock(return_value=observation)
            with mock.patch.object(qualify, "qualify", side_effect=AssertionError("no probes")), \
                    mock.patch("builtins.open", side_effect=AssertionError("no receipt writes")):
                state = qualify.activation_state("fixture-config", self.sha, loader=loader,
                                                  verifier=verifier, observer=observer)
            self.assertEqual(state["schema"], qualify.ACTIVATION_STATE_SCHEMA)
            self.assertEqual(state["release_sha"], self.sha)
            self.assertEqual(state["observation"], observation)
            self.assertEqual(loader.call_count, 2)
            observer.assert_called_once_with()
            changed = dict(config, nvidia_smi="/different/nvidia-smi")
            with self.assertRaisesRegex(qualify.QualificationError, "configuration changed"):
                qualify.activation_state("fixture-config", self.sha,
                                         loader=mock.Mock(side_effect=[config, changed]),
                                         verifier=verifier, observer=observer)
            with self.assertRaisesRegex(qualify.QualificationError, "output bound"):
                qualify.activation_state("fixture-config", self.sha, loader=loader,
                                         verifier=verifier,
                                         observer=lambda: {"large": "x" * qualify.MAX_ACTIVATION_STATE_BYTES})

    def test_readiness_cli_never_qualifies_or_writes_a_receipt(self):
        state = {"schema": qualify.ACTIVATION_STATE_SCHEMA, "release_sha": self.sha,
                 "config_sha256": "b" * 64, "observation": {"fixture": True}}
        output = io.StringIO()
        with mock.patch.object(qualify, "activation_state", return_value=state) as read, \
                mock.patch.object(qualify, "qualify", side_effect=AssertionError("no probes")), \
                mock.patch("builtins.open", side_effect=AssertionError("no receipt writes")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(qualify.main(["--readiness", "--expected-release", self.sha]), 0)
        read.assert_called_once_with(qualify.DEFAULT_CONFIG, self.sha)
        self.assertEqual(json.loads(output.getvalue()), state)

    def test_readiness_cli_requires_release_and_refuses_rehearsal(self):
        for argv in (["--readiness"],
                     ["--expected-release", "", "--owner-window-ref", "fixture"],
                     ["--readiness", "--expected-release", self.sha, "--rehearse-build", self.sha],
                     ["--expected-release", self.sha, "--rehearse-build", "b" * 40,
                      "--owner-window-ref", "fixture"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                qualify.main(argv)

    def test_qualification_keeps_failed_receipt_on_post_run_configuration_change(self):
        binding = {"release_sha": self.sha, "config_sha256": "b" * 64}
        changed = dict(binding, config_sha256="c" * 64)
        receipt = {"schema": qualify.QUALIFICATION_SCHEMA, "result": "QUALIFIED", "failures": []}
        output = io.StringIO()
        with mock.patch.object(qualify, "load_config", return_value={}), \
                mock.patch.object(qualify, "verify_release", side_effect=[binding, changed]), \
                mock.patch.object(qualify, "qualify", return_value=receipt) as run, \
                mock.patch("builtins.open", mock.mock_open()) as opened, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = qualify.main(["--expected-release", self.sha, "--rehearse-build", self.sha,
                                 "--owner-window-ref", "fixture-window"])
        self.assertEqual(code, 1)
        run.assert_called_once_with({}, rehearse_sha=self.sha, owner_window_ref="fixture-window")
        actual = json.loads(output.getvalue())
        self.assertEqual(actual["result"], "BLOCKED")
        self.assertEqual(actual["release_sha"], self.sha)
        self.assertEqual(actual["config_sha256"], binding["config_sha256"])
        self.assertIn("configuration changed", actual["failures"][0])
        opened().write.assert_called_once()

    def test_qualification_binds_success_and_records_post_run_release_failure(self):
        binding = {"release_sha": self.sha, "config_sha256": "b" * 64}
        for after, expected_result in ((binding, "QUALIFIED"),
                                       (qualify.QualificationError("current release drift"), "BLOCKED")):
            receipt = {"schema": qualify.QUALIFICATION_SCHEMA, "result": "QUALIFIED", "failures": []}
            output = io.StringIO()
            with self.subTest(expected_result=expected_result), \
                    mock.patch.object(qualify, "load_config", return_value={}), \
                    mock.patch.object(qualify, "verify_release", side_effect=[binding, after]), \
                    mock.patch.object(qualify, "qualify", return_value=receipt), \
                    mock.patch("builtins.open", mock.mock_open()) as opened, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = qualify.main(["--expected-release", self.sha, "--rehearse-build", self.sha,
                                     "--owner-window-ref", "fixture-window"])
            self.assertEqual(code, 0 if expected_result == "QUALIFIED" else 1)
            actual = json.loads(output.getvalue())
            self.assertEqual(actual["result"], expected_result)
            self.assertEqual(actual["release_sha"], self.sha)
            self.assertEqual(actual["config_sha256"], binding["config_sha256"])
            opened().write.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "symlink behavior exercised on hosted POSIX")
    def test_current_symlink_resolves_but_release_alias_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            release, source, config = self.fixture(scratch)
            current = Path(scratch) / "current"
            current.symlink_to(release, target_is_directory=True)
            kwargs = {"source_file": source, "release_root": scratch, "current_path": current}
            self.assertEqual(qualify.verify_release(config, self.sha, **kwargs)["release_sha"], self.sha)
            alias = Path(scratch) / ("b" * 40)
            alias.symlink_to(release, target_is_directory=True)
            with self.assertRaisesRegex(qualify.QualificationError, "immutable release directory"):
                qualify.verify_release(config, "b" * 40, **kwargs)

    def test_release_mismatch_before_qualification_performs_no_probes_or_write(self):
        with mock.patch.object(qualify, "load_config", return_value={}), \
                mock.patch.object(qualify, "verify_release", side_effect=qualify.QualificationError("wrong release")), \
                mock.patch.object(qualify, "qualify") as run, \
                mock.patch("builtins.open", side_effect=AssertionError("no receipt writes")), \
                contextlib.redirect_stderr(io.StringIO()):
            code = qualify.main(["--expected-release", self.sha, "--owner-window-ref", "fixture"])
        self.assertEqual(code, 1)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
