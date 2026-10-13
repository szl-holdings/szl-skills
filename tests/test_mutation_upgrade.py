"""Upgrade tests for szl-analysis-mutation-test. Stdlib only, offline.

Covers the explicit-baseline rule (BASELINE_NOT_TESTED), optional variant binding,
strict generate inputs, the strict CLI JSON reader and the opt-in property/boundary
classes. The BASE_* digests below were computed by running the unchanged base-commit
(b8e259c) kernel and CLI on the same inputs, so they pin that default plans, variant
files and the shipped example report stay byte-identical after the upgrade.
"""
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-analysis-mutation-test"
RUN = SKILL / "scripts" / "run.py"
LARGEST_DOUBLE = sys.float_info.max

DEFAULT_TEN = ["duplicate_rows", "drop_rows", "shuffle_labels", "scale_column", "swap_columns",
               "inject_missing", "precision_drift", "type_confusion", "reorder_rows", "truncate"]
PROPERTY_SIX = ["scale_column_inverse", "negate_column", "extreme_value",
                "conflicting_duplicate_id", "all_missing_column", "single_row"]

# --- pinned from base commit b8e259c ------------------------------------------------------------
BASE_EXAMPLE_PLAN = "e49b4d16e5c5484ccd8d1374999d9607342b96fcb37464ed4dbb55f24f9028a0"
BASE_EXAMPLE_BASELINE = "87d5a475ebda7227610f37951aae690e67cbcc2f5730a3d064032d0f8254254b"
BASE_EXAMPLE_VARIANTS = {
    "duplicate_rows": "43cd160eb51ba82e523acce6fb7f9f61694ccd7857368cd7fb4a66bea2e2523f",
    "drop_rows": "51e51b0dc757cb1c3cc865ac8f2cac9b96c60aa8aeced5a8625d31b4a3be9aa7",
    "shuffle_labels": "dca87d6dd51b8c421d2d1c3aa25a20d568df10c66905e4d43db17af47b9a9f45",
    "scale_column": "4d9c8692b872cffecc60c768a739a5187d7aa0064fef08b5339f5021ab5eae4c",
    "swap_columns": "b3dc8aaabe0b0907b7b05aed8da058b07df5ab92861a3c179cb3c1958016ced1",
    "inject_missing": "bde78c3ed365f14fa079bd145e4c09fde4b26f0ce48abd71a8109757b3cc8455",
    "precision_drift": "2fe6fde21122a209ed5e770a2d88b512a350c185dc8169486e5810303cd89d63",
    "type_confusion": "5bcd583300af256043261632cd981765672f05f7aa432a30896d794964b2eb7b",
    "reorder_rows": "9ee38f5dba9b6d45a02c8a848f1791c6cb0ad1275892f5da4f94ef51774b3cf3",
    "truncate": "1da89dc2a2282a2f512b2dde74ccfa35dc06714ed560c80ab16ccb310d6ddcef",
}
BASE_EXAMPLE_REPORT = "69d30700df495ce9b5b5f28a2b91820657dbd1f53b9012569f2d8b3f46960877"
BASE_SLIM_PLAN = "569c54bcbf5dd78f99b2c8a011785a8f5d3609c370080e7a3f6e41ee89f582df"
BASE_SLIM_REPORT = "b83ca834ce7118fa8026c43c49f4ae05c47cbc41d57724daf9e3106c19ec127d"
# CLI output files, sha256 after normalising CRLF to LF (Windows text-mode writes).
BASE_CLI_FILES = {
    "drop_rows.json": "7d3a5cd8682d792d2b83ac52c64ca0b271be9ab58fc8e614afcd01ffb0f826f1",
    "duplicate_rows.json": "7ed8dd6c7dceea7487cd46af420e56a90ed15a652abd457f54952d4bb2d01119",
    "inject_missing.json": "77e158ff9b49b28448c81c57b8d4b3d52bcc1fa96d7179cb532f55b0635e5eed",
    "plan.json": "5574c9f6d18b02feef623f4971db5d91604aec59252cbb7a96ba19729aef933b",
    "precision_drift.json": "5800fe0322347e17a32087406cbba86bc6b23ba7d73dc41529741faa5b615cc2",
    "reorder_rows.json": "edc6bb3f1b172475a0faa29bf67beb442b4744702e7f14883bf6cdb2f9ea1b24",
    "scale_column.json": "4aca81d4f48d41d9860f272b6a7e285f178c3cd1e98f543ee9785842bce16730",
    "shuffle_labels.json": "d8e86cfbb1e8a59d56cb20863b9e9753e85e536531a4f4c78e507f93ee24e9ce",
    "swap_columns.json": "275dd6d4015e9bcd178593b98e0a3f4d1f63fbe052d85db4f8a40d7816b5d141",
    "truncate.json": "44e95e7608ad7ca2bd44a334952ece2427b216e9cb3a8e828edfff7843a9baea",
    "type_confusion.json": "dd5a20f08c6a5205c0b44af6c322150199e405316d9d7a16252407251928f40b",
}
BASE_CLI_SCORE_STDOUT = "6887c7968876acf167c052cd8409fe5a7d8a9117d509cdd3b62658e17c872df0"
VECTOR_ROWS = [
    {"id": "a", "x": 1.5, "y": 2, "z": None, "flag": True, "tag": "p"},
    {"id": "b", "x": -0.25, "y": 7, "z": 3, "flag": False, "tag": "q"},
    {"id": "c", "x": 0.0, "y": None, "z": 4.5, "flag": True, "tag": "r"},
    {"id": "d", "x": 12.0, "y": 1, "z": -2, "flag": False, "tag": "s"},
    {"id": "e", "x": 3.25, "y": 9, "z": 0, "flag": True, "tag": "t"},
]
BASE_VECTOR_PLAN = "d626cac7efcd15bbc2d80fa18d0cf1f4b622d4796429b732c1823dae8d0343a2"
BASE_VECTOR_VARIANTS = {
    "duplicate_rows": "b01679a1a16129800c21fc4865e1956141a63b36af5cbacddb03fb2895d9737c",
    "drop_rows": "274628b7708643975eb548a3d6f325aa24103f6a6c380ed7ad0825d423e1ed1b",
    "shuffle_labels": None,  # SKIPPED: no label column
    "scale_column": "0c1b02a96766941b46ee2ceb4e49993bf6afc7a53c6f790e8287068b25b34c49",
    "swap_columns": "71a504580f90ad1e106b916ff14f7e3f96ca675d42326d52001583be33b1df94",
    "inject_missing": "537b7f961dc5aa8fb5b95ae76c3640042e0e4a96a0a63e5c23be23a355da93ac",
    "precision_drift": "96681e77159bae70d8bd18cd686967eabefdb4e36d57838f4c54e55f7de46e82",
    "type_confusion": "1b9ed60c4fedb93c8c2d3b56ce1810634296800b90417f317d04b7fc48851bdd",
    "reorder_rows": "8d70e65376c2c350ba290207f70d840879b723c40a9de0553a4e250380ab2327",
    "truncate": "5075772350df6fe40f9047d1b3a0da67615f5665a85bc59e0011a1e5b26a7d25",
}
BASE_VECTOR_SUBSET_PLAN = "5582d224b8eef55af745cd28664853cbce555aa037e90ba18e8406e88774793b"

