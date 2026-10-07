# SPDX-License-Identifier: Apache-2.0
"""szl.unit-invariants.v2: strict unit grammar and power invariant; v1 reports stay byte-identical."""
import ast
import builtins
import contextlib
import copy
import decimal
import hashlib
import importlib.util
import io
import json
import pathlib
import runpy
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-unit-invariants"
PILOT = ROOT / "pilots" / "range-propagation" / "szl-range-propagation"
API = runpy.run_path(str(SKILL / "kernel.py"))
AUDIT = API["szl_audit_unit_invariants"]
PARSE = API["szl_unit_parse_expression"]

# Pinned from the unmodified kernel and CLI at base commit b8e259c (computed before this change).
V1_EXAMPLE_CLI_STDOUT_SHA256 = "ee7c04c28c471e836e7a2e5d2b205fa128c7e91e3d7858a88fdf95f45fc58166"
V1_EXAMPLE_REPORT_SHA256 = "71c7c4dd6fa5436b0702f7efd8ca0747840ab482f5d73c530d74a3830bc9c3b3"
V1_SCALE_ERROR_REPORT_SHA256 = "f8b7a28d1b355b3e901cdec208f6f81e030221654472fdff1646f98ac81949db"
V1_UNDEFINED_AND_DECLARED_REPORT_SHA256 = "2de6386721d3f3c81b3ffecc18835db6513fa1357bff525e5763c2a4b7aa3b71"
V1_UNIT_TABLE_SHA256 = "b7f2b05d154576010beabe09c3b2901eca130fed230447779513fdcba1b4294d"
PILOT_EXAMPLE_REPORT_SHA256 = "a5d0e83f1d8118d19d42c36b9eb13bfe8fc7d43076b7842efd08d5b8ac954b74"

LENGTH = [1, 0, 0, 0, 0, 0, 0]
MASS = [0, 1, 0, 0, 0, 0, 0]
TIME = [0, 0, 1, 0, 0, 0, 0]
NONE = [0, 0, 0, 0, 0, 0, 0]
ENERGY = [2, 1, -2, 0, 0, 0, 0]


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def asset(name):
    return json.loads((SKILL / "assets" / name).read_text(encoding="utf-8"))


def cli(path):
    main = runpy.run_path(str(SKILL / "scripts" / "run.py"))["main"]
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main([str(path)])
    return code, stdout.getvalue(), stderr.getvalue()


def cli_raw(raw):
    with tempfile.TemporaryDirectory() as temp:
        source = pathlib.Path(temp) / "input.json"
        source.write_bytes(raw.encode("utf-8"))
        return cli(source)


def quantity(name, unit, dimension, values):
    return {"id": name, "unit": unit, "dimension": dimension, "values": values, "range_si": None}


def check(name, operation, operands, result, **extra):
    return dict({"id": name, "operation": operation, "operands": operands, "result": result,
                 "atol_si": "0", "rtol": "0"}, **extra)


def v2(quantities, invariants):
    return {"schema": "szl.unit-invariants.v2", "quantities": quantities, "invariants": invariants}


def invariant(report, name):
    return next(item for item in report["invariants"] if item["id"] == name)


