"""Protocol tests for the uninstalled CPU broker draft.

These tests do not qualify a VM, a cgroup, GPU denial, or a live permit.
A memory claim double is call-order evidence only. host_claim_qualified stays
false unless the object is an entered ClaimStore.
"""

import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from tools import ssh_ci_admission as admission
from tools import ssh_ci_claims as claims
from tools import ssh_ci_client as client
from tools import ssh_ci_contract as contract
from tools import ssh_ci_server as server


def fixtures(mode="pinned"):
    from datetime import datetime, timezone
    now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    request = {
        "schema": contract.REQUEST_SCHEMA,
        "operation": "build",
        "request_id": "b58224fe-b0a7-4d81-ac3c-509e3ac46b53",
        "permit_id": "8c83c2c0-df82-4541-8cfa-4d0bddb41791",
        "repository": contract.REPOSITORY,
        "source_sha": "a" * 40,
        "controller_sha": "a" * 40,
        "workflow_ref": contract.WORKFLOW_REF,
        "run_id": 1234,
        "run_attempt": 1,
    }
    host = {"hostname": "sample-build-host",
            "boot_id": "90941c8a-32c2-45c3-a526-381429617329"}
    process = {"pid": 123, "start_ticks": 4567}
    workload = {"mode": mode}
    if mode == "pinned":
        workload.update(process)
    permit = {
        "schema": admission.PERMIT_SCHEMA,
        "permit_id": request["permit_id"],
        "request_sha256": contract.sha256(contract.canonical(request)),
        "not_before": "2030-01-02T03:04:00Z",
        "expires_at": "2030-01-02T03:19:00Z",
        "max_runs": 1,
        "cpu_profile": dict(contract.CPU_PROFILE),
        "host": dict(host),
        "workload": workload,
    }
    observation = {
        "schema": admission.OBSERVATION_SCHEMA,
        "observed_at": "2030-01-02T03:04:05Z",
        "host": dict(host),
        "gpu_compute_processes": [dict(process)] if mode == "pinned" else [],
        "competing_cpu_jobs": 0,
    }
    return request, permit, observation, now


class MemoryClaims:
    """Test double. Not a ClaimStore and not durable host storage."""

    def __init__(self):
        self._lock = threading.Lock()
        self.permits = {}
        self.calls = 0

    def claim(self, request_id, permit_id, request_sha256):
        with self._lock:
            self.calls += 1
            if permit_id in self.permits:
                raise claims.ClaimError(
                    "permit already claimed; do not retry or remove claim")
            self.permits[permit_id] = (request_id, request_sha256)


