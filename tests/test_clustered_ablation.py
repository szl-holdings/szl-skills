"""Clustered ablation planner/analyzer (szl.clustered-ablation.v1) and v1 byte-stability checks."""
import ast
import builtins
import copy
import hashlib
import itertools
import json
import pathlib
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-clustered-replication"
K = runpy.run_path(str(SKILL / "kernel.py"))
A = runpy.run_path(str(SKILL / "ablation.py"))
M = runpy.run_path(str(ROOT / "skills" / "szl-multiplicity-audit" / "kernel.py"))
RUN = [sys.executable, "-B", str(SKILL / "scripts" / "run.py")]
EXAMPLE = SKILL / "assets" / "ablation-example.json"
INCOMPLETE = SKILL / "assets" / "ablation-incomplete.json"

# Expected values computed from main (b8e259c) code in a separate scratch checkout before this
# change: SHA-256 of the newline-normalized CLI stdout, exit code, and canonical_sha256 of the
# kernel report. kernel.py, example.json and fixture-lock.json bytes are unchanged from main.
MAIN_FILE_SHA256 = {
    "kernel.py": "f1a59d113f53fd6eaf6f1993f1d73db9a092de4b3e5d7611d131dcd9c0a86a93",
    "assets/example.json": "57ad0adbc6462099a7027f16bf17c5781d766646bbd7795433267add993f51a1",
    "assets/fixture-lock.json": "a39e8427052bdfe7b3136b8ef02b1af95e3221b2d2affce59395dfd556796e1a",
}
MAIN_CLI = [
    ("example.json", False, 1, "fd6d9c8def485a1a584ebb90fa706b6b6ff183f9a16adb83d2f8907aa5170478"),
    ("example.json", True, 0, "062ee1cc80bf05d02cfb563ae5e657a98a7ed4a9230d29f034d4c7ba3b824530"),
    ("fixture-lock.json", False, 2, "f700dc2e0da84376efe7e3eb5cc858cb1be7b08b6e43c38982441a35f640298c"),
]
MAIN_KERNEL = [
    ("example.json", False, "15e9258442fc8812c9ce7a67b035751685448764c75deeebae1b70a1e501401c"),
    ("example.json", True, "bb97be2fa917270529d218864df07f2c6a3ea9c3d72210270580f48a6918195c"),
    ("fixture-lock.json", False, "e2470d5be0f3005173de8d5bb58e73cc53a77728407488a994bfc873d068e064"),
]
FIXTURE_PLAN_SHA256 = "759f8ff746ebd3df19a148b2dd9c4cbcb59c6e542dd42fe7a151bb06e18a1b85"


def lock():
    return json.loads((SKILL / "assets" / "fixture-lock.json").read_bytes())["plan_sha256"]


def example():
    return K["read_json"](EXAMPLE.read_bytes())


def ablate(document, bound=True):
    return A["szl_cluster_ablation"](document, K["canonical_sha256"](document["plan"]) if bound else None)


def cli(path, *extra):
    process = subprocess.run(RUN + [str(path)] + list(extra), capture_output=True, text=True)
    return process.returncode, json.loads(process.stdout), process.stdout


def oracle(document, arm):
    """Independent recomputation: cluster means of arm minus full loss, Cartesian sign flips."""
    loss = {(r["arm"], r["id"]): Fraction(r["loss"]) for r in document["rows"]}
    grouped = {}
    for case in document["plan"]["arms"][0]["expected_cases"]:
        grouped.setdefault(case["cluster"], []).append(loss[(arm, case["id"])] - loss[("full", case["id"])])
    means = [sum(v) / len(v) for _, v in sorted(grouped.items())]
    observed = abs(sum(means))
    extreme = sum(abs(sum(s * m for s, m in zip(signs, means))) >= observed
                  for signs in itertools.product((-1, 1), repeat=len(means)))
    leave = [(sum(means) - m) / (len(means) - 1) for m in means]
    return sum(means) / len(means), Fraction(extreme, 2 ** len(means)), leave


