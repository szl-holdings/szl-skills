# SPDX-License-Identifier: Apache-2.0
"""Original synthetic-only acceptance tests; run under the bounded offline runner."""
import contextlib
import copy
import decimal
import io
import json
import pathlib
import runpy
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def package(name):
    return ROOT / "skills" / name


def fixture(name):
    return json.loads((package(name) / "assets" / "example.json").read_text(encoding="utf-8"))


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.audit = runpy.run_path(str(package("szl-artifact-lineage") / "kernel.py"))["szl_audit_lineage"]
        self.record = fixture("szl-artifact-lineage")

    def codes(self, record):
        return {finding["code"] for finding in self.audit(record)["findings"]}

    def test_complete_continuity_is_deterministic_and_not_execution(self):
        original = copy.deepcopy(self.record)
        first = self.audit(self.record)
        self.assertEqual(first, self.audit(self.record))
        self.assertEqual(self.record, original)
        self.assertEqual(first["status"], "CONTINUITY_ON_SUPPLIED_DIGESTS")
        self.assertEqual(first["stage_order"], ["normalize", "summarize"])
        self.assertEqual(first["findings"], [])
        self.assertTrue(all(item["observation"] == "MATCH" for item in first["artifacts"]))
        self.assertFalse(first["authentic"])
        self.assertFalse(first["transformations_executed"])
        self.assertFalse(first["code_and_config_pins_verified"])
        self.assertEqual(first["scientific_validity"], "NOT_MEASURED")

    def test_substituted_consumer_digest_breaks_all_three_bindings(self):
        self.record["stages"][1]["inputs"][0]["sha256"] = "e" * 64
        self.assertTrue({"INPUT_DIGEST_MISMATCH", "PARENT_DIGEST_MISMATCH", "OBSERVED_INPUT_MISMATCH"} <= self.codes(self.record))

    def test_substituted_producer_digest_breaks_parent_continuity(self):
        self.record["stages"][0]["outputs"][0]["sha256"] = "f" * 64
        self.assertTrue({"OUTPUT_DIGEST_MISMATCH", "PARENT_DIGEST_MISMATCH", "OBSERVED_OUTPUT_MISMATCH"} <= self.codes(self.record))

    def test_changed_source_readback_never_counts_as_match(self):
        self.record["observed_digests"]["raw-v1"] = "9" * 64
        self.assertTrue({"ARTIFACT_DIGEST_MISMATCH", "OBSERVED_INPUT_MISMATCH"} <= self.codes(self.record))

    def test_detached_input_and_missing_stage_are_findings(self):
        detached = copy.deepcopy(self.record)
        detached["stages"][0]["inputs"] = []
        self.assertIn("REQUIRED_INPUT_MISSING", self.codes(detached))
        absent = copy.deepcopy(self.record)
        absent["stages"].pop(0)
        self.assertTrue({"MISSING_STAGE", "MISSING_PRODUCER"} <= self.codes(absent))

    def test_missing_readback_reports_unobserved_input_and_output(self):
        del self.record["observed_digests"]["normalized-v1"]
        report = self.audit(self.record)
        self.assertTrue({"INPUT_NOT_OBSERVED", "OUTPUT_NOT_OBSERVED"} <= {item["code"] for item in report["findings"]})
        self.assertEqual(next(item for item in report["artifacts"] if item["id"] == "normalized-v1")["observation"], "NOT_OBSERVED")
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_duplicate_producer_and_cycle_are_rejected(self):
        duplicate = copy.deepcopy(self.record)
        stage = copy.deepcopy(duplicate["stages"][0])
        stage["id"] = "duplicate"
        duplicate["stages"].append(stage)
        with self.assertRaisesRegex(ValueError, "Duplicate producer"):
            self.audit(duplicate)
        cycle = copy.deepcopy(self.record)
        cycle["stages"][0]["required_inputs"] = ["summary-v1"]
        cycle["stages"][0]["inputs"] = [{"artifact": "summary-v1", "sha256": "c" * 64}]
        with self.assertRaisesRegex(ValueError, "Cycle"):
            self.audit(cycle)

    def test_unknown_edge_invalid_pin_duplicate_id_and_unknown_field_rejected(self):
        for mode in ("unknown-edge", "pin", "duplicate-id", "unknown-field", "bool-hash", "source-output"):
            with self.subTest(mode=mode):
                record = copy.deepcopy(self.record)
                if mode == "unknown-edge":
                    record["stages"][0]["inputs"][0]["artifact"] = "absent"
                elif mode == "pin":
                    record["stages"][0]["code_sha256"] = "main"
                elif mode == "duplicate-id":
                    record["artifacts"].append(copy.deepcopy(record["artifacts"][0]))
                elif mode == "unknown-field":
                    record["stages"][0]["command"] = "never execute this"
                elif mode == "bool-hash":
                    record["observed_digests"]["raw-v1"] = True
                else:
                    record["artifacts"][1]["kind"] = "source"
                with self.assertRaises(ValueError):
                    self.audit(record)

    def test_unlisted_stage_and_orphan_derived_artifact_require_review(self):
        self.record["required_stages"] = ["normalize"]
        self.record["artifacts"].append({"id": "orphan", "kind": "derived", "sha256": "7" * 64})
        self.assertTrue({"UNDECLARED_STAGE", "MISSING_PRODUCER"} <= self.codes(self.record))

    def test_bounds_and_sorted_findings(self):
        self.record["observed_digests"] = {}
        report = self.audit(self.record)
        keys = [(item["code"], item["stage"] or "", item["artifact"] or "") for item in report["findings"]]
        self.assertEqual(keys, sorted(keys))
        too_many = copy.deepcopy(self.record)
        too_many["artifacts"] *= 334
        with self.assertRaisesRegex(ValueError, "1..1000"):
            self.audit(too_many)

    def test_missing_edge_report_work_is_bounded(self):
        sources = ["source-" + str(position) for position in range(989)]
        stage_ids = ["stage-" + str(position) for position in range(11)]
        artifacts = [{"id": key, "kind": "source", "sha256": "a" * 64} for key in sources]
        stages = []
        for key in stage_ids:
            result = "result-" + key
            artifacts.append({"id": result, "kind": "derived", "sha256": "b" * 64})
            stages.append({"id": key, "operation": "synthetic boundedness fixture", "code_sha256": "c" * 64, "config_sha256": "d" * 64,
                           "required_inputs": sources, "inputs": [], "outputs": [{"artifact": result, "sha256": "b" * 64}]})
        record = {"schema": "szl.artifact-lineage.v1", "artifacts": artifacts, "stages": stages, "required_stages": stage_ids, "observed_digests": {}}
        with self.assertRaisesRegex(ValueError, "10000 total required input references"):
            self.audit(record)


