"""Offline observer tests over a synthetic /proc tree and fake commands."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools import ssh_ci_admission as admission
from tools import ssh_ci_observer as observer


BOOT = "90941c8a-32c2-45c3-a526-381429617329"
NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def completed(argv, stdout=b"", code=0):
    return subprocess.CompletedProcess(argv, code, stdout, b"")


class ObserverTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.proc = Path(scratch.name)
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/hostname").write_text("0136-ict-prxmx50112\n")
        (self.proc / "sys/kernel/random/boot_id").write_text(BOOT + "\n")
        self.stat(445607, 98765)
        self.gpu = b"445607\n"
        self.units = b""

    def stat(self, pid, ticks, comm="VLLM::EngineCore (x)"):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        fields = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 10
        (directory / "stat").write_text("%d (%s) %s\n" % (pid, comm, " ".join(fields)))

    def fake_run(self, argv, **_kwargs):
        if argv[0] == observer.NVIDIA_SMI:
            return completed(argv, self.gpu)
        if argv[0] == observer.SYSTEMCTL:
            return completed(argv, self.units)
        raise AssertionError("unexpected command " + argv[0])

    def observe(self):
        return observer.observe(lambda: NOW, proc=self.proc, run=self.fake_run)

    def test_observation_matches_admission_contract(self):
        value = self.observe()
        self.assertEqual(value, {
            "schema": admission.OBSERVATION_SCHEMA, "observed_at": "2030-01-02T03:04:05Z",
            "host": {"hostname": "0136-ict-prxmx50112", "boot_id": BOOT},
            "gpu_compute_processes": [{"pid": 445607, "start_ticks": 98765}],
            "competing_cpu_jobs": 0})

    def test_start_ticks_survives_parentheses_in_process_name(self):
        self.stat(445607, 4242, comm="a) b (c")
        self.assertEqual(observer.start_ticks(445607, self.proc), 4242)

    def test_idle_gpu_and_running_build_unit_are_reported(self):
        self.gpu = b""
        self.units = (b"szl-cpu-build-x.service loaded active running SZL CPU build\n"
                      b"szl-cpu-build-y.service loaded failed failed SZL CPU build\n")
        value = self.observe()
        self.assertEqual(value["gpu_compute_processes"], [])
        self.assertEqual(value["competing_cpu_jobs"], 1)

    def test_unreadable_or_noncontract_runtime_is_an_error_not_idle(self):
        cases = {
            "uppercase hostname": lambda: (self.proc / "sys/kernel/hostname").write_text("Host\n"),
            "bad boot id": lambda: (self.proc / "sys/kernel/random/boot_id").write_text("x\n"),
            "nvidia-smi failure": lambda: setattr(self, "fake_run", lambda argv, **_: completed(argv, b"", 9)),
            "garbage gpu line": lambda: setattr(self, "gpu", b"No running processes\n"),
            "missing process": lambda: setattr(self, "gpu", b"999999\n"),
            "unexpected unit": lambda: setattr(self, "units", b"other.service loaded active running\n"),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                self.setUp()
                mutate()
                with self.assertRaises((observer.ObservationError, OSError)):
                    self.observe()

    def test_command_timeout_is_unavailable(self):
        def timeout(argv, **_kwargs):
            raise subprocess.TimeoutExpired(argv, 15)
        with self.assertRaisesRegex(observer.ObservationError, "unavailable"):
            observer.gpu_compute_pids(run=timeout)

    def test_clock_must_be_aware_utc(self):
        for clock in (lambda: NOW.replace(tzinfo=None),
                      lambda: NOW.astimezone(timezone(timedelta(hours=2)))):
            with self.subTest(clock=clock), self.assertRaises(observer.ObservationError):
                observer.observe(clock, proc=self.proc, run=self.fake_run)


if __name__ == "__main__":
    unittest.main()
