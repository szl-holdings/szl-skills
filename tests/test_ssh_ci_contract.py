"""Offline synthetic fixtures for the closed SSH CPU-build protocol."""

import base64
import copy
import json
import unittest
from unittest import mock

from tools import ssh_ci_contract as contract


def request_fixture():
    return {
        "schema": contract.REQUEST_SCHEMA,
        "operation": "build",
        "request_id": "c96c7054-648e-43b1-9e0b-f2474141496a",
        "permit_id": "02be74d5-2377-4b15-a4f7-419d21e41fcb",
        "repository": contract.REPOSITORY,
        "source_sha": "a" * 40,
        "controller_sha": "b" * 40,
        "workflow_ref": contract.WORKFLOW_REF,
        "run_id": 1234,
        "run_attempt": 1,
    }


def response_fixture(request=None):
    request = request_fixture() if request is None else request
    payloads = {name: ("SAMPLE fixture for " + name).encode("utf-8")
                for name in contract.REQUIRED_FILES}
    identity = {"hostname": "sample-build-host", "pid": 987,
                "start_ticks": 123456,
                "boot_id": "a239a737-e860-4ec8-aef8-d90b62a0c1dd"}
    receipt = {field: request[field] for field in contract.BINDING_FIELDS}
    receipt.update({
        "schema": contract.RECEIPT_SCHEMA,
        "request_sha256": contract.sha256(contract.canonical(request)),
        "result": "PASS", "evidence_class": "DECLARED", "signed": False,
        "independent_witness": False, "gpu_requests": 0,
        "actual_cpu_profile": dict(contract.CPU_PROFILE),
        "isolation_verified": True,
        "runtime_before": dict(identity), "runtime_after": dict(identity),
        "steps": [{"name": name, "returncode": 0} for name in contract.STEP_NAMES],
        "files": {name: {"sha256": contract.sha256(data), "bytes": len(data)}
                  for name, data in payloads.items()},
    })
    envelope = {"schema": contract.RESPONSE_SCHEMA,
                "request_sha256": receipt["request_sha256"], "receipt": receipt,
                "files": {name: base64.b64encode(data).decode("ascii")
                          for name, data in payloads.items()}}
    return envelope, payloads


