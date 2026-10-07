"""Upgrade tests for szl-evidence-gate: fail-closed input checks and opt-in numeric binding.

Stdlib only, offline. These are software checks of the gate's behaviour; they do not
establish that any claim is true.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-evidence-gate"
ASSETS = SKILL / "assets"
CSV_SHA256 = "4853f43c459a2ee7ab8ba3ec1769dce97534cf8068a4d9b2f240de88eb74f180"
# Report for assets/example.json produced by the base commit b8e259c kernel and CLI.
# Canonical = sorted-key compact JSON; pretty = the CLI's json.dumps(report, indent=2) text.
MAIN_EXAMPLE_CANONICAL_SHA256 = "8d0561bfa0967a4c9793a955fd03964a0536b23cae1a294a5e17e20781f2756a"
MAIN_EXAMPLE_PRETTY_SHA256 = "c8d6243a25323a02ded642619a3b2e3ffe72e8cbe37dbb741a0bf9ad71908ecb"
MAIN_EXAMPLE_CLAIMS_SHA256 = "f746e3df5a0a7d587ce6c9941963d5270896da8f5fef5eba86463acc6ee4376d"
ABSTAIN_CLAIM = {"id": "c", "text": "The model is calibrated", "evidence": []}


def load():
    spec = importlib.util.spec_from_file_location("szl_evidence_gate_upgrade", SKILL / "kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_sha256(report):
    return sha256_text(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def asset(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


def run_cli(*args, cwd=SKILL):
    return subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / "run.py"), *args], cwd=str(cwd),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def make_dir_link(link, target):
    """Directory symlink, or a Windows junction when symlinks need privilege. None if neither works."""
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return "junction"
        except (ImportError, AttributeError, OSError):
            pass
    return None


class ExistingExampleRegressionTests(unittest.TestCase):
    """The pre-upgrade example must give the same report, byte for byte, as on main."""

    def setUp(self):
        self.k = load()

    def test_kernel_report_matches_main(self):
        report = self.k.szl_evidence_gate(ASSETS, asset("example.json"))
        self.assertEqual(canonical_sha256(report), MAIN_EXAMPLE_CANONICAL_SHA256)
        self.assertEqual(sha256_text(json.dumps(report, indent=2, sort_keys=False)), MAIN_EXAMPLE_PRETTY_SHA256)
        self.assertEqual(report["schema"], "szl.evidence-gate-report.v1")
        self.assertEqual(report["status"], "ABSTAIN")
        self.assertEqual(report["counts"], {"PASS": 2, "FAIL": 1, "ABSTAIN": 1, "ERROR": 0})
        self.assertEqual(report["claims_sha256"], MAIN_EXAMPLE_CLAIMS_SHA256)
        self.assertNotIn("reason", report)
        for claim in report["claims"]:
            self.assertNotIn("numeric_binding", claim)

    def test_cli_report_matches_main(self):
        p = run_cli("assets/example.json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(sha256_text(p.stdout.rstrip("\n")), MAIN_EXAMPLE_PRETTY_SHA256)

    def test_skill_md_stays_within_160_lines(self):
        lines = (SKILL / "SKILL.md").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 160)

    def test_values_field_is_ignored_without_opt_in(self):
        doc = asset("example.json")
        doc["claims"][0]["evidence"][0]["values"] = ["999"]
        report = self.k.szl_evidence_gate(ASSETS, doc)
        self.assertEqual(report["schema"], "szl.evidence-gate-report.v1")
        self.assertEqual(report["claims"][0]["status"], "PASS")
        self.assertNotIn("numeric_binding", report["claims"][0])


class FailClosedInputTests(unittest.TestCase):
    def setUp(self):
        self.k = load()

    def test_empty_claims_abstain_instead_of_vacuous_pass(self):
        report = self.k.szl_evidence_gate(ASSETS, {"subject": "s", "claims": []})
        self.assertEqual(report["status"], "ABSTAIN")
        self.assertEqual(report["reason"], "NO_CLAIMS_DECLARED")
        self.assertEqual(report["counts"], {"PASS": 0, "FAIL": 0, "ABSTAIN": 0, "ERROR": 0})
        self.assertEqual(report["claims"], [])
        self.assertEqual(report["required_failures"], [])

    def test_empty_claims_cli_reports_abstain(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "claims.json"
            path.write_text('{"claims": []}', encoding="utf-8")
            p = run_cli(str(path))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "ABSTAIN")

    def test_cli_rejects_duplicate_json_keys(self):
        top = '{"claims": [%s], "claims": []}' % json.dumps(ABSTAIN_CLAIM)
        nested = '{"claims": [{"id": "a", "id": "b", "text": "t", "evidence": []}]}'
        for raw in (top, nested):
            with tempfile.TemporaryDirectory() as d:
                path = pathlib.Path(d) / "claims.json"
                path.write_text(raw, encoding="utf-8")
                p = run_cli(str(path))
            self.assertEqual(p.returncode, 2, raw)
            out = json.loads(p.stdout)
            self.assertEqual(out["status"], "ERROR")
            self.assertIn("duplicate JSON key", out["reason"])

    def test_cli_rejects_non_finite_numbers(self):
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            raw = '{"claims": [], "weight": %s}' % token
            with tempfile.TemporaryDirectory() as d:
                path = pathlib.Path(d) / "claims.json"
                path.write_text(raw, encoding="utf-8")
                p = run_cli(str(path))
            self.assertEqual(p.returncode, 2, token)
            self.assertIn("non-finite JSON number", json.loads(p.stdout)["reason"])

    def test_duplicate_claim_ids_error(self):
        doc = {"claims": [dict(ABSTAIN_CLAIM, id="a"), dict(ABSTAIN_CLAIM, id="b"), dict(ABSTAIN_CLAIM, id="a")]}
        report = self.k.szl_evidence_gate(ASSETS, doc)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["reason"], "DUPLICATE_CLAIM_ID")
        self.assertEqual(report["duplicate_claim_ids"], ["a"])
        self.assertEqual(report["claims"], [])
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "claims.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(run_cli(str(path)).returncode, 2)

    def test_non_boolean_required_is_error_and_counts_as_required(self):
        evidence = [{"path": "artifacts/held_out_metrics.csv", "sha256": CSV_SHA256}]
        for value in (0, 1, "false", None, [], 0.0):
            doc = {"claims": [{"id": "a", "text": "t", "required": value, "evidence": evidence}]}
            report = self.k.szl_evidence_gate(ASSETS, doc)
            claim = report["claims"][0]
            self.assertEqual(claim["status"], "ERROR", value)
            self.assertEqual(claim["reason"], "REQUIRED_NOT_BOOLEAN")
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["required_failures"], ["a"])

    def test_boolean_required_unchanged(self):
        missing = [{"path": "artifacts/not-there.txt"}]
        doc = {"claims": [{"id": "a", "text": "t", "required": False, "evidence": missing}]}
        self.assertEqual(self.k.szl_evidence_gate(ASSETS, doc)["status"], "FAIL_OPTIONAL_ONLY")
        doc["claims"][0]["required"] = True
        self.assertEqual(self.k.szl_evidence_gate(ASSETS, doc)["status"], "FAIL")

    def test_lexical_escape_still_rejected(self):
        doc = {"claims": [{"id": "a", "text": "t", "evidence": [{"path": "../kernel.py"}]}]}
        claim = self.k.szl_evidence_gate(ASSETS, doc)["claims"][0]
        self.assertEqual(claim["status"], "ERROR")
        self.assertEqual(claim["reason"], "MALFORMED_EVIDENCE")
        self.assertIn("may not escape the root", claim["evidence"][0]["reason"])

    def test_link_to_sibling_prefix_directory_is_outside_root(self):
        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            root, sibling = base / "root", base / "root-x"
            root.mkdir()
            sibling.mkdir()
            (sibling / "secret.txt").write_text("held-out rows: 113\n", encoding="utf-8")
            (root / "inside").mkdir()
            (root / "inside" / "note.txt").write_text("ok\n", encoding="utf-8")
            if make_dir_link(root / "link", sibling) is None:
                self.skipTest("host cannot create a directory symlink or junction")
            make_dir_link(root / "inner", root / "inside")
            doc = {"claims": [{"id": "out", "text": "t", "evidence": [{"path": "link/secret.txt"}]},
                              {"id": "in", "text": "t", "evidence": [{"path": "inner/note.txt"}]}]}
            report = self.k.szl_evidence_gate(root, doc)
        out, inside = report["claims"]
        self.assertEqual(out["status"], "ERROR")
        self.assertEqual(out["reason"], "PATH_OUTSIDE_ROOT")
        self.assertEqual(out["evidence"][0]["reason"], "PATH_OUTSIDE_ROOT")
        self.assertIsNone(out["evidence"][0]["observed_sha256"])
        self.assertEqual(inside["status"], "PASS")
        self.assertEqual(report["status"], "FAIL")

    def test_artifact_size_cap_boundary(self):
        self.assertEqual(self.k.MAX_ARTIFACT_BYTES, 32 * 1024 * 1024)
        self.k.MAX_ARTIFACT_BYTES = 8
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "at.txt").write_bytes(b"12345678")
            (root / "over.txt").write_bytes(b"123456789")
            doc = {"claims": [{"id": "at", "text": "t", "evidence": [{"path": "at.txt"}]},
                              {"id": "over", "text": "t", "evidence": [{"path": "over.txt"}]}]}
            report = self.k.szl_evidence_gate(root, doc)
        at, over = report["claims"]
        self.assertEqual(at["status"], "PASS")
        self.assertEqual(at["evidence"][0]["size_bytes"], 8)
        self.assertEqual(over["status"], "ERROR")
        self.assertEqual(over["reason"], "ARTIFACT_TOO_LARGE")
        self.assertEqual(over["evidence"][0]["reason"], "ARTIFACT_TOO_LARGE")
        self.assertEqual(over["evidence"][0]["size_bytes"], 9)
        self.assertIsNone(over["evidence"][0]["observed_sha256"])
        self.assertEqual(report["required_failures"], ["over"])

    def test_default_cap_rejects_file_one_byte_over_32_mib(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            with (root / "big.bin").open("wb") as handle:
                handle.truncate(self.k.MAX_ARTIFACT_BYTES + 1)
            doc = {"claims": [{"id": "big", "text": "t", "evidence": [{"path": "big.bin"}]}]}
            report = self.k.szl_evidence_gate(root, doc)
        self.assertEqual(report["claims"][0]["reason"], "ARTIFACT_TOO_LARGE")


class NumericBindingTests(unittest.TestCase):
    def setUp(self):
        self.k = load()

    def gate(self, claims, strict=True, root=ASSETS):
        return self.k.szl_evidence_gate(root, {"strict_numeric_binding": strict, "claims": claims})

    def test_number_tokens(self):
        cases = {
            "Mean accuracy is 0.912.": ["0.912"],
            "1.8kWh and 95% and 3 runs, 3 seeds": ["1.8", "95", "3"],
            "delta -0.3 versus +2 at 1e-5": ["-0.3", "+2", "1e-5"],
            "version 1.2.3, R07, v2, SHA-256, run_4": [],
            "CI (0.91,0.93)": ["0.91", "0.93"],
            "range 1-5 on 2026-10-06": ["1", "2026"],
            "no digits here": [],
        }
        for text, expected in cases.items():
            self.assertEqual(self.k.szl_number_tokens(text), expected, text)

    def test_strict_example_one_bound_one_unbound(self):
        report = self.k.szl_evidence_gate(ASSETS, asset("strict-example.json"))
        self.assertEqual(report["schema"], "szl.evidence-gate-report.v2")
        self.assertEqual(report["status"], "ABSTAIN")
        self.assertEqual(report["counts"], {"PASS": 1, "FAIL": 0, "ABSTAIN": 1, "ERROR": 0})
        bound, unbound = report["claims"]
        self.assertEqual(bound["status"], "PASS")
        self.assertEqual(bound["numeric_binding"], {"numbers_in_text": ["11", "0.912"], "bound": ["11", "0.912"], "unbound": []})
        self.assertEqual(unbound["status"], "ABSTAIN")
        self.assertEqual(unbound["reason"], "NUMBERS_NOT_BOUND")
        self.assertEqual(unbound["numeric_binding"], {"numbers_in_text": ["0.912"], "bound": [], "unbound": ["0.912"]})
        self.assertEqual(unbound["evidence"][0]["status"], "PASS")

    def test_strict_example_cli(self):
        p = run_cli("assets/strict-example.json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "ABSTAIN")

    def test_report_only_mode_keeps_status(self):
        doc = asset("strict-example.json")
        doc["strict_numeric_binding"] = False
        report = self.k.szl_evidence_gate(ASSETS, doc)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["claims"][1]["status"], "PASS")
        self.assertEqual(report["claims"][1]["numeric_binding"]["unbound"], ["0.912"])

    def test_all_bound_strict_document_passes(self):
        doc = asset("strict-example.json")
        doc["claims"] = doc["claims"][:1]
        self.assertEqual(self.k.szl_evidence_gate(ASSETS, doc)["status"], "PASS")

    def test_must_contain_snippet_binds_numbers(self):
        claim = {"id": "rows", "text": "The held-out split has 113 rows",
                 "evidence": [{"path": "artifacts/eval_protocol.txt", "must_contain": ["held-out rows: 113"]}]}
        report = self.gate([claim])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["claims"][0]["numeric_binding"]["bound"], ["113"])

    def test_snippet_substring_inside_longer_number_does_not_bind(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "m.txt").write_text("accuracy 0.9123\n", encoding="utf-8")
            claim = {"id": "a", "text": "Accuracy is 0.912", "evidence": [{"path": "m.txt", "must_contain": ["0.912"]}]}
            report = self.gate([claim], root=root)
        self.assertEqual(report["claims"][0]["evidence"][0]["status"], "PASS")
        self.assertEqual(report["claims"][0]["status"], "ABSTAIN")
        self.assertEqual(report["claims"][0]["numeric_binding"]["unbound"], ["0.912"])

    def test_values_must_appear_as_tokens_in_artifact(self):
        claim = {"id": "a", "text": "Accuracy is 0.91",
                 "evidence": [{"path": "artifacts/held_out_metrics.csv", "values": ["0.91"]}]}
        report = self.gate([claim])
        item = report["claims"][0]["evidence"][0]
        self.assertEqual(item["status"], "FAIL")
        self.assertEqual(item["reason"], "TEXT_NOT_FOUND")
        self.assertEqual(item["missing_values"], ["0.91"])
        self.assertEqual(report["claims"][0]["status"], "FAIL")
        self.assertEqual(report["claims"][0]["numeric_binding"]["unbound"], ["0.91"])
        self.assertEqual(report["status"], "FAIL")

    def test_malformed_values_are_errors(self):
        for values in ([0.912], ["abc"], ["1.2.3"], "0.912", [""], [True]):
            claim = {"id": "a", "text": "t", "evidence": [{"path": "artifacts/held_out_metrics.csv", "values": values}]}
            report = self.gate([claim])
            self.assertEqual(report["claims"][0]["evidence"][0]["status"], "ERROR", values)
            self.assertEqual(report["status"], "FAIL")

    def test_digest_only_number_is_unbound_and_failures_stay_failures(self):
        claims = [{"id": "digest", "text": "Accuracy 0.912", "evidence": [{"path": "artifacts/held_out_metrics.csv", "sha256": CSV_SHA256}]},
                  {"id": "missing", "text": "Energy 1.8 kWh", "evidence": [{"path": "artifacts/energy.json"}]},
                  {"id": "none", "text": "ECE < 0.05", "evidence": []},
                  {"id": "words", "text": "No leakage", "evidence": [{"path": "artifacts/eval_protocol.txt"}]}]
        report = self.gate(claims)
        by = {c["id"]: c for c in report["claims"]}
        self.assertEqual((by["digest"]["status"], by["digest"]["reason"]), ("ABSTAIN", "NUMBERS_NOT_BOUND"))
        self.assertEqual((by["missing"]["status"], by["missing"]["reason"]), ("FAIL", "MISSING_ARTIFACT"))
        self.assertEqual((by["none"]["status"], by["none"]["reason"]), ("ABSTAIN", "NO_CHECKABLE_EVIDENCE_DECLARED"))
        self.assertEqual(by["none"]["numeric_binding"]["unbound"], ["0.05"])
        self.assertEqual(by["words"]["status"], "PASS")
        self.assertEqual(by["words"]["numeric_binding"], {"numbers_in_text": [], "bound": [], "unbound": []})
        self.assertEqual(report["status"], "FAIL")

    def test_optional_unbound_claim_makes_aggregate_abstain(self):
        claim = {"id": "a", "text": "Accuracy 0.912", "required": False,
                 "evidence": [{"path": "artifacts/held_out_metrics.csv", "sha256": CSV_SHA256}]}
        self.assertEqual(self.gate([claim])["status"], "ABSTAIN")

    def test_non_boolean_strict_flag_is_error(self):
        for value in ("yes", 1, None):
            report = self.k.szl_evidence_gate(ASSETS, {"strict_numeric_binding": value, "claims": [ABSTAIN_CLAIM]})
            self.assertEqual(report["status"], "ERROR", value)
            self.assertEqual(report["claims"], [])


if __name__ == "__main__":
    unittest.main()
