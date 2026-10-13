"""Restore-verifier upgrade tests for szl-session-receipt. Stdlib only, offline.

Software checks of path containment, per-file size cap, empty-receipt refusal,
verify --scan extra-file detection and the path-bound root_sha256_v2. The pinned
v1 digests were computed from the kernel at base commit b8e259c so the existing
example report stays byte-identical.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-session-receipt"
RUN = SKILL / "scripts" / "run.py"

# Computed from skills/szl-session-receipt at b8e259c (before this upgrade).
MAIN_RECORD_CANONICAL = "fa122018471be79333f6eb119ccbc28ac143718e7bf3bf328f4a95b4f3f6743c"
MAIN_RECORD_STDOUT = "3d9861c44431aabb7d69bbcb67a66ba778ba98ae6313357af814f769a3e66a74"
MAIN_VERIFY_CANONICAL = "062f89f24a72838643f5ef2253a94930b0b96fb7e3d9e9e3c9b6220e38efcac6"
MAIN_VERIFY_STDOUT = "6f3f1c28e9b5755959192950c0ba2d819c4eff45fdbf8d3adf8890ab4ee05640"
MAIN_RECEIPT_SHA256 = "e042750035a157ae6c2e61986acdad336f6d8ba9b71c610d72d266237fda07c9"
MAIN_ROOT_SHA256 = "02e6fa22ccbb5bf5a30348d551e9b3124fa636c38cd2735c4bf88e95cf1641c0"
V2_EXAMPLE_RECEIPT_SHA256 = "2c4ad4efc414efb08c3fcb88cb2e8a445ba560838bc4262dc8e1f40bde87d268"


def load():
    spec = importlib.util.spec_from_file_location("szl_session_receipt_restore", SKILL / "kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def asset(name):
    return json.loads((SKILL / "assets" / name).read_text(encoding="utf-8"))


def cli(*args):
    p = subprocess.run([sys.executable, "-B", str(RUN), *map(str, args)], capture_output=True)
    return p.returncode, p.stdout.replace(b"\r\n", b"\n")


class SessionReceiptRestoreTests(unittest.TestCase):
    def setUp(self):
        self.k = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.project = self.tmp / "project"
        shutil.copytree(SKILL / "assets" / "project", self.project)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_json(self, name, value):
        path = self.tmp / name
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path

    # -- existing v1 behaviour stays byte-identical -------------------------------------------------
    def test_v1_example_record_and_verify_reports_match_main(self):
        receipt = self.k.szl_make_session_receipt(self.project, asset("example.json"))
        self.assertEqual(canonical_digest(receipt), MAIN_RECORD_CANONICAL)
        self.assertEqual((receipt["receipt_sha256"], receipt["root_sha256"]), (MAIN_RECEIPT_SHA256, MAIN_ROOT_SHA256))
        self.assertNotIn("root_sha256_v2", receipt)
        verdict = self.k.szl_verify_session_receipt(self.project, receipt)
        self.assertEqual(canonical_digest(verdict), MAIN_VERIFY_CANONICAL)
        code, out = cli("record", SKILL / "assets" / "example.json", "--root", SKILL / "assets" / "project")
        self.assertEqual(code, 0)
        self.assertEqual(hashlib.sha256(out).hexdigest(), MAIN_RECORD_STDOUT)
        code, out = cli("verify", self.write_json("receipt.json", receipt), "--root", self.project)
        self.assertEqual(code, 0)
        self.assertEqual(hashlib.sha256(out).hexdigest(), MAIN_VERIFY_STDOUT)

    # -- restore mode -------------------------------------------------------------------------------
    def test_restored_copy_matches_with_scan(self):
        receipt = self.k.szl_make_session_receipt(self.project, asset("example.json"))
        restored = self.tmp / "restored"
        shutil.copytree(self.project, restored)
        verdict = self.k.szl_verify_session_receipt(restored, receipt, scan=True)
        self.assertEqual(verdict["status"], "MATCH")
        self.assertEqual(verdict["files_checked"], 3)
        self.assertEqual(verdict["scan"], {"directories": [".", "data", "results"], "extra_files": []})

    def test_injected_extra_file_and_directory_are_reported_only_in_scan_mode(self):
        receipt = self.k.szl_make_session_receipt(self.project, asset("example.json"))
        (self.project / "results" / "injected.csv").write_text("x\n", encoding="utf-8")
        (self.project / "results" / "sub").mkdir()
        (self.project / "results" / "sub" / "hidden.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.k.szl_verify_session_receipt(self.project, receipt)["status"], "MATCH")
        verdict = self.k.szl_verify_session_receipt(self.project, receipt, scan=True)
        self.assertEqual(verdict["status"], "EXTRA_FILES_PRESENT")
        self.assertEqual(verdict["scan"]["extra_files"], ["results/injected.csv", "results/sub/"])
        code, out = cli("verify", self.write_json("receipt.json", receipt), "--root", self.project, "--scan")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "EXTRA_FILES_PRESENT")

    def test_mismatch_outranks_extra_files(self):
        receipt = self.k.szl_make_session_receipt(self.project, asset("example.json"))
        (self.project / "data" / "raw.csv").write_text("changed\n", encoding="utf-8")
        (self.project / "data" / "extra.csv").write_text("x\n", encoding="utf-8")
        verdict = self.k.szl_verify_session_receipt(self.project, receipt, scan=True)
        self.assertEqual(verdict["status"], "MISMATCH")
        self.assertEqual(verdict["scan"]["extra_files"], ["data/extra.csv"])

    def test_scan_lists_root_and_intermediate_directories_of_nested_paths(self):
        nested = self.tmp / "nested"
        (nested / "results" / "tables").mkdir(parents=True)
        (nested / "results" / "tables" / "t1.csv").write_text("t\n", encoding="utf-8")
        receipt = self.k.szl_make_session_receipt(nested, {"outputs": ["results/tables/t1.csv"]})
        verdict = self.k.szl_verify_session_receipt(nested, receipt, scan=True)
        self.assertEqual(verdict["status"], "MATCH")
        self.assertEqual(verdict["scan"], {"directories": [".", "results", "results/tables"], "extra_files": []})
        (nested / "results" / "injected.py").write_text("x = 1\n", encoding="utf-8")
        (nested / "stray.txt").write_text("s\n", encoding="utf-8")
        verdict = self.k.szl_verify_session_receipt(nested, receipt, scan=True)
        self.assertEqual(verdict["status"], "EXTRA_FILES_PRESENT")
        self.assertEqual(verdict["scan"]["extra_files"], ["stray.txt", "results/injected.py"])
        # The example project with only a nested output recorded: the unrecorded root entries show up.
        receipt = self.k.szl_make_session_receipt(self.project, {"outputs": ["results/summary.csv"]})
        verdict = self.k.szl_verify_session_receipt(self.project, receipt, scan=True)
        self.assertEqual(verdict["scan"]["extra_files"], ["analysis.py", "data/"])

    # -- path containment ---------------------------------------------------------------------------
    def test_drive_absolute_and_noncanonical_paths_are_refused_on_every_host(self):
        bad = ["C:/data/raw.csv", "C:data/raw.csv", "c:\\data\\raw.csv", "\\\\server\\share\\raw.csv",
               "/etc/hostname", "data\\raw.csv", "../kernel.py", "data/../data/raw.csv", "./data/raw.csv",
               "data//raw.csv", "data/raw.csv/", ".", "", "data/raw.csv:stream", "data/raw.csv.", "data/raw.csv ",
               "data/\x00raw.csv", "data/\ud800.csv"]
        for path in bad:
            with self.subTest(path=path):
                r = self.k.szl_make_session_receipt(self.project, {"inputs": [path]})
                self.assertEqual((r["status"], r["reason"]), ("ERROR", "PATH_OUTSIDE_ROOT"))

    def test_real_file_outside_root_is_not_read(self):
        outside = self.tmp / "projectx" / "outside.txt"
        outside.parent.mkdir()
        outside.write_text("secret\n", encoding="utf-8")
        absolute = outside.resolve().as_posix()  # "C:/..." on Windows, "/..." on POSIX
        r = self.k.szl_make_session_receipt(self.project, {"inputs": [absolute]})
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "PATH_OUTSIDE_ROOT"))
        self.assertNotIn("files", r)
        receipt = self.k.szl_make_session_receipt(self.project, asset("example.json"))
        receipt["files"]["inputs"].append({"path": absolute, "state": "RECORDED", "size_bytes": 7, "sha256": "0" * 64})
        verdict = self.k.szl_verify_session_receipt(self.project, receipt, scan=True)
        self.assertEqual((verdict["status"], verdict["reason"]), ("ERROR", "PATH_OUTSIDE_ROOT"))
        bad_row = [row for row in verdict["rows"] if row["path"] == absolute][0]
        self.assertEqual(bad_row["result"], "ERROR")
        self.assertNotIn("observed_sha256", bad_row)
        self.assertEqual(verdict["scan"]["directories"], [".", "data", "results"])
        code, out = cli("verify", self.write_json("receipt.json", receipt), "--root", self.project)
        self.assertEqual((code, json.loads(out)["reason"]), (2, "PATH_OUTSIDE_ROOT"))

    def test_negative_fixture_is_refused_by_cli(self):
        code, out = cli("record", SKILL / "assets" / "negative-drive-path.json", "--root", self.project)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["reason"], "PATH_OUTSIDE_ROOT")

    def test_file_replaced_after_containment_check_is_refused(self):
        real = self.k._locate

        def replace_after_check(root, relative):
            found = real(root, relative)
            fresh = found[0].with_name("fresh.tmp")  # created first, so it cannot reuse the old inode
            fresh.write_text("swapped\n", encoding="utf-8")
            os.replace(fresh, found[0])
            return found

        with mock.patch.object(self.k, "_locate", replace_after_check):
            r = self.k.szl_make_session_receipt(self.project, {"inputs": ["data/raw.csv"]})
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "PATH_OUTSIDE_ROOT"))
        self.assertIn("changed after the containment check", r["detail"])

    def test_symlinks_are_refused_not_followed(self):
        outside = self.tmp / "outside.csv"
        outside.write_text("o\n", encoding="utf-8")
        try:
            os.symlink(outside, self.project / "data" / "out.csv")
            os.symlink(self.project / "data" / "raw.csv", self.project / "data" / "in.csv")
            os.symlink(self.project / "data", self.project / "linked", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        for path in ("data/out.csv", "data/in.csv", "linked/raw.csv"):
            with self.subTest(path=path):
                r = self.k.szl_make_session_receipt(self.project, {"inputs": [path]})
                self.assertEqual((r["status"], r["reason"]), ("ERROR", "PATH_OUTSIDE_ROOT"))
        os.symlink(self.project, self.tmp / "root-link", target_is_directory=True)
        r = self.k.szl_make_session_receipt(self.tmp / "root-link", asset("example.json"))
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "PATH_OUTSIDE_ROOT"))

    @unittest.skipUnless(os.name == "nt", "Windows junctions only")
    def test_windows_junction_is_refused(self):
        import _winapi
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "raw.csv").write_text("j\n", encoding="utf-8")
        try:
            _winapi.CreateJunction(str(elsewhere), str(self.project / "junction"))
        except (OSError, AttributeError) as error:
            self.skipTest(f"junction creation unavailable: {error}")
        r = self.k.szl_make_session_receipt(self.project, {"inputs": ["junction/raw.csv"]})
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "PATH_OUTSIDE_ROOT"))

    # -- size cap -----------------------------------------------------------------------------------
    def test_per_file_size_cap(self):
        # analysis.py is the largest example file at 76 bytes.
        r = self.k.szl_make_session_receipt(self.project, asset("example.json"), max_file_bytes=75)
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "FILE_TOO_LARGE"))
        receipt = self.k.szl_make_session_receipt(self.project, asset("example.json"))
        verdict = self.k.szl_verify_session_receipt(self.project, receipt, max_file_bytes=75)
        self.assertEqual((verdict["status"], verdict["reason"]), ("ERROR", "FILE_TOO_LARGE"))
        self.assertEqual(self.k.szl_make_session_receipt(self.project, asset("example.json"), max_file_bytes=76)["status"], "RECORDED")
        for small in (0, -1):  # a smaller cap only refuses more files
            with self.subTest(max_file_bytes=small):
                self.assertEqual(self.k.szl_make_session_receipt(self.project, asset("example.json"), max_file_bytes=small)["reason"], "FILE_TOO_LARGE")
        code, out = cli("record", SKILL / "assets" / "example.json", "--root", self.project, "--max-file-bytes", "75")
        self.assertEqual((code, json.loads(out)["reason"]), (2, "FILE_TOO_LARGE"))

    # -- empty receipt ------------------------------------------------------------------------------
    def test_empty_receipt_is_not_a_match(self):
        receipt = self.k.szl_make_session_receipt(self.project, {"project": "nothing listed"})
        verdict = self.k.szl_verify_session_receipt(self.project, receipt, scan=True)
        self.assertEqual((verdict["status"], verdict["files_checked"]), ("EMPTY_RECEIPT", 0))
        self.assertEqual(verdict["scan"]["extra_files"], [])
        receipt["notes"] = "edited"
        self.assertEqual(self.k.szl_verify_session_receipt(self.project, receipt)["status"], "RECEIPT_TAMPERED")

    # -- path-bound root ----------------------------------------------------------------------------
    def test_v2_example_records_path_bound_root_and_verifies(self):
        receipt = self.k.szl_make_session_receipt(self.project, asset("example-v2.json"))
        self.assertEqual((receipt["schema"], receipt["status"]), ("szl.session-receipt.v2", "RECORDED"))
        self.assertEqual(receipt["root_sha256"], MAIN_ROOT_SHA256)
        rows = [[role, f["path"], f["sha256"]] for role in ("inputs", "code", "outputs") for f in receipt["files"][role]]
        expected = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(receipt["root_sha256_v2"], expected)
        self.assertEqual(receipt["receipt_sha256"], V2_EXAMPLE_RECEIPT_SHA256)
        self.assertIn("path-bound root digest " + expected[:16], receipt["methods_paragraph"])
        verdict = self.k.szl_verify_session_receipt(self.project, receipt, scan=True)
        self.assertEqual((verdict["schema"], verdict["status"]), ("szl.session-receipt.v2.verification", "MATCH"))
        self.assertEqual(verdict["observed_root_sha256_v2"], expected)
        receipt["root_sha256_v2"] = "0" * 64
        self.assertEqual(self.k.szl_verify_session_receipt(self.project, receipt)["status"], "RECEIPT_TAMPERED")

    def test_v2_root_is_recomputed_even_when_receipt_digest_is_rederived(self):
        unsigned = ("receipt_sha256", "signature", "methods_paragraph", "status", "scope")
        for edit in ("root", "row"):
            with self.subTest(edit=edit):
                receipt = self.k.szl_make_session_receipt(self.project, asset("example-v2.json"))
                if edit == "root":
                    receipt["root_sha256_v2"] = "0" * 64
                else:
                    receipt["files"]["inputs"][0]["sha256"] = "0" * 64
                receipt["receipt_sha256"] = canonical_digest({k: v for k, v in receipt.items() if k not in unsigned})
                verdict = self.k.szl_verify_session_receipt(self.project, receipt)
                self.assertEqual((verdict["status"], verdict["receipt_digest_intact"]), ("RECEIPT_TAMPERED", False))

    def test_content_swap_between_paths_changes_v2_root_but_not_v1(self):
        (self.project / "a.csv").write_text("alpha\n", encoding="utf-8")
        (self.project / "b.csv").write_text("beta\n", encoding="utf-8")
        manifest = {"receipt_schema": "szl.session-receipt.v2", "inputs": ["a.csv", "b.csv"]}
        before = self.k.szl_make_session_receipt(self.project, manifest)
        (self.project / "a.csv").write_text("beta\n", encoding="utf-8")
        (self.project / "b.csv").write_text("alpha\n", encoding="utf-8")
        after = self.k.szl_make_session_receipt(self.project, manifest)
        self.assertEqual(before["root_sha256"], after["root_sha256"])
        self.assertNotEqual(before["root_sha256_v2"], after["root_sha256_v2"])
        verdict = self.k.szl_verify_session_receipt(self.project, before)
        self.assertEqual((verdict["status"], verdict["mismatches"]), ("MISMATCH", 2))
        self.assertEqual(verdict["observed_root_sha256_v2"], after["root_sha256_v2"])

    # -- strict JSON input and malformed documents --------------------------------------------------
    def test_cli_rejects_duplicate_keys_and_non_finite_numbers(self):
        dup = self.tmp / "dup.json"
        dup.write_text('{"inputs": ["data/raw.csv"], "inputs": ["analysis.py"]}', encoding="utf-8")
        nan = self.tmp / "nan.json"
        nan.write_text('{"inputs": ["data/raw.csv"], "environment": {"x": NaN}}', encoding="utf-8")
        inf = self.tmp / "inf.json"
        inf.write_text('{"schema": "szl.session-receipt.v1", "files": {}, "x": -Infinity}', encoding="utf-8")
        big = self.tmp / "big.json"  # float("1e400") is inf; record used to write a receipt verify refuses
        big.write_text('{"inputs": ["data/raw.csv"], "environment": {"x": 1e400}}', encoding="utf-8")
        for args in (("record", dup, "--root", self.project), ("record", nan, "--root", self.project),
                     ("record", big, "--root", self.project), ("verify", dup, "--root", self.project),
                     ("verify", inf, "--root", self.project), ("methods", nan)):
            with self.subTest(args=args[:2]):
                code, out = cli(*args)
                self.assertEqual((code, json.loads(out)["status"]), (2, "ERROR"))

    def test_unknown_schema_and_malformed_receipt_are_errors(self):
        self.assertEqual(self.k.szl_make_session_receipt(self.project, {"receipt_schema": "v3"})["status"], "ERROR")
        receipt = self.k.szl_make_session_receipt(self.project, asset("example.json"))
        receipt["files"]["inputs"] = [{"sha256": "0" * 64}]
        self.assertEqual(self.k.szl_verify_session_receipt(self.project, receipt)["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
