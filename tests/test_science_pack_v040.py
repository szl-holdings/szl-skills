"""Behavioral tests for the five skills added in 0.4.0. Stdlib only, offline (szl-repo-pin uses the local git executable)."""
import importlib.util
import json
import pathlib
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


class RefutationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-refutation-ledger")
        self.ledger = example("szl-refutation-ledger")

    def test_example_chain_is_valid_and_states_follow_dependencies(self):
        self.assertEqual(self.k.szl_verify_ledger(self.ledger)["status"], "VALID")
        s = self.k.szl_ledger_status(self.ledger)
        states = {r["claim_id"]: (r["state"], r["foundation"]) for r in s["claims"]}
        self.assertEqual(states, {"A": ("CONTESTED", "SOUND"), "B": ("NOT_REPLICATED", "SHAKEN"), "C": ("UNTESTED", "SHAKEN")})
        self.assertEqual(s["unreceipted_attempts_total"], 1)
        self.assertEqual(s["shaken_claims"], ["B", "C"])
        self.assertIn("foundation SHAKEN", self.k.szl_replication_record(s, "C"))

    def test_tampering_breaks_the_chain_at_the_edited_entry(self):
        bad = json.loads(json.dumps(self.ledger))
        bad["entries"][4]["body"]["outcome"] = "REPLICATED"
        r = self.k.szl_verify_ledger(bad)
        self.assertEqual((r["status"], r["at_seq"]), ("BROKEN", 5))
        self.assertEqual(self.k.szl_append(bad, "withdrawal", {"claim_id": "A", "reason": "x"}, "2026-08-01T00:00:00Z")["status"], "ERROR")
        self.assertEqual(self.k.szl_ledger_status(bad)["status"], "ERROR")

    def test_append_validates_references_outcomes_and_receipts(self):
        at = "2026-08-01T00:00:00Z"
        self.assertEqual(self.k.szl_append(self.ledger, "attempt", {"attempt_id": "x", "claim_id": "Z", "outcome": "REPLICATED"}, at)["status"], "ERROR")
        self.assertEqual(self.k.szl_append(self.ledger, "attempt", {"attempt_id": "x", "claim_id": "A", "outcome": "MAYBE"}, at)["status"], "ERROR")
        self.assertEqual(self.k.szl_append(self.ledger, "attempt", {"attempt_id": "A-rep-1", "claim_id": "A", "outcome": "REPLICATED"}, at)["status"], "ERROR")
        self.assertEqual(self.k.szl_append(self.ledger, "attempt", {"attempt_id": "y", "claim_id": "A", "outcome": "REPLICATED", "receipt": {"sha256": "zz"}}, at)["status"], "ERROR")
        self.assertEqual(self.k.szl_append(self.ledger, "claim", {"claim_id": "D", "statement": "s", "depends_on": ["Q"]}, at)["status"], "ERROR")
        self.assertEqual(self.k.szl_append(self.ledger, "attempt", {"attempt_id": "y", "claim_id": "A", "outcome": "REPLICATED"}, "2026-08-01 00:00")["status"], "ERROR")
        new = example("szl-refutation-ledger", "new_attempt.json")
        r = self.k.szl_append(self.ledger, new["kind"], new["body"], at)
        self.assertEqual(r["status"], "APPENDED")
        self.assertEqual(len(self.ledger["entries"]), 7, "append must not mutate the input")
        s = self.k.szl_ledger_status(r["ledger"])
        c = next(x for x in s["claims"] if x["claim_id"] == "C")
        self.assertEqual((c["state"], c["foundation"]), ("REPLICATED", "SHAKEN"))
        w = self.k.szl_append(r["ledger"], "withdrawal", {"claim_id": "B", "reason": "contamination found"}, "2026-09-01T00:00:00Z")
        self.assertEqual(next(x for x in self.k.szl_ledger_status(w["ledger"])["claims"] if x["claim_id"] == "B")["state"], "WITHDRAWN")


class RetrievalEvalTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-retrieval-eval")

    def test_example_metrics_and_complete_denominators(self):
        r = self.k.szl_retrieval_eval(example("szl-retrieval-eval"))
        a = r["aggregate"]
        self.assertEqual(r["status"], "SCORED")
        self.assertAlmostEqual(a["ndcg_at_k"], 0.5144, places=4)
        self.assertAlmostEqual(a["mrr"], 0.5, places=6)
        self.assertAlmostEqual(a["map"], 0.4511, places=4)
        self.assertAlmostEqual(a["recall_at_k"], 0.6, places=6)
        self.assertEqual(a["queries_missing_from_run"], ["q5-never-run"])
        self.assertEqual(a["queries_unjudged_in_run"], ["q9-unjudged"])
        self.assertEqual(a["duplicates_dropped_total"], 1)
        self.assertEqual(a["queries_judged"], 5)

    def test_hand_computed_query(self):
        m = self.k.szl_query_metrics(["d12", "d99", "d07", "d20", "d33"], {"d12": 2, "d07": 1, "d33": 1}, 5)
        self.assertAlmostEqual(m["ndcg"], 0.9409, places=4)
        self.assertEqual(m["reciprocal_rank"], 1.0)
        self.assertAlmostEqual(m["average_precision"], (1 + 2 / 3 + 3 / 5) / 3, places=9)
        self.assertEqual(m["recall"], 1.0)

    def test_gates_and_errors(self):
        doc = example("szl-retrieval-eval"); doc["gates"] = {"min_judged_queries": 20}
        self.assertEqual(self.k.szl_retrieval_eval(doc)["status"], "INSUFFICIENT")
        doc["gates"] = {"min_ndcg": 0.9}
        self.assertEqual(self.k.szl_retrieval_eval(doc)["status"], "BELOW_GATES")
        self.assertEqual(self.k.szl_retrieval_eval({"qrels": {}, "run": {}})["status"], "ERROR")
        self.assertEqual(self.k.szl_retrieval_eval({"qrels": {"q": {"d": -1}}, "run": {}})["status"], "ERROR")
        self.assertEqual(self.k.szl_retrieval_eval({"k": 0, "qrels": {"q": {"d": 1}}, "run": {}})["status"], "ERROR")


class QuantizationCheckTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-quantization-check")

    def test_example_is_degraded_by_the_flipped_row(self):
        r = self.k.szl_quantization_check(example("szl-quantization-check"))
        self.assertEqual(r["status"], "DEGRADED")
        self.assertEqual(r["worst_rows"][0], "p05")
        self.assertAlmostEqual(r["aggregate"]["cosine_min"], -1.0, places=9)
        self.assertEqual(len(r["tolerances_failed"]), 3)

    def test_without_the_bad_row_it_is_within_tolerance(self):
        doc = example("szl-quantization-check")
        for key in ("reference", "candidate", "ids"):
            doc[key] = [v for i, v in enumerate(doc[key]) if i != 5]
        r = self.k.szl_quantization_check(doc)
        self.assertEqual(r["status"], "WITHIN_TOLERANCE", r["tolerances_failed"])
        self.assertEqual(r["aggregate"]["top1_agreement"], 1.0)

    def test_incomparable_and_errors(self):
        doc = example("szl-quantization-check"); doc["candidate"] = doc["candidate"][:-1]
        self.assertEqual(self.k.szl_quantization_check(doc)["status"], "INCOMPARABLE")
        doc = example("szl-quantization-check"); doc["candidate"][0][0] = float("nan")
        self.assertEqual(self.k.szl_quantization_check(doc)["status"], "INCOMPARABLE")
        doc = example("szl-quantization-check"); doc["candidate"][0] = [0.0] * 6
        self.assertEqual(self.k.szl_quantization_check(doc)["status"], "INCOMPARABLE")
        doc = example("szl-quantization-check"); doc["temperature"] = 0
        self.assertEqual(self.k.szl_quantization_check(doc)["status"], "ERROR")
        self.assertAlmostEqual(self.k.szl_kl([0.5, 0.5], [0.5, 0.5]), 0.0, places=9)
        self.assertAlmostEqual(self.k.szl_cosine([1, 0], [0, 1]), 0.0, places=12)


class RepoPinTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-repo-pin")
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        for rel in ("code/analysis", "code/prep", "vendor/labutils"):
            d = self.tmp / rel; d.mkdir(parents=True)
            subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.decl = example("szl-repo-pin", "declaration.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_trees_pin_and_verify_match(self):
        pin = self.k.szl_make_pin(self.tmp, self.decl)
        self.assertEqual(pin["status"], "PINNED")
        self.assertEqual(len(pin["composite_sha256"]), 64)
        self.assertEqual(self.k.szl_verify_pin(self.tmp, pin)["status"], "MATCH")

    def test_dirty_tree_blocks_the_pin_and_drift_is_named(self):
        (self.tmp / "code/prep/new.txt").write_text("x")
        pin = self.k.szl_make_pin(self.tmp, self.decl)
        self.assertEqual((pin["status"], pin["composite_sha256"], pin["not_pinnable"]), ("UNPINNED", None, ["data-prep"]))
        (self.tmp / "code/prep/new.txt").unlink()
        self.assertEqual(self.k.szl_verify_pin(self.tmp, pin)["status"], "ERROR")
        pin = self.k.szl_make_pin(self.tmp, self.decl)
        subprocess.run(["git", "-C", str(self.tmp / "code/analysis"), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "more"], check=True)
        v = self.k.szl_verify_pin(self.tmp, pin)
        self.assertEqual(v["status"], "DRIFT")
        self.assertEqual({r["name"]: r["state"] for r in v["repos"]}, {"analysis": "DRIFT", "data-prep": "MATCH", "shared-utils": "MATCH"})

    def test_escapes_missing_and_duplicates_rejected(self):
        self.assertEqual(self.k.szl_make_pin(self.tmp, {"repos": [{"name": "a", "path": "../x"}]})["status"], "ERROR")
        self.assertEqual(self.k.szl_make_pin(self.tmp, {"repos": [{"name": "a", "path": "x"}, {"name": "a", "path": "y"}]})["status"], "ERROR")
        pin = self.k.szl_make_pin(self.tmp, {"repos": [{"name": "gone", "path": "nope"}]})
        self.assertEqual((pin["status"], pin["repos"][0]["state"]), ("UNPINNED", "MISSING"))
        example_pin = example("szl-repo-pin")
        self.assertEqual(self.k.szl_verify_pin(self.tmp, example_pin)["status"], "DRIFT")

    def test_drive_qualified_and_windows_traversal_paths_are_rejected(self):
        clean_pin = self.k.szl_make_pin(self.tmp, self.decl)
        for path in ("C:/outside", "C:outside", "C:\\outside", "\\\\server\\share\\repo", "..\\outside"):
            with self.subTest(path=path):
                self.assertEqual(self.k.szl_make_pin(self.tmp, {"repos": [{"name": "outside", "path": path}]})["status"], "ERROR")
                recorded = json.loads(json.dumps(clean_pin))
                recorded["repos"][0]["path"] = path
                self.assertEqual(self.k.szl_verify_pin(self.tmp, recorded)["status"], "ERROR")

    def test_symlink_outside_root_is_rejected(self):
        clean_pin = self.k.szl_make_pin(self.tmp, self.decl)
        with tempfile.TemporaryDirectory() as outside:
            link = self.tmp / "linked-outside"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"directory symlink unavailable: {error}")
            try:
                self.assertEqual(self.k.szl_make_pin(self.tmp, {"repos": [{"name": "outside", "path": link.name}]})["status"], "ERROR")
                recorded = json.loads(json.dumps(clean_pin))
                recorded["repos"][0]["path"] = link.name
                self.assertEqual(self.k.szl_verify_pin(self.tmp, recorded)["status"], "ERROR")
            finally:
                link.unlink()

    def test_child_directory_cannot_pin_ancestor_repository(self):
        declared_root = self.tmp / "code/analysis/declared-root"
        (declared_root / "child").mkdir(parents=True)
        inspected = self.k.szl_inspect_repo(declared_root, "child")
        self.assertEqual(inspected["state"], "NOT_A_REPOSITORY")
        pin = self.k.szl_make_pin(declared_root, {"repos": [{"name": "child", "path": "child"}]})
        self.assertEqual((pin["status"], pin["repos"][0]["state"]), ("UNPINNED", "NOT_A_REPOSITORY"))

    def test_empty_or_malformed_pin_cannot_match(self):
        self.assertEqual(self.k.szl_verify_pin(self.tmp, {"schema": self.k.SCHEMA, "repos": []})["status"], "ERROR")
        self.assertEqual(self.k.szl_verify_pin(self.tmp, {"schema": self.k.SCHEMA, "repos": [None]})["status"], "ERROR")
        clean_pin = self.k.szl_make_pin(self.tmp, self.decl)
        dirty_record = json.loads(json.dumps(clean_pin))
        dirty_record["repos"][0]["state"] = "DIRTY"
        self.assertEqual(self.k.szl_verify_pin(self.tmp, dirty_record)["status"], "ERROR")
        wrong_digest = json.loads(json.dumps(clean_pin))
        wrong_digest["composite_sha256"] = "0" * 64
        self.assertEqual(self.k.szl_verify_pin(self.tmp, wrong_digest)["status"], "ERROR")
        duplicate_name = json.loads(json.dumps(clean_pin))
        duplicate_name["repos"][1]["name"] = duplicate_name["repos"][0]["name"]
        self.assertEqual(self.k.szl_verify_pin(self.tmp, duplicate_name)["status"], "ERROR")
        non_object = json.loads(json.dumps(clean_pin))
        non_object["repos"][0] = None
        self.assertEqual(self.k.szl_verify_pin(self.tmp, non_object)["status"], "ERROR")


