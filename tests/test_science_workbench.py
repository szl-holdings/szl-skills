"""Integration behavior: retained source binding, invalidation and real execution."""
import copy
import json
import pathlib
import runpy
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-science-workbench"


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.temp.name)
        self.root = self.base / "project"
        self.f = runpy.run_path(str(SKILL / "scripts" / "workbench.py"))
        self.f["initialize"](self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_components_execute_and_capsule_binds_actual_files(self):
        result = self.f["run_project"](self.root)
        self.assertEqual(result["status"], "COMPLETED_WITH_FINDINGS")
        self.assertEqual(set(result["findings"]), {"dataset-check", "math-check", "paired-check"})
        check = self.f["assess_snapshot"](self.root, self.f["latest_run"](self.root))
        self.assertEqual(check["capsule"]["integrity"], "MATCH")
        self.assertEqual(check["changed_sources"], [])
        benchmark = json.loads((self.root / result["checks"]["kernel-check"]["path"]).read_text())
        self.assertTrue(benchmark["measurement_executed_locally"])
        self.assertEqual(benchmark["mismatch_count"], 0)
        self.assertEqual(len(benchmark["measurement_record"]["reference_seconds"]), 9)
        self.assertFalse(result["scientific_truth_verified"])

    def test_changed_input_invalidates_transitive_results_and_retains_history(self):
        old = self.f["run_project"](self.root)
        path = self.root / "inputs" / "predictions.json"
        payload = json.loads(path.read_text())
        payload["probabilities"][0] = 0.3
        path.write_text(json.dumps(payload))
        assessment = self.f["assess_snapshot"](self.root, self.f["latest_run"](self.root))
        self.assertEqual(assessment["changed_sources"], ["predictions"])
        self.assertTrue({"model-check", "kernel-check", "paired-check", "run", "conclusion"} <= set(assessment["recheck"]))
        new = self.f["run_project"](self.root)
        self.assertTrue((self.root / "runs" / old["run_id"] / "report.json").is_file())
        graph = json.loads((self.root / "runs" / new["run_id"] / "graph.json").read_text())
        self.assertTrue(any(n.get("needs_recheck") for n in graph["nodes"] if n["id"] == "conclusion"))
        self.assertTrue(graph["history"])
        self.assertFalse(any(n.get("needs_recheck") for n in graph["nodes"] if n["id"] == "model-check"))

    def test_deleted_input_is_unavailable_and_execution_error_never_completes(self):
        old = self.f["run_project"](self.root)
        (self.root / "inputs" / "predictions.json").unlink()
        assessment = self.f["assess_snapshot"](self.root, self.f["latest_run"](self.root))
        self.assertEqual(assessment["unavailable_sources"], ["predictions"])
        self.assertIn("model-check", assessment["recheck"])
        with self.assertRaises(FileNotFoundError):
            self.f["run_project"](self.root)
        self.assertEqual(self.f["latest_run"](self.root)[1], old["run_id"])
        self.assertEqual(len(list((self.root / "runs").glob("*/error.json"))), 1)

    def test_plan_path_escape_and_undeclared_dependency_are_rejected(self):
        original = json.loads((self.root / "project.json").read_text())
        bad = copy.deepcopy(original)
        bad["artifacts"][0]["path"] = "../outside.json"
        with self.assertRaises(ValueError):
            self.f["validate_project"](bad, self.root)
        bad = copy.deepcopy(original)
        bad["checks"][0]["depends_on"] = []
        with self.assertRaises(ValueError):
            self.f["validate_project"](bad, self.root)

    def test_concurrent_writer_and_duplicate_json_fail(self):
        (self.root / ".science-lock").write_text("another writer")
        with self.assertRaises(FileExistsError):
            self.f["run_project"](self.root)
        self.assertEqual((self.root / ".science-lock").read_text(), "another writer")
        with self.assertRaises(ValueError):
            self.f["parse"](b'{"x":1,"x":2}')

    def test_graph_change_and_completion_marker_tamper_are_detected(self):
        report = self.f["run_project"](self.root)
        graph_path = self.root / "graph.json"
        value = json.loads(graph_path.read_text())
        value["nodes"][0]["title"] = "A changed scientific question"
        graph_path.write_text(json.dumps(value))
        assessment = self.f["assess_snapshot"](self.root, self.f["latest_run"](self.root))
        self.assertEqual(assessment["changed_sources"], ["graph-template"])
        self.assertIn("conclusion", assessment["recheck"])
        path = self.root / "runs" / report["run_id"] / "report.json"
        report["status"] = "FAKE_POSITIVE"
        path.write_text(json.dumps(report))
        assessment = self.f["assess_snapshot"](self.root, self.f["latest_run"](self.root))
        self.assertEqual(assessment["completion_record_binding"], "MISMATCH")

    def test_explicit_nested_projection_preserves_missing_findings(self):
        row = {"family": "f", "row": {"input": "scientific input"}}
        mapped = self.f["project_row"](row, {"prompt": ["row", "input"], "family": ["family"], "missing": ["row", "absent"]})
        self.assertEqual(mapped, {"prompt": "scientific input", "family": "f", "missing": None})
        with self.assertRaises(ValueError):
            self.f["project_row"](row, {"prompt": "infer it"})

    def test_import_package_can_run_from_renamed_folder(self):
        renamed = self.base / "publisher-renamed-workbench"
        shutil.copytree(SKILL, renamed)
        alternate = runpy.run_path(str(renamed / "scripts" / "workbench.py"))
        report = alternate["run_project"](self.root)
        self.assertTrue(report["completed"])


class CategoricalTests(unittest.TestCase):
    def setUp(self):
        self.f = runpy.run_path(str(ROOT / "skills" / "szl-model-evaluation" / "kernel.py"))["szl_evaluate_categories"]
        self.target = {"label": "BUG", "state": "MEASURED", "evidence": ["traceback"]}
        self.held = [{"row_id": "a", "family": "f", "target": self.target}, {"row_id": "b", "family": "g", "target": self.target}]
        self.records = [{"row_id": "a", "family": "f", "target": self.target, "parsed": self.target, "label_exact": False},
                        {"row_id": "b", "family": "g", "target": self.target, "parsed": None, "label_exact": True}]

    def test_recomputes_and_keeps_invalid_outputs_in_denominator(self):
        result = self.f(self.records, self.held, ["BUG"])
        self.assertEqual(result["label_accuracy"], 0.5)
        self.assertEqual(result["invalid_outputs"], 1)
        self.assertFalse(result["model_loaded"])

    def test_missing_duplicate_or_wrong_target_never_scores(self):
        for records in [self.records[:1], [self.records[0], self.records[0]], copy.deepcopy(self.records)]:
            if records == self.records:
                records[0]["target"] = {"label": "BUG", "state": "PROVEN", "evidence": []}
            with self.assertRaises(ValueError):
                self.f(records, self.held, ["BUG"])


if __name__ == "__main__":
    unittest.main()
