"""Independent numerical oracles and failure contracts for paired experimental units."""
import copy
import importlib.util
import itertools
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-clustered-replication"
SPEC = importlib.util.spec_from_file_location("clustered_replication", SKILL / "kernel.py")
K = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(K)


def experiment(differences, repeats=1):
    rows = []
    for i, difference in enumerate(differences):
        for repeat in range(repeats):
            rows.append({"id": "case-%d-%d" % (i, repeat), "cluster": "donor-%d" % i,
                         "unit": "loss", "baseline_loss": 100 + difference, "candidate_loss": 100})
    return {"plan": {"schema": K.SCHEMA, "experimental_unit": "donor",
                     "observational_unit": "cell", "unit": "loss",
                     "expected_cases": [{"id": r["id"], "cluster": r["cluster"]} for r in rows],
                     "reported_n": len(differences), "independent_clusters_declared": True,
                     "sign_flip_basis": "symmetric_cluster_differences", "min_clusters": 2,
                     "alpha": 0.05, "minimum_improvement": 0.1}, "rows": rows}


def audit(document):
    return K.szl_clustered_replication(document, K.canonical_sha256(document["plan"]))


class ExactClusterTests(unittest.TestCase):
    def test_gray_code_matches_independent_cartesian_oracle(self):
        for values in ([1, 2], [0, 0, 0], [1, -1, 2, -2],
                       [Fraction(1, 3), Fraction(-2, 7), Fraction(1, 5)], [0.1, 0.2, -0.3]):
            values = [Fraction(v) for v in values]
            observed = abs(sum(values))
            tails = sum(abs(sum(s * d for s, d in zip(signs, values))) >= observed
                        for signs in itertools.product((-1, 1), repeat=len(values)))
            result = K.exact_two_sided(values)
            self.assertEqual(result["extreme_sign_vectors"], tails)
            self.assertEqual(result["total_sign_vectors"], 2 ** len(values))
            self.assertEqual(result["p_two_sided"], tails / 2 ** len(values))

    def test_same_direction_six_independent_units_has_exact_tail(self):
        r = audit(experiment([1] * 6))
        self.assertEqual(r["status"], "CONDITIONAL_CLUSTER_RESULT")
        test = r["conditional_exact_test"]
        self.assertEqual((test["extreme_sign_vectors"], test["total_sign_vectors"]), (2, 64))
        self.assertTrue(test["conditional_thresholds_met"])
        self.assertFalse(r["independence_verified"])
        self.assertFalse(r["plan_timing_verified"])

    def test_zero_differences_keep_every_sign_vector_and_p_one(self):
        result = audit(experiment([0] * 6))["conditional_exact_test"]
        self.assertEqual((result["extreme_sign_vectors"], result["total_sign_vectors"]), (64, 64))
        self.assertFalse(result["positive_minimum_effect_met"])

    def test_two_sided_tail_does_not_promote_candidate_regression(self):
        result = audit(experiment([-1] * 6))["conditional_exact_test"]
        self.assertEqual(result["p_two_sided"], 2 / 64)
        self.assertFalse(result["conditional_thresholds_met"])

    def test_technical_copies_do_not_increase_effective_units_or_change_p(self):
        single, repeated = audit(experiment([1, 2, 3, 1, 2, 3])), audit(experiment([1, 2, 3, 1, 2, 3], 25))
        self.assertEqual(repeated["observations"], 150)
        self.assertEqual(repeated["declared_experimental_units"], 6)
        self.assertEqual(single["conditional_exact_test"], repeated["conditional_exact_test"])
        self.assertEqual(single["equal_cluster_mean_improvement"], repeated["equal_cluster_mean_improvement"])
        self.assertIn("MULTIPLE_OBSERVATIONS_PER_EXPERIMENTAL_UNIT", repeated["findings"])

    def test_unbalanced_clusters_do_not_gain_inferential_weight(self):
        doc = experiment([3, -2, -2])
        row = doc["rows"][0]
        for index in range(1, 21):
            added = dict(row, id="extra-%d" % index)
            doc["rows"].append(added)
            doc["plan"]["expected_cases"].append({"id": added["id"], "cluster": added["cluster"]})
        result = audit(doc)
        self.assertLess(result["equal_cluster_mean_improvement"], 0)
        self.assertGreater(result["observation_weighted_mean_improvement"], 0)
        self.assertIn("ROW_WEIGHTING_CHANGES_POSITIVE_EFFECT_DECISION", result["findings"])
        self.assertFalse(result["conditional_exact_test"]["positive_minimum_effect_met"])

    def test_one_influential_unit_is_named_without_removing_it(self):
        doc = experiment([3, -1, -1])
        before = copy.deepcopy(doc)
        r = audit(doc)
        self.assertIn("POSITIVE_EFFECT_DEPENDS_ON_ONE_EXPERIMENTAL_UNIT", r["findings"])
        omitted = next(v for v in r["leave_one_cluster_out"] if v["omitted_cluster"] == "donor-0")
        self.assertEqual(omitted["mean_improvement"], -1)
        self.assertEqual(r["declared_experimental_units"], 3)
        self.assertEqual(doc, before)

    def test_missing_plan_or_assumptions_never_produce_inferential_p(self):
        doc = experiment([1] * 6)
        r = K.szl_clustered_replication(doc)
        self.assertEqual(r["status"], "DESCRIPTIVE_ONLY")
        self.assertIsNone(r["conditional_exact_test"])
        self.assertIn("EXPECTED_PLAN_COMMITMENT_UNAVAILABLE", r["inference_blockers"])
        for key, value, blocker in [("independent_clusters_declared", False, "INDEPENDENT_CLUSTER_ASSUMPTION_UNDECLARED"),
                                    ("sign_flip_basis", "undeclared", "SIGN_FLIP_ASSUMPTION_UNDECLARED"),
                                    ("min_clusters", 7, "TOO_FEW_DECLARED_EXPERIMENTAL_UNITS")]:
            altered = copy.deepcopy(doc)
            altered["plan"][key] = value
            r = audit(altered)
            self.assertEqual(r["status"], "DESCRIPTIVE_ONLY")
            self.assertIsNone(r["conditional_exact_test"])
            self.assertIn(blocker, r["inference_blockers"])

    def test_one_experimental_unit_is_descriptive_with_no_omission_test(self):
        r = audit(experiment([1], repeats=100))
        self.assertEqual(r["status"], "DESCRIPTIVE_ONLY")
        self.assertEqual(r["leave_one_cluster_out"], [])
        self.assertIsNone(r["conditional_exact_test"])

    def test_plan_commitment_mismatch_rejected(self):
        r = K.szl_clustered_replication(experiment([1, 2]), "0" * 64)
        self.assertEqual(r["status"], "INPUT_ERROR")
        self.assertIsNone(r["conditional_exact_test"])

    def test_missing_extra_duplicate_reassigned_and_mixed_units_rejected(self):
        base = experiment([1, 2])
        cases = []
        missing = copy.deepcopy(base); missing["rows"].pop(); cases.append(missing)
        extra = copy.deepcopy(base); extra["rows"].append(dict(extra["rows"][0], id="extra")); cases.append(extra)
        duplicate = copy.deepcopy(base); duplicate["rows"][1] = dict(duplicate["rows"][0]); cases.append(duplicate)
        changed = copy.deepcopy(base); changed["rows"][0]["cluster"] = "donor-1"; cases.append(changed)
        mixed = copy.deepcopy(base); mixed["rows"][0]["unit"] = "seconds"; cases.append(mixed)
        for doc in cases:
            with self.subTest(doc=doc):
                self.assertEqual(audit(doc)["status"], "INPUT_ERROR")

    def test_invalid_numbers_and_unknown_fields_never_pass(self):
        for value in (True, float("nan"), float("inf"), -1, 10 ** 13, "1"):
            doc = experiment([1, 2]); doc["rows"][0]["baseline_loss"] = value
            self.assertEqual(audit(doc)["status"], "INPUT_ERROR")
        doc = experiment([1, 2]); doc["plan"]["silently_drop_failed_rows"] = True
        self.assertEqual(audit(doc)["status"], "INPUT_ERROR")
        for value in (0, 1, True, "0.05"):
            doc = experiment([1, 2]); doc["plan"]["alpha"] = value
            self.assertEqual(audit(doc)["status"], "INPUT_ERROR")

    def test_cluster_and_row_budgets_are_bounded(self):
        self.assertEqual(audit(experiment([1] * 17))["status"], "INPUT_ERROR")
        self.assertEqual(audit(experiment([1], 10001))["status"], "INPUT_ERROR")
        with self.assertRaises(ValueError):
            K.exact_two_sided([1] * 17)

    def test_row_order_does_not_change_inference_but_changes_raw_order_digest(self):
        doc = experiment([1, 2, 3], 3)
        first = audit(doc); doc["rows"].reverse(); second = audit(doc)
        self.assertEqual(first["clusters"], second["clusters"])
        self.assertEqual(first["conditional_exact_test"], second["conditional_exact_test"])
        self.assertNotEqual(first["observations_sha256"], second["observations_sha256"])

    def test_reader_rejects_duplicate_nonfinite_invalid_unicode_and_deep_json(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":"\\ud800"}',
                    b'{"x":' + b'[' * 33 + b'0' + b']' * 33 + b'}', b'\xff', b' ' * (K.MAX_BYTES + 1)):
            with self.assertRaises((ValueError, UnicodeError)):
                K.read_json(raw)
        self.assertEqual(K.read_json(b'{"x":"[[[\\\""}'), {"x": '[[["'})

    def test_numeric_token_budgets_and_float_underflow_are_rejected(self):
        for token in ("1e999", "1e-999", "1" * 65, "0." + "1" * 128):
            with self.assertRaises(ValueError):
                K.read_json(('{"x":' + token + '}').encode())
        self.assertEqual(K.read_json(b'{"x":0e-999}'), {"x": 0.0})
        self.assertEqual(K.read_json(b'{"x":1e-100}'), {"x": 1e-100})

    def test_marketplace_installer_and_actual_folders_have_same_inventory(self):
        market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_bytes())
        selected = next(p for p in market["plugins"] if p["name"] == "szl-science-skills")["skills"]
        expected = [pathlib.PurePosixPath(name).name for name in selected]
        installer = runpy.run_path(str(ROOT / "tools/install_claude_science.py"))
        self.assertEqual(set(installer["NAMES"]), set(expected))
        self.assertEqual(len(installer["NAMES"]), len(set(installer["NAMES"])))
        profile = (ROOT / "specialists/szl-science-workbench/SPECIALIST.md").read_text()
        curated = profile.split("Skills (curated):", 1)[1].split("\n\n", 1)[0].strip().removesuffix(".")
        self.assertEqual({name.strip() for name in curated.split(",")}, set(installer["NAMES"]))
        self.assertNotIn("szl-typesafe-ai", installer["NAMES"])
        self.assertNotIn("szl-governed-decision", installer["NAMES"])
        manifest = [name for plugin in market["plugins"] for name in plugin["skills"]]
        self.assertEqual({pathlib.PurePosixPath(name).name for name in manifest},
                         {path.name for path in (ROOT / "skills").iterdir() if path.is_dir()})

    def test_subnormal_aggregate_cannot_report_zero_with_positive_inference(self):
        document = experiment([0, 0], repeats=2)
        document["plan"]["minimum_improvement"] = 0
        for row in document["rows"]:
            row["baseline_loss"], row["candidate_loss"] = 5e-324, 0
        representable = audit(document)
        self.assertEqual(representable["equal_cluster_mean_improvement"], 5e-324)
        self.assertTrue(representable["conditional_exact_test"]["positive_minimum_effect_met"])
        document["rows"][1]["baseline_loss"] = 0
        underflow = audit(document)
        self.assertEqual(underflow["status"], "INPUT_ERROR")
        self.assertIsNone(underflow["conditional_exact_test"])
        self.assertIn("Aggregate", underflow["reason"])

    def test_fixture_commitment_and_declared_findings(self):
        doc = K.read_json((SKILL / "assets/example.json").read_bytes())
        lock = json.loads((SKILL / "assets/fixture-lock.json").read_bytes())
        r = K.szl_clustered_replication(doc, lock["plan_sha256"])
        self.assertEqual(r["status"], "CONDITIONAL_CLUSTER_RESULT")
        self.assertEqual((r["observations"], r["declared_experimental_units"]), (12, 3))
        self.assertEqual(r["conditional_exact_test"]["p_two_sided"], 1.0)
        self.assertIn("POSITIVE_EFFECT_DEPENDS_ON_ONE_EXPERIMENTAL_UNIT", r["findings"])
        self.assertIn("REPORTED_N_DIFFERS_FROM_DECLARED_EXPERIMENTAL_UNITS", r["findings"])

    def test_cli_keeps_descriptive_and_error_exit_codes(self):
        cmd = [sys.executable, "-B", str(SKILL / "scripts/run.py"), str(SKILL / "assets/example.json")]
        descriptive = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(descriptive.returncode, 1)
        self.assertEqual(json.loads(descriptive.stdout)["status"], "DESCRIPTIVE_ONLY")
        import hashlib
        self.assertEqual(json.loads(descriptive.stdout)["input_raw_sha256"],
                         hashlib.sha256((SKILL / "assets/example.json").read_bytes()).hexdigest())
        lock = json.loads((SKILL / "assets/fixture-lock.json").read_bytes())
        bound = subprocess.run(cmd + ["--expected-plan-sha256", lock["plan_sha256"]], capture_output=True, text=True)
        self.assertEqual(bound.returncode, 0)
        self.assertFalse(json.loads(bound.stdout)["conditional_exact_test"]["conditional_thresholds_met"])
        with tempfile.TemporaryDirectory() as temp:
            corrupt = pathlib.Path(temp) / "bad.json"; corrupt.write_text('{"x":NaN}')
            bad = subprocess.run(cmd[:3] + [str(corrupt)], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertEqual(json.loads(bad.stdout)["status"], "INPUT_ERROR")


if __name__ == "__main__":
    unittest.main()