class UnitInvariantTests(unittest.TestCase):
    def setUp(self):
        self.audit = runpy.run_path(str(package("szl-unit-invariants") / "kernel.py"))["szl_audit_unit_invariants"]
        self.record = fixture("szl-unit-invariants")

    def codes(self, record):
        return {finding["code"] for finding in self.audit(record)["findings"]}

    def test_exact_conversion_and_speed_definition(self):
        original = copy.deepcopy(self.record)
        report = self.audit(self.record)
        self.assertEqual(report, self.audit(self.record))
        self.assertEqual(self.record, original)
        self.assertEqual(report["status"], "PASS_DECLARED_CHECKS")
        self.assertEqual(next(item for item in report["quantities"] if item["id"] == "length-cm")["values_si"], ["1", "2"])
        self.assertTrue(all(item["cases_checked"] == 2 for item in report["invariants"]))
        self.assertFalse(report["observed_units_verified"])
        self.assertFalse(report["physical_law_verified"])

    def test_rounding_and_traps_are_independent_of_caller_context(self):
        self.record["quantities"][2]["values"] = ["3", "3"]
        self.record["quantities"][3]["values"] = ["0.33333333333333333333", "0.66666666666666666667"]
        self.record["invariants"][1]["atol_si"] = "1e-19"
        expected = self.audit(self.record)
        with decimal.localcontext() as context:
            context.prec = 3
            context.rounding = decimal.ROUND_DOWN
            context.traps[decimal.Inexact] = True
            self.assertEqual(self.audit(self.record), expected)

    def test_meters_plus_seconds_is_a_dimension_failure(self):
        self.record["invariants"][0]["operation"] = "sum"
        self.record["invariants"][0]["operands"] = ["length-m", "duration"]
        report = self.audit(self.record)
        item = next(item for item in report["invariants"] if item["id"] == "length-conversion")
        self.assertEqual(item["status"], "DIMENSION_MISMATCH")
        self.assertEqual(item["cases_checked"], 0)
        self.assertIn("DIMENSION_MISMATCH", self.codes(self.record))

    def test_thousandfold_scale_error_is_flagged_with_full_counts(self):
        self.record["quantities"][3]["values"] = ["500", "1000"]
        report = self.audit(self.record)
        self.assertTrue({"NUMERICAL_MISMATCH", "SCALE_MISMATCH", "RANGE_VIOLATION"} <= {item["code"] for item in report["findings"]})
        item = next(item for item in report["invariants"] if item["id"] == "speed-definition")
        self.assertEqual(item["scale_mismatch_count"], 2)
        self.assertEqual([case["scale_ratio"] for case in item["failures"]], ["1000", "1000"])

    def test_declared_dimension_mismatch_uses_actual_unit_dimension(self):
        self.record["quantities"][0]["dimension"] = [0, 0, 1, 0, 0, 0, 0]
        report = self.audit(self.record)
        self.assertIn("DECLARED_DIMENSION_MISMATCH", self.codes(self.record))
        self.assertEqual(next(item for item in report["invariants"] if item["id"] == "length-conversion")["status"], "AGREEMENT_ON_DECLARED_CASES")
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_nonfinite_boolean_expression_and_numeric_magnitude_rejected(self):
        for value in (float("nan"), float("inf"), True, "NaN", "1+1", "__import__('os')", "1e999", "1e-101", "1" * 81):
            with self.subTest(value=repr(value)):
                record = copy.deepcopy(self.record)
                record["quantities"][0]["values"][0] = value
                with self.assertRaises(ValueError):
                    self.audit(record)

    def test_missing_unsupported_offset_and_unknown_units_rejected(self):
        for unit in (None, "degC", "dB", "m + s", "meter", "M", []):
            with self.subTest(unit=unit):
                record = copy.deepcopy(self.record)
                record["quantities"][0]["unit"] = unit
                with self.assertRaises(ValueError):
                    self.audit(record)
        del self.record["quantities"][0]["unit"]
        with self.assertRaises(ValueError):
            self.audit(self.record)

    def test_zero_denominator_is_undefined_not_clamped(self):
        self.record["quantities"][2]["values"][0] = "0"
        report = self.audit(self.record)
        item = next(item for item in report["invariants"] if item["id"] == "speed-definition")
        self.assertEqual(item["status"], "UNDEFINED_RATIO")
        self.assertEqual(item["undefined_count"], 1)
        self.assertEqual(item["cases_checked"], 1)
        self.assertIn("UNDEFINED_RATIO", self.codes(self.record))

    def test_array_length_mismatch_is_not_broadcast(self):
        self.record["quantities"][2]["values"] = ["2"]
        report = self.audit(self.record)
        item = next(item for item in report["invariants"] if item["id"] == "speed-definition")
        self.assertEqual(item["status"], "ARRAY_LENGTH_MISMATCH")
        self.assertEqual(item["cases_checked"], 0)

    def test_product_sum_and_difference_follow_dimension_algebra(self):
        self.record["quantities"].extend([
            {"id": "sum-length", "unit": "m", "dimension": [1, 0, 0, 0, 0, 0, 0], "values": ["2", "4"], "range_si": None},
            {"id": "zero-length", "unit": "m", "dimension": [1, 0, 0, 0, 0, 0, 0], "values": ["0", "0"], "range_si": None}
        ])
        self.record["invariants"].extend([
            {"id": "speed-times-time", "operation": "product", "operands": ["speed", "duration"], "result": "length-m", "atol_si": "0", "rtol": "0"},
            {"id": "doubled-length", "operation": "sum", "operands": ["length-m", "length-m"], "result": "sum-length", "atol_si": "0", "rtol": "0"},
            {"id": "difference-length", "operation": "difference", "operands": ["length-m", "length-cm"], "result": "zero-length", "atol_si": "0", "rtol": "0"}
        ])
        self.assertEqual(self.audit(self.record)["status"], "PASS_DECLARED_CHECKS")

    def test_inclusive_range_and_predeclared_tolerance_boundary(self):
        self.record["quantities"][0]["range_si"] = {"min": "1", "max": "2"}
        self.record["quantities"][1]["values"][0] = "101"
        self.record["invariants"][0]["atol_si"] = "0.01"
        self.assertEqual(self.audit(self.record)["status"], "PASS_DECLARED_CHECKS")
        self.record["quantities"][1]["values"][0] = "101.1"
        self.assertIn("NUMERICAL_MISMATCH", self.codes(self.record))

    def test_duplicate_id_bad_dimension_negative_tolerance_and_expression_operation_rejected(self):
        for mode in ("duplicate", "dimension", "tolerance", "expression", "range", "operand"):
            with self.subTest(mode=mode):
                record = copy.deepcopy(self.record)
                if mode == "duplicate":
                    record["quantities"].append(copy.deepcopy(record["quantities"][0]))
                elif mode == "dimension":
                    record["quantities"][0]["dimension"][0] = True
                elif mode == "tolerance":
                    record["invariants"][0]["rtol"] = "-0.1"
                elif mode == "expression":
                    record["invariants"][0]["operation"] = "eval"
                elif mode == "range":
                    record["quantities"][0]["range_si"] = {"min": 2, "max": 1}
                else:
                    record["invariants"][0]["operands"] = ["unknown"]
                with self.assertRaises(ValueError):
                    self.audit(record)

    def test_bounded_failure_details_preserve_total_counts(self):
        for quantity in self.record["quantities"]:
            quantity["values"] = [quantity["values"][0]] * 101
        self.record["quantities"][3]["values"] = ["500"] * 101
        report = self.audit(self.record)
        item = next(item for item in report["invariants"] if item["id"] == "speed-definition")
        self.assertEqual(item["mismatch_count"], 101)
        self.assertEqual(len(item["failures"]), 100)
        mismatch = next(item for item in report["findings"] if item["code"] == "SCALE_MISMATCH")
        self.assertEqual(mismatch["count"], 101)
        self.assertEqual(len(mismatch["indexes"]), 100)

    def test_total_invariant_work_bound_is_enforced(self):
        for quantity in self.record["quantities"]:
            quantity["values"] = [quantity["values"][0]] * 11
        original = self.record["invariants"][0]
        self.record["invariants"] = [dict(original, id="check-" + str(position)) for position in range(1000)]
        with self.assertRaisesRegex(ValueError, "10000 total invariant cases"):
            self.audit(self.record)


