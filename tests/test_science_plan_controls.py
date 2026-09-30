# SPDX-License-Identifier: Apache-2.0
"""Synthetic-only acceptance tests for original control and frozen-plan auditors.

No subprocesses, network, model calls, external data or host environment reads.
Run only through the contributor's reviewed bounded sandbox.
"""
import ast
import builtins
import contextlib
import copy
import hashlib
import io
import json
import pathlib
import runpy
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL = "szl-negative-control-audit"
PLAN = "szl-analysis-plan-audit"


def load(name):
    return runpy.run_path(str(ROOT / "skills" / name / "kernel.py"))


def example(name):
    return json.loads((ROOT / "skills" / name / "assets" / "example.json").read_text(encoding="utf-8"))


def codes(report):
    return {finding["code"] for finding in report["findings"]}


def invoke_cli(name, content, existing_output=False):
    """Invoke the authored CLI in-process, preserving interpreter arguments."""
    previous = sys.argv
    stdout, stderr = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as temporary:
        folder = pathlib.Path(temporary)
        input_file = folder / "synthetic.json"
        input_file.write_text(content, encoding="utf-8")
        sys.argv = ["run.py", str(input_file)]
        if existing_output:
            output = folder / "retained.json"
            output.write_text("retain this prior result", encoding="utf-8")
            sys.argv += ["--output", str(output)]
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = runpy.run_path(str(ROOT / "skills" / name / "scripts" / "run.py"))["main"]()
            retained = output.read_text(encoding="utf-8") if existing_output else None
        finally:
            sys.argv = previous
    return status, stdout.getvalue(), stderr.getvalue(), retained


