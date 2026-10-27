"""Offline owner-window envelope tests; no provider or owner authentication."""

from datetime import datetime, timezone
import copy
import json
import unittest

from tools import ssh_ci_contract as contract
from tools import ssh_ci_owner_window as window


def fixtures():
    request = {
        "schema": contract.REQUEST_SCHEMA, "operation": "build",
        "request_id": "b58224fe-b0a7-4d81-ac3c-509e3ac46b53",
        "permit_id": "8c83c2c0-df82-4541-8cfa-4d0bddb41791",
        "repository": contract.REPOSITORY, "source_sha": "a" * 40,
        "controller_sha": "a" * 40, "workflow_ref": contract.WORKFLOW_REF,
        "run_id": 1234, "run_attempt": 1,
    }
    value = {
        "schema": window.WINDOW_SCHEMA,
        "window_id": "36d9de7f-0ac1-41c8-b1d3-76d7f6032a11",
        "permit_id": request["permit_id"],
        "request_sha256": contract.sha256(contract.canonical(request)),
        "not_before": "2030-01-02T03:04:00Z",
        "expires_at": "2030-01-02T03:19:00Z",
        "provider": {"name": "Massed Compute",
                     "instance_uuid": "bd6e0a2e-c7d1-4423-bfc2-99b575c6a233"},
        "host": {"hostname": "sample-build-host",
                 "boot_id": "90941c8a-32c2-45c3-a526-381429617329"},
        "execution": {"execution_id": "5c3b4a3e-df6f-4b67-9f54-c1dcfe7fd9e4",
                       "run_nonce": "e0a84a84-7e1c-49f0-b0a4-2e4a8b0d5a67"},
        "source": {"github_sha": request["source_sha"],
                   "hf_revision": "dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f"},
        "cpu_profile": dict(contract.CPU_PROFILE),
        "budget": {"ledger_ref": "ledger://massed-compute/2026-10-27",
                    "remaining_seconds": 180000, "remaining_usd": 2000.0},
        "workload": {"mode": "cpu_build", "gpu_devices": False,
                      "model_interaction": "none", "training": False,
                      "inference": False},
        "checkpoint_manifest_sha256": "c" * 64,
        "owner_receipt_ref": "owner://vm2/cpu-window/2026-10-27",
    }
    return request, value, datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class OwnerWindowTests(unittest.TestCase):
    def test_valid_window_is_detached_and_request_bound(self):
        request, value, now = fixtures()
        original = copy.deepcopy(value)
        result = window.validate_owner_window(request, value, now)
        self.assertEqual(result, value)
        result["host"]["hostname"] = "changed"
        self.assertEqual(value, original)

    def test_raw_json_and_duplicate_members(self):
        request, value, now = fixtures()
        self.assertEqual(window.validate_owner_window(
            contract.canonical(request), json.dumps(value), now), value)
        raw = json.dumps(value)[:-1] + ', "schema": "other"}'
        with self.assertRaises(contract.ContractError):
            window.validate_owner_window(request, raw, now)

    def test_top_level_and_nested_closure(self):
        request, value, now = fixtures()
        for field in window.WINDOW_FIELDS:
            bad = copy.deepcopy(value)
            del bad[field]
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, bad, now)
        for parent in ("provider", "host", "execution", "source", "budget",
                       "workload", "cpu_profile"):
            bad = copy.deepcopy(value)
            bad[parent]["unexpected"] = True
            with self.subTest(parent=parent), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, bad, now)

    def test_window_bounds_and_remaining_time(self):
        request, value, now = fixtures()
        for start, end in (("2030-01-02T03:04:06Z", "2030-01-02T03:19:00Z"),
                           ("2030-01-02T03:04:00Z", "2030-01-02T03:19:01Z"),
                           ("2030-01-02T03:04:00Z", "2030-01-02T03:10:00Z")):
            bad = copy.deepcopy(value)
            bad["not_before"], bad["expires_at"] = start, end
            with self.subTest(start=start, end=end), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, bad, now)

    def test_request_permit_and_source_digests_are_exact(self):
        request, value, now = fixtures()
        for field, bad in (("permit_id", "8c83c2c0-df82-4541-8cfa-4d0bddb41792"),
                           ("request_sha256", "f" * 64)):
            changed = copy.deepcopy(value)
            changed[field] = bad
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, changed, now)
        changed = copy.deepcopy(value)
        changed["source"]["github_sha"] = "b" * 40
        with self.assertRaises(contract.ContractError):
            window.validate_owner_window(request, changed, now)

    def test_provider_host_and_execution_identity_are_closed(self):
        request, value, now = fixtures()
        mutations = (("provider", "name", "Other Cloud"),
                      ("provider", "instance_uuid", "not-a-uuid"),
                      ("host", "boot_id", "not-a-uuid"),
                      ("execution", "run_nonce", value["execution"]["execution_id"]))
        for parent, field, bad in mutations:
            changed = copy.deepcopy(value)
            changed[parent][field] = bad
            with self.subTest(parent=parent, field=field), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, changed, now)

    def test_cpu_profile_budget_and_workload_cannot_expand_scope(self):
        request, value, now = fixtures()
        for parent, field, bad in (
                ("cpu_profile", "gpu_devices", True),
                ("cpu_profile", "memory_max_bytes", 2**40),
                ("budget", "remaining_seconds", 449),
                ("budget", "remaining_usd", 0),
                ("workload", "mode", "gpu_eval"),
                ("workload", "inference", True)):
            changed = copy.deepcopy(value)
            changed[parent][field] = bad
            with self.subTest(parent=parent, field=field), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, changed, now)

    def test_gpu_grant_shaped_data_is_rejected(self):
        request, _, now = fixtures()
        gpu = {"schema": "szl.gpu-eval-slot.v2", "max_requests": 8,
               "execution_claim": "consumed"}
        with self.assertRaises(contract.ContractError):
            window.validate_owner_window(request, gpu, now)

    def test_invalid_time_and_json_types_fail_closed(self):
        request, value, now = fixtures()
        for bad in (None, True, [], 1, b"\xff", '{"x":NaN}'):
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, bad, now)
        with self.assertRaises(contract.ContractError):
            window.validate_owner_window(request, value, datetime(2030, 1, 2, 3, 4, 5))

    def test_source_revision_and_receipt_references_cannot_be_unknown(self):
        request, value, now = fixtures()
        for field, bad in (("hf_revision", "UNKNOWN"), ("hf_revision", "")):
            changed = copy.deepcopy(value)
            changed["source"][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(contract.ContractError):
                window.validate_owner_window(request, changed, now)
        changed = copy.deepcopy(value)
        changed["checkpoint_manifest_sha256"] = "UNKNOWN"
        with self.assertRaises(contract.ContractError):
            window.validate_owner_window(request, changed, now)


if __name__ == "__main__":
    unittest.main()