class V1UnchangedTests(unittest.TestCase):
    def test_existing_v1_assets_produce_the_reports_pinned_on_main(self):
        v1_assets = sorted(path.name for path in (SKILL / "assets").glob("*.json")
                           if json.loads(path.read_text(encoding="utf-8"))["schema"] == "szl.unit-invariants.v1")
        self.assertEqual(v1_assets, ["example.json"])
        code, output, diagnostic = cli(SKILL / "assets" / "example.json")
        self.assertEqual((code, diagnostic), (0, ""))
        self.assertEqual(hashlib.sha256(output.encode("utf-8")).hexdigest(), V1_EXAMPLE_CLI_STDOUT_SHA256)
        record = asset("example.json")
        report = AUDIT(record)
        self.assertEqual(canonical(report), V1_EXAMPLE_REPORT_SHA256)
        self.assertEqual(report["schema"], "szl.unit-invariants-report.v1")
        self.assertTrue(all("si_scale" not in item for item in report["quantities"]))

    def test_v1_failure_reports_are_unchanged(self):
        scale_error = asset("example.json")
        scale_error["quantities"][3]["values"] = ["500", "1000"]
        self.assertEqual(canonical(AUDIT(scale_error)), V1_SCALE_ERROR_REPORT_SHA256)
        undefined = asset("example.json")
        undefined["quantities"][2]["values"][0] = "0"
        undefined["quantities"][0]["dimension"] = TIME
        self.assertEqual(canonical(AUDIT(undefined)), V1_UNDEFINED_AND_DECLARED_REPORT_SHA256)

    def test_v1_grammar_is_not_widened(self):
        for unit in ("ug", "L", "V", "kg*m^2/s^2", "m^2", "1/s"):
            with self.subTest(unit=unit):
                record = asset("example.json")
                record["quantities"][0]["unit"] = unit
                with self.assertRaises(ValueError):
                    AUDIT(record)
        record = asset("example.json")
        record["invariants"][0].update(operation="power", exponent=1)
        with self.assertRaises(ValueError):
            AUDIT(record)
        del record["invariants"][0]["exponent"]
        with self.assertRaises(ValueError):
            AUDIT(record)
        for schema in ("szl.unit-invariants.v3", "szl.unit-invariants", None, ["szl.unit-invariants.v2"]):
            with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, "Expected"):
                AUDIT(dict(asset("example.json"), schema=schema))

    def test_range_propagation_pilot_is_unaffected(self):
        self.assertEqual(canonical(API["SZL_UNITS"]), V1_UNIT_TABLE_SHA256)
        spec = importlib.util.spec_from_file_location("range_kernel_v2_check", PILOT / "kernel.py")
        pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pilot)
        self.assertEqual(set(pilot.UNITS), set(API["SZL_UNITS"]))
        example = json.loads((PILOT / "assets" / "example.json").read_text(encoding="utf-8"))
        self.assertEqual(canonical(pilot.audit(example)), PILOT_EXAMPLE_REPORT_SHA256)


class ExpressionGrammarTests(unittest.TestCase):
    def test_longest_match_and_exact_power_of_ten_scales(self):
        cases = {
            "1": (NONE, "1e0"), "1/s": ([0, 0, -1, 0, 0, 0, 0], "1e0"),
            "m": (LENGTH, "1e0"), "mm": (LENGTH, "1e-3"), "Mm": (LENGTH, "1e6"),
            "m*m": ([2, 0, 0, 0, 0, 0, 0], "1e0"), "m^2": ([2, 0, 0, 0, 0, 0, 0], "1e0"),
            "cm^2": ([2, 0, 0, 0, 0, 0, 0], "1e-4"), "ms": (TIME, "1e-3"),
            "cd": ([0, 0, 0, 0, 0, 0, 1], "1e0"), "mol": ([0, 0, 0, 0, 0, 1, 0], "1e0"),
            "mmol": ([0, 0, 0, 0, 0, 1, 0], "1e-3"), "kg": (MASS, "1e0"), "g": (MASS, "1e-3"),
            "mg": (MASS, "1e-6"), "ug": (MASS, "1e-9"), "ng": (MASS, "1e-12"),
            "L": ([3, 0, 0, 0, 0, 0, 0], "1e-3"), "uL": ([3, 0, 0, 0, 0, 0, 0], "1e-9"),
            "mol/L": ([-3, 0, 0, 0, 0, 1, 0], "1e3"), "kg*m^2/s^2": (ENERGY, "1e0"),
            "J*kg^-1*K^-1": ([2, 0, -2, 0, -1, 0, 0], "1e0"), "W/m^2": ([0, 1, -3, 0, 0, 0, 0], "1e0"),
            "kohm": ([2, 1, -3, -2, 0, 0, 0], "1e3"), "Mohm": ([2, 1, -3, -2, 0, 0, 0], "1e6"),
            "mohm": ([2, 1, -3, -2, 0, 0, 0], "1e-3"), "kHz": ([0, 0, -1, 0, 0, 0, 0], "1e3"),
            "nm^4": ([4, 0, 0, 0, 0, 0, 0], "1e-36"), "Mm^6": ([6, 0, 0, 0, 0, 0, 0], "1e36"),
        }
        for text, expected in cases.items():
            with self.subTest(unit=text):
                self.assertEqual(PARSE(text), expected)

    def test_derived_symbols_agree_with_their_base_unit_compositions(self):
        for derived, composed in (("J", "kg*m^2/s^2"), ("N", "kg*m/s^2"), ("Pa", "N/m^2"), ("W", "J/s"),
                                  ("V", "W/A"), ("C", "A*s"), ("ohm", "V/A"), ("Hz", "1/s"), ("kJ", "kN*m")):
            with self.subTest(derived=derived):
                self.assertEqual(PARSE(derived), PARSE(composed))
        ug, mg = (decimal.Decimal(PARSE(unit)[1]) for unit in ("ug", "mg"))
        self.assertEqual(mg / ug, 1000)
        for unit, (dimension, factor) in API["SZL_UNITS"].items():
            if unit not in ("min", "h"):
                with self.subTest(v1_unit=unit):
                    parsed_dimension, parsed_scale = PARSE(unit)
                    self.assertEqual(parsed_dimension, dimension)
                    self.assertEqual(decimal.Decimal(parsed_scale), decimal.Decimal(factor))

    def test_offset_log_ambiguous_and_malformed_units_are_rejected(self):
        rejected = ["degC", "dB", "pH", "min", "h", "µg", "μg", "m/s/s", "J/kg*K", "m^0", "m^9",
                    "m^-9", "m^+2", "m^2.0", "m^02", "m ", " m", "m**2", "m^^2", "(m)", "m.s", "m-s", "",
                    "1*m", "m*1", "1/1", "m*", "/s", "m/", "kkg", "M", "Kg", "KG", "meter", "nm^5",
                    "Mm^8*m", "m*s*m*s*m*s*m*s*m", "m^٢", "m" * 65, None, 1, ["m"]]
        for text in rejected:
            with self.subTest(unit=text), self.assertRaises(ValueError):
                PARSE(text)

    def test_offset_and_log_units_still_fail_closed_in_v2_records(self):
        for unit in ("degC", "dB", "pH", "µg"):
            with self.subTest(unit=unit), self.assertRaisesRegex(ValueError, "offset/logarithmic"):
                AUDIT(v2([quantity("x", unit, NONE, ["1"])], []))


