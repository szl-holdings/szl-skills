"""Behavior and Claude Science sidecar contract checks; stdlib and offline."""
import ast
import builtins
import copy
import hashlib
import json
import math
import pathlib
import random
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAMES = ["szl-research-anatomy", "szl-math-claim-check", "szl-dataset-readiness",
         "szl-model-evaluation", "szl-kernel-comparison", "szl-reproducibility-capsule"]


def load(name):
    return runpy.run_path(str(ROOT / "skills" / name / "kernel.py"))


def example(name):
    return json.loads((ROOT / "skills" / name / "assets" / "example.json").read_text())


class AnatomyTests(unittest.TestCase):
    def setUp(self):
        self.f = load(NAMES[0])
        self.input = example(NAMES[0])
        self.graph = self.input["graph"]

    def test_transitive_source_change(self):
        r = self.f["szl_anatomy_assess"](**self.input)
        self.assertEqual(r["recheck"], ["conclusion", "data", "run"])
        self.assertFalse(any(n["truth_verified"] for n in r["nodes"]))
        self.assertEqual(next(n for n in r["nodes"] if n["id"] == "open-proof")["state"], "MISSING_EVIDENCE")

    def test_missing_readback_never_counts_as_match(self):
        r = self.f["szl_anatomy_assess"](self.graph)
        self.assertTrue(all(n["artifact_binding"] == "NOT_CHECKED" for n in r["nodes"]))

    def test_update_preserves_history_and_persists_invalidation(self):
        node = copy.deepcopy(self.graph["nodes"][1])
        node["sha256"] = "b" * 64
        updated = self.f["szl_anatomy_update"](self.graph, [node])
        self.assertEqual(updated["history"][0]["previous"]["sha256"], "a" * 64)
        self.assertEqual(self.graph["nodes"][1]["sha256"], "a" * 64)
        report = self.f["szl_anatomy_assess"](updated, {"data": "b" * 64})
        self.assertEqual(report["changed_sources"], [])
        self.assertEqual(report["recheck"], ["conclusion", "run"])

    def test_redundant_update_does_not_add_history(self):
        updated = self.f["szl_anatomy_update"](self.graph, [copy.deepcopy(self.graph["nodes"][1])])
        self.assertEqual(updated["history"], [])

    def test_cycle_dangling_duplicate_and_bad_hash_rejected(self):
        for mode in ["cycle", "dangling", "duplicate", "hash"]:
            with self.subTest(mode=mode):
                g = copy.deepcopy(self.graph)
                if mode == "cycle":
                    g["nodes"][1]["depends_on"] = ["conclusion"]
                elif mode == "dangling":
                    g["nodes"][1]["depends_on"] = ["absent"]
                elif mode == "duplicate":
                    g["nodes"].append(copy.deepcopy(g["nodes"][0]))
                else:
                    g["nodes"][1]["sha256"] = "wrong"
                with self.assertRaises(ValueError):
                    self.f["szl_anatomy_assess"](g)