class NegativeControlAuditTests(unittest.TestCase):
    def setUp(self):
        self.helper = load(CONTROL)
        self.audit = self.helper["szl_audit_negative_controls"]
        self.input = example(CONTROL)

    def outcome(self):
        return json.loads(next(a for a in self.input["artifacts"] if a["id"] == "outcome")["content_utf8"])

    def change_outcome(self, **updates):
        outcome = self.outcome()
        outcome.update(updates)
        artifact = next(a for a in self.input["artifacts"] if a["id"] == "outcome")
        artifact["content_utf8"] = json.dumps(outcome, separators=(",", ":"), allow_nan=False)
        artifact["sha256"] = hashlib.sha256(artifact["content_utf8"].encode("utf-8")).hexdigest()

    def change_artifact(self, key, content):
        artifact = next(a for a in self.input["artifacts"] if a["id"] == key)
        artifact["content_utf8"] = content
        artifact["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()

    def rebind_registry(self):
        self.change_outcome(registry_sha256=self.helper["canonical_sha256"](self.input["registry"]))

    def test_consistent_synthetic_registry_binds_actual_bytes(self):
        report = self.audit(self.input)
        self.assertEqual(report["status"], "CONTROLS_CONSISTENT_ON_SUPPLIED_EVIDENCE")
        self.assertEqual(report["findings"], [])
        self.assertTrue(all(a["binding"] == "MATCH" for a in report["artifacts"]))
        self.assertEqual(report["controls"][0]["delta"], 0.005)
        self.assertEqual(report["scientific_performance"], "NOT_MEASURED")
        self.assertFalse(report["execution_performed"])

    def test_abs_delta_boundary_is_inclusive(self):
        self.change_outcome(delta=-0.01)
        self.assertEqual(self.audit(self.input)["findings"], [])
        self.change_outcome(delta=-0.010001)
        self.assertIn("EXPECTED_NULL_FAILED", codes(self.audit(self.input)))

    def test_missing_registered_control_is_inconclusive(self):
        self.input["results"] = []
        report = self.audit(self.input)
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertIn("MISSING_REGISTERED_CONTROL", codes(report))

    def test_changed_input_and_scorer_are_detected_from_bytes(self):
        for key, expected in [("input", "CONTROL_INPUT_BINDING_MISMATCH"), ("scorer", "SCORER_BINDING_MISMATCH")]:
            with self.subTest(artifact=key):
                self.input = example(CONTROL)
                self.change_artifact(key, "changed synthetic artifact\n")
                self.assertIn(expected, codes(self.audit(self.input)))

    def test_declared_artifact_hash_mismatch_is_not_accepted(self):
        self.input["artifacts"][0]["sha256"] = "f" * 64
        report = self.audit(self.input)
        self.assertIn("ARTIFACT_DIGEST_MISMATCH", codes(report))
        self.assertEqual(report["status"], "INCONCLUSIVE")

    def test_changed_tolerance_cannot_hide_a_null_failure(self):
        self.change_outcome(tolerance=1.0, delta=0.5)
        self.assertTrue({"TOLERANCE_CHANGED", "EXPECTED_NULL_FAILED"} <= codes(self.audit(self.input)))

    def test_expected_null_with_directed_path_is_rejected(self):
        self.input["registry"]["graph"]["edges"].append(["dummy", "loss"])
        self.rebind_registry()
        self.assertIn("EXPECTED_NULL_HAS_CAUSAL_PATH", codes(self.audit(self.input)))

    def test_irrelevant_nuisance_is_not_shared(self):
        self.input["registry"]["graph"]["nodes"].append("unused")
        self.input["registry"]["controls"][0]["shared_nuisance"] = ["unused"]
        self.rebind_registry()
        self.assertIn("NUISANCE_NOT_SHARED_IN_DECLARED_GRAPH", codes(self.audit(self.input)))

    def test_common_readout_alone_does_not_establish_shared_nuisance(self):
        self.input["registry"]["graph"]["edges"].remove(["batch", "dummy"])
        self.rebind_registry()
        self.assertIn("NUISANCE_NOT_SHARED_IN_DECLARED_GRAPH", codes(self.audit(self.input)))

    def test_identity_contamination_and_late_freeze_are_inconclusive(self):
        self.change_outcome(control_id="different-control", contaminated=True, started_at="2025-12-31T23:59:59Z")
        found = codes(self.audit(self.input))
        self.assertTrue({"CONTROL_IDENTITY_MISMATCH", "CONTROL_CONTAMINATED", "CONTROL_TIMING_CONFLICT"} <= found)

    def test_failed_control_is_retained_without_numeric_estimate(self):
        self.change_outcome(status="FAILED", delta=None, failure_reason="Synthetic failed computation")
        report = self.audit(self.input)
        self.assertIn("CONTROL_NOT_SUCCESSFUL", codes(report))
        self.assertIsNone(report["controls"][0]["delta"])
        self.assertEqual(report["controls"][0]["status"], "FAILED")

    def test_duplicate_cycle_unbounded_and_nonfinite_inputs_are_invalid(self):
        base = example(CONTROL)
        for mode in ["duplicate", "cycle", "bool", "nan", "huge", "extra", "scope", "missing-field"]:
            self.input = copy.deepcopy(base)
            if mode == "duplicate":
                self.input["results"].append(copy.deepcopy(self.input["results"][0]))
            elif mode == "cycle":
                self.input["registry"]["graph"]["edges"].append(["loss", "algorithm"])
            elif mode == "extra":
                self.input["registry"]["unexpected"] = True
            elif mode == "scope":
                self.input["registry"]["scope"] = "WET_LAB"
            elif mode == "missing-field":
                del self.input["registry"]["controls"][0]["rationale"]
            else:
                self.input["registry"]["controls"][0]["tolerance"] = {"bool": True, "nan": float("nan"), "huge": 10 ** 1000}[mode]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.audit(self.input)

    def test_findings_are_stably_sorted(self):
        self.change_outcome(tolerance=1.0, delta=0.5, contaminated=True)
        report = self.audit(self.input)
        self.assertEqual(report["findings"], sorted(report["findings"], key=lambda f: (f["control_id"], f["code"], f["detail"])))


class AnalysisPlanAuditTests(unittest.TestCase):
    def setUp(self):
        self.helper = load(PLAN)
        self.audit = self.helper["szl_audit_analysis_plan"]
        self.input = example(PLAN)

    def test_fixed_plan_with_retained_failure_is_consistent(self):
        report = self.audit(self.input)
        self.assertEqual(report["status"], "CONSISTENT_WITH_DECLARED_PLAN")
        self.assertEqual(report["attempt_counts"], {"SUCCESS": 1, "FAILED": 1, "ABORTED": 0})
        self.assertEqual(report["observed_attempt_count"], report["scheduled_attempt_count"])
        self.assertEqual(report["preregistration"], "DECLARED_ONLY")
        self.assertEqual(report["statistical_inference"], "NOT_PERFORMED")

    def test_metric_direction_estimand_units_split_margin_and_exclusions_detected(self):
        edits = [("primary_metric", "mae"), ("direction", "HIGHER"), ("estimand", "Post hoc selected subgroup effect"), ("sample_unit", "horizon"), ("group_unit", "none"), ("split_sha256", "c" * 64), ("practical_margin", 0.0), ("exclusions", [])]
        for field, value in edits:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.input)
                payload["run"]["hypotheses"][0][field] = value
                report = self.audit(payload)
                self.assertEqual(report["status"], "EXPLORATORY")
                self.assertIn("UNLOGGED_DEVIATION", codes(report))
                self.assertIn("/hypotheses/h1/" + field, [v["path"] for v in report["changes"]])

    def test_changed_multiplicity_family_is_detected(self):
        self.input["run"]["families"][0]["method"] = "HOLM"
        report = self.audit(self.input)
        self.assertIn("/families/primary/method", [v["path"] for v in report["changes"]])

    def test_coherent_post_hoc_alpha_change_is_still_a_deviation(self):
        self.input["run"]["families"][0]["alpha"] = 0.04
        self.input["run"]["hypotheses"][0]["alpha"] = 0.04
        report = self.audit(self.input)
        paths = {value["path"] for value in report["changes"]}
        self.assertTrue({"/families/primary/alpha", "/hypotheses/h1/alpha"} <= paths)
        self.assertEqual(report["status"], "EXPLORATORY")

    def test_missing_failed_attempt_cannot_disappear(self):
        self.input["run"]["attempts"].pop()
        report = self.audit(self.input)
        self.assertIn("MISSING_SCHEDULED_ATTEMPT", codes(report))
        self.assertEqual(report["status"], "EXPLORATORY")

    def test_unscheduled_attempt_and_stopping_change_are_detected(self):
        extra = copy.deepcopy(self.input["run"]["attempts"][0])
        extra["id"] = "selected-repeat"
        self.input["run"]["attempts"].append(extra)
        self.input["run"]["stopping_rule"]["maximum_attempts"] = 1
        self.assertTrue({"UNSCHEDULED_ATTEMPT", "STOPPING_LIMIT_EXCEEDED", "UNLOGGED_DEVIATION"} <= codes(self.audit(self.input)))

    def test_unproven_precommit_and_wrong_plan_digest_are_exploratory(self):
        self.input["run"]["precommit_declared"] = False
        self.input["run"]["plan_sha256"] = "0" * 64
        self.assertTrue({"PRECOMMIT_UNPROVEN", "PLAN_DIGEST_MISMATCH"} <= codes(self.audit(self.input)))

    def test_correctly_bound_amendment_remains_exploratory(self):
        digest = self.helper["canonical_sha256"]
        self.input["run"]["hypotheses"][0]["direction"] = "HIGHER"
        self.input["deviations"] = [{"id": "amendment-1", "plan_version": 1, "path": "/hypotheses/h1/direction", "planned_sha256": digest("LOWER"), "observed_sha256": digest("HIGHER"), "declared_at": "2026-01-01T01:30:00Z", "reason": "Synthetic recorded amendment"}]
        report = self.audit(self.input)
        self.assertEqual(report["status"], "EXPLORATORY")
        self.assertIn("LOGGED_EXPLORATORY_DEVIATION", codes(report))
        self.assertIn("DEVIATION_AFTER_RUN_START", codes(report))
        self.assertNotIn("UNLOGGED_DEVIATION", codes(report))
        self.assertNotIn("DEVIATION_BINDING_MISMATCH", codes(report))

    def test_wrong_deviation_version_and_hash_are_detected(self):
        self.input["run"]["hypotheses"][0]["direction"] = "HIGHER"
        self.input["deviations"] = [{"id": "amendment-1", "plan_version": 2, "path": "/hypotheses/h1/direction", "planned_sha256": "a" * 64, "observed_sha256": "b" * 64, "declared_at": "2026-01-01T00:30:00Z", "reason": "Synthetic amendment with incorrect bindings"}]
        self.assertIn("DEVIATION_BINDING_MISMATCH", codes(self.audit(self.input)))

    def test_attempt_identity_and_timing_conflicts_are_detected(self):
        self.input["run"]["attempts"][0]["hypothesis_id"] = "other-hypothesis"
        self.input["run"]["attempts"][0]["recorded_at"] = "2026-01-01T03:00:00Z"
        self.assertTrue({"ATTEMPT_HYPOTHESIS_MISMATCH", "ATTEMPT_TIMING_CONFLICT"} <= codes(self.audit(self.input)))

    def test_invalid_family_duplicates_booleans_nonfinite_and_extra_fields(self):
        for mode in ["family", "duplicate", "bool", "nan", "huge", "extra", "failure-number"]:
            payload = copy.deepcopy(self.input)
            if mode == "family":
                payload["plan"]["families"][0]["size"] = 2
            elif mode == "duplicate":
                payload["run"]["attempts"].append(copy.deepcopy(payload["run"]["attempts"][0]))
            elif mode == "extra":
                payload["run"]["unknown"] = "not accepted"
            elif mode == "failure-number":
                payload["run"]["attempts"][1]["result_sha256"] = "f" * 64
            else:
                payload["plan"]["hypotheses"][0]["alpha"] = {"bool": True, "nan": float("nan"), "huge": 10 ** 1000}[mode]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.audit(payload)

    def test_malformed_result_digest_is_invalid_not_a_clean_report(self):
        self.input["run"]["attempts"][0]["result_sha256"] = "not-a-hash"
        with self.assertRaises(ValueError):
            self.audit(self.input)

    def test_findings_are_stably_sorted(self):
        self.input["run"]["precommit_declared"] = False
        self.input["run"]["attempts"] = []
        report = self.audit(self.input)
        self.assertEqual(report["findings"], sorted(report["findings"], key=lambda f: (f["path"], f["code"], f["detail"])))


