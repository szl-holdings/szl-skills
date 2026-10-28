"""Offline tests for the v2 pinned GPU process set (vLLM, training, or idle)."""

import copy
import unittest

from tests.test_ssh_ci_admission import fixtures as admission_fixtures
from tests.test_ssh_ci_contract import request_fixture, response_fixture
from tests.test_ssh_ci_executor import FakeStore, fixtures as executor_fixtures
from tools import ssh_ci_admission as admission
from tools import ssh_ci_broker as broker
from tools import ssh_ci_contract as contract
from tools import ssh_ci_executor as executor


SET = [{"pid": 123, "start_ticks": 4567}, {"pid": 900, "start_ticks": 77}]


def pinned_set(values, processes=SET):
    request, permit, observation, now = values
    permit = dict(permit, workload={"mode": "pinned_set", "processes": copy.deepcopy(processes)})
    observation = dict(observation, gpu_compute_processes=copy.deepcopy(processes))
    return request, permit, observation, now


def v2_response(processes=SET):
    envelope, payloads = response_fixture()
    receipt = envelope["receipt"]
    identity = {"hostname": "sample-build-host",
                "boot_id": "a239a737-e860-4ec8-aef8-d90b62a0c1dd",
                "gpu_processes": copy.deepcopy(processes)}
    receipt.update(schema=contract.RECEIPT_SCHEMA_V2, runtime_before=identity,
                   runtime_after=copy.deepcopy(identity))
    return envelope, payloads


class ContractV2Tests(unittest.TestCase):
    def test_v2_receipt_with_a_set_or_no_process_verifies(self):
        for processes in (SET, []):
            with self.subTest(processes=processes):
                envelope, payloads = v2_response(processes)
                self.assertEqual(contract.verify_response(request_fixture(), envelope), payloads)

    def test_v1_receipts_still_verify_unchanged(self):
        envelope, payloads = response_fixture()
        self.assertEqual(contract.verify_response(request_fixture(), envelope), payloads)

    def test_v2_set_drift_shape_and_schema_mixing_are_rejected(self):
        cases = []
        drift, _ = v2_response()
        drift["receipt"]["runtime_after"]["gpu_processes"][1]["start_ticks"] += 1
        cases.append(drift)
        unsorted, _ = v2_response(list(reversed(SET)))
        cases.append(unsorted)
        duplicate, _ = v2_response([SET[0], dict(SET[0])])
        cases.append(duplicate)
        too_many, _ = v2_response([{"pid": pid, "start_ticks": 1} for pid in range(1, 10)])
        cases.append(too_many)
        v1_shape_in_v2, _ = response_fixture()
        v1_shape_in_v2["receipt"]["schema"] = contract.RECEIPT_SCHEMA_V2
        cases.append(v1_shape_in_v2)
        v2_shape_in_v1, _ = v2_response()
        v2_shape_in_v1["receipt"]["schema"] = contract.RECEIPT_SCHEMA
        cases.append(v2_shape_in_v1)
        unknown, _ = v2_response()
        unknown["receipt"]["schema"] = "szl.ssh-cpu-build.receipt.v3"
        cases.append(unknown)
        for index, envelope in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)


class AdmissionPinnedSetTests(unittest.TestCase):
    def test_exact_observed_set_is_admitted_in_any_observation_order(self):
        request, permit, observation, now = pinned_set(admission_fixtures())
        observation["gpu_compute_processes"].reverse()
        self.assertEqual(admission.validate_permit(request, permit, observation, now), permit)
        idle = pinned_set(admission_fixtures(), [])
        self.assertEqual(admission.validate_permit(*idle), idle[1])

    def test_any_set_change_is_denied(self):
        changes = [
            lambda o: o["gpu_compute_processes"].append({"pid": 5000, "start_ticks": 1}),
            lambda o: o["gpu_compute_processes"].pop(),
            lambda o: o["gpu_compute_processes"][0].update(start_ticks=1),
            lambda o: o.update(gpu_compute_processes=[]),
        ]
        for index, change in enumerate(changes):
            request, permit, observation, now = pinned_set(admission_fixtures())
            change(observation)
            with self.subTest(change=index), self.assertRaisesRegex(contract.ContractError, "set"):
                admission.validate_permit(request, permit, observation, now)

    def test_permit_set_must_be_closed_sorted_unique_and_bounded(self):
        for processes in (list(reversed(SET)), [SET[0], SET[0]],
                          [{"pid": pid, "start_ticks": 1} for pid in range(1, 10)],
                          [{"pid": 1}], [{"pid": 1, "start_ticks": 1, "extra": 1}]):
            request, permit, observation, now = pinned_set(admission_fixtures())
            permit["workload"]["processes"] = processes
            with self.subTest(processes=processes), self.assertRaises(contract.ContractError):
                admission.validate_permit(request, permit, observation, now)
        request, permit, observation, now = pinned_set(admission_fixtures())
        permit["workload"]["pid"] = 123
        with self.assertRaises(contract.ContractError):
            admission.validate_permit(request, permit, observation, now)

    def test_v1_pinned_still_requires_exactly_its_one_process(self):
        request, permit, observation, now = admission_fixtures("pinned")
        observation["gpu_compute_processes"].append({"pid": 900, "start_ticks": 77})
        with self.assertRaisesRegex(contract.ContractError, "pinned GPU process identity"):
            admission.validate_permit(request, permit, observation, now)


class ExecutorPinnedSetTests(unittest.TestCase):
    def test_pinned_set_reaches_the_executor_and_idle_still_does_not(self):
        request, permit, observation, window, now = executor_fixtures()
        permit = dict(permit, workload={"mode": "pinned_set", "processes": copy.deepcopy(SET)})
        observation = dict(observation, gpu_compute_processes=copy.deepcopy(SET))
        events, calls = [], []
        result = executor.execute(
            request,
            permit_loader=lambda _r: executor.OwnerPermit(permit, window, True),
            observation_reader=lambda: copy.deepcopy(observation),
            claim_store_factory=lambda: FakeStore(events),
            executor=lambda *args: calls.append(args) or {"ok": True}, clock=lambda: now)
        self.assertEqual(result["result"], "PASS", result)
        self.assertEqual(events, ["lock", "claim", "unlock"])
        self.assertEqual(len(calls), 1)
        request, permit, observation, window, now = executor_fixtures("idle")
        result = executor.execute(
            request, permit_loader=lambda _r: executor.OwnerPermit(permit, window, True),
            observation_reader=lambda: copy.deepcopy(observation),
            claim_store_factory=lambda: FakeStore([]),
            executor=lambda *args: self.fail("idle must not execute"), clock=lambda: now)
        self.assertEqual(result["reason"], "IDLE_RECEIPT_PROFILE_UNSUPPORTED")


class BrokerRuntimeTests(unittest.TestCase):
    def test_runtime_identity_shapes(self):
        observation = {"host": {"hostname": "h", "boot_id": "b"},
                       "gpu_compute_processes": [SET[1], SET[0]]}
        self.assertEqual(broker.runtime_identity(observation, "pinned_set"),
                         {"hostname": "h", "boot_id": "b", "gpu_processes": SET})
        self.assertEqual(broker.runtime_identity(dict(observation, gpu_compute_processes=[]),
                                                 "pinned_set")["gpu_processes"], [])
        with self.assertRaises(broker.BrokerError):
            broker.runtime_identity(observation, "pinned")
        self.assertEqual(broker.runtime_identity(dict(observation, gpu_compute_processes=[SET[0]])),
                         {"hostname": "h", "pid": 123, "start_ticks": 4567, "boot_id": "b"})


if __name__ == "__main__":
    unittest.main()