# --- forward vectors for the new opt-in fixture (computed with this change) ---------------------
PROPERTY_PLAN = "3873c4cc4c1a3bdc46238d50cb779257a0b1d247f276e2d9c785b9e189c9d1ae"


def load():
    spec = importlib.util.spec_from_file_location("szl_mutation_upgrade_kernel", SKILL / "kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def asset(name):
    return json.loads((SKILL / "assets" / name).read_text(encoding="utf-8"))


def run_cli(*args):
    return subprocess.run([sys.executable, "-B", str(RUN), *map(str, args)], capture_output=True, timeout=60)


def lf_sha256(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def by_name(plan):
    return {v["mutation"]: v for v in plan["variants"]}


def flag_all(plan, **extra):
    variants = {v["mutation"]: {"flagged": True} for v in plan["variants"] if v["status"] == "GENERATED"}
    return dict(extra, variants=variants)


class BaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.k = load()

    def test_default_list_is_unchanged_and_property_classes_are_outside_it(self):
        self.assertEqual(self.k.MUTATIONS, DEFAULT_TEN)
        self.assertEqual(self.k.PROPERTY_MUTATIONS, PROPERTY_SIX)
        self.assertFalse(set(self.k.MUTATIONS) & set(self.k.PROPERTY_MUTATIONS))
        self.assertEqual(self.k.SCHEMA_PLAN, "szl.mutation-plan.v1")
        self.assertEqual(self.k.SCHEMA_REPORT, "szl.mutation-coverage-report.v1")

    def test_example_plan_and_report_match_base_commit(self):
        plan = self.k.szl_generate_mutations(asset("example.json"))
        self.assertEqual(self.k.szl_digest(plan), BASE_EXAMPLE_PLAN)
        self.assertEqual(plan["schema"], "szl.mutation-plan.v1")
        self.assertEqual(plan["baseline_sha256"], BASE_EXAMPLE_BASELINE)
        self.assertEqual({v["mutation"]: v["sha256"] for v in plan["variants"]}, BASE_EXAMPLE_VARIANTS)
        report = self.k.szl_score_mutations(plan, asset("outcomes-example.json"))
        self.assertEqual(self.k.szl_digest(report), BASE_EXAMPLE_REPORT)
        self.assertEqual((report["status"], report["coverage"]), ("SCORED", "6/10"))
        self.assertNotIn("reason", report)
        slim = dict(plan, variants=[{a: b for a, b in v.items() if a != "rows"} for v in plan["variants"]])
        self.assertEqual(self.k.szl_digest(slim), BASE_SLIM_PLAN)
        self.assertEqual(self.k.szl_digest(self.k.szl_score_mutations(slim, asset("outcomes-example.json"))),
                         BASE_SLIM_REPORT)

    def test_cli_files_and_score_output_match_base_commit(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "variants"
            p = run_cli("generate", SKILL / "assets" / "example.json", "--out-dir", out)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual({f.name: lf_sha256(f.read_bytes()) for f in out.iterdir()}, BASE_CLI_FILES)
            p = run_cli("score", out / "plan.json", SKILL / "assets" / "outcomes-example.json")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(lf_sha256(p.stdout), BASE_CLI_SCORE_STDOUT)

    def test_determinism_vectors_for_the_ten_default_classes(self):
        for _ in range(2):
            plan = self.k.szl_generate_mutations({"k": 3, "seed": 5, "rows": VECTOR_ROWS})
            self.assertEqual(self.k.szl_digest(plan), BASE_VECTOR_PLAN)
            self.assertEqual({v["mutation"]: v["sha256"] for v in plan["variants"]}, BASE_VECTOR_VARIANTS)
        subset = {"k": 3, "seed": 5, "rows": VECTOR_ROWS, "label_column": "tag",
                  "mutations": ["shuffle_labels", "swap_columns", "inject_missing"]}
        self.assertEqual(self.k.szl_digest(self.k.szl_generate_mutations(subset)), BASE_VECTOR_SUBSET_PLAN)

    def test_explicit_false_binding_keeps_the_v1_plan_byte_identical(self):
        doc = dict(asset("example.json"), require_variant_binding=False)
        self.assertEqual(self.k.szl_digest(self.k.szl_generate_mutations(doc)), BASE_EXAMPLE_PLAN)
        doc = dict(asset("example.json"), mutations=[])
        self.assertEqual(self.k.szl_digest(self.k.szl_generate_mutations(doc)), BASE_EXAMPLE_PLAN)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.k = load()
        self.plan = self.k.szl_generate_mutations(asset("example.json"))

    def test_always_flag_qc_without_baseline_is_not_scored(self):
        report = self.k.szl_score_mutations(self.plan, asset("outcomes-no-baseline.json"))
        self.assertEqual(report["status"], "BASELINE_NOT_TESTED")
        self.assertEqual(report["coverage"], "10/10")
        self.assertIsNone(report["baseline_flagged"])
        self.assertIn("baseline_flagged", report["reason"])

    def test_only_an_explicit_boolean_false_scores(self):
        outcomes = asset("outcomes-no-baseline.json")
        for value, status in ((False, "SCORED"), (True, "BASELINE_FLAGGED"), ("false", "BASELINE_NOT_TESTED"),
                              (0, "BASELINE_NOT_TESTED"), (None, "BASELINE_NOT_TESTED")):
            report = self.k.szl_score_mutations(self.plan, dict(outcomes, baseline_flagged=value))
            self.assertEqual(report["status"], status, value)
            self.assertEqual("reason" in report, status == "BASELINE_NOT_TESTED")

    def test_existing_status_precedence_is_kept(self):
        self.assertEqual(self.k.szl_score_mutations(self.plan, {"variants": {}})["status"], "NO_OUTCOMES_RECORDED")
        self.assertEqual(self.k.szl_score_mutations(self.plan, {"baseline_flagged": True, "variants": {}})["status"],
                         "BASELINE_FLAGGED")

    def test_cli_reports_baseline_not_tested_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli("generate", SKILL / "assets" / "example.json", "--out-dir", d).returncode, 0)
            p = run_cli("score", pathlib.Path(d) / "plan.json", SKILL / "assets" / "outcomes-no-baseline.json")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["status"], "BASELINE_NOT_TESTED")


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.k = load()

    def test_default_plans_still_accept_unbound_outcomes(self):
        plan = self.k.szl_generate_mutations(asset("example.json"))
        report = self.k.szl_score_mutations(plan, flag_all(plan, baseline_flagged=False))
        self.assertEqual((report["status"], report["coverage"]), ("SCORED", "10/10"))

    def test_outcomes_can_require_binding_on_a_v1_plan(self):
        plan = self.k.szl_generate_mutations(asset("example.json"))
        report = self.k.szl_score_mutations(plan, flag_all(plan, baseline_flagged=False, require_variant_binding=True))
        self.assertEqual(report["not_tested"], DEFAULT_TEN)
        self.assertEqual(report["status"], "NO_OUTCOMES_RECORDED")
        self.assertEqual({r["reason"] for r in report["rows"]}, {"variant_sha256 required"})

    def test_plan_binding_needs_matching_variant_digests(self):
        plan = self.k.szl_generate_mutations(asset("property-example.json"))
        self.assertIs(plan["require_variant_binding"], True)
        unbound = self.k.szl_score_mutations(plan, flag_all(plan, baseline_flagged=False))
        self.assertEqual(unbound["not_tested"], PROPERTY_SIX)
        bound = flag_all(plan, baseline_flagged=False)
        for v in plan["variants"]:
            bound["variants"][v["mutation"]]["variant_sha256"] = v["sha256"]
        bound["variants"]["single_row"]["variant_sha256"] = "0" * 64
        report = self.k.szl_score_mutations(plan, bound)
        self.assertEqual(report["status"], "SCORED")
        self.assertEqual(report["caught"], PROPERTY_SIX[:-1])
        self.assertEqual(report["not_tested"], ["single_row"])
        self.assertEqual(report["coverage"], "5/6")

    def test_dataset_binding_on_default_classes_gives_a_v2_plan_that_enforces_it(self):
        plan = self.k.szl_generate_mutations(dict(asset("example.json"), require_variant_binding=True))
        self.assertEqual(plan["schema"], "szl.mutation-plan.v2")
        self.assertIs(plan["require_variant_binding"], True)
        self.assertEqual((plan["columns"], plan["id_column"]), (None, None))
        self.assertEqual({v["mutation"]: v["sha256"] for v in plan["variants"]}, BASE_EXAMPLE_VARIANTS)
        report = self.k.szl_score_mutations(plan, flag_all(plan, baseline_flagged=False))
        self.assertEqual(report["not_tested"], DEFAULT_TEN)
        self.assertEqual((report["status"], report["coverage"]), ("NO_OUTCOMES_RECORDED", "0/10"))
        self.assertEqual({r["reason"] for r in report["rows"]}, {"variant_sha256 required"})

    def test_non_boolean_binding_is_an_error(self):
        plan = self.k.szl_generate_mutations(asset("example.json"))
        for p, o in ((plan, {"require_variant_binding": "yes", "variants": {}}),
                     (dict(plan, require_variant_binding=1), {"variants": {}})):
            self.assertEqual(self.k.szl_score_mutations(p, o)["status"], "ERROR")
        doc = dict(asset("example.json"), require_variant_binding="true")
        self.assertEqual(self.k.szl_generate_mutations(doc)["status"], "ERROR")


class StrictInputTests(unittest.TestCase):
    def setUp(self):
        self.k = load()

    def gen(self, **changes):
        return self.k.szl_generate_mutations(dict(asset("example.json"), **changes))

    def test_k_and_seed_must_be_json_integers(self):
        for key, value in (("k", "2"), ("k", True), ("k", 2.0), ("k", -1), ("k", None),
                           ("seed", "11"), ("seed", False), ("seed", 1.5), ("seed", [11])):
            report = self.gen(**{key: value})
            self.assertEqual(report["status"], "ERROR", (key, value))
            self.assertIn("integer", report["reason"])
        self.assertEqual(self.gen(k=0)["status"], "GENERATED")
        self.assertEqual(self.gen(seed=-7)["status"], "GENERATED")

    def test_duplicate_or_malformed_mutation_names_are_rejected(self):
        for value in (["drop_rows", "drop_rows"], "drop_rows", [1], [""], {"drop_rows": True}):
            self.assertEqual(self.gen(mutations=value)["status"], "ERROR", value)
        self.assertEqual(self.gen(mutations=["drop_rows", "nonsense"])["status"], "ERROR")
        plan = self.gen(mutations=["drop_rows"])
        twice = dict(plan, variants=plan["variants"] * 2)
        report = self.k.szl_score_mutations(twice, {"baseline_flagged": False, "variants": {}})
        self.assertEqual(report["status"], "ERROR")
        broken = dict(plan, variants=[{"status": "GENERATED"}])
        self.assertEqual(self.k.szl_score_mutations(broken, {"variants": {}})["status"], "ERROR")

    def test_columns_and_id_column_are_validated(self):
        prop = {"mutations": ["negate_column"]}
        for changes in ({"columns": []}, {"columns": ["dose_mg", "dose_mg"]}, {"columns": [1]},
                        {"columns": "dose_mg"}, {"columns": ["missing"]}, {"id_column": ""},
                        {"id_column": 3}, {"id_column": "missing"}):
            self.assertEqual(self.gen(**prop, **changes)["status"], "ERROR", changes)
        self.assertEqual(self.gen(columns=["dose_mg"])["status"], "ERROR")
        self.assertEqual(self.gen(id_column="sample")["status"], "ERROR")
        self.assertEqual(self.gen(**prop, columns=["dose_mg"])["status"], "GENERATED")


class PropertyClassTests(unittest.TestCase):
    ROWS = [{"id": "r1", "a": 10, "b": 0.5, "s": "x", "label": 1},
            {"id": "r2", "a": 0, "b": -2.0, "s": "y", "label": 0},
            {"id": "r3", "a": 30, "b": None, "s": "z", "label": 1}]

    def setUp(self):
        self.k = load()

    def gen(self, mutations, rows=None, **changes):
        doc = {"rows": rows or self.ROWS, "label_column": "label", "mutations": mutations, **changes}
        return self.k.szl_generate_mutations(doc)

    def variant(self, name, rows=None, **changes):
        plan = self.gen([name], rows, **changes)
        self.assertEqual(plan["status"], "GENERATED", plan)
        return plan["variants"][0]

    def test_fixture_generates_all_six_as_a_deterministic_v2_plan(self):
        a = self.k.szl_generate_mutations(asset("property-example.json"))
        b = self.k.szl_generate_mutations(asset("property-example.json"))
        self.assertEqual(self.k.szl_digest(a), self.k.szl_digest(b))
        self.assertEqual(self.k.szl_digest(a), PROPERTY_PLAN)
        self.assertEqual(a["schema"], "szl.mutation-plan.v2")
        self.assertEqual((a["columns"], a["id_column"]), (["dose_mg"], "sample"))
        self.assertEqual(a["expected_detection"], PROPERTY_SIX)
        for v in a["variants"]:
            self.assertNotEqual(v["sha256"], a["baseline_sha256"])

    def test_scale_column_inverse(self):
        v = self.variant("scale_column_inverse")
        self.assertEqual([r["a"] for r in v["rows"]], [0.01, 0, 0.03])  # zero cell left alone
        self.assertEqual(v["detail"], {"columns": ["a"], "cells": 2, "factor": 0.001})
        self.assertEqual([r["b"] for r in v["rows"]], [0.5, -2.0, None])
        skipped = self.gen(["scale_column_inverse"], columns=["s"])["variants"][0]
        self.assertEqual(skipped["status"], "SKIPPED")
        self.assertIn("not numeric", skipped["detail"]["skipped"])
        strings = [{"name": "x", "label": "p"}, {"name": "y", "label": "q"}]
        self.assertEqual(self.gen(["scale_column_inverse"], strings)["variants"][0]["status"], "SKIPPED")

    def test_scale_column_inverse_leaves_cells_beyond_double_range_alone(self):
        huge = 10 ** 400
        v = self.variant("scale_column_inverse", [{"a": huge, "label": 1}, {"a": 2000, "label": 0}])
        self.assertEqual([r["a"] for r in v["rows"]], [huge, 2.0])
        self.assertEqual(v["detail"]["cells"], 1)
        only_huge = self.gen(["scale_column_inverse", "negate_column"], [{"a": huge, "label": 1}, {"a": -huge, "label": 0}])
        self.assertEqual([x["status"] for x in only_huge["variants"]], ["NO_OP", "NO_OP"])

    def test_negate_column_over_listed_columns(self):
        v = self.variant("negate_column", columns=["a", "b"])
        self.assertEqual([(r["a"], r["b"]) for r in v["rows"]], [(-10, -0.5), (0, 2.0), (-30, None)])
        self.assertEqual(v["detail"]["cells"], 4)
        zeros = [{"a": 0, "label": 1}, {"a": 0.0, "label": 0}]
        self.assertEqual(self.gen(["negate_column"], zeros)["variants"][0]["status"], "NO_OP")
        self.assertEqual(self.gen(["negate_column"], [{"s": "x"}])["variants"][0]["status"], "SKIPPED")

    def test_extreme_value_sets_one_cell_per_column(self):
        v = self.variant("extreme_value", columns=["a", "b"])
        for col in ("a", "b"):
            hits = [i for i, r in enumerate(v["rows"]) if r[col] == LARGEST_DOUBLE]
            self.assertEqual(len(hits), 1, col)
        self.assertEqual(v["detail"]["value"], LARGEST_DOUBLE)
        self.assertEqual(len(v["detail"]["row_indices"]), 2)
        self.assertEqual(self.gen(["extreme_value"], [{"s": "x"}])["variants"][0]["status"], "SKIPPED")

    def test_conflicting_duplicate_id(self):
        v = self.variant("conflicting_duplicate_id", id_column="id")
        self.assertEqual(v["row_count"], 4)
        twin, src = v["rows"][-1], v["rows"][v["detail"]["row"]]
        self.assertEqual(twin["id"], src["id"])
        self.assertEqual(twin["a"], src["a"] + 1)
        self.assertEqual({k: x for k, x in twin.items() if k != "a"}, {k: x for k, x in src.items() if k != "a"})
        self.assertEqual(self.gen(["conflicting_duplicate_id"])["variants"][0]["detail"], {"skipped": "no id_column"})
        nulls = self.gen(["conflicting_duplicate_id"], id_column="id", columns=["b"],
                         rows=[{"id": "r1", "b": None, "label": 1}])["variants"][0]
        self.assertEqual(nulls["status"], "SKIPPED")

    def test_conflicting_duplicate_id_needs_an_id_and_a_value_that_changes(self):
        for rows in ([{"id": None, "a": 1, "label": 1}, {"id": "r2", "a": None, "label": 0}],
                     [{"id": "r1", "a": 1e300, "label": 1}, {"id": "r2", "a": -2e300, "label": 0}]):
            v = self.gen(["conflicting_duplicate_id"], rows, id_column="id", columns=["a"])["variants"][0]
            self.assertEqual(v["detail"], {"skipped": "no row with an id and numeric targets"}, rows)

    def test_default_target_skips_label_and_id_columns(self):
        rows = [{"aa_id": 1, "ab_label": 5, "b": 2}, {"aa_id": 2, "ab_label": 6, "b": 4}]
        v = self.variant("negate_column", rows, label_column="ab_label", id_column="aa_id")
        self.assertEqual(v["detail"]["columns"], ["b"])
        listed = self.variant("negate_column", rows, id_column="aa_id", columns=["aa_id", "b"])
        self.assertEqual(listed["detail"]["columns"], ["b"])
        self.assertEqual([r["aa_id"] for r in listed["rows"]], [1, 2])

    def test_all_missing_column(self):
        v = self.variant("all_missing_column")
        self.assertTrue(all(r["a"] is None for r in v["rows"]))
        self.assertEqual(v["detail"], {"columns": ["a"], "cells": 3})
        listed = self.variant("all_missing_column", columns=["s"])
        self.assertTrue(all(r["s"] is None for r in listed["rows"]))
        self.assertEqual(self.gen(["all_missing_column"], [{"s": "x"}])["variants"][0]["status"], "SKIPPED")

    def test_single_row(self):
        v = self.variant("single_row")
        self.assertEqual(v["rows"], self.ROWS[:1])
        self.assertEqual(self.gen(["single_row"], self.ROWS[:1])["variants"][0]["status"], "NO_OP")

    def test_per_class_target_rules_match_the_contract(self):
        rows = [{"i": "a", "x": 0, "s": "t"}, {"i": "b", "x": 0, "s": "u"}]
        expected = {"x": ["NO_OP", "NO_OP", "GENERATED", "GENERATED", "GENERATED", "GENERATED"],
                    "s": ["SKIPPED", "SKIPPED", "SKIPPED", "SKIPPED", "GENERATED", "GENERATED"]}
        for col, statuses in expected.items():
            plan = self.gen(PROPERTY_SIX, rows, id_column="i", columns=[col])
            self.assertEqual([v["status"] for v in plan["variants"]], statuses, col)

    def test_mixed_plan_keeps_default_class_bytes(self):
        mixed = self.k.szl_generate_mutations(dict(asset("example.json"), mutations=DEFAULT_TEN + ["single_row"]))
        self.assertEqual(mixed["schema"], "szl.mutation-plan.v2")
        self.assertEqual({n: v["sha256"] for n, v in by_name(mixed).items() if n in DEFAULT_TEN}, BASE_EXAMPLE_VARIANTS)


class CliStrictJsonTests(unittest.TestCase):
    def check_rejected(self, text, phase="generate"):
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d) / "bad.json"
            bad.write_text(text, encoding="utf-8")
            if phase == "generate":
                p = run_cli("generate", bad, "--out-dir", pathlib.Path(d) / "out")
                self.assertFalse((pathlib.Path(d) / "out").exists())
            else:
                gen = run_cli("generate", SKILL / "assets" / "example.json", "--out-dir", d)
                self.assertEqual(gen.returncode, 0)
                p = run_cli("score", pathlib.Path(d) / "plan.json", bad)
            self.assertEqual(p.returncode, 2, (text, p.stdout, p.stderr))
            self.assertEqual(json.loads(p.stdout)["status"], "ERROR")

    def test_duplicate_keys_and_non_finite_numbers_exit_2(self):
        for text in ('{"rows": [{"a": 1}], "rows": [{"a": 2}]}', '{"rows": [{"a": NaN}]}',
                     '{"rows": [{"a": Infinity}]}', '{"rows": [{"a": -Infinity}]}', '{"rows": [{"a": 1e400}]}',
                     '{"rows": [{"a": 1, "a": 2}]}', "not json"):
            self.check_rejected(text)
        self.check_rejected('{"baseline_flagged": false, "baseline_flagged": true, "variants": {}}', "score")
        self.check_rejected('{"variants": {"drop_rows": {"flagged": true}}, "variants": {}}', "score")

    def test_too_deeply_nested_input_exits_2(self):
        for depth in (600, 100000):  # 600 fails while copying rows, 100000 while reading
            self.check_rejected('{"rows": [{"a": 1, "b": %s%s}, {"a": 2, "b": 3}]}' % ("[" * depth, "]" * depth))

    def test_report_too_deep_to_serialise_exits_2(self):
        deep = "[" * 1200 + "]" * 1200  # reads and scores; the indented report encoder recurses too deep
        self.check_rejected('{"baseline_flagged": false, "variants": {"drop_rows": {"flagged": true, "check": %s}}}'
                            % deep, "score")

    def test_unwritable_output_paths_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            taken = pathlib.Path(d) / "taken"
            taken.write_text("", encoding="utf-8")
            gen = run_cli("generate", SKILL / "assets" / "example.json", "--out-dir", pathlib.Path(d) / "v")
            self.assertEqual(gen.returncode, 0, gen.stderr)
            for args in (("generate", SKILL / "assets" / "example.json", "--out-dir", taken),
                         ("score", pathlib.Path(d) / "v" / "plan.json", SKILL / "assets" / "outcomes-example.json",
                          "--output", d)):
                p = run_cli(*args)
                self.assertEqual(p.returncode, 2, (args, p.stdout, p.stderr))
                self.assertEqual(json.loads(p.stdout)["status"], "ERROR")
                self.assertNotIn(b"Traceback", p.stderr)

    def test_missing_file_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            p = run_cli("generate", pathlib.Path(d) / "absent.json", "--out-dir", d)
            self.assertEqual(p.returncode, 2)
            self.assertEqual(json.loads(p.stdout)["status"], "ERROR")

    def test_property_fixture_through_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p = run_cli("generate", SKILL / "assets" / "property-example.json", "--out-dir", d)
            self.assertEqual(p.returncode, 0, p.stderr)
            names = sorted(f.name for f in pathlib.Path(d).iterdir())
            self.assertEqual(names, sorted([n + ".json" for n in PROPERTY_SIX] + ["plan.json"]))
            plan = json.loads((pathlib.Path(d) / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["schema"], "szl.mutation-plan.v2")
            p = run_cli("score", pathlib.Path(d) / "plan.json", SKILL / "assets" / "outcomes-no-baseline.json")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["status"], "NO_OUTCOMES_RECORDED")


class SkillDocumentTests(unittest.TestCase):
    def test_skill_md_limits_and_references(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 160)
        for ref in re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|json|md))`", text):
            if "/" in ref:
                self.assertTrue((SKILL / ref).is_file(), ref)
        self.assertIn("## What it does not do", text)
        self.assertIn("BASELINE_NOT_TESTED", text)

    def test_provenance_and_contract_sections(self):
        provenance = (SKILL / "references" / "provenance.md").read_text(encoding="utf-8")
        self.assertIn("b8e259c", provenance)
        self.assertIn("Apache-2.0", provenance)
        self.assertIn("NOT_MEASURED", provenance)
        contract = (SKILL / "references" / "contract.md").read_text(encoding="utf-8")
        for word in ["szl.mutation-plan.v2", "require_variant_binding", "BASELINE_NOT_TESTED"] + PROPERTY_SIX:
            self.assertIn(word, contract)


if __name__ == "__main__":
    unittest.main()