class FixtureTests(unittest.TestCase):
    def test_v2_example_passes_e_equals_m_c_squared(self):
        code, output, diagnostic = cli(SKILL / "assets" / "v2-example.json")
        self.assertEqual((code, diagnostic), (0, ""))
        report = json.loads(output)
        self.assertEqual(report["schema"], "szl.unit-invariants-report.v2")
        self.assertEqual(report["status"], "PASS_DECLARED_CHECKS")
        self.assertEqual(report["findings"], [])
        self.assertEqual({item["status"] for item in report["invariants"]}, {"AGREEMENT_ON_DECLARED_CASES"})
        self.assertEqual(invariant(report, "c-squared")["exponent"], 2)
        self.assertEqual(invariant(report, "c-squared")["cases_checked"], 1)
        scales = {item["id"]: item["si_scale"] for item in report["quantities"]}
        self.assertEqual(scales["mass"], "0.001")
        dims = {item["id"]: item["si_dimension"] for item in report["quantities"]}
        self.assertEqual(dims["energy-j"], dims["energy-si"])
        self.assertFalse(report["observed_units_verified"])
        self.assertFalse(report["physical_law_verified"])
        self.assertEqual(report["scientific_validity"], "NOT_MEASURED")
        self.assertEqual(AUDIT(asset("v2-example.json")), AUDIT(asset("v2-example.json")))

    def test_v2_mismatch_reports_dimension_and_thousandfold_scale(self):
        code, output, diagnostic = cli(SKILL / "assets" / "v2-mismatch.json")
        self.assertEqual((code, diagnostic), (1, ""))
        report = json.loads(output)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual([(item["code"], item["invariant"]) for item in report["findings"]],
                         [("DIMENSION_MISMATCH", "work-definition"), ("NUMERICAL_MISMATCH", "dose-transcribed"),
                          ("SCALE_MISMATCH", "dose-transcribed")])
        work = invariant(report, "work-definition")
        self.assertEqual((work["status"], work["cases_checked"]), ("DIMENSION_MISMATCH", 0))
        dose = invariant(report, "dose-transcribed")
        self.assertEqual(dose["status"], "NUMERICAL_MISMATCH")
        self.assertEqual(dose["failures"][0]["scale_ratio"], "1000")

    def test_v1_example_as_v2_keeps_values_and_statuses(self):
        v1_report = AUDIT(asset("example.json"))
        promoted = dict(asset("example.json"), schema="szl.unit-invariants.v2")
        v2_report = AUDIT(promoted)
        self.assertEqual(v2_report["status"], v1_report["status"])
        self.assertEqual([item["values_si"] for item in v2_report["quantities"]],
                         [item["values_si"] for item in v1_report["quantities"]])
        self.assertEqual([item["status"] for item in v2_report["invariants"]],
                         [item["status"] for item in v1_report["invariants"]])
        self.assertNotEqual(v2_report["input_sha256"], v1_report["input_sha256"])


