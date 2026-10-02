"""Offline pilot tests. Fixtures do not prove actual Claude Science invocation."""
import copy
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
PILOT = ROOT / "pilots/skill-conformance/szl-skill-conformance"
spec = importlib.util.spec_from_file_location("skill_conformance_pilot", PILOT / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


class SkillConformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.source, self.export, self.retained = [self.root / name for name in ("source", "export", "retained")]
        self.source.mkdir(); self.retained.mkdir()
        (self.source / "SKILL.md").write_text("---\nname: szl-example\ndescription: Fixture skill\n---\nDo a related task.\n", encoding="utf-8")
        # This deliberately dangerous candidate must be treated as bytes only.
        (self.source / "candidate.py").write_text("raise RuntimeError('CANDIDATE MUST NOT RUN')\n", encoding="utf-8")
        shutil.copytree(self.source, self.export)
        digest = kernel.bundle(self.source)["bundle_sha256"]
        self.doc = {"schema_version": 1, "evidence_kind": "synthetic_fixture", "source_bundle_sha256": digest, "host_bundle_sha256": digest, "imported_skill": "szl-example", "cases": []}
        for kind, text in (("positive", "Summarize this fixture dataset."), ("negative", "What is the time zone of London?")):
            case = {"case_id": kind, "kind": kind, "invocation_reported": kind == "positive", "outcome": "PASS"}
            for prefix, content in (("task", text), ("result", f"Fixture-only declared {kind} result.")):
                rel = f"{kind}-{prefix}.txt"
                data = content.encode("utf-8")
                (self.retained / rel).write_bytes(data)
                case[prefix + "_path"] = rel
                case[prefix + "_sha256"] = hashlib.sha256(data).hexdigest()
            self.doc["cases"].append(case)
        self.trials = self.retained / "trials.json"

    def tearDown(self):
        self.temp.cleanup()

    def run_check(self, document=None, **kw):
        self.trials.write_text(json.dumps(self.doc if document is None else document), encoding="utf-8")
        return kernel.check(self.source, self.export, self.trials, self.retained, **kw)

    def reject(self, doc):
        result = self.run_check(doc)
        self.assertEqual(result["offline_conformance"], "REJECTED", result)
        self.assertEqual(result["actual_host_invocation"], "NOT_VERIFIED")

    def test_consistent_fixture_never_grants_host_or_scientific_authority(self):
        result = self.run_check()
        self.assertEqual(result["offline_conformance"], "CONSISTENT_SUPPLIED_EVIDENCE")
        self.assertEqual(result["actual_host_invocation"], "NOT_VERIFIED")
        self.assertEqual(result["scientific_result_validity"], "NOT_EVALUATED")

    def test_operator_export_does_not_grant_host_authority(self):
        self.doc["evidence_kind"] = "operator_supplied_export"
        self.assertEqual(self.run_check()["actual_host_invocation"], "NOT_VERIFIED")

    def test_missing_export_and_trials_are_incomplete(self):
        self.assertEqual(kernel.check(self.source)["offline_conformance"], "INCOMPLETE")
        self.assertEqual(kernel.check(self.source, self.export)["offline_conformance"], "INCOMPLETE")

    def test_changed_missing_extra_and_wrong_identity_exports_reject(self):
        for mode in ("changed", "missing", "extra", "wrong-identity"):
            with self.subTest(mode=mode):
                shutil.rmtree(self.export); shutil.copytree(self.source, self.export)
                if mode == "changed":
                    (self.export / "candidate.py").write_text("changed\n")
                elif mode == "missing":
                    (self.export / "candidate.py").unlink()
                elif mode == "extra":
                    (self.export / "extra.txt").write_text("extra")
                else:
                    path = self.export / "SKILL.md"
                    path.write_text(path.read_text().replace("szl-example", "other-skill"))
                self.reject(self.doc)

    def test_bundle_digest_is_order_independent_but_source_updates_invalidate(self):
        self.assertEqual(kernel.bundle(self.source)["bundle_sha256"], kernel.bundle(self.export)["bundle_sha256"])
        (self.source / "new.txt").write_text("update")
        shutil.copyfile(self.source / "new.txt", self.export / "new.txt")
        self.reject(self.doc)

    def test_explicit_display_alias_only(self):
        self.doc["imported_skill"] = "publisher-example"
        self.reject(self.doc)
        self.assertEqual(self.run_check(imported_name="publisher-example")["offline_conformance"], "CONSISTENT_SUPPLIED_EVIDENCE")

    def test_empty_explicit_alias_is_not_silently_defaulted(self):
        self.assertEqual(self.run_check(imported_name="")["offline_conformance"], "REJECTED")

    def test_duplicate_or_unsupported_frontmatter_identity_rejects(self):
        path = self.source / "SKILL.md"
        original = path.read_text(encoding="utf-8")
        for extra in ('name: "other-skill"', "'name': other-skill", '"name": other-skill', 'name : other-skill', '"\\u006eame": other-skill', '<<: {name: other-skill}'):
            path.write_text(original.replace("description: Fixture skill", extra + "\ndescription: Fixture skill"), encoding="utf-8")
            self.assertEqual(kernel.check(self.source)["offline_conformance"], "REJECTED", extra)
        path.write_text(original.replace("name: szl-example", 'name: "szl-example"'), encoding="utf-8")
        self.assertEqual(kernel.check(self.source)["offline_conformance"], "REJECTED")

    def test_forged_verified_field_and_unknown_case_field_reject(self):
        bad = copy.deepcopy(self.doc); bad["verified"] = True
        self.reject(bad)
        bad = copy.deepcopy(self.doc); bad["cases"][0]["verified"] = True
        self.reject(bad)

    def test_trial_identity_digest_and_schema_reject(self):
        for field, value in (("imported_skill", "other-skill"), ("source_bundle_sha256", "a" * 64), ("host_bundle_sha256", "bad"), ("schema_version", True), ("evidence_kind", "authenticated-host")):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.doc); bad[field] = value
                self.reject(bad)

    def test_retained_task_and_result_tampering_reject(self):
        for prefix in ("task", "result"):
            path = self.retained / self.doc["cases"][0][prefix + "_path"]
            before = path.read_bytes(); path.write_bytes(b"tamper")
            self.reject(self.doc); path.write_bytes(before)

    def test_duplicate_ids_same_task_bytes_and_missing_control_reject(self):
        bad = copy.deepcopy(self.doc); bad["cases"][1]["case_id"] = "positive"
        self.reject(bad)
        bad = copy.deepcopy(self.doc)
        for key in ("task_path", "task_sha256"):
            bad["cases"][1][key] = bad["cases"][0][key]
        self.reject(bad)
        bad = copy.deepcopy(self.doc); bad["cases"][1]["kind"] = "positive"
        self.reject(bad)
        bad["cases"] = []; self.reject(bad)

    def test_negative_control_trigger_and_unavailable_outcome_reject(self):
        bad = copy.deepcopy(self.doc); bad["cases"][1]["invocation_reported"] = True
        self.reject(bad)
        bad = copy.deepcopy(self.doc); bad["cases"][0]["outcome"] = "UNAVAILABLE"
        self.reject(bad)

    def test_duplicate_nonfinite_and_malformed_json_reject(self):
        for text in ('{"schema_version":1,"schema_version":1}', '{"value":NaN}', '{"value":Infinity}', '{broken'):
            self.trials.write_text(text)
            self.assertEqual(kernel.check(self.source, self.export, self.trials, self.retained)["offline_conformance"], "REJECTED")

    def test_traversal_windows_paths_and_nonportable_names_reject(self):
        for path in ("../x", "/x", "C:/x", "a\\b", "a//b", "NUL.txt", "a.", "x:e", "e\u0301.txt"):
            bad = copy.deepcopy(self.doc); bad["cases"][0]["task_path"] = path
            self.reject(bad)

    def test_reparse_point_rejects(self):
        real = kernel.regular_path
        def guarded(path, directory=False):
            if pathlib.Path(path).name == "candidate.py":
                raise ValueError("symlink or reparse point")
            return real(path, directory)
        with mock.patch.object(kernel, "regular_path", side_effect=guarded):
            self.assertEqual(kernel.check(self.source)["offline_conformance"], "REJECTED")

    def test_real_symlink_rejects_if_supported(self):
        target = self.retained / "link.txt"
        try:
            target.symlink_to(self.retained / "positive-task.txt")
        except OSError:
            self.skipTest("symlink privilege unavailable")
        self.doc["cases"][0]["task_path"] = "link.txt"
        self.reject(self.doc)

    def test_bounds_reject_without_executing_candidate(self):
        with mock.patch.object(kernel, "MAX_FILE", 8):
            self.assertEqual(kernel.check(self.source)["offline_conformance"], "REJECTED")
        with mock.patch.object(kernel, "MAX_NODES", 1):
            self.assertEqual(kernel.check(self.source)["offline_conformance"], "REJECTED")
        with mock.patch.object(kernel, "MAX_FILES", 1):
            self.assertEqual(kernel.check(self.source)["offline_conformance"], "REJECTED")
        with mock.patch.object(kernel, "MAX_TOTAL", 8):
            self.assertEqual(kernel.check(self.source)["offline_conformance"], "REJECTED")

    def test_cli_matches_kernel_and_exit_status(self):
        expected = self.run_check()
        command = [sys.executable, "-B", str(PILOT / "scripts/run.py"), "--source", str(self.source), "--host-export", str(self.export), "--trials", str(self.trials), "--trial-root", str(self.retained)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)
        result = subprocess.run(command[:6], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
