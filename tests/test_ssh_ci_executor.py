"""Offline orchestration tests; no VM, SSH, GPU, or permit writer."""

from datetime import datetime, timezone
import copy
import unittest

from tools import ssh_ci_admission as admission
from tools import ssh_ci_contract as contract
from tools import ssh_ci_executor as executor


def fixtures(mode="pinned"):
    now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    request = {
        "schema": contract.REQUEST_SCHEMA, "operation": "build",
        "request_id": "b58224fe-b0a7-4d81-ac3c-509e3ac46b53",
        "permit_id": "8c83c2c0-df82-4541-8cfa-4d0bddb41791",
        "repository": contract.REPOSITORY, "source_sha": "a" * 40,
        "controller_sha": "a" * 40, "workflow_ref": contract.WORKFLOW_REF,
        "run_id": 1234, "run_attempt": 1,
    }
    host = {"hostname": "sample-build-host",
            "boot_id": "90941c8a-32c2-45c3-a526-381429617329"}
    process = {"pid": 123, "start_ticks": 4567}
    workload = {"mode": mode}
    if mode == "pinned":
        workload.update(process)
    permit = {
        "schema": admission.PERMIT_SCHEMA, "permit_id": request["permit_id"],
        "request_sha256": contract.sha256(contract.canonical(request)),
        "not_before": "2030-01-02T03:04:00Z",
        "expires_at": "2030-01-02T03:19:00Z", "max_runs": 1,
        "cpu_profile": dict(contract.CPU_PROFILE), "host": dict(host),
        "workload": workload,
    }
    observation = {
        "schema": admission.OBSERVATION_SCHEMA,
        "observed_at": "2030-01-02T03:04:05Z", "host": dict(host),
        "gpu_compute_processes": [dict(process)] if mode == "pinned" else [],
        "competing_cpu_jobs": 0,
    }
    return request, permit, observation, now


class FakeStore:
    def __init__(self, events, fail=False):
        self.events, self.fail = events, fail

    def __enter__(self):
        self.events.append("lock")
        return self

    def __exit__(self, *_args):
        self.events.append("unlock")

    def claim(self, *_args):
        self.events.append("claim")
        if self.fail:
            raise RuntimeError("claimed or unavailable")


class ExecutorTests(unittest.TestCase):
    def run_case(self, mode="pinned", *, second_observation=None,
                 loader=None, store=None, run_executor=None):
        request, permit, observation, now = fixtures(mode)
        events = []
        observations = iter([observation, second_observation or observation])
        calls = []
        if loader is None:
            loader = lambda _request: executor.OwnerPermit(permit, True)
        if store is None:
            store = FakeStore(events)
        if run_executor is None:
            run_executor = lambda *_args: calls.append("executor") or {"ok": True}
        result = executor.execute(
            request, permit_loader=loader,
            observation_reader=lambda: events.append("observe") or next(observations),
            claim_store_factory=lambda: store, executor=lambda *args: (
                events.append("execute") or run_executor(*args)),
            clock=lambda: events.append("clock") or now)
        return result, events, calls, request, permit

    def test_claim_before_executor_and_lock_held_until_return(self):
        result, events, calls, *_ = self.run_case()
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(events, ["observe", "clock", "lock", "claim",
                                  "observe", "clock", "execute", "unlock"])
        self.assertEqual(result["claim_status"], "WRITTEN")
        self.assertTrue(result["executor_called"])

    def test_owner_authentication_unavailable_blocks_without_observation(self):
        result, events, *_ = self.run_case(
            loader=lambda _request: executor.OwnerPermit({}, False))
        self.assertEqual((result["result"], result["reason"]),
                         ("BLOCKED", "OWNER_AUTHENTICATION_UNAVAILABLE"))
        self.assertEqual(events, [])

    def test_loader_observer_and_store_fail_closed(self):
        loader = lambda _request: (_ for _ in ()).throw(RuntimeError())
        result, events, *_ = self.run_case(loader=loader)
        self.assertEqual(result["reason"], "PERMIT_LOADER_UNAVAILABLE")
        self.assertEqual(events, [])
        result, events, *_ = self.run_case(
            loader=lambda _request: executor.OwnerPermit({}, True))
        self.assertEqual(result["reason"], "INITIAL_OBSERVATION_REJECTED")
        self.assertEqual(events[:2], ["observe", "clock"])
        result, events, *_ = self.run_case(store=FakeStore(events, fail=True))
        self.assertEqual(result["reason"], "CLAIM_UNAVAILABLE")
        self.assertFalse(result["executor_called"])

    def test_idle_v1_incompatibility_never_claims_or_executes(self):
        result, events, calls, *_ = self.run_case(mode="idle")
        self.assertEqual(result["reason"], "IDLE_RECEIPT_PROFILE_UNSUPPORTED")
        self.assertEqual(events, ["observe", "clock"])
        self.assertEqual(calls, [])

    def test_revalidation_drift_preserves_claim_and_blocks(self):
        _, _, observation, _ = fixtures()
        changed = copy.deepcopy(observation)
        changed["gpu_compute_processes"][0]["start_ticks"] += 1
        result, events, calls, *_ = self.run_case(second_observation=changed)
        self.assertEqual(result["reason"], "WORKLOAD_DRIFT")
        self.assertEqual(result["claim_status"], "WRITTEN")
        self.assertFalse(result["executor_called"])
        self.assertEqual(calls, [])
        self.assertEqual(events[-1], "unlock")

    def test_executor_failure_and_replay_are_not_retried(self):
        result, events, calls, *_ = self.run_case(
            run_executor=lambda *_args: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(result["reason"], "EXECUTOR_FAILED")
        self.assertEqual(result["claim_status"], "WRITTEN")
        self.assertTrue(result["executor_called"])
        self.assertEqual(calls, [])
        result, *_ = self.run_case(store=FakeStore([], fail=True))
        self.assertEqual(result["reason"], "CLAIM_UNAVAILABLE")

    def test_invalid_executor_receipt_is_not_promoted(self):
        result, *_ = self.run_case(run_executor=lambda *_args: "not-a-receipt")
        self.assertEqual(result["reason"], "EXECUTOR_RESULT_INVALID")
        self.assertEqual(result["claim_status"], "WRITTEN")

    def test_no_cross_call_mutation(self):
        request, permit, observation, _ = fixtures()
        original = (copy.deepcopy(request), copy.deepcopy(permit),
                    copy.deepcopy(observation))
        result, *_ = self.run_case()
        self.assertEqual(result["result"], "PASS")
        self.assertEqual((request, permit, observation), original)


if __name__ == "__main__":
    unittest.main()