class PlanControlCLITests(unittest.TestCase):
    def test_sidecar_ast_matches_loadable_conventions_and_schema(self):
        expected = {CONTROL: "szl.negative-control-audit.v1", PLAN: "szl.analysis-plan-audit.v1"}
        entrypoints = {CONTROL: "szl_audit_negative_controls", PLAN: "szl_audit_analysis_plan"}
        allowed = (ast.Expr, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign)
        for name in [CONTROL, PLAN]:
            with self.subTest(name=name):
                tree = ast.parse((ROOT / "skills" / name / "kernel.py").read_text(encoding="utf-8"))
                assignments, functions = {}, set()
                for node in tree.body:
                    self.assertIsInstance(node, allowed)
                    if isinstance(node, ast.FunctionDef):
                        functions.add(node.name)
                        self.assertFalse(node.name.startswith("_"))
                        self.assertNotIn(node.name, dir(builtins))
                        self.assertEqual(node.decorator_list, [])
                        for default in node.args.defaults:
                            ast.literal_eval(default)
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                assignments[target.id] = ast.literal_eval(node.value)
                self.assertEqual(assignments["SCHEMA"], expected[name])
                self.assertEqual(assignments["MAX_BYTES"], 1024 * 1024)
                self.assertIn(entrypoints[name], functions)

    def test_clean_synthetic_clis_return_zero(self):
        for name in [CONTROL, PLAN]:
            with self.subTest(name=name):
                status, output, errors, _ = invoke_cli(name, json.dumps(example(name)))
                self.assertEqual(status, 0)
                self.assertEqual(errors, "")
                self.assertEqual(json.loads(output)["scientific_performance"], "NOT_MEASURED")

    def test_inconclusive_and_exploratory_clis_return_one(self):
        for name in [CONTROL, PLAN]:
            payload = example(name)
            if name == CONTROL:
                payload["results"] = []
            else:
                payload["run"]["precommit_declared"] = False
            with self.subTest(name=name):
                self.assertEqual(invoke_cli(name, json.dumps(payload))[0], 1)

    def test_duplicate_nonfinite_deep_and_unknown_schema_are_nonzero(self):
        for name in [CONTROL, PLAN]:
            for content in ['{"schema":"first","schema":"second"}', '{"value":NaN}', '[' * 33 + '0' + ']' * 33, '{"schema":"unknown"}']:
                with self.subTest(name=name, content=content[:80]):
                    status, output, errors, _ = invoke_cli(name, content)
                    self.assertEqual(status, 2)
                    self.assertEqual(output, "")
                    self.assertIn("INVALID_INPUT", errors)

    def test_existing_output_is_retained_and_reports_failure(self):
        for name in [CONTROL, PLAN]:
            with self.subTest(name=name):
                status, _, errors, retained = invoke_cli(name, json.dumps(example(name)), existing_output=True)
                self.assertEqual(status, 2)
                self.assertIn("INVALID_INPUT", errors)
                self.assertEqual(retained, "retain this prior result")


if __name__ == "__main__":
    unittest.main()