class CliContractTests(unittest.TestCase):
    def test_standalone_cli_reports_and_exclusive_output_without_processes(self):
        for name in ("szl-artifact-lineage", "szl-unit-invariants"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                main = runpy.run_path(str(package(name) / "scripts" / "run.py"))["main"]
                output = pathlib.Path(temp) / "report.json"
                args = [str(package(name) / "assets" / "example.json"), "--output", str(output)]
                self.assertEqual(main(args), 0)
                retained = output.read_bytes()
                self.assertTrue(json.loads(retained)["schema"].endswith("report.v1"))
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args), 2)
                self.assertEqual(output.read_bytes(), retained)

    def test_duplicate_keys_nonfinite_json_and_size_limit_rejected(self):
        for name in ("szl-artifact-lineage", "szl-unit-invariants"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                main = runpy.run_path(str(package(name) / "scripts" / "run.py"))["main"]
                source = pathlib.Path(temp) / "input.json"
                for raw in ('{"schema":"first","schema":"second"}', '{"x":NaN}', " " * (1024 * 1024 + 1)):
                    source.write_text(raw, encoding="utf-8")
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main([str(source)]), 2)

    def test_completed_negative_audit_returns_one_and_retains_findings(self):
        for name in ("szl-artifact-lineage", "szl-unit-invariants"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                main = runpy.run_path(str(package(name) / "scripts" / "run.py"))["main"]
                record = fixture(name)
                if name == "szl-artifact-lineage":
                    record["observed_digests"] = {}
                else:
                    record["quantities"][3]["values"] = ["500", "1000"]
                source = pathlib.Path(temp) / "negative.json"
                source.write_text(json.dumps(record), encoding="utf-8")
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(main([str(source)]), 1)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report["status"], "REVIEW_REQUIRED")
                self.assertGreater(report["finding_count"], 0)


if __name__ == "__main__":
    unittest.main()