class ResultFragilityTests(unittest.TestCase):
    def setUp(self):
        self.k = load("szl-result-fragility")

    def test_fisher_matches_textbook_and_symmetry(self):
        self.assertAlmostEqual(self.k.szl_fisher_two_sided(3, 1, 1, 3), 0.4857, places=4)
        self.assertAlmostEqual(self.k.szl_fisher_two_sided(12, 88, 25, 75), self.k.szl_fisher_two_sided(25, 75, 12, 88), places=12)
        self.assertEqual(self.k.szl_fisher_two_sided(0, 10, 0, 10), 1.0)

    def test_example_is_fragile_against_follow_up_loss(self):
        r = self.k.szl_result_fragility(example("szl-result-fragility"))
        self.assertEqual((r["status"], r["fragility_index"]), ("FRAGILE", 2))
        self.assertAlmostEqual(r["fisher_p_two_sided"], 0.0279, places=4)
        self.assertAlmostEqual(r["fragility_quotient"], 0.01, places=9)
        doc = example("szl-result-fragility"); doc["lost_to_follow_up"] = 1
        self.assertEqual(self.k.szl_result_fragility(doc)["status"], "ROBUST")
        doc.pop("lost_to_follow_up")
        self.assertEqual(self.k.szl_result_fragility(doc)["status"], "SIGNIFICANT_UNJUDGED")

    def test_reverse_index_and_errors(self):
        r = self.k.szl_result_fragility({"arms": {"treatment": {"events": 10, "total": 50}, "control": {"events": 14, "total": 50}}})
        self.assertEqual(r["status"], "NOT_SIGNIFICANT")
        self.assertEqual(r["reverse_fragility_index"], 5)
        self.assertEqual(self.k.szl_result_fragility({"arms": {"treatment": {"events": 10, "total": 5}, "control": {"events": 1, "total": 5}}})["status"], "ERROR")
        self.assertEqual(self.k.szl_result_fragility({"arms": {"treatment": {"events": 1, "total": 5}, "control": {"events": 1, "total": 5}}, "alpha": 1})["status"], "ERROR")
        self.assertEqual(self.k.szl_result_fragility({"arms": {"treatment": {"events": 1.5, "total": 5}, "control": {"events": 1, "total": 5}}})["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
