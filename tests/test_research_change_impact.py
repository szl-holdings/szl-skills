"""Research change-impact behavior, security boundaries and packaged integration; offline."""
import ast
import builtins
import copy
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/szl-research-change-impact"
KERNEL = runpy.run_path(str(SKILL / "kernel.py"))
PLAN = KERNEL["szl_plan_research_changes"]


def example():
    return json.loads((SKILL / "assets/example.json").read_text(encoding="utf-8"))


def unchanged():
    document = example()
    document["baseline"] = copy.deepcopy(document["current"])
    return document


class ChangeImpactTests(unittest.TestCase):
    def test_deleted_edge_cannot_hide_a_changed_input(self):
        report = PLAN(example())
        self.assertEqual(report["removed_edges"], [{"dependency": "data", "dependent": "evaluation"}])
        self.assertEqual(report["affected_nodes"], ["conclusion", "data", "evaluation"])
        self.assertEqual(report["recheck_order"], ["data", "evaluation", "conclusion"])
        self.assertEqual(report["claims"][0]["witness_path"], ["evaluation", "conclusion"])
        self.assertEqual(report["claims"][1]["state"], "NO_DECLARED_IMPACT")
        self.assertFalse(report["claims"][1]["truth_verified"])

    def test_removed_evidence_and_removed_claim_remain_in_denominator(self):
        document = example()
        document["current"]["nodes"] = [node for node in document["current"]["nodes"] if node["id"] not in {"data", "unrelated"}]
        del document["observed_digests"]["data"]
        report = PLAN(document)
        self.assertEqual(report["removed_nodes"], ["data", "unrelated"])
        self.assertIn("conclusion", report["affected_nodes"])
        self.assertEqual(report["claims"][1]["state"], "MISSING_REQUIRED_CLAIM")
        self.assertEqual(report["retired_affected_nodes"], ["data", "unrelated"])

    def test_missing_claim_never_becomes_empty_healthy_coverage(self):
        document = unchanged()
        document["required_claims"].append("never-recorded")
        report = PLAN(document)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(len(report["claims"]), 3)
        self.assertEqual(report["claims"][1]["state"], "MISSING_REQUIRED_CLAIM")

    def test_missing_null_changed_or_unpinned_evidence_propagates(self):
        for mode in ("missing", "null", "mismatch", "unpinned"):
            with self.subTest(mode=mode):
                document = unchanged()
                document["current"]["nodes"][1]["depends_on"] = ["data"]
                document["baseline"] = copy.deepcopy(document["current"])
                if mode == "missing":
                    del document["observed_digests"]["data"]
                elif mode == "null":
                    document["observed_digests"]["data"] = None
                elif mode == "mismatch":
                    document["observed_digests"]["data"] = "d" * 64
                else:
                    document["current"]["nodes"][0].pop("sha256")
                    document["baseline"] = copy.deepcopy(document["current"])
                    document["observed_digests"].pop("data")
                report = PLAN(document)
                self.assertEqual(report["claims"][0]["state"], "RECHECK_REQUIRED")
                self.assertEqual(report["claims"][0]["witness_path"], ["data", "evaluation", "conclusion"])
                if mode != "mismatch":
                    self.assertIn("data", report["unavailable_evidence"])

    def test_matching_outputs_do_not_clear_an_ancestor_change(self):
        document = example()
        document["current"]["nodes"][1]["depends_on"] = ["data"]
        report = PLAN(document)
        self.assertEqual(report["claims"][0]["state"], "RECHECK_REQUIRED")
        self.assertEqual(next(row for row in report["observations"] if row["id"] == "evaluation")["binding"], "MATCH_ON_SUPPLIED_DIGEST")

    def test_evidence_relation_change_seed_is_preserved(self):
        document = unchanged()
        document["baseline"]["evidence_links"] = [{"claim": "conclusion", "evidence": "data", "relation": "supports"}]
        document["current"]["evidence_links"] = [{"claim": "conclusion", "evidence": "data", "relation": "contradicts"}]
        report = PLAN(document)
        self.assertEqual(report["added_edges"], [])
        self.assertEqual(report["removed_edges"], [])
        self.assertEqual(report["seeds"], [{"id": "conclusion", "reasons": ["EVIDENCE_LINKS_CHANGED"]}])

    def test_union_cycle_terminates_when_individual_snapshots_are_dags(self):
        document = unchanged()
        document["baseline"]["nodes"][1]["depends_on"] = ["data"]
        document["current"]["nodes"][0]["depends_on"] = ["evaluation"]
        report = PLAN(document)
        self.assertEqual(report["recheck_order"], ["evaluation", "conclusion", "data"])
        self.assertEqual(len(report["affected_nodes"]), 3)

    def test_expired_future_corrected_and_persistent_observations(self):
        for fields, reason in [({"observed_at": "2026-09-01T00:00:00Z", "expires_at": "2026-10-01T00:00:00Z"}, "EXPIRED_OBSERVATION"),
                               ({"observed_at": "2026-10-02T00:00:00Z"}, "FUTURE_OBSERVATION"),
                               ({"evidence_status": "corrected"}, "RETRACTED_OR_CORRECTED"),
                               ({"needs_recheck": True}, "PERSISTENT_RECHECK")]:
            document = unchanged()
            document["current"]["nodes"][1].update(fields)
            document["baseline"] = copy.deepcopy(document["current"])
            report = PLAN(document)
            self.assertIn(reason, next(seed for seed in report["seeds"] if seed["id"] == "evaluation")["reasons"])
            self.assertEqual(report["claims"][0]["state"], "RECHECK_REQUIRED")

    def test_no_declared_impact_deterministic_and_input_untouched(self):
        document = unchanged()
        retained = copy.deepcopy(document)
        report = PLAN(document)
        self.assertEqual(report, PLAN(document))
        self.assertEqual(document, retained)
        self.assertEqual(report["status"], "NO_DECLARED_IMPACT")
        self.assertFalse(report["observations_independently_verified"])
        self.assertFalse(report["experiments_executed"])

    def test_node_and_dependency_order_do_not_change_impact(self):
        document = example()
        report = PLAN(document)
        document["baseline"]["nodes"].reverse()
        document["current"]["nodes"].reverse()
        other = PLAN(document)
        for field in ("affected_nodes", "recheck_order", "seeds", "claims"):
            self.assertEqual(report[field], other[field])
        self.assertNotEqual(report["input_sha256"], other["input_sha256"])

    def test_snapshot_cycles_dangling_parents_duplicate_ids_and_links_rejected(self):
        for snapshot in ("baseline", "current"):
            for mode in ("cycle", "dangling", "duplicate", "duplicate-edge", "duplicate-link"):
                with self.subTest(snapshot=snapshot, mode=mode):
                    document = example()
                    graph = document[snapshot]
                    if mode == "cycle":
                        graph["nodes"][1]["depends_on"] = ["conclusion"]
                    elif mode == "dangling":
                        graph["nodes"][1]["depends_on"] = ["missing"]
                    elif mode == "duplicate":
                        graph["nodes"].append(copy.deepcopy(graph["nodes"][0]))
                    elif mode == "duplicate-edge":
                        graph["nodes"][1]["depends_on"] = ["data", "data"]
                    else:
                        graph["evidence_links"] = [{"claim": "conclusion", "evidence": "data", "relation": "supports"}] * 2
                    with self.assertRaises(ValueError):
                        PLAN(document)

    def test_nonfinite_malformed_digest_id_kind_time_and_unknown_observation_rejected(self):
        for mode in ("nan", "inf", "hash", "kind", "status", "id", "time", "observation", "required", "field"):
            with self.subTest(mode=mode):
                document = example()
                node = document["current"]["nodes"][0]
                if mode in {"nan", "inf"}:
                    node["metadata"] = float(mode)
                elif mode == "hash":
                    node["sha256"] = "wrong"
                elif mode == "kind":
                    node["kind"] = []
                elif mode == "status":
                    node["evidence_status"] = []
                elif mode == "id":
                    node["id"] = True
                elif mode == "time":
                    document["as_of"] = "2026-02-30T00:00:00Z"
                elif mode == "observation":
                    document["observed_digests"]["unknown"] = "a" * 64
                elif mode == "required":
                    document["required_claims"] = ["data"]
                else:
                    del document["current"]
                with self.assertRaises(ValueError):
                    PLAN(document)

    def test_graph_json_size_and_depth_bounds(self):
        document = unchanged()
        document["current"]["nodes"] *= 251
        with self.assertRaises(ValueError):
            PLAN(document)
        document = unchanged()
        deep = None
        for index in range(65):
            deep = [deep]
        document["baseline"]["metadata"] = deep
        with self.assertRaises(ValueError):
            PLAN(document)
        document = unchanged()
        document["baseline"]["metadata"] = "x" * 2097152
        with self.assertRaises(ValueError):
            PLAN(document)

    def test_real_retained_workbench_fixture_change_has_recheck_plan(self):
        fixtures = sorted((ROOT / "skills/szl-reviewer-pack/assets/project/runs").glob("*/graph.json"))
        graph = json.loads(fixtures[0].read_text(encoding="utf-8"))
        current = copy.deepcopy(graph)
        data = next(node for node in current["nodes"] if node["id"] == "predictions")
        data["sha256"] = "f" * 64
        model = next(node for node in current["nodes"] if node["id"] == "model-check")
        model["depends_on"].remove("predictions")
        document = {"schema": "szl.research-change-impact.v1", "baseline": graph, "current": current,
                    "observed_digests": {node["id"]: node["sha256"] for node in current["nodes"] if node.get("sha256")},
                    "required_claims": ["conclusion", "lambda"], "as_of": "2026-10-01T23:00:00Z"}
        report = PLAN(document)
        self.assertEqual(set(report["affected_nodes"]), {"predictions", "model-check", "kernel-check", "paired-check", "run", "conclusion"})
        self.assertEqual(report["claims"][1]["state"], "NO_DECLARED_IMPACT")
        self.assertLess(report["recheck_order"].index("model-check"), report["recheck_order"].index("kernel-check"))

    def test_sidecar_ast_has_no_top_level_execution_or_private_functions(self):
        for node in ast.parse((SKILL / "kernel.py").read_text(encoding="utf-8")).body:
            self.assertIsInstance(node, (ast.Expr, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign))
            if isinstance(node, ast.FunctionDef):
                self.assertFalse(node.name.startswith("_"))
                self.assertNotIn(node.name, dir(builtins))
                self.assertEqual(node.decorator_list, [])


class ChangeImpactCliTests(unittest.TestCase):
    def run_cli(self, path, *args):
        return subprocess.run([sys.executable, "-B", str(SKILL / "scripts/run.py"), str(path), *map(str, args)],
                              text=True, capture_output=True)

    def test_cli_refuses_overwrite_duplicate_keys_nonfinite_and_deep_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            out = root / "keep.json"
            out.write_text("preserve", encoding="utf-8")
            process = self.run_cli(SKILL / "assets/example.json", "--output", out)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(out.read_text(encoding="utf-8"), "preserve")
            for content in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '[' * 1000 + '0' + ']' * 1000):
                path = root / "bad.json"
                path.write_text(content, encoding="utf-8")
                process = self.run_cli(path)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertEqual(json.loads(process.stdout)["status"], "INVALID_INPUT")

    def test_cli_treats_titles_as_data_and_creates_report_exclusively(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            document = unchanged()
            document["current"]["nodes"][0]["title"] = "__import__('os').system('unexpected-command')"
            path, output = root / "input.json", root / "report.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            process = self.run_cli(path, "--output", output)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), json.loads(process.stdout))
            self.assertEqual(json.loads(process.stdout)["execution_authority"], "NONE")


if __name__ == "__main__":
    unittest.main()