class PowerInvariantTests(unittest.TestCase):
    def test_square_cube_and_reciprocal_follow_dimension_algebra(self):
        record = v2([quantity("side", "m", LENGTH, ["2", "-3", "0"]),
                     quantity("area", "m^2", [2, 0, 0, 0, 0, 0, 0], ["4", "9", "0"]),
                     quantity("cube", "m^3", [3, 0, 0, 0, 0, 0, 0], ["8", "-27", "0"]),
                     quantity("inverse", "1/m", [-1, 0, 0, 0, 0, 0, 0], ["0.5", "-0.25", "7"]),
                     quantity("side-mm", "mm", LENGTH, ["2000", "-3000", "0"])],
                    [check("square", "power", ["side"], "area", exponent=2),
                     check("cube-check", "power", ["side-mm"], "cube", exponent=3),
                     check("reciprocal", "power", ["side"], "inverse", exponent=-1, atol_si="0.1")])
        report = AUDIT(record)
        self.assertEqual(invariant(report, "square")["status"], "AGREEMENT_ON_DECLARED_CASES")
        self.assertEqual(invariant(report, "cube-check")["status"], "AGREEMENT_ON_DECLARED_CASES")
        reciprocal = invariant(report, "reciprocal")
        self.assertEqual(reciprocal["status"], "UNDEFINED_RATIO")
        self.assertEqual((reciprocal["undefined_count"], reciprocal["cases_checked"]), (1, 2))
        self.assertEqual(reciprocal["exponent"], -1)
        self.assertEqual([item["code"] for item in report["findings"]], ["UNDEFINED_RATIO"])

    def test_power_dimension_and_scale_failures_use_v1_status_names(self):
        record = v2([quantity("side", "m", LENGTH, ["2", "3"]),
                     quantity("wrong-dim", "m", LENGTH, ["4", "9"]),
                     quantity("area-cm2", "cm^2", [2, 0, 0, 0, 0, 0, 0], ["4", "9"])],
                    [check("dim", "power", ["side"], "wrong-dim", exponent=2),
                     check("scale", "power", ["side"], "area-cm2", exponent=2)])
        report = AUDIT(record)
        self.assertEqual(invariant(report, "dim")["status"], "DIMENSION_MISMATCH")
        scale = invariant(report, "scale")
        self.assertEqual((scale["status"], scale["scale_mismatch_count"]), ("NUMERICAL_MISMATCH", 2))
        self.assertEqual([case["scale_ratio"] for case in scale["failures"]], ["10000", "10000"])
        self.assertEqual(sorted({item["code"] for item in report["findings"]}),
                         ["DIMENSION_MISMATCH", "NUMERICAL_MISMATCH", "SCALE_MISMATCH"])

    def test_power_fields_and_exponents_fail_closed(self):
        base = v2([quantity("side", "m", LENGTH, ["2"]), quantity("area", "m^2", [2, 0, 0, 0, 0, 0, 0], ["4"])],
                  [check("square", "power", ["side"], "area", exponent=2)])
        self.assertEqual(AUDIT(base)["status"], "PASS_DECLARED_CHECKS")
        for exponent in (0, 9, -9, True, 2.0, "2", decimal.Decimal("2"), None, [2]):
            with self.subTest(exponent=exponent), self.assertRaises(ValueError):
                record = copy.deepcopy(base)
                record["invariants"][0]["exponent"] = exponent
                AUDIT(record)
        mutations = {
            "missing exponent": lambda inv: inv.pop("exponent"),
            "two operands": lambda inv: inv.update(operands=["side", "side"]),
            "exponent on product": lambda inv: inv.update(operation="product", operands=["side", "side"]),
            "expression operation": lambda inv: inv.update(operation="side**2"),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label), self.assertRaises(ValueError):
                record = copy.deepcopy(base)
                mutate(record["invariants"][0])
                AUDIT(record)

    def test_power_magnitude_guard_and_extreme_scales_raise_no_decimal_signal(self):
        big = v2([quantity("x", "1", NONE, ["1e100"]), quantity("y", "1", NONE, ["1"])],
                 [check("cube", "power", ["x"], "y", exponent=3)])
        with self.assertRaisesRegex(ValueError, "magnitude"):
            AUDIT(big)
        edge = v2([quantity("x", "1", NONE, ["1e-30"]), quantity("y", "1", NONE, ["1e-100"])],
                  [check("eighth", "power", ["x"], "y", exponent=-8)])
        self.assertEqual(invariant(AUDIT(edge), "eighth")["scale_mismatch_count"], 1)
        wide = v2([quantity("a", "Mm^4", [4, 0, 0, 0, 0, 0, 0], ["1e100"]),
                   quantity("b", "Mm^4", [4, 0, 0, 0, 0, 0, 0], ["-1e100"]),
                   quantity("c", "nm^4*m^4", [8, 0, 0, 0, 0, 0, 0], ["1e-100"])],
                  [check("product", "product", ["a", "b"], "c"), check("ratio", "ratio", ["c", "a"], "a"),
                   check("square", "power", ["a"], "c", exponent=2)])
        with decimal.localcontext() as context:
            context.prec, context.Emax = 3, 10
            context.traps[decimal.Inexact] = True
            report = AUDIT(wide)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(invariant(report, "product")["scale_mismatch_count"], 1)


