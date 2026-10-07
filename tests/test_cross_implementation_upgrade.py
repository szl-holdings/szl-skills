"""Upgrade tests for szl-cross-implementation-check: strict tolerance overrides, input
binding, flatten collisions, strict CLI JSON and the stability mode. Stdlib only, offline."""
import copy
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
SKILL = ROOT / "skills" / "szl-cross-implementation-check"
DIGEST = "3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e"
TOLERANCE_ERROR = "tolerances must be finite and non-negative"


def load():
    spec = importlib.util.spec_from_file_location("szl_cross_implementation_upgrade", SKILL / "kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


K = load()


def asset(name):
    return json.loads((SKILL / "assets" / name).read_text(encoding="utf-8"))


def check(document):
    return K.szl_cross_implementation_check(document)


def report_sha256(report):
    return hashlib.sha256(K.szl_canonical(report).encode()).hexdigest()


def cli(*args):
    return subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / "run.py"), *args], cwd=str(SKILL),
                          capture_output=True, text=True, timeout=60)


def legacy_documents():
    example = asset("example.json")
    no_digest = copy.deepcopy(example)
    del no_digest["a"]["input_sha256"], no_digest["b"]["input_sha256"]
    return {
        "example": example,
        "example_without_digests": no_digest,
        "identical": {"a": {"results": {"x": 1.0, "ok": True}}, "b": {"results": {"x": 1.0, "ok": True}},
                      "tolerance": {"rtol": 0.01}},
        "digest_mismatch": {"a": {"input_sha256": "a" * 64, "results": {"x": 1.0, "ok": True}},
                            "b": {"input_sha256": "b" * 64, "results": {"x": 1.0, "ok": True}},
                            "tolerance": {"rtol": 0.01}},
        "non_finite": {"a": {"results": {"x": float("nan")}}, "b": {"results": {"x": 1.0}}},
        "negative_tolerance": {"a": {"results": {"x": 1}}, "b": {"results": {"x": 1}}, "tolerance": {"rtol": -1}},
        "nested_lists": {"a": {"input_sha256": "c" * 64, "results": {"coefs": [1, 2.5], "name": "ols"}},
                         "b": {"input_sha256": "c" * 64, "results": {"coefs": [1, 2.5000001], "name": "ols"}},
                         "tolerance": {"atol": 1e-6}},
        "one_side_null": {"a": {"results": {"x": None, "y": 1}}, "b": {"results": {"x": 2, "y": 1}}},
    }


# sha256 of szl_canonical(report), computed with skills/szl-cross-implementation-check/kernel.py
# at base commit b8e259c (before this upgrade). These documents must keep producing the same bytes.
MAIN_REPORT_SHA256 = {
    "example": "f54f5b4c376e0ffde1f099eee919fbe43b6aff103386566775e6713a25616187",
    "example_without_digests": "c71f60f66ec7bf8bb2c8f3b421a19ee4283e41e454ac68ec4db6dea0965317cd",
    "identical": "dfc489010b79cbaea630a0955fab394daa22eedec623ac20f3786abcda321dde",
    "digest_mismatch": "1b6cb1513d016c5e1e059724ab396f0c8617e16a5af61ed58593a60c1acaab90",
    "non_finite": "5cd9cf32827fdc9732240aeeac58dd8a8fe96f5293929fd8129817fd63abde36",
    "negative_tolerance": "c3db2e261b94dee5488707d62644b9dfa3c36b3b052d2ebe898516e8b29d167d",
    "nested_lists": "91d0e3f80987105d64fa69ab2ba943f4909d85d530747315833c0ade16ea2949",
    "one_side_null": "08bb6136a3fb948a815d9f6075ac7a6d9d916ad875a727ae8c480011ef56ad33",
}
# sha256 of the CLI stdout for assets/example.json at b8e259c (json.dumps(report, indent=2) + newline).
MAIN_EXAMPLE_STDOUT_SHA256 = "8cb1020f726ddc9eb571ed19b73c07eb2946227fce7bdfb98b33d013c4f7be6f"


def run(perturbation, results, digest=DIGEST):
    entry = {"perturbation": perturbation, "results": results}
    if digest is not None:
        entry["input_sha256"] = digest
    return entry


def stability(values, required=3, digests=(DIGEST, DIGEST, DIGEST)):
    runs = [run({"seed": i}, {"q": v}, d) for i, (v, d) in enumerate(zip(values, digests))]
    return {"schema": K.STABILITY_SCHEMA, "required_digits": required, "runs": runs}


def quantity(report, name):
    return next(q for q in report["quantities"] if q["quantity"] == name)


