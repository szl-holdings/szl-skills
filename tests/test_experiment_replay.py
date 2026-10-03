"""Synthetic, offline acceptance tests for the fixed experiment replay engine."""
# SPDX-License-Identifier: Apache-2.0

import ast
import contextlib
import hashlib
import io
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-experiment-replay"
ENGINE = runpy.run_path(str(SKILL / "scripts" / "engine.py"))
CLI = runpy.run_path(str(SKILL / "scripts" / "run.py"))


class ExperimentReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="szl-experiment-replay-")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        (self.root / "assets").mkdir()
        for name in ("synthetic.csv", "expected.json", "declaration.json"):
            (self.root / "assets" / name).write_bytes((SKILL / "assets" / name).read_bytes())
        self.declaration = json.loads((self.root / "assets" / "declaration.json").read_text(encoding="utf-8"))

    def prepare(self):
        return ENGINE["prepare"](self.root, self.declaration)

    def test_match_has_frozen_hashes_and_honest_receipt(self):
        pin = self.prepare()
        result = ENGINE["replay"](self.root, pin)
        self.assertEqual((result["status"], result["reason"], result["execution"]),
                         ("MATCH", "WITHIN_TOLERANCE", "COMPLETED"))
        self.assertEqual((result["row_count"], result["observed_mean"], result["expected_mean"]),
                         (2, "2.5", "2.5"))
        self.assertEqual(result["absolute_error"], "0.0")
        self.assertEqual(pin["input"]["sha256"], hashlib.sha256((self.root / "assets" / "synthetic.csv").read_bytes()).hexdigest())
        self.assertEqual(pin["reference"]["sha256"], hashlib.sha256((self.root / "assets" / "expected.json").read_bytes()).hexdigest())
        self.assertEqual(pin["engine_sha256"], hashlib.sha256((SKILL / "scripts" / "engine.py").read_bytes()).hexdigest())
        self.assertEqual(result, ENGINE["replay"](self.root, pin))
        self.assertEqual((result["evidence_scope"], result["signed"], result["independent_witness"], result["scientific_claims_verified"]),
                         ("SAME_HOST_LOCAL", False, False, False))

    def test_changed_input_is_incomplete_and_not_executed(self):
        pin = self.prepare()
        (self.root / "assets" / "synthetic.csv").write_text("sample,measurement\nsynthetic-a,2.0\nsynthetic-b,4.0\n", encoding="utf-8")
        result = ENGINE["replay"](self.root, pin)
        self.assertEqual((result["status"], result["reason"], result["execution"]),
                         ("INCOMPLETE", "INPUT_CHANGED", "NOT_RUN"))
        self.assertNotIn("observed_mean", result)

    def test_different_frozen_expected_result_diverges_but_tampering_does_not_run(self):
        reference = self.root / "assets" / "expected.json"
        reference.write_text(json.dumps({"schema": "szl.experiment-replay.reference.v1", "value": "2.6"}), encoding="utf-8")
        pin = self.prepare()
        result = ENGINE["replay"](self.root, pin)
        self.assertEqual((result["status"], result["reason"], result["execution"]),
                         ("DIVERGED", "OUTSIDE_TOLERANCE", "COMPLETED"))
        self.assertEqual(result["absolute_error"], "0.1")
        reference.write_text(json.dumps({"schema": "szl.experiment-replay.reference.v1", "value": "2.5"}), encoding="utf-8")
        result = ENGINE["replay"](self.root, pin)
        self.assertEqual((result["status"], result["reason"], result["execution"]),
                         ("INCOMPLETE", "REFERENCE_CHANGED", "NOT_RUN"))

    def test_missing_reference_yields_incomplete_retained_receipt(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(CLI["main"](["prepare", "assets/declaration.json", "--root", str(self.root), "--output", "pin.json"]), 0)
        (self.root / "assets" / "expected.json").unlink()
        with contextlib.redirect_stdout(stdout):
            code = CLI["main"](["replay", "pin.json", "--root", str(self.root), "--receipt", "receipt.json"])
        self.assertEqual(code, 2)
        receipt = json.loads((self.root / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual((receipt["status"], receipt["reason"], receipt["execution"]),
                         ("INCOMPLETE", "REFERENCE_MISSING", "NOT_RUN"))
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_documented_isolated_cli_writes_match_receipt(self):
        script = SKILL / "scripts" / "run.py"
        prepare = subprocess.run([sys.executable, "-I", "-B", str(script), "prepare", "assets/declaration.json",
                                  "--root", str(self.root), "--output", "pin.json"], capture_output=True, text=True, timeout=15)
        self.assertEqual(prepare.returncode, 0, prepare.stderr)
        replay = subprocess.run([sys.executable, "-I", "-B", str(script), "replay", "pin.json",
                                 "--root", str(self.root), "--receipt", "receipt.json"], capture_output=True, text=True, timeout=15)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        receipt = json.loads((self.root / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "MATCH")
        self.assertEqual(json.loads(replay.stdout)["receipt_sha256"], receipt["receipt_sha256"])

    def test_declared_tolerance_changes_comparison_not_pin_evidence(self):
        reference = self.root / "assets" / "expected.json"
        reference.write_text(json.dumps({"schema": "szl.experiment-replay.reference.v1", "value": "2.6"}), encoding="utf-8")
        self.declaration["absolute_tolerance"] = "0.1"
        pin = self.prepare()
        result = ENGINE["replay"](self.root, pin)
        self.assertEqual((result["status"], result["absolute_error"], result["absolute_tolerance"]),
                         ("MATCH", "0.1", "0.1"))

    def test_paths_and_tampered_pin_are_refused(self):
        self.declaration["input_path"] = "../other.csv"
        with self.assertRaises(ValueError):
            self.prepare()
        self.declaration["input_path"] = "assets/synthetic.csv"
        pin = self.prepare()
        pin["column"] = "sample"
        result = ENGINE["replay"](self.root, pin)
        self.assertEqual((result["status"], result["reason"]), ("REFUSED", "INVALID_PIN"))
        self.assertEqual(result["execution"], "NOT_RUN")
        self.assertIsNone(result["pin_sha256"])

    def test_missing_and_malformed_on_disk_pins_retain_refusal_receipts(self):
        (self.root / "malformed.json").write_text('{"pin_sha256":"' + "a" * 64 + '",', encoding="utf-8")
        for pin_name, receipt_name, reason in (("missing.json", "missing-receipt.json", "PIN_MISSING"),
                                               ("malformed.json", "malformed-receipt.json", "INVALID_PIN")):
            with self.subTest(pin=pin_name), contextlib.redirect_stdout(io.StringIO()):
                code = CLI["main"](["replay", pin_name, "--root", str(self.root), "--receipt", receipt_name])
                self.assertEqual(code, 2)
                receipt = json.loads((self.root / receipt_name).read_text(encoding="utf-8"))
                self.assertEqual((receipt["status"], receipt["reason"], receipt["execution"]),
                                 ("REFUSED", reason, "NOT_RUN"))
                self.assertIsNone(receipt["pin_sha256"])
                self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_oversized_engine_is_refused_before_opening(self):
        source = self.root / "oversized_engine.py"
        source.write_bytes(b"x" * (ENGINE["MAX_ENGINE"] + 1))
        engine_hash = ENGINE["_engine_hash"]
        with mock.patch.dict(engine_hash.__globals__, {"__file__": str(source)}), \
             mock.patch.object(pathlib.Path, "open", side_effect=AssertionError("oversized engine opened")):
            with self.assertRaisesRegex(ValueError, "engine exceeds byte limit"):
                engine_hash()

    def test_installer_selected_replay_resources_are_cli_only(self):
        installer = runpy.run_path(str(ROOT / "tools" / "install_claude_science.py"))
        resources = installer["bundle"](ROOT, family="replay")
        self.assertEqual(set(resources), {"szl-experiment-replay", "szl-figure-data-contract"})
        files = resources["szl-experiment-replay"]
        self.assertEqual(set(files), {"SKILL.md", "LICENSE", "NOTICE", "scripts/run.py",
                                      "scripts/engine.py", "assets/declaration.json",
                                      "assets/synthetic.csv", "assets/expected.json"})
        self.assertNotIn("kernel.py", files)
        for relative in ("scripts/run.py", "scripts/engine.py"):
            with self.subTest(resource=relative):
                ast.parse(files[relative], filename=relative)
        figure_files = resources["szl-figure-data-contract"]
        self.assertEqual(set(figure_files), {"SKILL.md", "LICENSE", "NOTICE", "scripts/run.py",
                                             "assets/spec.json", "assets/data.csv"})
        self.assertNotIn("kernel.py", figure_files)
        ast.parse(figure_files["scripts/run.py"], filename="szl-figure-data-contract/scripts/run.py")
        self.assertLessEqual(sum(len(content.encode()) for content in files.values()), 1000000)
        self.assertLessEqual(sum(len(content.encode()) for group in resources.values() for content in group.values()), 1000000)

    def test_existing_receipt_is_not_overwritten(self):
        original = (self.root / "assets" / "expected.json").read_bytes()
        with self.assertRaises(FileExistsError):
            ENGINE["write_new_json"](self.root, "assets/expected.json", {"status": "overwrite"})
        self.assertEqual((self.root / "assets" / "expected.json").read_bytes(), original)

    def test_symlink_is_refused_where_host_supports_it(self):
        link = self.root / "assets" / "linked.csv"
        try:
            link.symlink_to(self.root / "assets" / "synthetic.csv")
        except (OSError, NotImplementedError):
            self.skipTest("local host cannot create symlinks")
        self.declaration["input_path"] = "assets/linked.csv"
        with self.assertRaises(ValueError):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
