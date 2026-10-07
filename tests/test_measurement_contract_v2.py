"""Measurement harmonizer v2 data contract: declared provenance, staleness and missingness.

The v1 schema must keep its exact behaviour. Reports for the v1 cases below were computed
with the harmonizer at base commit b8e259c and are pinned as canonical-JSON SHA-256 digests.
"""

import ast
import copy
import hashlib
import json
import pathlib
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-measurement-harmonizer"
ASSETS = SKILL / "assets"
SCRIPT = SKILL / "scripts" / "run.py"
IMPL = runpy.run_path(str(SKILL / "scripts" / "harmonizer.py"))
HARMONIZE = IMPL["harmonize"]
NOT_EVALUATED_CONTRACT = {"schema_checked": False, "provenance_declared": "NOT_EVALUATED",
                          "staleness": "NOT_EVALUATED", "missingness": "NOT_EVALUATED"}
NA_CSV = b"sample,kelvin\nB1,NA\nB2,293.15\n"

# Canonical digests of main's (b8e259c) reports for the v1 cases in v1_cases().
MAIN_V1_REPORT_SHA256 = {
    "ambiguous_join": "97447100df56151baefcbd7daf0d3036477c87fa279f230e630c4fc1b4d4a5a7",
    "example": "dc1c39badf675d678a71bed5b414a7adf1158eaf88b648d1c3fdeb074042ef22",
    "na_cell_blocks_under_v1": "2f7db240d51be89cd408bb4823e6b0acb613edd44bf7c4a5ada2f8860c88c0d9",
    "nonidentity_target": "bd027e9e72d25c364e8a3d1772450af8ab8dbeb08d4a56d657bd60ebb6893e4f",
    "not_an_object": "c5c55995c1656bc9d88e40d34e61da04e2fdf7b171a2d8e6d058b0aaaa366a96",
    "provenance_under_v1": "a75d568f13eac349341154231831edde270e4552fb0cf7edf3749eab2e968a38",
    "unknown_schema": "5c80b5bdabc13857bfcfcb8e328ad32b6b1f539c7a384ac10f1d368eba2f7339",
    "v2_manifest_field_under_v1": "c5c55995c1656bc9d88e40d34e61da04e2fdf7b171a2d8e6d058b0aaaa366a96",
    "v2_missing_tokens_under_v1": "c5c55995c1656bc9d88e40d34e61da04e2fdf7b171a2d8e6d058b0aaaa366a96",
}


def load(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


def canonical_sha256(report):
    text = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def codes(report):
    return [item["code"] for item in report["findings"]]


def v1_cases():
    """Return name -> (manifest, extra files); every case runs in a fresh copy of the assets."""
    base = load("example.json")

    def edit(change, files=None):
        manifest = copy.deepcopy(base)
        change(manifest)
        return manifest, files or {}

    def na_cell(manifest):
        manifest["sources"][1]["path"] = "na.csv"
        manifest["sources"][1]["sha256"] = hashlib.sha256(NA_CSV).hexdigest()

    return {
        "example": (base, {}),
        "v2_manifest_field_under_v1": edit(lambda m: m.update(as_of="2026-09-30T00:00:00Z")),
        "v2_missing_tokens_under_v1": edit(lambda m: m.update(missing_tokens=["NA"])),
        "provenance_under_v1": edit(lambda m: m["sources"][0].update(provenance={})),
        "unknown_schema": edit(lambda m: m.update(schema="szl.measurement-harmonizer.v3")),
        "ambiguous_join": edit(lambda m: m["sources"][0]["id_map"].update({"A-2": "specimen-1"})),
        "nonidentity_target": edit(lambda m: m["conversions"]["K"].update(offset="1")),
        "na_cell_blocks_under_v1": edit(na_cell, {"na.csv": NA_CSV}),
        "not_an_object": ([], {}),
    }


def run_case(harmonize, manifest, files):
    with tempfile.TemporaryDirectory() as temp:
        root = pathlib.Path(temp)
        for name in ("lab-a.csv", "lab-b.csv", "v2-lab-c.csv", "v2-over-missing.csv"):
            shutil.copyfile(ASSETS / name, root / name)
        for name, content in files.items():
            (root / name).write_bytes(content)
        return harmonize(manifest, root)


def v1_report_digests(harmonize):
    return {name: canonical_sha256(run_case(harmonize, manifest, files))
            for name, (manifest, files) in v1_cases().items()}


def run_cli(manifest_path, output):
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(manifest_path), "--root", str(ASSETS),
                             "--output", str(output)], capture_output=True, text=True, check=False)
    report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    return result, report