class MathTests(unittest.TestCase):
    def setUp(self):
        self.f = load(NAMES[1])

    def test_detects_false_statement(self):
        r = self.f["szl_check_math_cases"](**example(NAMES[1]))
        self.assertEqual(r["status"], "NUMERICAL_COUNTEREXAMPLE")
        self.assertEqual(len(r["failures"]), 1)
        self.assertFalse(r["proof_discharged"])

    def test_samples_never_discharge_uniqueness(self):
        r = self.f["szl_check_math_cases"]("sample equality", [{"lhs": 0.5, "rhs": 0.5}])
        self.assertEqual(r["status"], "NO_COUNTEREXAMPLE_IN_TESTED_CASES")
        self.assertEqual(r["lambda_status"], "Conjecture 1 (OPEN)")

    def test_weighted_mean_zero_weight_and_boundaries(self):
        f = self.f["szl_weighted_geomean"]
        self.assertAlmostEqual(f([0.25, 1]), 0.5)
        self.assertEqual(f([0, 0.5]), 0)
        self.assertAlmostEqual(f([0, 0.5], [0, 1]), 0.5)
        self.assertAlmostEqual(f([1, 1]), 1)

    def test_nonfinite_bool_negative_weight_invalid_domain_rejected(self):
        f = self.f["szl_weighted_geomean"]
        for axes, ws in [([float("nan")], None), ([float("inf")], None), ([True], None), ([2], None),
                         ([0.1, 0.2], [-1, 2]), ([0.1, 0.2], [0.5]), ([0.1, 0.2], [0.1, 0.1])]:
            with self.subTest(axes=axes, ws=ws), self.assertRaises(ValueError):
                f(axes, ws)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.f = load(NAMES[2])["szl_audit_dataset"]

    def test_feature_and_subject_leakage(self):
        r = self.f(**example(NAMES[2]))
        self.assertIn("CROSS_SPLIT_FEATURE_LEAKAGE", r["issues"])
        self.assertIn("CROSS_SPLIT_GROUP_LEAKAGE", r["issues"])
        self.assertEqual(r["feature_leakage"][0]["row_indexes"], [0, 2])
        self.assertEqual(r["readiness"], "REVIEW")

    def test_missing_empty_unknown_and_other_terms(self):
        r = self.f([], ["signal"], metadata={"license": "other"})
        self.assertIn("EMPTY_DATASET", r["issues"])
        self.assertIn("SOURCE_REVISION_UNDECLARED", r["issues"])
        self.assertIn("REUSE_TERMS_REQUIRE_REVIEW", r["issues"])
        r = self.f([{"split": "train"}], ["signal"])
        self.assertEqual(r["missing"]["signal"], 1)

    def test_clean_fixture_still_not_approved(self):
        r = self.f([{"x": 1, "split": "train"}, {"x": 2, "split": "test"}], ["x"], metadata={"source_revision": "fixture", "license": "Apache-2.0"})
        self.assertEqual(r["status"], "NO_CHECKED_ISSUES")
        self.assertEqual(r["readiness"], "REVIEW")
        self.assertFalse(r["provenance_verified"])

    def test_selected_features_must_exclude_split(self):
        with self.assertRaises(ValueError):
            self.f([], ["split"])


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.f = load(NAMES[3])

    def test_analytic_metrics(self):
        r = self.f["szl_evaluate_predictions"](**example(NAMES[3]))
        m = r["metrics"]
        self.assertAlmostEqual(m["brier"], 0.025)
        self.assertAlmostEqual(m["log_loss"], -(math.log(0.9) + math.log(0.8)) / 2)
        self.assertAlmostEqual(m["ece"], 0.15)
        self.assertEqual(m["auroc"], 1)
        self.assertEqual(m["accuracy"], 1)
        self.assertFalse(r["model_loaded"])
        self.assertEqual(r["promotion_effect"], "NONE")

    def test_ties_and_single_class(self):
        f = self.f["szl_binary_metrics"]
        self.assertEqual(f([0.5, 0.5], [0, 1])["auroc"], 0.5)
        self.assertIsNone(f([0.2, 0.3], [0, 0])["auroc"])

    def test_auc_agrees_with_pairwise_oracle(self):
        rng = random.Random(7)
        for trial in range(20):
            probs = [rng.randrange(5) / 4 for i in range(16)]
            labels = [i % 2 for i in range(16)]
            pos = [p for p, y in zip(probs, labels) if y]
            neg = [p for p, y in zip(probs, labels) if not y]
            oracle = sum(1 if p > n else 0.5 if p == n else 0 for p in pos for n in neg) / (len(pos) * len(neg))
            self.assertAlmostEqual(self.f["szl_binary_metrics"](probs, labels)["auroc"], oracle)

    def test_invalid_input_never_manufactures_score(self):
        f = self.f["szl_binary_metrics"]
        for probs, ys, bins in [([], [], 5), ([float("nan")], [0], 5), ([True], [0], 5), ([0.5], [True], 5), ([0.5], [0], True), ([0.5], [2], 5)]:
            with self.subTest(probs=probs, ys=ys, bins=bins), self.assertRaises(ValueError):
                f(probs, ys, bins)


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.f = load(NAMES[4])
        self.record = example(NAMES[4])
        self.context = {"hardware": "SYNTHETIC CPU", "dtype": "float64", "input_sha256": "a" * 64,
                        "threads": 1, "warmup": 3, "synchronized": True, "measurement_method": "fixture"}

    def timings(self):
        r = copy.deepcopy(self.record)
        r.update(reference_seconds=[2, 3, 4], candidate_seconds=[1, 1.5, 2],
                 reference_context=self.context, candidate_context=copy.deepcopy(self.context))
        return r

    def test_disagreement_suppresses_speedup(self):
        r = self.f["szl_compare_kernel_runs"](self.timings())
        self.assertEqual(r["mismatch_count"], 1)
        self.assertIsNone(r["median_speedup"])

    def test_compatible_timings_and_matching_outputs(self):
        r = self.timings()
        r["candidate_output"] = copy.deepcopy(r["reference_output"])
        report = self.f["szl_compare_kernel_runs"](r)
        self.assertEqual(report["median_speedup"], 2)
        self.assertFalse(report["timing_verified"])

    def test_different_or_unsynchronized_context_suppresses_ratio(self):
        for key, value in [("dtype", "float32"), ("synchronized", False)]:
            r = self.timings()
            r["candidate_output"] = copy.deepcopy(r["reference_output"])
            r["candidate_context"][key] = value
            self.assertIsNone(self.f["szl_compare_kernel_runs"](r)["median_speedup"])

    def test_invalid_shapes_empty_nan_and_times_rejected(self):
        for bad in [[1, 2], [[1], [2, 3]], [[float("nan"), 2], [3, 4]], []]:
            r = copy.deepcopy(self.record)
            r["candidate_output"] = bad
            with self.assertRaises(ValueError):
                self.f["szl_compare_kernel_runs"](r)
        r = self.timings()
        r["candidate_seconds"] = [0, 1, 2]
        with self.assertRaises(ValueError):
            self.f["szl_compare_kernel_runs"](r)

    def test_real_cpu_callable_and_sync_counts(self):
        calls, sync = [], []
        r = self.f["szl_time_callable"](lambda: calls.append(1), warmup=2, repeats=3, synchronize=lambda: sync.append(1))
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(sync), 6)
        self.assertEqual(len(r["seconds"]), 3)
        self.assertIsNone(r["energy_joules"])

    def test_finite_inputs_with_difference_overflow_rejected(self):
        with self.assertRaises(ValueError):
            self.f["szl_compare_kernel_runs"]({"reference_output": [1e308], "candidate_output": [-1e308], "atol": 0, "rtol": 0})