class DecisionTests(unittest.TestCase):
    def test_forced_command_matches_the_client_and_rejects_extras(self):
        self.assertEqual(server.FORCED_COMMAND, client.FORCED_COMMAND)
        self.assertIsNone(server.accept_forced_command((server.FORCED_COMMAND,)))
        for argv in ((), ("bash",), (server.FORCED_COMMAND, "--help"), ("sh", "-c", "id")):
            with self.subTest(argv=argv), self.assertRaises(server.BrokerError):
                server.accept_forced_command(argv)

    def test_malformed_request_does_not_claim(self):
        _request, permit, observation, now = fixtures()
        store = MemoryClaims()
        samples = (
            b"",
            b"[]",
            b"{" * 4097,
            b'{"schema":"szl.ssh-cpu-build.request.v1","schema":"other"}',
            contract.canonical({**fixtures()[0], "command": "bash"}),
        )
        for sample in samples:
            with self.subTest(sample=sample[:24]):
                decision = server.decide(sample, permit, observation, now, store)
                self.assertEqual(decision["reason"], "malformed_request")
                self.assertEqual(decision["result"], "BLOCKED")
                self.assertFalse(decision["execution_attempted"])
                self.assertFalse(decision["claim_preserved"])
        self.assertEqual(store.calls, 0)

    def test_stale_permit_is_denied_before_claim(self):
        from datetime import datetime, timezone
        request, permit, observation, _now = fixtures()
        later = datetime(2030, 1, 2, 3, 12, 0, tzinfo=timezone.utc)
        store = MemoryClaims()
        decision = server.decide(request, permit, observation, later, store)
        self.assertEqual(decision["reason"], "permit_denied")
        self.assertFalse(decision["execution_attempted"])
        self.assertEqual(store.calls, 0)

    def test_replay_and_concurrency_leave_one_unqualified_claim(self):
        request, permit, observation, now = fixtures()
        store = MemoryClaims()
        barrier = threading.Barrier(2)
        found = []

        def worker():
            barrier.wait()
            found.append(server.decide(request, permit, observation, now, store))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        reasons = sorted(item["reason"] for item in found)
        self.assertEqual(reasons, ["execution_not_qualified", "replay_denied"])
        self.assertEqual(store.calls, 2)
        self.assertEqual(len(store.permits), 1)
        for item in found:
            self.assertEqual(item["result"], "BLOCKED")
            self.assertFalse(item["execution_attempted"])
            self.assertFalse(item["retry_allowed"])
            self.assertFalse(item["host_claim_qualified"])
            self.assertFalse(item["isolation_verified"])
        again = server.decide(request, permit, observation, now, store)
        self.assertEqual(again["reason"], "replay_denied")
        self.assertTrue(again["claim_preserved"])
        self.assertFalse(again["retry_allowed"])

    def test_missing_claim_store_does_not_execute(self):
        decision = server.decide(*fixtures(), None)
        self.assertEqual(decision["reason"], "claim_store_required")
        self.assertEqual(decision["execution_outcome"], "NOT_RUN")

    def test_execution_constants_cannot_be_turned_on_by_the_request(self):
        self.assertIs(server.EXECUTION_AUTHORIZED, False)
        self.assertIsNone(server.REVIEWED_ARGV)
        with self.assertRaisesRegex(server.BrokerError, "not bound"):
            server.reject_unreviewed_command(None)
        with self.assertRaisesRegex(server.BrokerError, "network install"):
            server.reject_unreviewed_command(("pip", "install", "numpy"))
        with self.assertRaisesRegex(server.BrokerError, "not admitted"):
            server.reject_unreviewed_command(("python", "-B", "-m", "unittest"))

    def test_disconnect_is_unknown_and_nonzero_exit_is_failed(self):
        unknown = server.classify_outcome(True, True, None)
        failed = server.classify_outcome(True, False, 1)
        zero = server.classify_outcome(True, False, 0)
        self.assertEqual(unknown["execution_outcome"], "UNKNOWN")
        self.assertEqual(failed["execution_outcome"], "FAILED")
        self.assertNotEqual(unknown["execution_outcome"], failed["execution_outcome"])
        self.assertEqual(zero["execution_outcome"], "EXIT_ZERO")
        for item in (unknown, failed, zero):
            self.assertEqual(item["result"], "BLOCKED")
            self.assertFalse(item["retry_allowed"])
            self.assertTrue(item["claim_preserved"])
            self.assertFalse(item["pass_declared"])
        with self.assertRaises(server.BrokerError):
            server.classify_outcome(False, True, None)
        with self.assertRaises(server.BrokerError):
            server.classify_outcome(True, False, True)

    def test_windows_claim_store_still_fails_closed(self):
        if os.name == "posix" and claims.fcntl is not None:
            self.skipTest("POSIX claim qualification belongs to test_ssh_ci_claims")
        with self.assertRaisesRegex(claims.ClaimError, "POSIX"):
            with claims.ClaimStore(Path("unprovisioned")):
                self.fail("unsupported platform must not enter")


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="szl-broker-artifact-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "out"
        self.directory.mkdir()

    def test_traversal_and_oversize_names_create_nothing(self):
        before = set(self.directory.iterdir())
        for name in ("../outside", "dir/file", "dir\\file", "", ".", "..", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(server.BrokerError):
                server.write_regular_artifact(self.directory, name, b"x", 16)
        self.assertEqual(set(self.directory.iterdir()), before)
        with self.assertRaisesRegex(server.BrokerError, "byte budget"):
            server.write_regular_artifact(
                self.directory, "packages.tar", b"x", 0)
        self.assertEqual(set(self.directory.iterdir()), before)

    def test_second_write_does_not_overwrite(self):
        written = server.write_regular_artifact(
            self.directory, "packages.tar", b"one", 16)
        self.assertEqual(written, 3)
        with self.assertRaises(server.BrokerError):
            server.write_regular_artifact(
                self.directory, "packages.tar", b"two", 16)
        self.assertEqual((self.directory / "packages.tar").read_bytes(), b"one")

    def test_symlink_bit_is_rejected_before_create(self):
        with mock.patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(server.BrokerError, "link"):
                server.write_regular_artifact(
                    self.directory, "packages.tar", b"x", 16)
        self.assertFalse((self.directory / "packages.tar").exists())

    def test_real_symlink_is_not_followed_when_the_host_allows_one(self):
        outside = self.root / "outside"
        outside.write_bytes(b"keep")
        link = self.directory / "packages.tar"
        try:
            os.symlink(outside, link)
        except OSError as exc:
            self.skipTest("real symlink qualification NOT_RUN: " + type(exc).__name__)
        with self.assertRaises(server.BrokerError):
            server.write_regular_artifact(
                self.directory, "packages.tar", b"changed", 16)
        self.assertEqual(outside.read_bytes(), b"keep")


class EntrypointTests(unittest.TestCase):
    def test_stdin_cap_and_valid_syntax_still_do_not_install(self):
        code, decision = server.entry(
            (server.FORCED_COMMAND,), b"{" * (contract.MAX_REQUEST_BYTES + 1))
        self.assertEqual(code, 2)
        self.assertEqual(decision["reason"], "malformed_request")
        request, _permit, _observation, _now = fixtures()
        code, decision = server.entry(
            (server.FORCED_COMMAND,), contract.canonical(request))
        self.assertEqual(code, 2)
        self.assertEqual(decision["reason"], "broker_not_installed")
        self.assertEqual(decision["request_syntax"], "valid")
        self.assertFalse(decision["execution_attempted"])
        self.assertFalse(decision["installation_performed"])

    def test_main_stops_reading_after_one_extra_byte(self):
        stdout = io.BytesIO()
        code = server.main(
            (server.FORCED_COMMAND,),
            io.BytesIO(b"x" * 5000),
            stdout,
        )
        self.assertEqual(code, 2)
        parsed = contract.strict_json_loads(stdout.getvalue())
        self.assertEqual(parsed["reason"], "malformed_request")
        self.assertFalse(parsed["automatic_install"])

    def test_deployment_text_is_not_an_installer(self):
        plan = server.deployment_instructions()
        self.assertFalse(plan["automatic_install"])
        self.assertFalse(plan["executed"])
        self.assertFalse(plan["deletes_claims"])
        self.assertFalse(plan["stops_model_workload"])
        self.assertFalse(plan["enables_workflow"])
        self.assertEqual(plan["cpu_profile"], contract.CPU_PROFILE)
        self.assertEqual(plan["scratch_limit"], "PROPOSE_AND_REVIEW")
        self.assertEqual(plan["log_limit"], "PROPOSE_AND_REVIEW")
        self.assertTrue(plan["steps"])
        self.assertTrue(plan["rollback"])


if __name__ == "__main__":
    unittest.main()
