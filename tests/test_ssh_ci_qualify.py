"""Offline qualification tests: probe parsing, pass/fail evaluation, and run guards."""

import contextlib
from datetime import datetime, timezone
import errno
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
import uuid

from tools import ssh_ci_qualify as qualify
from tools.ssh_ci_claims import ClaimError


LINUX_X86_64 = sys.platform.startswith("linux") and os.uname().machine == "x86_64"
CONFINED_ISOLATION = {
    "go": True, "mode": "isolation", "proc_pids": [1], "model_pid_visible": [],
    "dev_shm_entries": 0, "dev_shm_marker": "ABSENT", "gpu_devices": [],
    "inet_socket": "DENIED:OSError",
    "tcp:8000": "DENIED:OSError", "tcp:external": "DENIED:OSError",
    "unix:/run/szl-cpu-broker.sock": "DENIED:PermissionError",
    "list:/home": "DENIED:PermissionError", "list:/data/models": "ABSENT",
    "write:/src/.probe": "DENIED:OSError", "write:/etc/.probe": "DENIED:OSError",
    "write:/out/.probe": "ALLOWED", "write:/tmp/.probe": "ALLOWED",
    "setuid_root": "DENIED:PermissionError", "cap_eff": "0000000000000000",
    "no_new_privs": "1", "uid": 999, "read:/etc/systemd/system/vllm.service": "DENIED:PermissionError",
    "syscall:io_uring_setup": "DENIED:EPERM", "syscall:add_key": "DENIED:EPERM",
    "namespace:unshare": "DENIED:EPERM", "namespace:clone": "DENIED:EPERM",
    "namespace:clone3": "DENIED:ENOSYS", "thread_start": "ALLOWED",
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

    def test_namespace_creation_shm_marker_and_threads_must_be_exact(self):
        cases = {"namespace:unshare": ("namespace:unshare", ("ALLOWED", "DENIED:ENOSYS",
                                                             "UNKNOWN:killed", None)),
                 "namespace:clone": ("namespace:clone", ("ALLOWED", "DENIED:ENOSYS", None)),
                 "namespace:clone3": ("namespace:clone3", ("ALLOWED", "DENIED:EPERM", None)),
                 "thread_start": ("threads do not start", ("DENIED:RuntimeError", None)),
                 "dev_shm_marker": ("/dev/shm marker", ("ALLOWED", "DENIED:PermissionError", None))}
        for key, (message, values) in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    probe = dict(CONFINED_ISOLATION, **{key: value})
                    if value is None:
                        del probe[key]
                    failures = qualify.evaluate("isolation", probe, outcome(), self.context)
                    self.assertEqual(len(failures), 1, failures)
                    self.assertIn(message, failures[0])

    def test_unverified_readback_fails_every_control(self):
        for mode in ("isolation", "tasks", "memory", "tmpfs", "deadline"):
            with self.subTest(mode=mode):
                self.assertTrue(qualify.evaluate(mode, {}, outcome(verified=False), self.context))

    def test_probe_has_no_systemd_specifier_or_variable_characters(self):
        self.assertNotIn("%", qualify.PROBE)
        self.assertNotIn("$", qualify.PROBE)
        compile(qualify.PROBE, "probe", "exec")


# Runs the probe's namespace and thread block in a fresh interpreter, optionally
# under a seccomp filter shaped like the unit's: unshare/clone with CLONE_NEWUSER
# get EPERM and clone3 gets argv[1] (0 means no filter at all).
FILTER_HARNESS = r"""
import ctypes, errno, json, os, sys, threading, time
clone3_errno, block = int(sys.argv[1]), sys.stdin.read()
libc = ctypes.CDLL(None, use_errno=True)
if clone3_errno:
    class Instruction(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte),
                    ("k", ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Instruction))]
    code = [(0x20, 0, 0, 4), (0x15, 0, 4, 0xc000003e), (0x20, 0, 0, 0), (0x15, 4, 0, 272),
            (0x15, 3, 0, 56), (0x15, 1, 0, 435), (0x06, 0, 0, 0x7fff0000),
            (0x06, 0, 0, 0x50000 | clone3_errno), (0x20, 0, 0, 16), (0x45, 1, 0, 0x10000000),
            (0x06, 0, 0, 0x7fff0000), (0x06, 0, 0, 0x50000 | errno.EPERM)]
    program = Program(len(code), (Instruction * len(code))(*(Instruction(*c) for c in code)))
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise OSError(ctypes.get_errno(), "seccomp filter not installed")
r = {}
def attempt(name, action):
    try:
        action(); r[name] = "ALLOWED"
    except FileNotFoundError:
        r[name] = "ABSENT"
    except Exception as error:
        r[name] = "DENIED:" + type(error).__name__
exec(block)
try:
    os.waitpid(-1, os.WNOHANG); r["unreaped_child"] = True
except ChildProcessError:
    pass
print(json.dumps(r, sort_keys=True))
"""


@unittest.skipUnless(LINUX_X86_64, "raw x86_64 Linux syscalls and seccomp")
class ProbeNamespaceTests(unittest.TestCase):
    def run_block(self, clone3_errno):
        start = qualify.PROBE.index("    def reap(pid):")
        block = textwrap.dedent(qualify.PROBE[start:qualify.PROBE.index("    reachable, seen")])
        done = subprocess.run([sys.executable, "-I", "-B", "-c", FILTER_HARNESS, str(clone3_errno)],
                              input=block.encode(), capture_output=True, timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.decode().splitlines()
        self.assertEqual(len(lines), 1, lines)  # No child of an allowed call ran on.
        result = json.loads(lines[0])
        self.assertNotIn("unreaped_child", result)
        return result

    def test_unit_shaped_filter_gives_the_exact_denials_and_threads_still_start(self):
        result = self.run_block(errno.ENOSYS)
        self.assertEqual({key: result[key] for key in qualify.NAMESPACE_DENIALS},
                         qualify.NAMESPACE_DENIALS)
        self.assertEqual(result["thread_start"], "ALLOWED")
        probe = dict(CONFINED_ISOLATION, **result)
        self.assertEqual(qualify.evaluate("isolation", probe, outcome(), {"build_uid": 999}), [])

    def test_clone3_falling_through_to_eperm_breaks_threads_and_fails(self):
        result = self.run_block(errno.EPERM)
        self.assertEqual((result["namespace:clone3"], result["thread_start"]),
                         ("DENIED:EPERM", "DENIED:RuntimeError"))
        probe = dict(CONFINED_ISOLATION, **result)
        failures = "\n".join(qualify.evaluate("isolation", probe, outcome(), {"build_uid": 999}))
        self.assertIn("namespace:clone3 is DENIED:EPERM", failures)
        self.assertIn("threads do not start", failures)

    def test_unfiltered_calls_report_and_reap_any_child(self):
        # Host policy decides; an allowed call's child exits at once and is reaped.
        result = self.run_block(0)
        for key in qualify.NAMESPACE_DENIALS:
            self.assertTrue(result[key] == "ALLOWED" or result[key].startswith("DENIED:"), result)
        self.assertEqual(result["thread_start"], "ALLOWED")


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


HOST = {"hostname": "h", "boot_id": "90941c8a-32c2-45c3-a526-381429617329"}
PROBES = {"isolation": CONFINED_ISOLATION, "tasks": {"threads_started": 3},
          "tmpfs": {"bytes_written": 1}, "memory": {"allocated_mib": 64},
          "deadline": {"sleeping": True}}


def observation(processes=(), competing=0):
    return {"host": HOST, "gpu_compute_processes": list(processes), "competing_cpu_jobs": competing}


class FakeLock:
    """Stands in for the broker's ClaimStore context; ``held`` is a busy broker."""

    def __init__(self, events, held=False):
        self.events, self.held = events, held

    def __enter__(self):
        if self.held:
            raise ClaimError("cannot acquire trusted claim store; execution denied")
        self.events.append("lock")
        return self

    def __exit__(self, *_args):
        self.events.append("unlock")


class FakeBuild:
    run = popen = None

    @staticmethod
    def scan():
        return ["/data", "/run/snapd.socket"], []


class QualifyFlowTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.shm = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.shm, True)
        if not hasattr(os, "O_NOFOLLOW"):  # Windows: O_CREAT|O_EXCL still refuses any existing name.
            patcher = unittest.mock.patch.object(os, "O_NOFOLLOW", 0, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_control(self, _build, _config, mode, arguments, **_kwargs):
        self.events.append(mode)
        if mode == "isolation":
            marker = self.shm / arguments["shm_marker"]
            self.assertTrue(marker.is_file() and not marker.is_symlink())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(marker.lstat().st_mode), 0o600)
            self.events.append("marker")
        stderr = {"memory": b"Finished with result: oom-kill\n",
                  "deadline": b"Finished with result: timeout\n"}.get(mode, b"")
        return dict(outcome(stderr), runner_stdout=json.dumps(PROBES[mode]).encode())

    def run_qualify(self, observations, *, lock=None, control=None, **options):
        observations = iter(observations)

        def observer():
            self.events.append("observe")
            return next(observations)
        with unittest.mock.patch.object(qualify, "run_control", control or self.fake_control):
            return qualify.qualify({"build_user": "szl-build", "hidden_paths": [],
                                    "nvidia_smi": "/usr/bin/nvidia-smi"},
                                   build=FakeBuild(), build_uid=999, observer=observer,
                                   clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
                                   proc=Path(tempfile.gettempdir()) / "no-proc",
                                   claim_store_factory=lambda: lock or FakeLock(self.events),
                                   shm_root=self.shm, **options)

    def test_model_identity_change_blocks_qualification(self):
        receipt = self.run_qualify([observation([{"pid": 1, "start_ticks": 2}]), observation()])
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertEqual(receipt["failures"], ["model identity changed during qualification"])
        self.assertFalse(receipt["independent_witness"])

    def test_whole_run_holds_the_broker_claim_lock_and_removes_the_marker(self):
        def rehearse(*_args, **_kwargs):
            self.events.append("rehearse")
            return {"failures": []}
        with unittest.mock.patch.object(qualify, "rehearse", rehearse):
            receipt = self.run_qualify([observation(), observation()], rehearse_sha="a" * 40)
        self.assertEqual(receipt["result"], "QUALIFIED", receipt["failures"])
        self.assertEqual(self.events, ["lock", "observe", "isolation", "marker", "tasks", "memory",
                                       "tmpfs", "deadline", "rehearse", "observe", "unlock"])
        self.assertEqual(list(self.shm.iterdir()), [])

    def test_held_claim_lock_refuses_before_anything_runs(self):
        with self.assertRaisesRegex(qualify.QualifyError, "claim lock is held"):
            self.run_qualify([observation()], lock=FakeLock(self.events, held=True))
        self.assertEqual(self.events, [])
        self.assertEqual(list(self.shm.iterdir()), [])

    def test_running_build_unit_refuses_to_start(self):
        for competing in (1, None, True, "0", 0.0):
            with self.subTest(competing=competing):
                self.events.clear()
                with self.assertRaisesRegex(qualify.QualifyError, "already running"):
                    self.run_qualify([observation(competing=competing)])
                self.assertEqual(self.events, ["lock", "observe", "unlock"])
                self.assertEqual(list(self.shm.iterdir()), [])

    def test_marker_is_removed_and_lock_released_when_a_control_fails(self):
        def failing(*args, **kwargs):
            if args[2] == "tasks":
                raise RuntimeError("unit failed to launch")
            return self.fake_control(*args, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "unit failed to launch"):
            self.run_qualify([observation(), observation()], control=failing)
        self.assertEqual(self.events, ["lock", "observe", "isolation", "marker", "unlock"])
        self.assertEqual(list(self.shm.iterdir()), [])

    def test_marker_never_reuses_or_follows_an_existing_name(self):
        fixed = uuid.UUID(int=7)
        existing = self.shm / ("szl-qualify-" + fixed.hex)
        existing.write_bytes(b"not ours")
        with unittest.mock.patch.object(qualify.uuid, "uuid4", return_value=fixed), \
                self.assertRaisesRegex(qualify.QualifyError, "FileExistsError"):
            qualify.shm_marker(self.shm)
        self.assertEqual(existing.read_bytes(), b"not ours")
        if os.name == "posix":
            existing.unlink()
            os.symlink(self.shm / "target", existing)
            with unittest.mock.patch.object(qualify.uuid, "uuid4", return_value=fixed), \
                    self.assertRaises(qualify.QualifyError):
                qualify.shm_marker(self.shm)
            self.assertFalse((self.shm / "target").exists())


class MainTests(unittest.TestCase):
    def test_refusal_prints_a_reason_and_writes_no_receipt(self):
        stderr = io.StringIO()
        with unittest.mock.patch.object(qualify, "load_config", return_value={}), \
                unittest.mock.patch.object(qualify, "qualify",
                                           side_effect=qualify.QualifyError("lock held")), \
                unittest.mock.patch("builtins.open", side_effect=AssertionError("receipt written")), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(qualify.main(["--owner-window-ref", "window-1"]), 1)
        self.assertIn("QUALIFICATION REFUSED: lock held", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