class CapsuleTests(unittest.TestCase):
    def setUp(self):
        self.f = load(NAMES[5])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "data.csv").write_bytes(b"x,y\n1,2\n")
        self.capsule = self.f["szl_make_capsule"](self.root, ["data.csv"], {"seed": 7})

    def test_exact_bytes_and_determinism_not_authentication(self):
        r = self.f["szl_verify_capsule"](self.root, self.capsule)
        self.assertEqual(r["integrity"], "MATCH")
        self.assertFalse(r["authentic"])
        self.assertEqual(self.f["szl_make_capsule"](self.root, ["data.csv"], {"seed": 7}), self.capsule)

    def test_changed_and_missing_files(self):
        (self.root / "data.csv").write_bytes(b"changed")
        self.assertEqual(self.f["szl_verify_capsule"](self.root, self.capsule)["files"][0]["status"], "CHANGED")
        (self.root / "data.csv").unlink()
        self.assertEqual(self.f["szl_verify_capsule"](self.root, self.capsule)["files"][0]["status"], "MISSING")

    def test_manifest_tampering(self):
        self.capsule["metadata"]["seed"] = 9
        self.assertEqual(self.f["szl_verify_capsule"](self.root, self.capsule)["integrity"], "MANIFEST_CHANGED")

    def test_escape_absolute_and_duplicate_paths_rejected(self):
        for files in [["../escape"], ["/absolute"], ["C:/absolute"], ["data.csv", "data.csv"], ["a\\b"]]:
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.f["szl_make_capsule"](self.root, files)

    def test_symlink_rejected(self):
        try:
            (self.root / "link").symlink_to(self.root / "data.csv")
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaises(ValueError):
            self.f["szl_make_capsule"](self.root, ["link"])


