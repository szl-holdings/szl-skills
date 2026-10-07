# SPDX-License-Identifier: Apache-2.0
"""Benchmark parity (szl.benchmark-parity/v1) for szl-dataset-readiness; synthetic specs only.

Missing evidence must stay UNKNOWN, never PARITY_OK, and the existing table audit, its examples
and the mirrored kernel must keep the reports they produced on main.
"""
import ast
import builtins
import copy
import hashlib
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-dataset-readiness"
CLI = SKILL / "scripts" / "run.py"
AUDIT = runpy.run_path(str(SKILL / "parity.py"))["szl_audit_benchmark_parity"]
SYSTEM_FIELDS = ("evaluation_row_ids_sha256", "tuning_trials", "compute_budget", "seed_count",
                 "scorer_sha256", "transforms")
# SHA-256 of the CLI stdout (newlines normalized to LF) for each pre-existing example, computed
# with the unmodified skill at base commit b8e259c (identical skill bytes at df6d2d7).
MAIN_REPORT_SHA256 = {
    "example.json": "3ce3fcaf45dcb36c5ca2e5446bfe7044d1c27ad62b488a38cd74751372cc74c0",
    "leakage-clean.json": "5ac759bec9cf0fd286e769e2f95731ee034c42e22e05b2189a3a72f37af1124d",
}


def fixture(name):
    return json.loads((SKILL / "assets" / name).read_text(encoding="utf-8"))


def row_ids_sha256(row_ids):
    """Independent restatement of the fixture convention: sorted ids as compact ASCII JSON."""
    return hashlib.sha256(json.dumps(sorted(row_ids), separators=(",", ":")).encode()).hexdigest()


def run_cli(*args):
    return subprocess.run([sys.executable, "-B", str(CLI), *map(str, args)], capture_output=True,
                          timeout=60, check=False)


