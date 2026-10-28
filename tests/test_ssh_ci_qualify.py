"""Offline qualification tests: probe parsing and pass/fail evaluation only."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