class LegacyReportTests(unittest.TestCase):
    def test_existing_documents_report_identical_bytes_to_main(self):
        for name, document in legacy_documents().items():
            with self.subTest(name=name):
                self.assertEqual(report_sha256(check(document)), MAIN_REPORT_SHA256[name])

    def test_example_cli_stdout_identical_to_main(self):
        p = cli("assets/example.json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(hashlib.sha256(p.stdout.encode()).hexdigest(), MAIN_EXAMPLE_STDOUT_SHA256)

    def test_example_status_and_counts_unchanged(self):
        r = check(asset("example.json"))
        self.assertEqual(r["status"], "DIVERGENT")
        self.assertEqual((r["consistent"], r["divergent"], r["incomparable"]), (3, 2, 1))
        self.assertNotIn("reason", r)
        self.assertEqual(quantity(r, "aic")["reason"], "REPORTED_BY_ONE_SIDE_ONLY")


class OverrideTests(unittest.TestCase):
    def with_overrides(self, overrides):
        doc = asset("example.json")
        doc["per_quantity_tolerance"] = overrides
        return check(doc)

    def test_infinite_overrides_cannot_turn_divergence_consistent(self):
        r = self.with_overrides({"effect.se": {"rtol": float("inf")}, "p_value": {"rtol": float("inf")}})
        self.assertEqual((r["status"], r["reason"], r["keys"]), ("ERROR", "INVALID_OVERRIDE", ["effect.se", "p_value"]))

    def test_negative_boolean_string_null_nan_and_oversized_values_rejected(self):
        for bad in (-5, -0.1, True, False, "0.5", None, float("nan"), float("-inf"), 10 ** 400, [0.1], {"v": 1}):
            for field in ("atol", "rtol"):
                with self.subTest(bad=bad, field=field):
                    r = self.with_overrides({"effect.se": {field: bad}})
                    self.assertEqual((r["status"], r["reason"], r["keys"]), ("ERROR", "INVALID_OVERRIDE", ["effect.se"]))

    def test_malformed_override_shapes_rejected(self):
        for overrides, keys in (({"effect.se": 0.5}, ["effect.se"]), ({"effect.se": {"rtl": 0.5}}, ["effect.se"]),
                                ({"effect.se": {"rtol": 0.5, "note": "x"}}, ["effect.se"]), (["effect.se"], []),
                                ("p_value", [])):
            with self.subTest(overrides=overrides):
                r = self.with_overrides(overrides)
                self.assertEqual((r["status"], r["reason"], r["keys"]), ("ERROR", "INVALID_OVERRIDE", keys))

    def test_unknown_override_key_is_error_and_invalid_takes_precedence(self):
        r = self.with_overrides({"effect.SE": {"rtol": 0.5}, "p_value": {"rtol": 0.5}})
        self.assertEqual((r["status"], r["reason"], r["keys"]), ("ERROR", "UNKNOWN_OVERRIDE_KEY", ["effect.SE"]))
        r = self.with_overrides({"effect.SE": {"rtol": 0.5}, "p_value": {"rtol": -1}})
        self.assertEqual((r["reason"], r["keys"]), ("INVALID_OVERRIDE", ["p_value"]))

    def test_valid_overrides_still_apply(self):
        r = self.with_overrides({"p_value": {"rtol": 1.0}, "effect.se": {"atol": 0.02}})
        self.assertEqual(r["status"], "INCOMPARABLE")
        self.assertEqual((quantity(r, "p_value")["status"], quantity(r, "effect.se")["status"]), ("CONSISTENT", "CONSISTENT"))
        self.assertEqual(self.with_overrides({"p_value": {}})["status"], "DIVERGENT")
        self.assertEqual(self.with_overrides(None)["status"], "DIVERGENT")

    def test_global_tolerance_rejects_non_numbers_without_crashing(self):
        for tol in ({"rtol": True}, {"rtol": "0.01"}, {"atol": None}, {"atol": float("inf")}, {"rtol": 10 ** 400}, [0.01]):
            with self.subTest(tol=tol):
                r = check({"a": {"results": {"x": 1}}, "b": {"results": {"x": 1}}, "tolerance": tol})
                self.assertEqual((r["status"], r["reason"]), ("ERROR", TOLERANCE_ERROR))

    def test_oversized_integer_result_is_incomparable_not_a_crash(self):
        r = check({"a": {"results": {"x": 10 ** 400}}, "b": {"results": {"x": 1}}})
        self.assertEqual((r["status"], quantity(r, "x")["reason"]), ("INCOMPARABLE", "NON_NUMERIC_OR_NON_FINITE"))


class BindingAndFlattenTests(unittest.TestCase):
    def identical(self):
        return {"a": {"results": {"x": 1.0, "ok": True}}, "b": {"results": {"x": 1.0, "ok": True}}}

    def test_one_sided_digest_is_incomparable_input_binding_incomplete(self):
        for side in ("a", "b"):
            doc = self.identical()
            doc[side]["input_sha256"] = DIGEST
            r = check(doc)
            self.assertEqual((r["status"], r["reason"], r["inputs_match"]), ("INCOMPARABLE", "INPUT_BINDING_INCOMPLETE", None))
            self.assertEqual((r["consistent"], r["incomparable"]), (2, 0))
        doc = self.identical()
        doc["a"]["input_sha256"], doc["b"]["input_sha256"] = DIGEST, ""
        self.assertEqual(check(doc)["reason"], "INPUT_BINDING_INCOMPLETE")

    def test_undeclared_or_matching_digests_unchanged(self):
        doc = self.identical()
        self.assertEqual(check(doc)["status"], "CONSISTENT")
        doc["a"]["input_sha256"] = doc["b"]["input_sha256"] = ""
        self.assertNotIn("reason", check(doc))
        doc["a"]["input_sha256"] = doc["b"]["input_sha256"] = DIGEST
        r = check(doc)
        self.assertEqual((r["status"], r["inputs_match"]), ("CONSISTENT", True))
        self.assertNotIn("reason", r)

    def test_one_sided_digest_does_not_mask_divergence(self):
        doc = asset("example.json")
        del doc["b"]["input_sha256"]
        r = check(doc)
        self.assertEqual((r["status"], r["reason"]), ("DIVERGENT", "INPUT_BINDING_INCOMPLETE"))

    def test_flatten_collision_is_error(self):
        doc = self.identical()
        doc["a"]["results"] = {"x.y": 1, "x": {"y": 2}}
        r = check(doc)
        self.assertEqual((r["status"], r["reason"], r["keys"]), ("ERROR", "FLATTEN_COLLISION", ["a: x.y"]))
        doc = self.identical()
        doc["b"]["results"] = {"x[0]": 1, "x": [2]}
        self.assertEqual(check(doc)["keys"], ["b: x[0]"])

    def test_null_on_both_sides_is_distinguished_from_one_side(self):
        r = check({"a": {"results": {"x": None, "y": None, "w": 1}},
                   "b": {"results": {"x": None, "y": 2, "w": 1, "z": None}}})
        self.assertEqual(r["status"], "INCOMPARABLE")
        reasons = {q["quantity"]: (q["status"], q.get("reason")) for q in r["quantities"]}
        self.assertEqual(reasons["x"], ("INCOMPARABLE", "NULL_ON_BOTH_SIDES"))
        self.assertEqual(reasons["z"], ("INCOMPARABLE", "NULL_ON_BOTH_SIDES"))
        self.assertEqual(reasons["y"], ("INCOMPARABLE", "REPORTED_BY_ONE_SIDE_ONLY"))
        self.assertEqual(reasons["w"], ("CONSISTENT", "WITHIN_TOLERANCE"))


class CliStrictJsonTests(unittest.TestCase):
    def run_text(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "doc.json"
            path.write_text(text, encoding="utf-8")
            p = cli(str(path))
        return p.returncode, json.loads(p.stdout)

    def test_duplicate_keys_rejected(self):
        for text in ('{"a": {"results": {"x": 1}}, "a": {"results": {"x": 2}}, "b": {"results": {"x": 1}}}',
                     '{"a": {"results": {"x": 1, "x": 9}}, "b": {"results": {"x": 1}}}'):
            with self.subTest(text=text):
                code, out = self.run_text(text)
                self.assertEqual((code, out["status"]), (2, "ERROR"))
                self.assertIn("duplicate JSON key", out["reason"])

    def test_nan_and_infinity_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            for text in ('{"a": {"results": {"x": %s}}, "b": {"results": {"x": 1}}}' % token,
                         '{"a": {"results": {"x": 1}}, "b": {"results": {"x": 1}}, '
                         '"per_quantity_tolerance": {"x": {"rtol": %s}}}' % token):
                with self.subTest(text=text):
                    code, out = self.run_text(text)
                    self.assertEqual((code, out["status"]), (2, "ERROR"))
                    self.assertIn("non-finite JSON number", out["reason"])

    def test_overflowing_override_literal_is_invalid_override(self):
        code, out = self.run_text('{"a": {"results": {"x": 1}}, "b": {"results": {"x": 2}}, '
                                  '"per_quantity_tolerance": {"x": {"rtol": 1e999}}}')
        self.assertEqual((code, out["reason"]), (2, "INVALID_OVERRIDE"))

    def test_stability_fixtures_run_through_cli(self):
        for name, status in (("stability-stable.json", "STABLE"), ("stability-unstable.json", "UNSTABLE")):
            p = cli("assets/" + name)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["status"], status)


class StabilityTests(unittest.TestCase):
    def test_stable_fixture(self):
        r = check(asset("stability-stable.json"))
        self.assertEqual((r["schema"], r["status"], r["inputs_match"], r["issues"]),
                         (K.STABILITY_REPORT, "STABLE", True, []))
        self.assertNotIn("reason", r)
        self.assertEqual((r["runs"], r["required_digits"], r["perturbed_axes"]), (3, 5, ["precision", "threads", "seed"]))
        self.assertEqual([q["status"] for q in r["quantities"]], ["STABLE"] * 3)
        self.assertEqual((quantity(r, "mean")["agreed_digits"], quantity(r, "sd")["agreed_digits"]), (7, 7))
        n = quantity(r, "n")
        self.assertEqual((n["agreed_digits"], n["relative_spread"], n["median"]), (None, 0.0, "100000"))

    def test_unstable_fixture_flags_float32_cancellation(self):
        r = check(asset("stability-unstable.json"))
        self.assertEqual((r["status"], r["issues"], r["perturbed_axes"]), ("UNSTABLE", ["var_naive"], ["precision", "summation_order"]))
        naive = quantity(r, "var_naive")
        self.assertEqual((naive["status"], naive["reason"], naive["agreed_digits"]), ("UNSTABLE", "BELOW_REQUIRED_DIGITS", 0))
        self.assertEqual((naive["min"], naive["max"], naive["median"]), ("-8", "16", "0.0833449"))
        self.assertEqual((naive["sign_consistent"], naive["cancellation"], naive["relative_spread"]), (False, True, 1.5))
        two_pass = quantity(r, "var_two_pass")
        self.assertEqual((two_pass["status"], two_pass["agreed_digits"], two_pass["cancellation"]), ("STABLE", 4, False))

    def test_agreed_digits_boundary_is_exact(self):
        for values in (["1", "1", "0.99999"], [1, 1.0, 0.99999]):
            with self.subTest(values=values):
                q = quantity(check(stability(values, required=5)), "q")
                self.assertEqual((q["status"], q["agreed_digits"]), ("STABLE", 5))
                self.assertEqual(quantity(check(stability(values, required=6)), "q")["status"], "UNSTABLE")

    def test_median_and_text_are_exact_decimals(self):
        doc = stability(["0.1", "0.2", "0.3"])
        doc["runs"].append(run({"seed": 9}, {"q": "0.4"}))
        q = quantity(check(doc), "q")
        self.assertEqual((q["min"], q["max"], q["median"]), ("0.1", "0.4", "0.25"))
        q = quantity(check(stability([0.1, 0.3, 0.2])), "q")
        self.assertEqual((q["min"], q["max"], q["median"]), ("0.1", "0.3", "0.2"))
        self.assertEqual(K.szl_exact_value("-1.5e-3"), K.Fraction(-3, 2000))

        class Wrapped(float):
            def __repr__(self):
                return "Wrapped(%s)" % float(self)

        self.assertEqual(K.szl_exact_value(Wrapped(0.1)), K.Fraction(1, 10))

    def test_identical_and_zero_values_are_stable(self):
        for values in ([0, 0, 0], ["2.50", "2.5", 2.5]):
            with self.subTest(values=values):
                q = quantity(check(stability(values, required=50)), "q")
                self.assertEqual((q["status"], q["agreed_digits"], q["sign_consistent"], q["cancellation"]),
                                 ("STABLE", None, True, False))

    def test_cancellation_threshold_and_sign_change(self):
        q = quantity(check(stability([-1, "0.1", 1])), "q")
        self.assertEqual((q["cancellation"], q["sign_consistent"], q["status"]), (True, False, "UNSTABLE"))
        self.assertFalse(quantity(check(stability([-1, "0.11", 1])), "q")["cancellation"])
        self.assertFalse(quantity(check(stability([0, 1, 1])), "q")["sign_consistent"])

    def test_missing_or_non_numeric_values_are_never_stable(self):
        for bad in (None, True, "1,5", "abc", "+1", "1e5000", float("inf"), float("nan"), 10 ** 400):
            with self.subTest(bad=bad):
                r = check(stability(["1", "1", bad]))
                self.assertEqual((r["status"], r["issues"]), ("UNSTABLE", ["q"]))
                self.assertEqual(quantity(r, "q")["reason"], "NOT_NUMERIC_IN_EVERY_RUN")
        doc = stability(["1", "1", "1"])
        doc["runs"][2]["results"] = {"other": "1"}
        r = check(doc)
        self.assertEqual((quantity(r, "q")["reason"], quantity(r, "other")["status"]), ("NOT_NUMERIC_IN_EVERY_RUN", "UNSTABLE"))

    def test_input_binding(self):
        r = check(stability(["1", "1", "1"], digests=(DIGEST, DIGEST, None)))
        self.assertEqual((r["status"], r["reason"], r["inputs_match"]), ("INCOMPARABLE", "INPUT_BINDING_INCOMPLETE", None))
        r = check(stability(["1", "1", "1"], digests=(DIGEST, DIGEST, "")))
        self.assertEqual(r["reason"], "INPUT_BINDING_INCOMPLETE")
        r = check(stability(["1", "1", "1"], digests=(DIGEST, DIGEST, "f" * 64)))
        self.assertEqual((r["status"], r["inputs_match"]), ("INCOMPARABLE", False))
        self.assertNotIn("reason", r)
        r = check(stability(["1", "2", "3"], digests=(DIGEST, None, "f" * 64)))
        self.assertEqual((r["status"], r["reason"], r["inputs_match"]), ("UNSTABLE", "INPUT_BINDING_INCOMPLETE", False))
        doc = asset("stability-stable.json")
        del doc["runs"][1]["input_sha256"]
        self.assertEqual(check(doc)["status"], "INCOMPARABLE")

    def test_no_quantities_is_incomparable(self):
        doc = stability(["1", "1", "1"])
        for entry in doc["runs"]:
            entry["results"] = {}
        r = check(doc)
        self.assertEqual((r["status"], r["quantities"]), ("INCOMPARABLE", []))

    def test_malformed_documents_are_errors(self):
        base = asset("stability-stable.json")
        cases = []
        cases.append(dict(base, runs=base["runs"][:2]))
        cases.append(dict(base, runs={"0": base["runs"][0]}))
        broken = copy.deepcopy(base)
        del broken["runs"][0]["results"]
        cases.append(broken)
        for required in (0, -1, True, "5", 2.5, None):
            cases.append(dict(base, required_digits=required))
        for perturbation in (None, {}, {"compiler": "gcc"}, {"seed": True}, {"seed": 1.5}, {"seed": [1]},
                             base["runs"][0]["perturbation"]):
            doc = copy.deepcopy(base)
            doc["runs"][2]["perturbation"] = perturbation
            cases.append(doc)
        for doc in cases:
            with self.subTest(doc=doc):
                r = check(doc)
                self.assertEqual((r["schema"], r["status"]), (K.STABILITY_REPORT, "ERROR"))
        doc = copy.deepcopy(base)
        doc["runs"][1]["perturbation"] = doc["runs"][0]["perturbation"]
        self.assertEqual(check(doc)["keys"], ["runs[1]"])

    def test_flatten_collision_in_a_run(self):
        doc = stability(["1", "1", "1"])
        doc["runs"][1]["results"] = {"x.y": 1, "x": {"y": 2}}
        r = check(doc)
        self.assertEqual((r["status"], r["reason"], r["keys"]), ("ERROR", "FLATTEN_COLLISION", ["runs[1]: x.y"]))


class PackageShapeTests(unittest.TestCase):
    def test_skill_md_limits_and_references(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 160)
        self.assertIn("## What it does not do", text)
        for ref in re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|json|md))`", text):
            self.assertTrue((SKILL / ref).is_file(), ref)
        self.assertIn("assets/stability-stable.json", text)
        self.assertIn("assets/stability-unstable.json", text)

    def test_contract_and_provenance(self):
        contract = (SKILL / "references" / "contract.md").read_text(encoding="utf-8")
        for name in ("INPUT_BINDING_INCOMPLETE", "INVALID_OVERRIDE", "UNKNOWN_OVERRIDE_KEY", "FLATTEN_COLLISION",
                     "NULL_ON_BOTH_SIDES", K.STABILITY_SCHEMA, "STABLE", "UNSTABLE"):
            self.assertIn(name, contract)
        provenance = (SKILL / "references" / "provenance.md").read_text(encoding="utf-8")
        for phrase in ("Apache-2.0", "b8e259c", "No third-party code"):
            self.assertIn(phrase, provenance)


if __name__ == "__main__":
    unittest.main()