class ExistingBehaviourTests(unittest.TestCase):
    def test_existing_examples_keep_main_report_bytes(self):
        self.assertEqual(sorted(p.name for p in (SKILL / "assets").glob("*.json") if not p.name.startswith("parity-")),
                         sorted(MAIN_REPORT_SHA256))
        for name, expected in MAIN_REPORT_SHA256.items():
            with self.subTest(example=name):
                result = run_cli(SKILL / "assets" / name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(hashlib.sha256(result.stdout.replace(b"\r\n", b"\n")).hexdigest(), expected)

    def test_table_mode_exit_codes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            duplicate = pathlib.Path(temp) / "duplicate.json"
            duplicate.write_text('{"rows": [], "rows": []}', encoding="utf-8")
            self.assertEqual(run_cli(duplicate).returncode, 1)
        self.assertEqual(run_cli().returncode, 2)


class ParityFixtureTests(unittest.TestCase):
    def test_fixture_digests_follow_the_documented_convention(self):
        for name in ("parity-example.json", "parity-issues.json"):
            with self.subTest(fixture=name):
                spec = fixture(name)
                held = [row["id"] for row in spec["rows"] if row["split"] == spec["held_out"]["split"]]
                self.assertEqual(spec["held_out"]["row_ids_sha256"], row_ids_sha256(held))
        issues = fixture("parity-issues.json")
        self.assertEqual(issues["systems"][1]["evaluation_row_ids_sha256"], row_ids_sha256(["te-1"]))

    def test_positive_fixture_has_no_checked_parity_issue(self):
        report = AUDIT(fixture("parity-example.json"))
        self.assertEqual(report["status"], "PARITY_OK")
        self.assertEqual(report["issues"], [])
        self.assertEqual({c["status"] for c in report["checks"].values()}, {"PARITY_OK"})
        self.assertEqual(report["declared_asymmetries"], [])
        self.assertEqual((report["scientific_suitability"], report["behavioral_evaluation"], report["readiness"]),
                         ("NOT_ASSESSED", "NOT_MEASURED", "REVIEW"))

    def test_negative_fixture_names_each_issue_and_lists_the_declared_asymmetry(self):
        report = AUDIT(fixture("parity-issues.json"))
        self.assertEqual(report["status"], "PARITY_ISSUES")
        self.assertEqual(report["issues"], ["BUDGET_ASYMMETRY", "EVALUATION_ROWS_MISMATCH",
                                            "PREPROCESSING_FIT_SCOPE_LEAKAGE", "SCORER_MISMATCH",
                                            "SEED_COUNT_MISMATCH"])
        checks = report["checks"]
        self.assertEqual(checks["baseline"]["status"], "PARITY_OK")
        self.assertEqual(checks["evaluation_rows"]["findings"][0]["by_system"],
                         {"candidate-gbm": row_ids_sha256(["te-1"])})
        self.assertEqual(checks["budget"]["findings"], [{"code": "BUDGET_ASYMMETRY", "field": "tuning_trials",
                                                         "by_system": {"baseline-ridge": 20, "candidate-gbm": 100}}])
        self.assertEqual(report["declared_asymmetries"], [{
            "field": "compute_budget", "reason": "Synthetic: compute difference disclosed in the write-up",
            "by_system": {"baseline-ridge": 2, "candidate-gbm": 10}}])
        leak = checks["preprocessing"]["findings"]
        self.assertEqual([(f["code"], f["system"], f["transform"]) for f in leak],
                         [("PREPROCESSING_FIT_SCOPE_LEAKAGE", "candidate-gbm", "scale")])

    def test_report_is_deterministic_and_binds_the_spec(self):
        spec = fixture("parity-example.json")
        first, second = AUDIT(spec), AUDIT(copy.deepcopy(spec))
        self.assertEqual(first, second)
        canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self.assertEqual(first["spec_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(spec, fixture("parity-example.json"))


class ParityRuleTests(unittest.TestCase):
    def setUp(self):
        self.spec = fixture("parity-example.json")

    def audit(self):
        return AUDIT(self.spec)

    def test_every_absent_system_field_is_unknown_never_parity_ok(self):
        homes = {"evaluation_row_ids_sha256": "evaluation_rows", "tuning_trials": "budget",
                 "compute_budget": "budget", "seed_count": "seeds", "scorer_sha256": "scorer",
                 "transforms": "preprocessing"}
        for field in SYSTEM_FIELDS:
            for index in (0, 1):
                with self.subTest(field=field, system=index):
                    spec = fixture("parity-example.json")
                    sid = spec["systems"][index]["id"]
                    del spec["systems"][index][field]
                    report = AUDIT(spec)
                    self.assertEqual(report["status"], "UNKNOWN")
                    self.assertEqual(report["checks"][homes[field]]["status"], "UNKNOWN")
                    self.assertIn({"system": sid, "missing": field}, report["checks"][homes[field]]["missing_evidence"])
                    spec["systems"][index][field] = None
                    self.assertEqual(AUDIT(spec)["status"], "UNKNOWN")

    def test_absent_spec_level_evidence_is_unknown(self):
        for field, check in (("held_out", "evaluation_rows"), ("compute_unit", "budget"),
                             ("training_splits", "preprocessing"), ("rows", "preprocessing")):
            with self.subTest(field=field):
                spec = fixture("parity-example.json")
                del spec[field]
                report = AUDIT(spec)
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertEqual(report["checks"][check]["status"], "UNKNOWN")

    def test_systems_without_transforms_need_no_rows_or_training_scope(self):
        spec = fixture("parity-example.json")
        spec["systems"][0]["transforms"] = []
        del spec["rows"], spec["training_splits"]
        self.assertEqual(AUDIT(spec)["status"], "PARITY_OK")

    def test_baseline_is_required(self):
        self.spec["systems"][0]["role"] = "candidate"
        report = self.audit()
        self.assertEqual((report["status"], report["issues"]), ("PARITY_ISSUES", ["NO_BASELINE"]))

    def test_undeclared_budget_difference_is_an_issue_and_declared_one_is_listed(self):
        self.spec["systems"][1]["tuning_trials"] = 21
        report = self.audit()
        self.assertEqual((report["status"], report["issues"]), ("PARITY_ISSUES", ["BUDGET_ASYMMETRY"]))
        self.spec["declared_asymmetries"] = {"tuning_trials": "Synthetic disclosed difference"}
        report = self.audit()
        self.assertEqual(report["status"], "PARITY_OK")
        self.assertEqual(report["declared_asymmetries"][0]["by_system"], {"baseline-ridge": 20, "candidate-gbm": 21})
        self.spec["systems"][1]["compute_budget"] = 2.5
        self.assertEqual(self.audit()["issues"], ["BUDGET_ASYMMETRY"])

    def test_equal_numbers_in_int_and_float_form_are_equal_budgets(self):
        self.spec["systems"][1]["compute_budget"] = 2.0
        self.assertEqual(self.audit()["status"], "PARITY_OK")

    def test_seed_and_scorer_differences_cannot_be_declared_away(self):
        self.spec["systems"][1]["seed_count"] = 5
        self.spec["systems"][1]["scorer_sha256"] = "a" * 64
        report = self.audit()
        self.assertEqual(report["issues"], ["SCORER_MISMATCH", "SEED_COUNT_MISMATCH"])
        for field in ("seed_count", "scorer_sha256"):
            spec = copy.deepcopy(self.spec)
            spec["declared_asymmetries"] = {field: "not allowed"}
            with self.subTest(field=field), self.assertRaises(ValueError):
                AUDIT(spec)

    def test_evaluation_rows_must_equal_the_declared_held_out_digest(self):
        for index in (0, 1):
            with self.subTest(system=index):
                spec = fixture("parity-example.json")
                spec["systems"][index]["evaluation_row_ids_sha256"] = row_ids_sha256(["te-2"])
                report = AUDIT(spec)
                self.assertEqual(report["issues"], ["EVALUATION_ROWS_MISMATCH"])
                self.assertEqual(list(report["checks"]["evaluation_rows"]["findings"][0]["by_system"]),
                                 [spec["systems"][index]["id"]])
        both = fixture("parity-example.json")
        for system in both["systems"]:
            system["evaluation_row_ids_sha256"] = row_ids_sha256(["te-2"])
        self.assertEqual(AUDIT(both)["issues"], ["EVALUATION_ROWS_MISMATCH"])

    def test_disagreeing_systems_are_an_issue_even_without_held_out_digest(self):
        del self.spec["held_out"]
        self.spec["systems"][1]["evaluation_row_ids_sha256"] = row_ids_sha256(["te-1"])
        report = self.audit()
        self.assertEqual(report["status"], "PARITY_ISSUES")
        self.assertEqual(report["checks"]["evaluation_rows"]["missing_evidence"], [{"missing": "held_out.row_ids_sha256"}])
        self.assertEqual(len(report["checks"]["evaluation_rows"]["findings"][0]["by_system"]), 2)

    def test_preprocessing_reuses_kernel_fit_scope_rules(self):
        transform = self.spec["systems"][0]["transforms"][0]
        transform["fit_row_ids"] = ["tr-1", "te-1"]
        report = self.audit()
        self.assertEqual(report["status"], "PARITY_ISSUES")
        self.assertEqual({f["code"] for f in report["checks"]["preprocessing"]["findings"]},
                         {"PREPROCESSING_FIT_SCOPE_LEAKAGE", "FIT_SPLIT_DECLARATION_MISMATCH"})
        self.assertTrue(all(f["system"] == "baseline-ridge" for f in report["checks"]["preprocessing"]["findings"]))
        transform["fit_row_ids"] = ["tr-1", "tr-unlisted"]
        report = self.audit()
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn({"transform": "scale", "reason": "FIT_ROW_NOT_IN_SELECTED_EVIDENCE", "system": "baseline-ridge"},
                      report["checks"]["preprocessing"]["missing_evidence"])

    def test_contract_violations_raise(self):
        def mutate(change):
            spec = fixture("parity-example.json")
            change(spec)
            return spec

        cases = {
            "not an object": [],
            "unknown field": mutate(lambda s: s.update(extra=1)),
            "schema": mutate(lambda s: s.update(schema="szl.benchmark-parity/v2")),
            "one system": mutate(lambda s: s["systems"].pop()),
            "65 systems": mutate(lambda s: s.update(systems=[dict(s["systems"][1], id="s%d" % i) for i in range(65)])),
            "duplicate id": mutate(lambda s: s["systems"][1].update(id="baseline-ridge")),
            "missing id": mutate(lambda s: s["systems"][1].pop("id")),
            "role": mutate(lambda s: s["systems"][1].update(role="oracle")),
            "system field": mutate(lambda s: s["systems"][1].update(score=0.9)),
            "uppercase digest": mutate(lambda s: s["systems"][1].update(scorer_sha256="A" * 64)),
            "short digest": mutate(lambda s: s["held_out"].update(row_ids_sha256="ab")),
            "boolean trials": mutate(lambda s: s["systems"][1].update(tuning_trials=True)),
            "negative trials": mutate(lambda s: s["systems"][1].update(tuning_trials=-1)),
            "string compute": mutate(lambda s: s["systems"][1].update(compute_budget="2")),
            "negative compute": mutate(lambda s: s["systems"][1].update(compute_budget=-0.5)),
            "zero seeds": mutate(lambda s: s["systems"][1].update(seed_count=0)),
            "empty unit": mutate(lambda s: s.update(compute_unit=" ")),
            "held-out split trains": mutate(lambda s: s["training_splits"].append("test")),
            "held_out field": mutate(lambda s: s["held_out"].update(rows=4)),
            "row field": mutate(lambda s: s["rows"][0].update(label=1)),
            "duplicate row": mutate(lambda s: s["rows"].append({"id": "tr-1", "split": "train"})),
            "row split type": mutate(lambda s: s["rows"][0].update(split=1)),
            "empty training": mutate(lambda s: s.update(training_splits=[])),
            "declared field": mutate(lambda s: s.update(declared_asymmetries={"seeds": "x"})),
            "declared reason": mutate(lambda s: s.update(declared_asymmetries={"tuning_trials": ""})),
            "transform shape": mutate(lambda s: s["systems"][0]["transforms"][0].pop("allowed_fit_splits")),
            "duplicate transform": mutate(lambda s: s["systems"][0]["transforms"].append(
                dict(s["systems"][0]["transforms"][0]))),
            "transforms type": mutate(lambda s: s["systems"][1].update(transforms={})),
            "nonfinite": mutate(lambda s: s["systems"][1].update(compute_budget=float("nan"))),
            "infinite": mutate(lambda s: s["systems"][1].update(compute_budget=float("inf"))),
        }
        for label, spec in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                AUDIT(spec)


class ParityCliTests(unittest.TestCase):
    def test_cli_matches_the_api_for_both_fixtures(self):
        for name, status in (("parity-example.json", "PARITY_OK"), ("parity-issues.json", "PARITY_ISSUES")):
            with self.subTest(fixture=name):
                result = run_cli("--parity", SKILL / "assets" / name)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], status)
                self.assertEqual(report, AUDIT(fixture(name)))

    def test_cli_unknown_status_still_exits_zero(self):
        spec = fixture("parity-example.json")
        del spec["held_out"]
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            result = run_cli("--parity", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "UNKNOWN")

    def test_cli_rejects_duplicate_keys_nonfinite_numbers_and_bad_specs_with_exit_2(self):
        bodies = {
            "duplicate top-level": '{"schema": "szl.benchmark-parity/v1", "systems": [], "systems": []}',
            "duplicate nested": (SKILL / "assets" / "parity-example.json").read_text(encoding="utf-8").replace(
                '"tuning_trials":20,', '"tuning_trials":20,"tuning_trials":20,', 1),
            "NaN": '{"schema": "szl.benchmark-parity/v1", "compute_unit": NaN}',
            "Infinity": '{"schema": "szl.benchmark-parity/v1", "compute_unit": Infinity}',
            "negative Infinity": '{"schema": "szl.benchmark-parity/v1", "compute_unit": -Infinity}',
            "not JSON": "{",
            "not an object": "[]",
            "contract": '{"schema": "szl.benchmark-parity/v1", "systems": []}',
        }
        with tempfile.TemporaryDirectory() as temp:
            for label, body in bodies.items():
                with self.subTest(case=label):
                    path = pathlib.Path(temp) / "spec.json"
                    path.write_text(body, encoding="utf-8")
                    result = run_cli("--parity", path)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(result.stdout, b"")
            self.assertEqual(run_cli("--parity", pathlib.Path(temp) / "absent.json").returncode, 2)

    def test_cli_requires_exactly_one_input_and_never_overwrites(self):
        example = SKILL / "assets" / "parity-example.json"
        self.assertEqual(run_cli(SKILL / "assets" / "example.json", "--parity", example).returncode, 2)
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp) / "report.json"
            out.write_text("retain", encoding="utf-8")
            self.assertEqual(run_cli("--parity", example, "--output", out).returncode, 2)
            self.assertEqual(out.read_text(encoding="utf-8"), "retain")
            fresh = pathlib.Path(temp) / "fresh.json"
            self.assertEqual(run_cli("--parity", example, "--output", fresh).returncode, 0)
            self.assertEqual(json.loads(fresh.read_text(encoding="utf-8"))["status"], "PARITY_OK")


class ParitySidecarShapeTests(unittest.TestCase):
    def test_parity_module_follows_the_kernel_sidecar_rules(self):
        tree = ast.parse((SKILL / "parity.py").read_text(encoding="utf-8"))
        functions = []
        for node in tree.body:
            self.assertIsInstance(node, (ast.Expr, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign))
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
                self.assertTrue(node.name.startswith("szl_"))
                self.assertNotIn(node.name, dir(builtins))
                self.assertEqual(node.decorator_list, [])
                for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                    ast.literal_eval(default)
        self.assertIn("szl_audit_benchmark_parity", functions)


if __name__ == "__main__":
    unittest.main()
