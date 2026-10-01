"""Behavioral tests for the six skills added in 0.3.0. Stdlib only, offline."""
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SKILLS / name / "kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def example(name, fname="example.json"):
    return json.loads((SKILLS / name / "assets" / fname).read_text(encoding="utf-8"))


class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-evidence-gate")
        self.root = SKILLS / "szl-evidence-gate" / "assets"

    def test_example_abstains_with_one_optional_failure(self):
        r = self.k.szl_evidence_gate(self.root, example("szl-evidence-gate"))
        self.assertEqual(r["status"], "ABSTAIN")
        self.assertEqual(r["counts"], {"PASS": 2, "FAIL": 1, "ABSTAIN": 1, "ERROR": 0})
        self.assertEqual(r["required_failures"], [])

    def test_digest_mismatch_fails_required_claim(self):
        doc = example("szl-evidence-gate")
        doc["claims"][0]["evidence"][0]["sha256"] = "0" * 64
        r = self.k.szl_evidence_gate(self.root, doc)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("held-out-accuracy", r["required_failures"])
        self.assertEqual(r["claims"][0]["evidence"][0]["reason"], "DIGEST_MISMATCH")

    def test_missing_text_and_path_escape(self):
        doc = {"claims": [{"id": "a", "text": "t", "evidence": [{"path": "artifacts/eval_protocol.txt", "must_contain": ["not there"]}]},
                          {"id": "b", "text": "t", "evidence": [{"path": "../kernel.py"}]}]}
        r = self.k.szl_evidence_gate(self.root, doc)
        self.assertEqual(r["claims"][0]["status"], "FAIL")
        self.assertEqual(r["claims"][1]["status"], "ERROR")
        self.assertEqual(r["status"], "FAIL")

    def test_all_pass_when_every_claim_has_intact_evidence(self):
        doc = example("szl-evidence-gate")
        doc["claims"] = doc["claims"][:2]
        r = self.k.szl_evidence_gate(self.root, doc)
        self.assertEqual(r["status"], "PASS")


class CrossImplementationTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-cross-implementation-check")

    def test_example_is_divergent_with_counts(self):
        r = self.k.szl_cross_implementation_check(example("szl-cross-implementation-check"))
        self.assertEqual(r["status"], "DIVERGENT")
        self.assertEqual((r["consistent"], r["divergent"], r["incomparable"]), (3, 2, 1))
        by = {q["quantity"]: q for q in r["quantities"]}
        self.assertEqual(by["aic"]["status"], "INCOMPARABLE")
        self.assertEqual(by["effect.estimate"]["status"], "CONSISTENT")

    def test_identical_results_consistent_and_input_mismatch_blocks(self):
        doc = {"a": {"results": {"x": 1.0, "ok": True}}, "b": {"results": {"x": 1.0, "ok": True}}, "tolerance": {"rtol": 0.01}}
        self.assertEqual(self.k.szl_cross_implementation_check(doc)["status"], "CONSISTENT")
        doc["a"]["input_sha256"] = "a" * 64
        doc["b"]["input_sha256"] = "b" * 64
        r = self.k.szl_cross_implementation_check(doc)
        self.assertEqual(r["status"], "INCOMPARABLE")
        self.assertIs(r["inputs_match"], False)

    def test_non_finite_is_incomparable_and_bad_tolerance_errors(self):
        doc = {"a": {"results": {"x": float("nan")}}, "b": {"results": {"x": 1.0}}}
        self.assertEqual(self.k.szl_cross_implementation_check(doc)["status"], "INCOMPARABLE")
        doc = {"a": {"results": {"x": 1}}, "b": {"results": {"x": 1}}, "tolerance": {"rtol": -1}}
        self.assertEqual(self.k.szl_cross_implementation_check(doc)["status"], "ERROR")


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-analysis-mutation-test")

    def test_generate_is_deterministic_and_changes_bytes(self):
        a = self.k.szl_generate_mutations(example("szl-analysis-mutation-test"))
        b = self.k.szl_generate_mutations(example("szl-analysis-mutation-test"))
        self.assertEqual(a["status"], "GENERATED")
        self.assertEqual([v["sha256"] for v in a["variants"]], [v["sha256"] for v in b["variants"]])
        self.assertEqual(len(a["expected_detection"]), 10)
        for v in a["variants"]:
            self.assertNotEqual(v["sha256"], a["baseline_sha256"])

    def test_score_counts_caught_missed_and_untested(self):
        plan = self.k.szl_generate_mutations(example("szl-analysis-mutation-test"))
        r = self.k.szl_score_mutations(plan, example("szl-analysis-mutation-test", "outcomes-example.json"))
        self.assertEqual(r["status"], "SCORED")
        self.assertEqual(r["coverage"], "6/10")
        self.assertIn("shuffle_labels", r["missed"])
        r2 = self.k.szl_score_mutations(plan, {"variants": {}})
        self.assertEqual(r2["status"], "NO_OUTCOMES_RECORDED")
        r3 = self.k.szl_score_mutations(plan, {"baseline_flagged": True, "variants": {}})
        self.assertEqual(r3["status"], "BASELINE_FLAGGED")

    def test_skips_label_shuffle_without_label_column(self):
        doc = example("szl-analysis-mutation-test"); doc.pop("label_column")
        plan = self.k.szl_generate_mutations(doc)
        shuffled = next(v for v in plan["variants"] if v["mutation"] == "shuffle_labels")
        self.assertEqual(shuffled["status"], "SKIPPED")


class EnergyReceiptTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-compute-energy-receipt")

    def test_measured_from_counter_samples(self):
        r = self.k.szl_energy_receipt(example("szl-compute-energy-receipt"))
        self.assertEqual(r["status"], "MEASURED")
        self.assertAlmostEqual(r["energy"]["joules"], 8072.5, places=6)
        self.assertEqual(r["co2e"]["class"], "REPORTED")
        self.assertIn("measured via nvml", r["methods_sentence"])

    def test_gap_or_vendor_source_demotes_to_reported(self):
        doc = example("szl-compute-energy-receipt"); doc["measurement"]["max_gap_s"] = 5
        self.assertEqual(self.k.szl_energy_receipt(doc)["status"], "REPORTED")
        doc = example("szl-compute-energy-receipt"); doc["measurement"]["source"] = "vendor"
        self.assertEqual(self.k.szl_energy_receipt(doc)["status"], "REPORTED")

    def test_unavailable_never_invents_a_number(self):
        r = self.k.szl_energy_receipt(example("szl-compute-energy-receipt", "unavailable-example.json"))
        self.assertEqual(r["status"], "UNAVAILABLE")
        self.assertIsNone(r["energy"]["joules"])
        self.assertIn("UNAVAILABLE", r["methods_sentence"])

    def test_grid_factor_without_source_is_ignored(self):
        doc = example("szl-compute-energy-receipt"); doc["grid"] = {"g_co2e_per_kwh": 400}
        r = self.k.szl_energy_receipt(doc)
        self.assertIsNone(r["co2e"]["grams"])
        self.assertEqual(r["co2e"]["note"], "GRID_FACTOR_WITHOUT_SOURCE_IGNORED")


class SessionReceiptTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-session-receipt")
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        shutil.copytree(SKILLS / "szl-session-receipt" / "assets" / "project", self.tmp / "project")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_then_verify_match(self):
        receipt = self.k.szl_make_session_receipt(self.tmp / "project", example("szl-session-receipt"))
        self.assertEqual(receipt["status"], "RECORDED")
        self.assertEqual(receipt["signature"]["state"], "UNSIGNED")
        self.assertIn("unsigned", receipt["methods_paragraph"])
        v = self.k.szl_verify_session_receipt(self.tmp / "project", receipt)
        self.assertEqual(v["status"], "MATCH")
        self.assertEqual(v["files_checked"], 3)

    def test_changed_file_is_a_mismatch_and_tampered_receipt_is_detected(self):
        receipt = self.k.szl_make_session_receipt(self.tmp / "project", example("szl-session-receipt"))
        (self.tmp / "project" / "results" / "summary.csv").write_text("n_rows,4\n", encoding="utf-8")
        v = self.k.szl_verify_session_receipt(self.tmp / "project", receipt)
        self.assertEqual(v["status"], "MISMATCH")
        receipt["commands"] = ["python other.py"]
        v2 = self.k.szl_verify_session_receipt(self.tmp / "project", receipt)
        self.assertEqual(v2["status"], "RECEIPT_TAMPERED")

    def test_missing_file_and_escape(self):
        m = example("szl-session-receipt"); m["outputs"].append("results/missing.csv")
        r = self.k.szl_make_session_receipt(self.tmp / "project", m)
        self.assertEqual(r["status"], "RECORDED_WITH_MISSING_FILES")
        m["inputs"] = ["../kernel.py"]
        self.assertEqual(self.k.szl_make_session_receipt(self.tmp / "project", m)["status"], "ERROR")


class ReviewerPackTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-reviewer-pack")
        self.project = SKILLS / "szl-reviewer-pack" / "assets" / "project"

    def test_pack_reports_unresolved_and_renders(self):
        cfg = json.loads((SKILLS / "szl-reviewer-pack" / "assets" / "review-config.json").read_text(encoding="utf-8"))
        pack = self.k.szl_build_pack(self.project, config=cfg)
        self.assertEqual(pack["status"], "REVIEW_REQUIRED")
        self.assertEqual(len(pack["checks"]), 5)
        self.assertTrue(all(c["digest_match"] for c in pack["checks"]))
        self.assertEqual(pack["aggregate"]["value"], 0.0)
        md = self.k.szl_render_markdown(pack)
        self.assertIn("EXACT_FEATURE_DUPLICATES", md)
        self.assertIn("ADVISORY", md)

    def test_geomean_is_non_compensatory(self):
        self.assertEqual(self.k.szl_weighted_geomean([0.0, 1.0], [0.5, 0.5]), 0.0)
        self.assertAlmostEqual(self.k.szl_weighted_geomean([0.25, 1.0], [0.5, 0.5]), 0.5, places=12)
        with self.assertRaises(ValueError):
            self.k.szl_weighted_geomean([1.2], [1.0])

    def test_nothing_to_review_on_empty_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.k.szl_build_pack(d)["status"], "NOTHING_TO_REVIEW")


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, skill, *args):
        return subprocess.run([sys.executable, "-B", str(SKILLS / skill / "scripts" / "run.py"), *args],
                              cwd=str(SKILLS / skill), capture_output=True, text=True, timeout=60)

    def test_exit_codes(self):
        for skill, arg in (("szl-evidence-gate", "assets/example.json"), ("szl-cross-implementation-check", "assets/example.json"),
                           ("szl-compute-energy-receipt", "assets/example.json"), ("szl-compute-energy-receipt", "assets/unavailable-example.json")):
            p = self.run_cli(skill, arg)
            self.assertEqual(p.returncode, 0, p.stderr)
            json.loads(p.stdout)
        self.assertEqual(self.run_cli("szl-evidence-gate", "does-not-exist.json").returncode, 2)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cli("szl-analysis-mutation-test", "generate", "assets/example.json", "--out-dir", d).returncode, 0)
            self.assertTrue((pathlib.Path(d) / "plan.json").is_file())
            self.assertEqual(self.run_cli("szl-analysis-mutation-test", "score", str(pathlib.Path(d) / "plan.json"), "assets/outcomes-example.json").returncode, 0)
        self.assertEqual(self.run_cli("szl-reviewer-pack", "assets/project").returncode, 0)


if __name__ == "__main__":
    unittest.main()