class V1ByteStabilityTests(unittest.TestCase):
    def test_v1_files_are_byte_identical_to_main(self):
        for name, digest in MAIN_FILE_SHA256.items():
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((SKILL / name).read_bytes()).hexdigest(), digest)

    def test_v1_cli_reports_are_byte_identical_to_main(self):
        for name, bound, code, digest in MAIN_CLI:
            with self.subTest(file=name, bound=bound):
                extra = ["--expected-plan-sha256", lock()] if bound else []
                returncode, _, stdout = cli(SKILL / "assets" / name, *extra)
                self.assertEqual(returncode, code)
                self.assertEqual(hashlib.sha256(stdout.encode("utf-8")).hexdigest(), digest)

    def test_v1_kernel_reports_match_main(self):
        for name, bound, digest in MAIN_KERNEL:
            with self.subTest(file=name, bound=bound):
                document = K["read_json"]((SKILL / "assets" / name).read_bytes())
                report = K["szl_clustered_replication"](document, lock() if bound else None)
                self.assertEqual(K["canonical_sha256"](report), digest)


class AblationPlannerTests(unittest.TestCase):
    def test_example_plan_is_complete_and_lock_matches(self):
        report = A["szl_cluster_ablation"]({"plan": example()["plan"]})
        self.assertEqual(report["status"], "PLAN_COMPLETE")
        self.assertEqual(report["plan_findings"], [])
        self.assertEqual(report["plan_sha256"], FIXTURE_PLAN_SHA256)
        retained = json.loads((SKILL / "assets" / "ablation-lock.json").read_bytes())
        self.assertEqual(retained["plan_sha256"], FIXTURE_PLAN_SHA256)
        self.assertEqual(report["plan_binding"], "UNBOUND")

    def test_incomplete_fixture_flags_every_gap(self):
        report = A["szl_cluster_ablation"](K["read_json"](INCOMPLETE.read_bytes()))
        self.assertEqual(report["status"], "PLAN_INCOMPLETE")
        self.assertEqual(report["plan_findings"], [
            {"finding": "DUPLICATE_ARM", "subject": "full-rerun", "same_as": "full"},
            {"finding": "UNDECLARED_MULTI_FACTOR_DIFFERENCE", "subject": "no-aug-pretrain"},
            {"finding": "CASE_SETS_DIFFER", "subject": "no-aug-pretrain"},
            {"finding": "FACTOR_NEVER_ABLATED", "subject": "dropout"}])

    def test_each_gap_is_flagged_on_its_own(self):
        def findings(change):
            plan = copy.deepcopy(example()["plan"])
            change(plan)
            report = A["szl_cluster_ablation"]({"plan": plan})
            self.assertEqual(report["status"], "PLAN_INCOMPLETE")
            return report["plan_findings"]
        self.assertEqual(findings(lambda p: p["factors"].append("dropout")),
                         [{"finding": "FACTOR_NEVER_ABLATED", "subject": "dropout"}])
        self.assertEqual(findings(lambda p: p["arms"].append(dict(p["arms"][1], id="no-aug-again"))),
                         [{"finding": "DUPLICATE_ARM", "subject": "no-aug-again", "same_as": "no-aug"}])
        self.assertEqual(findings(lambda p: p["arms"][2]["removes"].append("augmentation")),
                         [{"finding": "UNDECLARED_MULTI_FACTOR_DIFFERENCE", "subject": "no-pretrain"}])
        self.assertEqual(findings(lambda p: p["arms"][2]["expected_cases"].pop()),
                         [{"finding": "CASE_SETS_DIFFER", "subject": "no-pretrain"}])
        moved = findings(lambda p: p["arms"][1]["expected_cases"][3].update(cluster="B"))
        self.assertEqual(moved, [{"finding": "CASE_SETS_DIFFER", "subject": "no-aug"}])

    def test_declared_multi_factor_arm_and_arm_order_are_accepted(self):
        plan = example()["plan"]
        plan["arms"].append(dict(plan["arms"][1], id="no-both", removes=["augmentation", "pretraining"],
                                 multi_factor_declared=True))
        plan["arms"].reverse()
        self.assertEqual(A["szl_cluster_ablation"]({"plan": plan})["status"], "PLAN_COMPLETE")

    def test_empty_removal_and_joint_only_ablation(self):
        plan = example()["plan"]
        plan["arms"].insert(0, dict(plan["arms"][1], id="baseline-copy", removes=[]))
        self.assertEqual(A["szl_cluster_ablation"]({"plan": plan})["plan_findings"],
                         [{"finding": "DUPLICATE_ARM", "subject": "baseline-copy", "same_as": "full"}])
        plan = example()["plan"]
        plan["arms"] = [plan["arms"][0], dict(plan["arms"][1], id="no-both", multi_factor_declared=True,
                                              removes=["augmentation", "pretraining"])]
        # A factor removed only jointly has been ablated; its separate effect is not identified.
        self.assertEqual(A["szl_cluster_ablation"]({"plan": plan})["status"], "PLAN_COMPLETE")

    def test_malformed_plans_are_input_errors(self):
        def broken(change):
            document = example()
            change(document["plan"])
            return A["szl_cluster_ablation"](document)
        changes = [
            lambda p: p.update(schema="szl.clustered-replication.v1"),
            lambda p: p.update(extra=True),
            lambda p: p.pop("alpha"),
            lambda p: p.update(alpha=0),
            lambda p: p.update(sign_flip_basis="bootstrap"),
            lambda p: p.update(factors=[]),
            lambda p: p.update(factors=["augmentation", "augmentation"]),
            lambda p: p.update(factors=["f%d" % i for i in range(16)]),
            lambda p: p.update(factors=["x" * 65]),
            lambda p: p.update(arms=p["arms"][:1]),
            lambda p: p.update(arms=[dict(p["arms"][1], id="a%d" % i) for i in range(17)]),
            lambda p: p["arms"].pop(0),
            lambda p: p["arms"][0].update(removes=["augmentation"]),
            lambda p: p["arms"][1].update(id="full"),
            lambda p: p["arms"][1].update(removes=["undeclared"]),
            lambda p: p["arms"][1].update(removes=["augmentation", "augmentation"]),
            lambda p: p["arms"][1].update(removes="augmentation"),
            lambda p: p["arms"][1].update(multi_factor_declared="false"),
            lambda p: p["arms"][1].update(expected_cases=[]),
            lambda p: p["arms"][1].update(expected_cases="A1"),
            lambda p: p["arms"][1]["expected_cases"].append(dict(p["arms"][1]["expected_cases"][0])),
            lambda p: p["arms"][1]["expected_cases"][0].update(note="x"),
            lambda p: p["arms"][1].pop("multi_factor_declared"),
            lambda p: p["arms"][1].update(id={"x": 1}),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                report = broken(change)
                self.assertEqual(report["status"], "INPUT_ERROR")
                self.assertIsNone(report["multiplicity_handoff"])
        for document in (None, [], {"rows": []}, {"plan": example()["plan"], "rows": [], "x": 1}):
            self.assertEqual(A["szl_cluster_ablation"](document)["status"], "INPUT_ERROR")


class AblationAnalyzerTests(unittest.TestCase):
    def test_bound_example_matches_independent_oracle(self):
        document = example()
        report = A["szl_cluster_ablation"](document, FIXTURE_PLAN_SHA256)
        self.assertEqual((report["status"], report["plan_binding"]), ("ANALYZED", "MATCHED_COMMITMENT"))
        self.assertEqual((report["observations"], report["missing"], report["inference_blockers"]), (12, [], []))
        self.assertEqual([arm["arm"] for arm in report["arms"]], ["no-aug", "no-pretrain"])
        for arm in report["arms"]:
            with self.subTest(arm=arm["arm"]):
                effect, p, leave = oracle(document, arm["arm"])
                audit = arm["audit"]
                self.assertEqual(audit["status"], "CONDITIONAL_CLUSTER_RESULT")
                self.assertEqual(audit["equal_cluster_mean_improvement"], float(effect))
                self.assertEqual(audit["conditional_exact_test"]["p_two_sided"], float(p))
                self.assertEqual([item["mean_improvement"] for item in audit["leave_one_cluster_out"]],
                                 [float(value) for value in leave])
                self.assertEqual(audit["declared_experimental_units"], 3)
                self.assertEqual(audit["plan_binding"], "MATCHED_COMMITMENT")
        pretrain = report["arms"][1]["audit"]
        self.assertIn("POSITIVE_EFFECT_DEPENDS_ON_ONE_EXPERIMENTAL_UNIT", pretrain["findings"])
        self.assertEqual(pretrain["observation_weighted_mean_improvement"], 2.5)
        self.assertEqual(report["multiplicity_handoff"], {
            "skill": "szl-multiplicity-audit", "p_values_adjusted": False,
            "family": [{"id": "no-aug", "p_value": "0.25"}, {"id": "no-pretrain", "p_value": "1"}]})

    def test_each_arm_is_exactly_the_v1_audit_of_the_derived_table(self):
        document = example()
        report = ablate(document)
        plan = {key: document["plan"][key] for key in K["PLAN_KEYS"] if key not in ("schema", "expected_cases")}
        plan.update(schema=K["SCHEMA"], expected_cases=document["plan"]["arms"][0]["expected_cases"])
        loss = {(r["arm"], r["id"]): r["loss"] for r in document["rows"]}
        for arm in report["arms"]:
            rows = [{"id": c["id"], "cluster": c["cluster"], "unit": "loss", "baseline_loss": loss[(arm["arm"], c["id"])],
                     "candidate_loss": loss[("full", c["id"])]} for c in plan["expected_cases"]]
            direct = K["szl_clustered_replication"]({"plan": plan, "rows": rows}, K["canonical_sha256"](plan))
            self.assertEqual(arm["audit"], direct)

    def test_unbound_example_is_descriptive_and_hands_off_null_p_values(self):
        report = ablate(example(), bound=False)
        self.assertEqual((report["status"], report["plan_binding"]), ("ANALYZED", "UNBOUND"))
        self.assertIn("EXPECTED_PLAN_COMMITMENT_UNAVAILABLE", report["inference_blockers"])
        for arm in report["arms"]:
            self.assertEqual(arm["audit"]["status"], "DESCRIPTIVE_ONLY")
            self.assertIsNone(arm["audit"]["conditional_exact_test"])
        self.assertEqual([item["p_value"] for item in report["multiplicity_handoff"]["family"]], [None, None])

    def test_shared_assumption_blockers_apply_to_every_arm(self):
        for key, value, blocker in [("independent_clusters_declared", False, "INDEPENDENT_CLUSTER_ASSUMPTION_UNDECLARED"),
                                    ("sign_flip_basis", "undeclared", "SIGN_FLIP_ASSUMPTION_UNDECLARED"),
                                    ("min_clusters", 4, "TOO_FEW_DECLARED_EXPERIMENTAL_UNITS")]:
            document = example()
            document["plan"][key] = value
            report = ablate(document)
            self.assertEqual(report["status"], "ANALYZED")
            self.assertIn(blocker, report["inference_blockers"])
            self.assertTrue(all(arm["audit"]["conditional_exact_test"] is None for arm in report["arms"]))

    def test_missing_case_or_arm_holds_without_statistics(self):
        document = example()
        document["rows"].pop(5)
        report = ablate(document)
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual(report["missing"], [{"arm": "no-aug", "id": "A2"}])
        self.assertEqual((report["arms"], report["multiplicity_handoff"], report["inference_blockers"]), ([], None, None))
        document = example()
        document["rows"] = [row for row in document["rows"] if row["arm"] != "no-pretrain"]
        report = ablate(document)
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual(report["missing"], [{"arm": "no-pretrain", "id": i} for i in ("A1", "A2", "B1", "C1")])
        self.assertEqual(ablate({"plan": example()["plan"], "rows": []})["status"], "HOLD")

    def test_incomplete_plan_is_never_analyzed(self):
        document = example()
        document["plan"]["factors"].append("dropout")
        report = ablate(document)
        self.assertEqual(report["status"], "PLAN_INCOMPLETE")
        self.assertEqual((report["arms"], report["multiplicity_handoff"]), ([], None))
        document = example()
        document["plan"]["arms"][2]["expected_cases"].pop()
        document["rows"] = [r for r in document["rows"] if (r["arm"], r["id"]) != ("no-pretrain", "C1")]
        report = ablate(document)
        self.assertEqual((report["status"], report["missing"], report["arms"]), ("PLAN_INCOMPLETE", [], []))

    def test_invalid_rows_are_input_errors(self):
        def broken(change):
            document = example()
            change(document["rows"])
            return ablate(document)["status"]
        changes = [
            lambda rows: rows.append(dict(rows[0])),
            lambda rows: rows.append(dict(rows[0], arm="no-dropout")),
            lambda rows: rows.append(dict(rows[0], id="D1")),
            lambda rows: rows[0].update(cluster="B"),
            lambda rows: rows[0].update(unit="seconds"),
            lambda rows: rows[0].update(extra=1),
            lambda rows: rows[0].pop("unit"),
            lambda rows: rows[0].update(arm=["full"]),
            lambda rows: rows.append("row"),
        ]
        for value in (True, float("nan"), float("inf"), -1, 10 ** 13, "1", None):
            changes.append(lambda rows, value=value: rows[0].update(loss=value))
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                self.assertEqual(broken(change), "INPUT_ERROR")
        self.assertEqual(ablate({"plan": example()["plan"], "rows": {}})["status"], "INPUT_ERROR")

    def test_plan_commitment_mismatch_is_input_error(self):
        for digest in ("0" * 64, lock(), "", 7, FIXTURE_PLAN_SHA256.upper()):
            with self.subTest(digest=digest):
                self.assertEqual(A["szl_cluster_ablation"](example(), digest)["status"], "INPUT_ERROR")
                self.assertEqual(A["szl_cluster_ablation"]({"plan": example()["plan"]}, digest)["status"], "INPUT_ERROR")
        self.assertEqual(A["szl_cluster_ablation"]({"plan": example()["plan"]}, FIXTURE_PLAN_SHA256)["plan_binding"],
                         "MATCHED_COMMITMENT")

    def test_row_order_and_input_mutation(self):
        document = example()
        before = copy.deepcopy(document)
        first = ablate(document)
        self.assertEqual(document, before)
        document["rows"].reverse()
        second = ablate(document)
        self.assertEqual(first["arms"], second["arms"])
        self.assertEqual(first["multiplicity_handoff"], second["multiplicity_handoff"])

    def test_handoff_is_consumed_by_multiplicity_audit_unadjusted(self):
        report = ablate(example())
        family = report["multiplicity_handoff"]["family"]
        plan = json.dumps({"schema": "szl.multiplicity.plan.v1", "family_id": "ablation", "alpha": "0.05",
                           "method": "holm", "hypotheses": [{"id": m["id"], "description": "Ablation arm " + m["id"]}
                                                            for m in family]}).encode()
        results = json.dumps({"schema": "szl.multiplicity.results.v1", "family_id": "ablation",
                              "plan_sha256": hashlib.sha256(plan).hexdigest(), "results": family,
                              "extra_attempts": []}).encode()
        audited = M["szl_audit_multiplicity"](plan, results)
        self.assertEqual(audited["status"], "COMPLETE")
        self.assertEqual([(row["id"], row["raw_p_value"], row["adjusted_p_value"]) for row in audited["adjusted"]],
                         [("no-aug", "0.25", "0.50"), ("no-pretrain", "1", "1")])
        self.assertEqual([m["p_value"] for m in family], ["0.25", "1"])

    def test_exact_decimal_handoff_for_small_dyadic_p_values(self):
        cases = [{"id": "c%d" % i, "cluster": "k%d" % i} for i in range(16)]
        plan = dict(example()["plan"], min_clusters=2, reported_n=16, factors=["augmentation"],
                    arms=[{"id": name, "removes": removes, "multi_factor_declared": False, "expected_cases": cases}
                          for name, removes in (("full", []), ("no-aug", ["augmentation"]))])
        rows = [dict(c, arm=arm, unit="loss", loss=loss) for c in cases for arm, loss in (("full", 1), ("no-aug", 2))]
        report = ablate({"plan": plan, "rows": rows})
        self.assertEqual(report["status"], "ANALYZED")
        self.assertEqual(report["multiplicity_handoff"]["family"], [{"id": "no-aug", "p_value": "0.000030517578125"}])
        self.assertEqual(Fraction(report["multiplicity_handoff"]["family"][0]["p_value"]), Fraction(2, 2 ** 16))


class AblationCliTests(unittest.TestCase):
    def test_cli_exit_codes_and_recorded_digests(self):
        code, report, _ = cli(INCOMPLETE)
        self.assertEqual((code, report["status"]), (1, "PLAN_INCOMPLETE"))
        code, report, _ = cli(EXAMPLE)
        self.assertEqual((code, report["status"]), (1, "ANALYZED"))
        code, report, _ = cli(EXAMPLE, "--expected-plan-sha256", FIXTURE_PLAN_SHA256)
        self.assertEqual((code, report["status"]), (0, "ANALYZED"))
        self.assertEqual(report["input_raw_sha256"], hashlib.sha256(EXAMPLE.read_bytes()).hexdigest())
        self.assertEqual(report["ablation_implementation_sha256"],
                         hashlib.sha256((SKILL / "ablation.py").read_bytes()).hexdigest())
        self.assertEqual(report["implementation_sha256"], MAIN_FILE_SHA256["kernel.py"])
        code, report, _ = cli(EXAMPLE, "--expected-plan-sha256", lock())
        self.assertEqual((code, report["status"]), (2, "INPUT_ERROR"))
        with tempfile.TemporaryDirectory() as temp:
            plan_only = pathlib.Path(temp) / "plan.json"
            plan_only.write_text(json.dumps({"plan": example()["plan"]}), encoding="utf-8")
            code, report, _ = cli(plan_only)
            self.assertEqual((code, report["status"], report["plan_sha256"]), (0, "PLAN_COMPLETE", FIXTURE_PLAN_SHA256))
            held = example()
            held["rows"].pop()
            held_path = pathlib.Path(temp) / "held.json"
            held_path.write_text(json.dumps(held), encoding="utf-8")
            code, report, _ = cli(held_path, "--expected-plan-sha256", FIXTURE_PLAN_SHA256)
            self.assertEqual((code, report["status"]), (1, "HOLD"))

    def test_cli_rejects_duplicate_keys_and_nonfinite_numbers(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        variants = [text.replace('"unit":"loss","factors"', '"unit":"loss","unit":"loss","factors"', 1),
                    text.replace('"loss":10}', '"loss":NaN}', 1),
                    text.replace('"loss":10}', '"loss":Infinity}', 1),
                    text.replace('"alpha":0.05', '"alpha":-Infinity', 1)]
        with tempfile.TemporaryDirectory() as temp:
            for index, variant in enumerate(variants):
                with self.subTest(variant=index):
                    self.assertNotEqual(variant, text)
                    path = pathlib.Path(temp) / ("bad-%d.json" % index)
                    path.write_text(variant, encoding="utf-8")
                    code, report, _ = cli(path)
                    self.assertEqual((code, report["status"]), (2, "INPUT_ERROR"))


class AblationPackageTests(unittest.TestCase):
    def test_skill_entrypoint_is_bounded_and_references_exist(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 160)
        self.assertIn("### What it does not do", text)
        for reference in re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|json|md))`", text):
            self.assertTrue((SKILL / reference).is_file(), reference)
        provenance = (SKILL / "references" / "provenance.md").read_text(encoding="utf-8")
        for phrase in ("Apache-2.0", "b8e259c", "No third-party code"):
            self.assertIn(phrase, provenance)
        self.assertIn("## Ablation contract (`szl.clustered-ablation.v1`)",
                      (SKILL / "references" / "contract.md").read_text(encoding="utf-8"))

    def test_ablation_module_keeps_sidecar_style(self):
        tree = ast.parse((SKILL / "ablation.py").read_text(encoding="utf-8"))
        for node in tree.body:
            self.assertIsInstance(node, (ast.Expr, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign))
            if isinstance(node, ast.FunctionDef):
                self.assertTrue(node.name.startswith("szl_"))
                self.assertNotIn(node.name, dir(builtins))
                self.assertEqual(node.decorator_list, [])
                for default in node.args.defaults:
                    ast.literal_eval(default)
        self.assertEqual(A["ABLATION_SCHEMA"], "szl.clustered-ablation.v1")


if __name__ == "__main__":
    unittest.main()