class CliAndSidecarTests(unittest.TestCase):
    def raw(self, invariant_text):
        return ('{"schema":"szl.unit-invariants.v2","quantities":['
                '{"id":"s","unit":"m","dimension":[1,0,0,0,0,0,0],"values":["2"],"range_si":null},'
                '{"id":"a","unit":"m^2","dimension":[2,0,0,0,0,0,0],"values":["4"],"range_si":null}],'
                '"invariants":[' + invariant_text + ']}')

    def test_cli_accepts_integer_exponent_and_rejects_bad_json_with_exit_2(self):
        good = '{"id":"p","operation":"power","operands":["s"],"exponent":2,"result":"a","atol_si":"0","rtol":"0"}'
        self.assertEqual(cli_raw(self.raw(good))[0], 0)
        for bad in (good.replace('"exponent":2', '"exponent":2.0'),
                    good.replace('"exponent":2', '"exponent":2,"exponent":3'),
                    good.replace('"exponent":2', '"exponent":NaN'),
                    good.replace('"exponent":2', '"exponent":Infinity'),
                    good.replace('"exponent":2', '"exponent":true')):
            with self.subTest(raw=bad):
                code, output, diagnostic = cli_raw(self.raw(bad))
                self.assertEqual((code, output), (2, ""))
                self.assertTrue(diagnostic.startswith("Skill input/execution error"))

    def test_kernel_remains_sidecar_ast_compliant(self):
        tree = ast.parse((SKILL / "kernel.py").read_text(encoding="utf-8"))
        names = []
        for node in tree.body:
            self.assertIsInstance(node, (ast.Expr, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign))
            if isinstance(node, ast.FunctionDef):
                names.append(node.name)
                self.assertTrue(node.name.startswith("szl_"))
                self.assertNotIn(node.name, dir(builtins))
                self.assertEqual(node.decorator_list, [])
                for default in node.args.defaults:
                    ast.literal_eval(default)
        self.assertIn("szl_unit_parse_expression", names)

    def test_skill_entrypoint_uses_only_supported_units(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 160)
        self.assertNotIn("µg column", text)
        self.assertIn("A ug column read as mg", text)
        self.assertEqual(decimal.Decimal(PARSE("mg")[1]) / decimal.Decimal(PARSE("ug")[1]), 1000)


if __name__ == "__main__":
    unittest.main()