class JsonTests(unittest.TestCase):
    def test_canonical_bytes_and_hash_are_stable(self):
        self.assertEqual(contract.canonical({"b": 2, "a": "lambda"}),
                         b'{"a":"lambda","b":2}')
        self.assertEqual(contract.sha256(b"abc"),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_duplicate_members_rejected_at_any_depth(self):
        for data in ('{"a":1,"a":2}', '{"x":{"a":1,"a":1}}',
                     '{"a":1,"\\u0061":2}'):
            with self.subTest(data=data), self.assertRaises(contract.ContractError):
                contract.strict_json_loads(data)

    def test_nonfinite_numbers_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e9999"):
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.strict_json_loads('{"a":' + value + "}")
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.canonical({"a": value})

    def test_malformed_encoding_and_json_rejected(self):
        for data in (b"\xff", b"\xef\xbb\xbf{}", b"{}{}", b"", '{"a":"\\ud800"}'):
            with self.subTest(data=data), self.assertRaises(contract.ContractError):
                contract.strict_json_loads(data)

    def test_size_limit_counts_utf8_bytes(self):
        with self.assertRaises(contract.ContractError):
            contract.strict_json_loads('"\u03bb\u03bb"', max_bytes=5)
        self.assertEqual(contract.strict_json_loads(b"{}", max_bytes=2), {})

    def test_deep_json_rejected(self):
        data = "[" * 40 + "0" + "]" * 40
        with self.assertRaises(contract.ContractError):
            contract.strict_json_loads(data)

    def test_non_json_or_cyclic_values_rejected(self):
        cycle = []
        cycle.append(cycle)
        for value in (cycle, {1: "a"}, {"a": (1,)}, {"a": b"x"}, {"a": {1}}):
            with self.subTest(kind=type(value)), self.assertRaises(contract.ContractError):
                contract.canonical(value)

    def test_sha256_requires_bytes(self):
        for data in ("abc", bytearray(b"abc"), None):
            with self.subTest(data=data), self.assertRaises(contract.ContractError):
                contract.sha256(data)


class RequestTests(unittest.TestCase):
    def test_valid_request_accepts_raw_and_dict_without_mutation(self):
        request = request_fixture()
        original = copy.deepcopy(request)
        for source in (request, contract.canonical(request), json.dumps(request)):
            self.assertEqual(contract.validate_request(source), request)
        self.assertEqual(request, original)
        self.assertIsNot(contract.validate_request(request), request)

    def test_every_field_is_required_and_unknown_fields_fail(self):
        for field in contract.REQUEST_FIELDS:
            request = request_fixture()
            del request[field]
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                contract.validate_request(request)
        request = request_fixture()
        request["command"] = "arbitrary"
        with self.assertRaises(contract.ContractError):
            contract.validate_request(request)

    def test_wrong_operation_repository_and_workflow_fail(self):
        for field, value in (("operation", "eval"), ("repository", "other/repo"),
                             ("workflow_ref", contract.WORKFLOW_REF.replace("main", "topic")),
                             ("schema", "szl.gpu-eval-slot.v2")):
            request = request_fixture()
            request[field] = value
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                contract.validate_request(request)

    def test_consumed_gpu_grant_is_not_cpu_request(self):
        consumed_grant = {"schema": "szl.gpu-eval-slot.v2", "granted": True,
                          "max_requests": 8, "execution_claim": "consumed"}
        with self.assertRaises(contract.ContractError):
            contract.validate_request(consumed_grant)

    def test_only_canonical_uuids_accepted(self):
        for field in ("request_id", "permit_id"):
            good = request_fixture()[field]
            for value in (good.upper(), "{" + good + "}", good.replace("-", ""),
                          "bad", True, None):
                request = request_fixture()
                request[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                    contract.validate_request(request)

    def test_source_and_controller_are_full_lowercase_sha(self):
        for field in ("source_sha", "controller_sha"):
            for value in ("a" * 39, "a" * 41, "A" * 40, "g" * 40, "main", None):
                request = request_fixture()
                request[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                    contract.validate_request(request)

    def test_run_identity_rejects_bool_float_retry_and_out_of_range(self):
        for field, values in (("run_id", (True, 1.0, "1", 0, -1, 1 << 63)),
                               ("run_attempt", (True, 1.0, "1", 0, 2))):
            for value in values:
                request = request_fixture()
                request[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                    contract.validate_request(request)

    def test_request_wire_duplicate_and_size_limits(self):
        raw = contract.canonical(request_fixture())
        with self.assertRaises(contract.ContractError):
            contract.validate_request(raw[:-1] + b',"run_id":5}')
        with self.assertRaises(contract.ContractError):
            contract.validate_request(b" " * contract.MAX_REQUEST_BYTES + raw)
        request = request_fixture()
        request["source_sha"] = "a" * (contract.MAX_REQUEST_BYTES + 1)
        with self.assertRaises(contract.ContractError):
            contract.validate_request(request)

    def test_non_objects_rejected(self):
        for value in ([], None, 1, "[]", "null"):
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.validate_request(value)


class ResponseTests(unittest.TestCase):
    def test_exact_bytes_returned_without_network_or_execution(self):
        request = request_fixture()
        envelope, expected = response_fixture(request)
        original = copy.deepcopy(envelope)
        with mock.patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process forbidden")):
            for value in (envelope, contract.canonical(envelope), json.dumps(envelope)):
                self.assertEqual(contract.verify_response(request, value), expected)
        self.assertEqual(envelope, original)

    def test_tar_is_opaque_and_never_extracted(self):
        envelope, expected = response_fixture()
        self.assertEqual(contract.verify_response(request_fixture(), envelope)["packages.tar"],
                         expected["packages.tar"])
        self.assertNotEqual(expected["packages.tar"][:5], b"ustar")

    def test_blocked_receipt_remains_blocked(self):
        envelope, expected = response_fixture()
        envelope["receipt"]["result"] = "BLOCKED"
        envelope["receipt"]["isolation_verified"] = False
        envelope["receipt"]["steps"][0]["returncode"] = 1
        self.assertEqual(contract.verify_response(request_fixture(), envelope), expected)
        self.assertEqual(envelope["receipt"]["result"], "BLOCKED")

    def test_envelope_and_receipt_fields_closed(self):
        for location in (None, "receipt"):
            envelope, _ = response_fixture()
            target = envelope if location is None else envelope[location]
            for field in tuple(target):
                candidate = copy.deepcopy(envelope)
                inner = candidate if location is None else candidate[location]
                del inner[field]
                with self.subTest(location=location, missing=field), self.assertRaises(contract.ContractError):
                    contract.verify_response(request_fixture(), candidate)
            target["extra"] = "unexpected"
            with self.subTest(location=location), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)

    def test_request_hash_and_all_bindings_are_checked(self):
        for location in (None, "receipt"):
            envelope, _ = response_fixture()
            target = envelope if location is None else envelope[location]
            target["request_sha256"] = "c" * 64
            with self.subTest(location=location), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)
        for field in contract.BINDING_FIELDS:
            envelope, _ = response_fixture()
            value = envelope["receipt"][field]
            envelope["receipt"][field] = value + 1 if type(value) is int else value + "x"
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)

    def test_response_for_different_request_cannot_replay(self):
        envelope, _ = response_fixture()
        request = request_fixture()
        request["request_id"] = "2c831687-0048-4c87-b732-cf5c270a265e"
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request, envelope)

    def test_honesty_and_zero_gpu_fields_strictly_typed(self):
        cases = {"signed": (True, 0), "independent_witness": (True, 0),
                 "gpu_requests": (1, False, 0.0),
                 "evidence_class": ("MEASURED", "REPORTED", "SAMPLE"),
                 "result": ("SUCCESS", True), "isolation_verified": (1, "true")}
        for field, values in cases.items():
            for value in values:
                envelope, _ = response_fixture()
                envelope["receipt"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                    contract.verify_response(request_fixture(), envelope)

    def test_pass_needs_isolation_declaration(self):
        envelope, _ = response_fixture()
        envelope["receipt"]["isolation_verified"] = False
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), envelope)

    def test_cpu_profile_exact_no_bool_integer_confusion(self):
        for field, value in contract.CPU_PROFILE.items():
            envelope, _ = response_fixture()
            bad = (not value) if type(value) is bool else value + 1 if type(value) is int else "host"
            envelope["receipt"]["actual_cpu_profile"][field] = bad
            with self.subTest(field=field), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)
        for value in (False, 0.0):
            envelope, _ = response_fixture()
            envelope["receipt"]["actual_cpu_profile"]["memory_swap_max_bytes"] = value
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)
        envelope, _ = response_fixture()
        envelope["receipt"]["actual_cpu_profile"]["extra"] = True
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), envelope)

    def test_identity_fields_and_pre_post_continuity(self):
        cases = {"hostname": ("bad/name", "BAD", "", "a" * 64),
                 "pid": (False, 0, -1, 1.0, 1 << 31),
                 "start_ticks": (True, 0, 1.0, 1 << 63),
                 "boot_id": ("bad", "A239A737-E860-4EC8-AEF8-D90B62A0C1DD")}
        for field, values in cases.items():
            for value in values:
                envelope, _ = response_fixture()
                for side in ("runtime_before", "runtime_after"):
                    envelope["receipt"][side][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                    contract.verify_response(request_fixture(), envelope)
        envelope, _ = response_fixture()
        envelope["receipt"]["runtime_after"]["pid"] += 1
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), envelope)
        envelope, _ = response_fixture()
        envelope["receipt"]["runtime_before"]["cmdline"] = "unexpected"
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), envelope)

    def test_steps_exact_order_and_zero_for_pass(self):
        envelope, _ = response_fixture()
        good = envelope["receipt"]["steps"]
        bad_steps = (list(reversed(good)), good[:-1], good + [good[0]],
                     [{"name": "shell", "returncode": 0}] + good[1:])
        for steps in bad_steps:
            candidate = copy.deepcopy(envelope)
            candidate["receipt"]["steps"] = steps
            with self.subTest(steps=steps), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), candidate)
        for code in (True, 0.0, 1, -9, 1 << 31):
            candidate = copy.deepcopy(envelope)
            candidate["receipt"]["steps"][0]["returncode"] = code
            with self.subTest(code=code), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), candidate)

    def test_missing_extra_traversal_absolute_and_encoded_names_rejected(self):
        bad_names = ("../selfcheck.stdout", "/selfcheck.stdout", "folder/file",
                     "folder\\file", "%2e%2e%2fselfcheck.stdout", "file:stream",
                     "SELFCHECK.stdout", "selfcheck.stdout.", "receipt.json")
        for location in ("wire", "metadata"):
            envelope, _ = response_fixture()
            target = envelope["files"] if location == "wire" else envelope["receipt"]["files"]
            del target["selfcheck.stdout"]
            with self.subTest(location=location, missing=True), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)
            for name in bad_names:
                envelope, _ = response_fixture()
                target = envelope["files"] if location == "wire" else envelope["receipt"]["files"]
                target[name] = target["selfcheck.stdout"]
                with self.subTest(location=location, name=name), self.assertRaises(contract.ContractError):
                    contract.verify_response(request_fixture(), envelope)

    def test_links_are_not_representable_as_file_values_or_metadata(self):
        envelope, _ = response_fixture()
        envelope["files"]["selfcheck.stdout"] = {"symlink": "target"}
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), envelope)
        envelope, _ = response_fixture()
        envelope["receipt"]["files"]["selfcheck.stdout"]["symlink"] = "target"
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), envelope)

    def test_tampered_bytes_hash_and_size_rejected(self):
        envelope, _ = response_fixture()
        envelope["files"]["selfcheck.stdout"] = base64.b64encode(b"X" * 35).decode("ascii")
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), envelope)
        for field, value in (("sha256", "0" * 64), ("bytes", 1), ("bytes", True),
                             ("bytes", -1), ("bytes", 1.0)):
            envelope, _ = response_fixture()
            envelope["receipt"]["files"]["selfcheck.stdout"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)

    def test_base64_invalid_and_noncanonical_rejected(self):
        for encoded in ("YQ=\n", "YQ!!", "YR==", "\u00e9Q=="):
            envelope, _ = response_fixture()
            envelope["receipt"]["files"]["selfcheck.stdout"] = {
                "bytes": 1, "sha256": contract.sha256(b"a")}
            envelope["files"]["selfcheck.stdout"] = encoded
            with self.subTest(encoded=encoded), self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)

    def test_per_file_and_aggregate_size_rejected_before_decode(self):
        for name, sizes in (("single", [contract.MAX_OUTPUT_BYTES + 1]),
                            ("aggregate", [contract.MAX_OUTPUT_BYTES, 1])):
            envelope, _ = response_fixture()
            for filename, size in zip(sorted(contract.REQUIRED_FILES), sizes):
                envelope["receipt"]["files"][filename]["bytes"] = size
            with mock.patch.object(base64, "b64decode", side_effect=AssertionError("must not decode")):
                with self.subTest(name=name), self.assertRaises(contract.ContractError):
                    contract.verify_response(request_fixture(), envelope)

    def test_exact_output_limit_is_accepted_and_plus_one_rejected(self):
        envelope, expected = response_fixture()
        total = sum(len(data) for data in expected.values())
        # Exercise the exact same production bound checks without allocating 16 MiB.
        with mock.patch.object(contract, "MAX_OUTPUT_BYTES", total):
            self.assertEqual(contract.verify_response(request_fixture(), envelope), expected)
        with mock.patch.object(contract, "MAX_OUTPUT_BYTES", total - 1):
            with self.assertRaises(contract.ContractError):
                contract.verify_response(request_fixture(), envelope)

    def test_envelope_wire_and_decoded_size_limits(self):
        envelope, _ = response_fixture()
        raw = contract.canonical(envelope)
        with mock.patch.object(contract, "MAX_RESPONSE_BYTES", len(raw)):
            contract.verify_response(request_fixture(), raw)
        with mock.patch.object(contract, "MAX_RESPONSE_BYTES", len(raw) - 1):
            for value in (raw, envelope):
                with self.subTest(kind=type(value)), self.assertRaises(contract.ContractError):
                    contract.verify_response(request_fixture(), value)

    def test_wire_duplicate_receipt_member_rejected(self):
        envelope, _ = response_fixture()
        raw = contract.canonical(envelope)
        raw = raw.replace(b'"gpu_requests":0', b'"gpu_requests":0,"gpu_requests":0')
        with self.assertRaises(contract.ContractError):
            contract.verify_response(request_fixture(), raw)


if __name__ == "__main__":
    unittest.main()