class PackagingTests(unittest.TestCase):
    def test_release_packages_match_immutable_git_blobs_and_are_deterministic(self):
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        import zipfile
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as temp:
            first = package["package_skills"](pathlib.Path(temp) / "one", revision)
            second = package["package_skills"](pathlib.Path(temp) / "two", revision)
            self.assertEqual(first, second)
            for report in first:
                with zipfile.ZipFile(pathlib.Path(temp) / "one" / report["archive"]) as archive:
                    expected = package["git_bytes"](revision, "skills/" + report["skill"] + "/SKILL.md")
                    self.assertEqual(archive.read(report["skill"] + "/SKILL.md"), expected)
            with self.assertRaises(ValueError):
                package["package_skills"](pathlib.Path(temp) / "bad", "main")

    def test_archives_contain_standalone_entrypoints_and_run(self):
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        import zipfile
        with tempfile.TemporaryDirectory() as temp:
            destination = pathlib.Path(temp)
            reports = package["package_skills"](destination)
            market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_bytes())
            expected = {pathlib.PurePosixPath(path).name for plugin in market["plugins"]
                        if plugin["name"] in {"szl-science-skills", "szl-science-design-skills", "szl-science-replay-skills",
                                              "szl-science-rare-disease-replay-skills",
                                              "szl-paper-evidence-skills", "szl-science-assay-skills",
                                              "szl-science-multiplicity-skills", "szl-science-change-impact-skills"}
                        for path in plugin["skills"]}
            self.assertEqual({report["skill"] for report in reports}, expected)
            for report in reports:
                with self.subTest(skill=report["skill"]):
                    extraction = destination / ("imported-" + report["skill"])
                    unpacked = extraction / report["skill"]
                    with zipfile.ZipFile(destination / report["archive"]) as z:
                        names = set(z.namelist())
                        self.assertTrue({report["skill"] + "/" + name for name in
                                         ("SKILL.md", "LICENSE", "NOTICE")} <= names)
                        self.assertTrue(all(name.startswith(report["skill"] + "/") for name in names))
                        self.assertLess(report["uncompressed_bytes"], 200000)
                        z.extractall(extraction)
                    if report["skill"] == "szl-science-workbench":
                        project = destination / "standalone-project"
                        init = subprocess.run([sys.executable, "-B", str(unpacked / "scripts" / "workbench.py"), "init", str(project)], capture_output=True, text=True)
                        self.assertEqual(init.returncode, 0, init.stderr)
                        p = subprocess.run([sys.executable, "-B", str(unpacked / "scripts" / "workbench.py"), "run", str(project)], capture_output=True, text=True)
                        self.assertEqual(p.returncode, 1, p.stderr)
                        self.assertTrue(json.loads(p.stdout)["completed"])
                        continue
                    if report["skill"] == "szl-paired-science":
                        self.assertTrue((unpacked / "scripts" / "qualify.py").is_file())
                        continue
                    if report["skill"] == "szl-paper-evidence-audit":
                        entrypoint = unpacked / "scripts" / "audit.py"
                        self.assertTrue(entrypoint.is_file())
                        pdf = destination / "paper-evidence-fixture.pdf"
                        extracted = destination / "paper-evidence-fixture.json"
                        claims = destination / "paper-evidence-claims.json"
                        pdf.write_bytes(b"%PDF-1.7\nsynthetic package contract\n")
                        doc = {"tables": [{"prov": [{"page_no": 1, "bbox":
                               {"l": 0, "t": 10, "r": 20, "b": 0,
                                "coord_origin": "BOTTOMLEFT"}}],
                               "data": {"table_cells": [{"text": "7 mg/L"}]}}]}
                        extracted.write_text(json.dumps(doc), encoding="utf-8")
                        claim = {"schema": "szl.paper-evidence-claims.v1",
                                 "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                                 "document_json_sha256": hashlib.sha256(extracted.read_bytes()).hexdigest(),
                                 "extraction": {"tool": "synthetic", "version": "test",
                                                "pipeline": "test", "ocr_engine": "none"},
                                 "claims": [{"id": "dose", "ref": "#/tables/0", "quote": "7 mg/L"}]}
                        claims.write_text(json.dumps(claim), encoding="utf-8")
                        command = [sys.executable, "-B", str(entrypoint), "--pdf", str(pdf),
                                   "--document-json", str(extracted), "--claims", str(claims)]
                        checked = subprocess.run(command, capture_output=True, text=True)
                        self.assertEqual(checked.returncode, 0, checked.stderr)
                        self.assertEqual(json.loads(checked.stdout)["status"], "REVIEW_REQUIRED")
                        pdf.write_bytes(b"%PDF-1.7\nchanged synthetic bytes\n")
                        rejected = subprocess.run(command, capture_output=True, text=True)
                        self.assertEqual(rejected.returncode, 2, rejected.stderr)
                        self.assertEqual(json.loads(rejected.stdout)["status"], "UNRESOLVED")
                        continue
                    if report["skill"] == "szl-experiment-replay":
                        runner = str(unpacked / "scripts" / "run.py")
                        prepare = subprocess.run(
                            [sys.executable, "-B", runner, "prepare", "assets/declaration.json",
                             "--root", str(unpacked), "--output", "replay-pin.json"],
                            capture_output=True, text=True,
                        )
                        self.assertEqual(prepare.returncode, 0, prepare.stderr)
                        self.assertEqual(json.loads(prepare.stdout)["status"], "PINNED")
                        replay = subprocess.run(
                            [sys.executable, "-B", runner, "replay", "replay-pin.json",
                             "--root", str(unpacked), "--receipt", "replay-receipt.json"],
                            capture_output=True, text=True,
                        )
                        self.assertEqual(replay.returncode, 0, replay.stderr)
                        self.assertEqual(json.loads(replay.stdout)["status"], "MATCH")
                        continue
                    if report["skill"] == "szl-rare-disease-evidence-replay":
                        runner = str(unpacked / "scripts" / "replay.py")
                        self.assertTrue((unpacked / "references" / "contract.md").is_file())
                        replay = subprocess.run(
                            [sys.executable, "-I", "-B", runner, "--root", str(unpacked),
                             "--manifest", "assets/manifest.json", "--output", "rare-replay-report.json"],
                            capture_output=True, text=True,
                        )
                        self.assertEqual(replay.returncode, 0, replay.stderr)
                        parsed = json.loads(replay.stdout)
                        self.assertEqual((parsed["status"], parsed["readiness"], parsed["qualification"]),
                                         ("DECLARED_ONLY", "HOLD", "NOT_EVALUATED"))
                        self.assertEqual(parsed, json.loads((unpacked / "rare-replay-report.json").read_text(encoding="utf-8")))
                        continue
                    self.assertTrue((unpacked / "kernel.py").is_file())
                    command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"),
                               str(unpacked / "assets" / "example.json")]
                    review_output = None
                    if report["skill"] == "szl-reproducibility-capsule":
                        command.extend(["--root", str(unpacked)])
                    elif report["skill"] == "szl-analysis-mutation-test":
                        command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"), "generate",
                                   str(unpacked / "assets" / "example.json"), "--out-dir", str(destination / "mutation-variants")]
                    elif report["skill"] == "szl-session-receipt":
                        command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"), "record",
                                   str(unpacked / "assets" / "example.json"), "--root", str(unpacked / "assets" / "project")]
                    elif report["skill"] == "szl-refutation-ledger":
                        command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"), "status", str(unpacked / "assets" / "example.json")]
                    elif report["skill"] == "szl-repo-pin":
                        command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"), "show", str(unpacked / "assets" / "example.json")]
                    elif report["skill"] == "szl-reviewer-pack":
                        command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"), str(unpacked / "assets" / "project"),
                                   "--json", str(destination / "pack.json"), "--output", str(destination / "REVIEW.md")]
                    elif report["skill"] == "szl-skill-update-review":
                        example = unpacked / "assets" / "example"
                        review_output = destination / "update-review"
                        command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"),
                                   str(example / "old-inventory.json"), str(example / "new-inventory.json"),
                                   "--old-root", str(example / "old-package"),
                                   "--new-root", str(example / "new-package"),
                                   "--lock", str(example / "retained-lock.json"),
                                   "--output-dir", str(review_output)]
                    elif report["skill"] == "szl-clustered-replication":
                        lock = json.loads((unpacked / "assets" / "fixture-lock.json").read_bytes())
                        command.extend(["--expected-plan-sha256", lock["plan_sha256"]])
                    elif report["skill"] == "szl-multiplicity-audit":
                        command = [sys.executable, "-B", str(unpacked / "scripts" / "run.py"),
                                   str(unpacked / "assets" / "plan.json"), str(unpacked / "assets" / "results.json")]
                    p = subprocess.run(command, capture_output=True, text=True)
                    deliberate_findings = {"szl-outcome-preservation": "REGRESSION_OR_GAP",
                                           "szl-release-continuity": "GAP_OR_CONFLICT"}
                    if report["skill"] in deliberate_findings:
                        self.assertEqual(p.returncode, 1, p.stderr)
                        self.assertEqual(json.loads(p.stdout)["status"], deliberate_findings[report["skill"]])
                        continue
                    self.assertEqual(p.returncode, 0, p.stderr)
                    if review_output is not None:
                        review = json.loads((review_output / "UPDATE_REVIEW.json").read_text(encoding="utf-8"))
                        self.assertEqual(review["status"], "CHANGES_REVIEW_REQUIRED")
                        self.assertTrue((review_output / "UPDATE_REVIEW.md").is_file())
                    else:
                        json.loads(p.stdout)

    def test_sidecar_ast_is_loadable_without_filesystem_or_network(self):
        for name in NAMES + ["szl-artifact-lineage", "szl-unit-invariants",
                             "szl-negative-control-audit", "szl-analysis-plan-audit", "szl-clustered-replication",
                             "szl-multiplicity-audit", "szl-assay-measurement-audit",
                             "szl-outcome-preservation", "szl-release-continuity"]:
            with self.subTest(skill=name):
                path = ROOT / "skills" / name / "kernel.py"
                tree = ast.parse(path.read_text())
                for node in tree.body:
                    self.assertIsInstance(node, (ast.Expr, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign))
                    if isinstance(node, ast.FunctionDef):
                        self.assertFalse(node.name.startswith("_"))
                        self.assertNotIn(node.name, dir(builtins))
                        self.assertEqual(node.decorator_list, [])
                        for default in node.args.defaults:
                            ast.literal_eval(default)
                namespace = load(name)
                self.assertTrue(any(k.startswith("szl_") and callable(v) for k, v in namespace.items()))

    def test_every_example_runs_as_standalone_skill(self):
        for name in NAMES:
            with self.subTest(skill=name):
                skill = ROOT / "skills" / name
                process = subprocess.run([sys.executable, "-B", str(skill / "scripts" / "run.py"),
                                          str(skill / "assets" / "example.json"), "--root", str(skill)],
                                         capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                json.loads(process.stdout)

    def test_cli_refuses_overwrite_and_duplicate_json(self):
        skill = ROOT / "skills" / NAMES[1]
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp) / "result.json"
            out.write_text("retain this")
            p = subprocess.run([sys.executable, "-B", str(skill / "scripts" / "run.py"), str(skill / "assets" / "example.json"), "--output", str(out)], capture_output=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(out.read_text(), "retain this")
            inp = pathlib.Path(temp) / "bad.json"
            inp.write_text('{"claim":"one","claim":"two"}')
            p = subprocess.run([sys.executable, "-B", str(skill / "scripts" / "run.py"), str(inp)], capture_output=True)
            self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