class MeasurementContractV2Tests(unittest.TestCase):
    def setUp(self):
        self.example = load("v2-example.json")

    def harmonize_v2(self, change, files=None):
        manifest = copy.deepcopy(self.example)
        change(manifest)
        return run_case(HARMONIZE, manifest, files or {})

    def assert_blocked(self, report, code):
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["rows"], [])
        self.assertIn(code, codes(report))

    def test_v1_reports_match_main_pins(self):
        self.assertEqual(set(MAIN_V1_REPORT_SHA256), set(v1_cases()))
        self.assertEqual(v1_report_digests(HARMONIZE), MAIN_V1_REPORT_SHA256)

    def test_v1_example_cli_still_harmonized_with_identical_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            result, report = run_cli(ASSETS / "example.json", pathlib.Path(temp) / "report.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((report["schema"], report["status"], len(report["rows"])),
                         ("szl.measurement-harmonizer.v1.report", "HARMONIZED", 4))
        self.assertNotIn("data_contract", report)
        self.assertEqual(canonical_sha256(report), MAIN_V1_REPORT_SHA256["example"])

    def test_v2_example_retains_missing_cell_as_null_with_counts(self):
        report = run_case(HARMONIZE, self.example, {})
        self.assertEqual((report["schema"], report["status"], report["findings"]),
                         ("szl.measurement-harmonizer.v2.report", "HARMONIZED", []))
        values = {(row["source"], row["canonical_id"]): (row["raw_value"], row["normalized_value"])
                  for row in report["rows"]}
        self.assertEqual(values, {("lab-a", "specimen-1"): ("25", "298.15"),
                                  ("lab-a", "specimen-2"): ("20", "293.15"),
                                  ("lab-c", "specimen-1"): ("298.15", "298.15"),
                                  ("lab-c", "specimen-2"): ("NA", None)})
        contract = report["data_contract"]
        self.assertIs(contract["schema_checked"], True)
        self.assertEqual(contract["provenance_declared"],
                         {source["id"]: source["provenance"] for source in self.example["sources"]})
        self.assertEqual(contract["staleness"], {"as_of": "2026-09-30T00:00:00Z", "max_age_days": 30,
                                                 "age_seconds": {"lab-a": 831600, "lab-c": 379800},
                                                 "stale": []})
        self.assertEqual(contract["missingness"], {
            "missing_tokens": ["", "NA"], "max_missing_fraction": "0.5",
            "per_source": {"lab-a": {"missing": 0, "rows": 2}, "lab-c": {"missing": 1, "rows": 2}},
            "per_specimen": {"specimen-1": 0, "specimen-2": 1}, "exceeded": []})

    def test_cli_v2_example_and_blocked_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            ok, good = run_cli(ASSETS / "v2-example.json", pathlib.Path(temp) / "good.json")
            bad, blocked = run_cli(ASSETS / "v2-blocked.json", pathlib.Path(temp) / "blocked.json")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual((good["status"], len(good["rows"])), ("HARMONIZED", 4))
        self.assertEqual(bad.returncode, 2, bad.stderr)
        self.assertEqual(blocked["rows"], [])
        self.assertEqual([(item["source"], item["code"]) for item in blocked["findings"]],
                         [("lab-a", "STALE"), ("lab-d", "MISSINGNESS_EXCEEDED")])
        contract = blocked["data_contract"]
        self.assertEqual(contract["staleness"]["stale"], ["lab-a"])
        self.assertEqual(contract["missingness"]["exceeded"], ["lab-d"])
        self.assertEqual(contract["missingness"]["per_source"]["lab-d"], {"missing": 2, "rows": 2})
        self.assertEqual(contract["missingness"]["per_specimen"], {"specimen-1": 1, "specimen-2": 1})
        self.assertEqual(blocked["input_sha256"]["lab-d"], hashlib.sha256(
            (ASSETS / "v2-over-missing.csv").read_bytes()).hexdigest())

    def test_staleness_boundary_uses_declared_instants(self):
        exact = self.harmonize_v2(lambda m: m["sources"][0]["provenance"].update(
            collected_at="2026-08-31T00:00:00Z"))
        self.assertEqual(exact["status"], "HARMONIZED")
        self.assertEqual(exact["data_contract"]["staleness"]["age_seconds"]["lab-a"], 30 * 86400)
        late = self.harmonize_v2(lambda m: m["sources"][0]["provenance"].update(
            collected_at="2026-08-30T23:59:59Z"))
        self.assert_blocked(late, "STALE")
        self.assertEqual(late["data_contract"]["staleness"]["stale"], ["lab-a"])
        self.assertEqual(late["data_contract"]["missingness"]["per_source"]["lab-c"], {"missing": 1, "rows": 2})
        zero = self.harmonize_v2(lambda m: (m.update(max_age_days=0), [
            source["provenance"].update(collected_at=m["as_of"]) for source in m["sources"]]))
        self.assertEqual(zero["status"], "HARMONIZED")

    def test_kernel_reads_no_wall_clock(self):
        tree = ast.parse((SKILL / "scripts" / "harmonizer.py").read_text(encoding="utf-8"))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names}
        self.assertFalse(attributes & {"now", "utcnow", "today", "time", "time_ns", "monotonic"})
        self.assertNotIn("time", imported)

    def test_missingness_boundary_blocks_without_imputation(self):
        under = self.harmonize_v2(lambda m: m.update(max_missing_fraction="0.49"))
        self.assert_blocked(under, "MISSINGNESS_EXCEEDED")
        self.assertEqual(under["findings"][0]["detail"], "lab-c:1/2")
        self.assertEqual(under["data_contract"]["missingness"]["exceeded"], ["lab-c"])
        zero = self.harmonize_v2(lambda m: (m.update(max_missing_fraction="0"),
                                            m["sources"][1].update(path="lab-b.csv",
                                                                   sha256=load("example.json")["sources"][1]["sha256"],
                                                                   id_map={"B1": "specimen-1", "B2": "specimen-2"})))
        self.assertEqual(zero["status"], "HARMONIZED")
        self.assertTrue(all(row["normalized_value"] is not None for row in zero["rows"]))
        undeclared = self.harmonize_v2(lambda m: m.update(missing_tokens=[]))
        self.assert_blocked(undeclared, "INVALID_DECIMAL")

    def test_digest_mismatch_leaves_source_missingness_not_evaluated(self):
        report = self.harmonize_v2(lambda m: m["sources"][1].update(sha256="0" * 64))
        self.assert_blocked(report, "DIGEST_MISMATCH")
        missingness = report["data_contract"]["missingness"]
        self.assertEqual(missingness["per_source"]["lab-c"], "NOT_EVALUATED")
        self.assertEqual(missingness["per_specimen"], {"specimen-1": 0, "specimen-2": 0})

    def test_missing_file_is_unavailable_only_without_staleness(self):
        missing = self.harmonize_v2(lambda m: m["sources"][1].update(path="absent.csv"))
        self.assertEqual((missing["status"], codes(missing)), ("UNAVAILABLE", ["MISSING_FILE"]))
        stale = self.harmonize_v2(lambda m: (m["sources"][1].update(path="absent.csv"),
                                             m.update(as_of="2027-01-01T00:00:00Z")))
        self.assertEqual(stale["status"], "BLOCKED")
        self.assertEqual(codes(stale), ["STALE", "STALE", "MISSING_FILE"])

    def test_invalid_v2_declarations_block_with_contract_not_evaluated(self):
        def provenance(**fields):
            return lambda m: m["sources"][1]["provenance"].update(fields)

        def drop(key):
            return lambda m: m["sources"][1]["provenance"].pop(key)

        cases = [
            (lambda m: m.update(as_of="2026-09-30"), "INVALID_DATA_CONTRACT", "as_of"),
            (lambda m: m.update(as_of="2026-09-30T00:00:00+00:00"), "INVALID_DATA_CONTRACT", "as_of"),
            (lambda m: m.update(as_of="2026-02-30T00:00:00Z"), "INVALID_DATA_CONTRACT", "as_of"),
            (lambda m: m.update(as_of="2016-12-31T23:59:60Z"), "INVALID_DATA_CONTRACT", "as_of"),
            (lambda m: m.update(as_of=20260930), "INVALID_DATA_CONTRACT", "as_of"),
            (provenance(collected_at="2026-09-30T00:00:01Z"), "INVALID_DATA_CONTRACT", "lab-c.collected_at"),
            (provenance(collected_at="2026-9-25T14:30:00Z"), "INVALID_DATA_CONTRACT", "lab-c.collected_at"),
            (lambda m: m.update(max_age_days=True), "INVALID_DATA_CONTRACT", "max_age_days"),
            (lambda m: m.update(max_age_days=-1), "INVALID_DATA_CONTRACT", "max_age_days"),
            (lambda m: m.update(max_age_days=1.5), "INVALID_DATA_CONTRACT", "max_age_days"),
            (lambda m: m.update(max_age_days="30"), "INVALID_DATA_CONTRACT", "max_age_days"),
            (lambda m: m.update(missing_tokens="NA"), "INVALID_DATA_CONTRACT", "missing_tokens"),
            (lambda m: m.update(missing_tokens=["0"]), "INVALID_DATA_CONTRACT", "missing_tokens"),
            (lambda m: m.update(missing_tokens=["NA", "NA"]), "INVALID_DATA_CONTRACT", "missing_tokens"),
            (lambda m: m.update(missing_tokens=[" NA"]), "INVALID_DATA_CONTRACT", "missing_tokens"),
            (lambda m: m.update(missing_tokens=[1]), "INVALID_DATA_CONTRACT", "missing_tokens"),
            (lambda m: m.update(missing_tokens=[{"NA": 1}]), "INVALID_DATA_CONTRACT", "missing_tokens"),
            (lambda m: m.update(missing_tokens=["t%d" % i for i in range(9)]), "INVALID_DATA_CONTRACT",
             "missing_tokens"),
            (lambda m: m.update(max_missing_fraction="1.5"), "INVALID_DATA_CONTRACT", "max_missing_fraction"),
            (lambda m: m.update(max_missing_fraction="-0.1"), "INVALID_DATA_CONTRACT", "max_missing_fraction"),
            (lambda m: m.update(max_missing_fraction=0.5), "INVALID_DATA_CONTRACT", "max_missing_fraction"),
            (lambda m: m.update(max_missing_fraction="NaN"), "INVALID_DATA_CONTRACT", "max_missing_fraction"),
            (drop("instrument"), "INVALID_SOURCE_FIELDS", "lab-c"),
            (provenance(operator="x"), "INVALID_SOURCE_FIELDS", "lab-c"),
            (lambda m: m["sources"][1].update(provenance="lab C"), "INVALID_SOURCE_FIELDS", "lab-c"),
            (provenance(origin=""), "INVALID_TEXT", ""),
            (lambda m: m["sources"][1].pop("provenance"), "INVALID_SOURCE_FIELDS", ""),
            (lambda m: m.pop("as_of"), "INVALID_MANIFEST_FIELDS", ""),
            (lambda m: m.update(note="extra"), "INVALID_MANIFEST_FIELDS", ""),
        ]
        for index, (change, code, detail) in enumerate(cases):
            with self.subTest(index=index, code=code, detail=detail):
                report = self.harmonize_v2(change)
                self.assertEqual(report["schema"], "szl.measurement-harmonizer.v2.report")
                self.assert_blocked(report, code)
                self.assertEqual([(item["code"], item["detail"]) for item in report["findings"]], [(code, detail)])
                self.assertEqual(report["data_contract"], NOT_EVALUATED_CONTRACT)
                self.assertEqual(report["input_sha256"], {})

    def test_cli_rejects_duplicate_keys_and_nonfinite_constants_in_v2_manifest(self):
        text = (ASSETS / "v2-example.json").read_text(encoding="utf-8")
        bodies = {
            "DUPLICATE_JSON_KEY": text.replace('"max_age_days": 30,', '"max_age_days": 30, "max_age_days": 3650,'),
            "NONFINITE_JSON": text.replace('"max_age_days": 30,', '"max_age_days": NaN,'),
        }
        bodies["NONFINITE_JSON_INFINITY"] = text.replace('"max_age_days": 30,', '"max_age_days": Infinity,')
        with tempfile.TemporaryDirectory() as temp:
            for name, body in bodies.items():
                with self.subTest(name=name):
                    self.assertNotEqual(body, text)
                    manifest = pathlib.Path(temp) / (name + ".json")
                    manifest.write_text(body, encoding="utf-8")
                    result, report = run_cli(manifest, pathlib.Path(temp) / (name + "-report.json"))
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual((report["status"], report["rows"]), ("BLOCKED", []))
                    self.assertEqual(codes(report), [name.replace("_INFINITY", "")])

    def test_fixtures_and_docs_are_bound(self):
        for name in ("v2-example.json", "v2-blocked.json"):
            for source in load(name)["sources"]:
                with self.subTest(fixture=name, source=source["id"]):
                    self.assertEqual(source["sha256"], hashlib.sha256((ASSETS / source["path"]).read_bytes()).hexdigest())
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(skill.splitlines()), 160)
        self.assertIn("## What it does not do", skill)
        for text in (skill, (SKILL / "references" / "contract.md").read_text(encoding="utf-8")):
            self.assertIn("szl-unit-invariants", text)
        provenance = (SKILL / "references" / "provenance.md").read_text(encoding="utf-8")
        self.assertIn("Apache-2.0", provenance)
        self.assertIn("b8e259c", provenance)


if __name__ == "__main__":
    unittest.main()
