"""Offline SAMPLE declarations; no live permit, GPU request, or execution."""

import copy
from datetime import datetime, timedelta, timezone
import json
import unittest

from tools import ssh_ci_admission as admission
from tools import ssh_ci_contract as contract


def fixtures(mode="pinned"):
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


class AdmissionTests(unittest.TestCase):
    def test_valid_idle_and_pinned_declarations(self):
        for mode in ("idle", "pinned"):
            values = fixtures(mode)
            with self.subTest(mode=mode):
                self.assertEqual(admission.validate_permit(*values), values[1])

    def test_raw_bytes_and_text_use_same_contract(self):
        request, permit, observation, now = fixtures()
        for encode in (contract.canonical, json.dumps):
            with self.subTest(encoder=encode.__name__):
                self.assertEqual(admission.validate_permit(
                    encode(request), encode(permit), encode(observation), now), permit)

    def test_result_is_deep_detached_and_inputs_unchanged(self):
        values = fixtures()
        original = copy.deepcopy(values)
        result = admission.validate_permit(*values)
        self.assertEqual(values, original)
        result["host"]["hostname"] = "changed"
        result["workload"]["pid"] = 999
        result["cpu_profile"]["wall_seconds"] = 999
        self.assertEqual(values, original)

    def test_missing_and_unknown_top_level_fields(self):
        for index, fields in ((1, admission.PERMIT_FIELDS),
                              (2, admission.OBSERVATION_FIELDS)):
            for field in fields:
                values = list(fixtures())
                del values[index][field]
                with self.subTest(index=index, field=field), self.assertRaises(contract.ContractError):
                    admission.validate_permit(*values)
            values = list(fixtures())
            values[index]["execute"] = True
            with self.subTest(index=index), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_closed_nested_objects(self):
        for index, field in ((1, "host"), (2, "host"), (1, "workload"),
                             (1, "cpu_profile")):
            values = list(fixtures())
            values[index][field]["unexpected"] = "value"
            with self.subTest(index=index, field=field), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)
        values = list(fixtures())
        values[2]["gpu_compute_processes"][0]["command"] = "sample"
        with self.assertRaises(contract.ContractError):
            admission.validate_permit(*values)

    def test_duplicate_json_members_rejected_in_all_objects(self):
        for index, field in ((0, "operation"), (1, "max_runs"), (2, "competing_cpu_jobs")):
            values = list(fixtures())
            text = json.dumps(values[index])
            values[index] = text[:-1] + ', "' + field + '": 1}'
            with self.subTest(index=index), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)
        values = list(fixtures())
        values[1] = json.dumps(values[1]).replace('"pid": 123', '"pid": 123, "pid": 123')
        with self.assertRaises(contract.ContractError):
            admission.validate_permit(*values)

    def test_oversized_raw_and_dictionary_inputs(self):
        for index, limit in ((0, contract.MAX_REQUEST_BYTES),
                              (1, admission.MAX_PERMIT_BYTES),
                              (2, admission.MAX_OBSERVATION_BYTES)):
            for raw in (False, True):
                values = list(fixtures())
                values[index]["padding"] = "x" * limit
                if raw:
                    values[index] = json.dumps(values[index])
                with self.subTest(index=index, raw=raw), self.assertRaises(contract.ContractError):
                    admission.validate_permit(*values)

    def test_invalid_json_types_and_values(self):
        for index in (0, 1, 2):
            for bad in (None, [], True, 1, b"\xff", b"\xef\xbb\xbf{}",
                        '{"x":NaN}', '{"x":"\\ud800"}'):
                values = list(fixtures())
                values[index] = bad
                with self.subTest(index=index, bad=bad), self.assertRaises(contract.ContractError):
                    admission.validate_permit(*values)

    def test_old_gpu_grants_and_observations_never_qualify(self):
        for index in (0, 1, 2):
            values = list(fixtures())
            values[index] = {"schema": "szl.gpu-eval-slot.v2", "max_requests": 8,
                             "execution_claim": "consumed"}
            with self.subTest(index=index), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_wrong_schema_rejected(self):
        for index in (1, 2):
            for bad in ("szl.gpu-eval-slot.v2", "", True, None):
                values = list(fixtures())
                values[index]["schema"] = bad
                with self.subTest(index=index, bad=bad), self.assertRaises(contract.ContractError):
                    admission.validate_permit(*values)

    def test_request_every_identity_is_hash_bound(self):
        mutations = {
            "request_id": "81046a4a-0fb1-4efe-9fc1-cecdfb34f14e",
            "permit_id": "7faebc1f-0439-40a9-92bc-3782f0d57739",
            "run_id": 5678,
            "source_sha": "b" * 40,
            "controller_sha": "b" * 40,
            "repository": "sample/other",
            "workflow_ref": contract.WORKFLOW_REF.replace("main", "topic"),
            "run_attempt": 2,
        }
        for field, value in mutations.items():
            values = list(fixtures())
            values[0][field] = value
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_source_controller_must_match_even_with_updated_hash(self):
        values = list(fixtures())
        values[0]["source_sha"] = "b" * 40
        values[1]["request_sha256"] = contract.sha256(contract.canonical(values[0]))
        with self.assertRaisesRegex(contract.ContractError, "revisions differ"):
            admission.validate_permit(*values)

    def test_permit_id_and_digest_must_match_exactly(self):
        for field, bad in (("permit_id", "8c83c2c0-df82-4541-8cfa-4d0bddb41792"),
                           ("permit_id", True), ("request_sha256", "0" * 64),
                           ("request_sha256", True)):
            values = list(fixtures())
            values[1][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_max_runs_one_not_bool_float_or_string(self):
        for bad in (True, False, 1.0, "1", 0, -1, 2):
            values = list(fixtures())
            values[1]["max_runs"] = bad
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_every_cpu_profile_value_requires_exact_type_and_value(self):
        for field, good in contract.CPU_PROFILE.items():
            if type(good) is int:
                invalid = (float(good), str(good), not bool(good), good + 1)
            elif type(good) is bool:
                invalid = (0, "false", True)
            else:
                invalid = (None, "host", "")
            for bad in invalid:
                values = list(fixtures())
                values[1]["cpu_profile"][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(contract.ContractError):
                    admission.validate_permit(*values)

    def test_now_requires_aware_utc_datetime(self):
        for bad in (None, "2030-01-02T03:04:05Z", True,
                    datetime(2030, 1, 2, 3, 4, 5),
                    datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=1)))):
            values = list(fixtures())
            values[3] = bad
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_timestamp_syntax_is_canonical_and_calendar_valid(self):
        invalid = (None, True, "2030-1-02T03:04:05Z", "2030-01-2T03:04:05Z",
                   "2030-01-02 03:04:05Z", "2030-01-02T03:04:05+00:00",
                   "2030-01-02T03:04:05.000Z", "2030-01-02T03:04:05z",
                   "2030-02-30T03:04:05Z", "2030-01-02T24:04:05Z",
                   "2030-01-02T03:04:60Z", "0000-01-02T03:04:05Z",
                   "2030-01-02T03:04:05Z\n")
        for index, field in ((1, "not_before"), (1, "expires_at"), (2, "observed_at")):
            for bad in invalid:
                values = list(fixtures())
                values[index][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(contract.ContractError):
                    admission.validate_permit(*values)

    def test_permit_must_be_active(self):
        values = list(fixtures())
        values[1]["not_before"] = "2030-01-02T03:04:06Z"
        with self.assertRaisesRegex(contract.ContractError, "not active"):
            admission.validate_permit(*values)

    def test_window_positive_and_no_more_than_900_seconds(self):
        for end in ("2030-01-02T03:03:59Z", "2030-01-02T03:04:00Z",
                    "2030-01-02T03:19:01Z"):
            values = list(fixtures())
            values[1]["expires_at"] = end
            with self.subTest(end=end), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_remaining_budget_boundary_and_expiry(self):
        values = list(fixtures())
        values[1]["expires_at"] = "2030-01-02T03:11:35Z"
        self.assertEqual(admission.validate_permit(*values), values[1])
        for end in ("2030-01-02T03:11:34Z", "2030-01-02T03:04:05Z",
                    "2030-01-02T03:04:04Z"):
            values[1]["expires_at"] = end
            with self.subTest(end=end), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)
        values[1]["expires_at"] = "2030-01-02T03:11:35Z"
        values[3] += timedelta(microseconds=1)
        with self.assertRaises(contract.ContractError):
            admission.validate_permit(*values)

    def test_freshness_exact_five_seconds_is_allowed(self):
        values = list(fixtures())
        values[2]["observed_at"] = "2030-01-02T03:04:00Z"
        admission.validate_permit(*values)
        values[3] += timedelta(microseconds=1)
        with self.assertRaisesRegex(contract.ContractError, "future-dated or stale"):
            admission.validate_permit(*values)

    def test_future_and_stale_observations_fail(self):
        for observed in ("2030-01-02T03:04:06Z", "2030-01-02T03:03:59Z"):
            values = list(fixtures())
            values[2]["observed_at"] = observed
            with self.subTest(observed=observed), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_host_and_boot_drift_fail(self):
        for field, value in (("hostname", "another-sample-host"),
                             ("boot_id", "90941c8a-32c2-45c3-a526-381429617328")):
            values = list(fixtures())
            values[2]["host"][field] = value
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_host_identity_types_and_shape(self):
        for field, invalid in (("hostname", (True, "", "Upper", "a.b", "-sample", "x" * 64)),
                               ("boot_id", (True, "", "bad", "90941C8A-32C2-45C3-A526-381429617329"))):
            for value in invalid:
                for index in (1, 2):
                    values = list(fixtures())
                    values[index]["host"][field] = value
                    with self.subTest(index=index, field=field, value=value), self.assertRaises(contract.ContractError):
                        admission.validate_permit(*values)

    def test_competing_jobs_zero_integer_only(self):
        for bad in (True, False, 0.0, "0", 1, -1, None):
            values = list(fixtures())
            values[2]["competing_cpu_jobs"] = bad
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_gpu_process_collection_closed_and_bounded(self):
        for bad in (None, {}, (), True, [{"pid": 123, "start_ticks": 4567}] * 2):
            values = list(fixtures())
            values[2]["gpu_compute_processes"] = bad
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_idle_requires_no_gpu_process_or_extra_workload_fields(self):
        for bad in (True, False):
            values = list(fixtures("idle"))
            if bad:
                values[2]["gpu_compute_processes"] = [{"pid": 123, "start_ticks": 4567}]
            else:
                values[1]["workload"]["pid"] = 123
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_pinned_requires_exact_process_not_missing_or_reused_pid(self):
        for bad in ([], [{"pid": 124, "start_ticks": 4567}],
                    [{"pid": 123, "start_ticks": 4568}]):
            values = list(fixtures())
            values[2]["gpu_compute_processes"] = bad
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_process_id_bounds_and_no_bool_or_float(self):
        for field, invalid in (("pid", (True, False, 123.0, "123", 0, -1, 1 << 31)),
                               ("start_ticks", (True, 4567.0, "4567", 0, -1, 1 << 63))):
            for bad in invalid:
                for target in ("permit", "observation"):
                    values = list(fixtures())
                    if target == "permit":
                        values[1]["workload"][field] = bad
                    else:
                        values[2]["gpu_compute_processes"][0][field] = bad
                    with self.subTest(field=field, target=target, bad=bad), self.assertRaises(contract.ContractError):
                        admission.validate_permit(*values)

    def test_workload_modes_closed(self):
        for bad in (None, True, {}, {"mode": True}, {"mode": "training"},
                    {"mode": "pinned"}, {"mode": "idle", "extra": 1}):
            values = list(fixtures())
            values[1]["workload"] = bad
            with self.subTest(bad=bad), self.assertRaises(contract.ContractError):
                admission.validate_permit(*values)

    def test_revalidation_is_not_claim_or_authentication(self):
        values = fixtures()
        # This intentionally proves that the pure check is not a single-use
        # authority. A protected broker must perform the atomic claim itself.
        self.assertEqual(admission.validate_permit(*values), admission.validate_permit(*values))


if __name__ == "__main__":
    unittest.main()
