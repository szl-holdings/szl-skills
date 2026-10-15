"""Supplied byte bindings cannot certify Claude Science invocation."""
import copy
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
from unittest import TestCase, main

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/szl-skill-conformance"
spec = importlib.util.spec_from_file_location("skill_use_record_kernel", SKILL / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


class UseRecordTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.source, self.retained = root / "source", root / "retained"
        self.source.mkdir(); self.retained.mkdir()
        (self.source / "SKILL.md").write_text("---\nname: szl-example\ndescription: Fixture\n---\nNever execute candidate code.\n", encoding="utf-8")
        (self.source / "candidate.py").write_text("raise RuntimeError('MUST NOT RUN')\n", encoding="utf-8")
        self.artifact = self.retained / "result.txt"
        self.artifact.write_bytes(b"Synthetic result only.\n")
        self.trace = self.retained / "trace.json"
        self.trace.write_bytes(b'{"operator_note":"not a host-authenticated trace"}\n')
        digest = kernel.bundle(self.source)["bundle_sha256"]
        self.document = {
            "schema_version": 1, "evidence_kind": "synthetic_fixture",
            "source": {"repository_url": "https://github.com/szl-holdings/szl-skills",
                       "revision": "a" * 40, "skill_path": "skills/szl-example",
                       "name": "szl-example", "imported_name": "publisher-szl-example",
                       "bundle_sha256": digest},
            "run_id": "synthetic-run-1",
            "artifact": {"id": "result-1", "path": "result.txt",
                         "sha256": hashlib.sha256(self.artifact.read_bytes()).hexdigest()},
            "reported_selection": None,
        }
        self.record = root / "use-record.json"

    def with_selection(self):
        source = self.document["source"]
        self.document["reported_selection"] = {
            "host": "fixture-host", "trace_id": "trace-1", "trace_path": "trace.json",
            "trace_sha256": hashlib.sha256(self.trace.read_bytes()).hexdigest(),
            "run_id": self.document["run_id"], "skill_name": source["imported_name"],
            "source_revision": source["revision"], "source_bundle_sha256": source["bundle_sha256"],
            "artifact_id": self.document["artifact"]["id"],
        }

    def run_check(self, document=None):
        self.record.write_text(json.dumps(self.document if document is None else document), encoding="utf-8")
        return kernel.check_use_record(self.source, self.record, self.retained)

    def test_absent_trace_preserves_bound_artifact_and_unknown_invocation(self):
        report = self.run_check()
        self.assertEqual(report["record_consistency"], "INCOMPLETE_HOST_TRACE")
        self.assertEqual(report["source_binding"], "MATCHED_LOCAL_BYTES")
        self.assertEqual(report["artifact_binding"], "MATCHED_SUPPLIED_BYTES")
        self.assertEqual(report["host_trace_binding"], "UNAVAILABLE")
        self.assertEqual(report["actual_host_invocation"], "UNKNOWN")
        self.assertEqual(report["source_location"], "DECLARED_NOT_VERIFIED")

    def test_matching_supplied_trace_still_does_not_verify_host_or_science(self):
        self.with_selection()
        report = self.run_check()
        self.assertEqual(report["record_consistency"], "CONSISTENT_SUPPLIED_RECORD")
        self.assertEqual(report["host_trace_binding"], "MATCHED_SUPPLIED_BYTES")
        self.assertEqual(report["actual_host_invocation"], "NOT_VERIFIED")
        self.assertEqual(report["scientific_result_validity"], "NOT_EVALUATED")

    def test_modified_source_artifact_and_trace_reject(self):
        self.with_selection()
        self.assertEqual(self.run_check()["record_consistency"], "CONSISTENT_SUPPLIED_RECORD")
        for path in (self.source / "candidate.py", self.artifact, self.trace):
            before = path.read_bytes()
            path.write_bytes(b"tampered\n")
            with self.subTest(path=path.name):
                self.assertEqual(self.run_check()["record_consistency"], "REJECTED")
            path.write_bytes(before)

    def test_false_selection_joins_and_forged_authority_reject(self):
        self.with_selection()
        for field, value in (("run_id", "other-run"), ("skill_name", "other-skill"),
                             ("source_revision", "b" * 40), ("source_bundle_sha256", "b" * 64),
                             ("artifact_id", "other-artifact")):
            bad = copy.deepcopy(self.document)
            bad["reported_selection"][field] = value
            with self.subTest(field=field):
                self.assertEqual(self.run_check(bad)["record_consistency"], "REJECTED")
        bad = copy.deepcopy(self.document)
        bad["actual_host_invocation"] = "VERIFIED"
        self.assertEqual(self.run_check(bad)["record_consistency"], "REJECTED")

    def test_unsafe_paths_and_mutable_revision_reject(self):
        for field, value in (("artifact", "../escape.txt"), ("trace", "C:/escape.txt")):
            self.with_selection()
            bad = copy.deepcopy(self.document)
            if field == "artifact":
                bad["artifact"]["path"] = value
            else:
                bad["reported_selection"]["trace_path"] = value
            with self.subTest(field=field):
                self.assertEqual(self.run_check(bad)["record_consistency"], "REJECTED")
        bad = copy.deepcopy(self.document)
        bad["source"]["revision"] = "main"
        self.assertEqual(self.run_check(bad)["record_consistency"], "REJECTED")

    def test_artifact_cannot_double_as_host_trace(self):
        self.with_selection()
        self.document["reported_selection"]["trace_path"] = "result.txt"
        self.document["reported_selection"]["trace_sha256"] = self.document["artifact"]["sha256"]
        self.assertEqual(self.run_check()["record_consistency"], "REJECTED")

    def test_case_alias_cannot_double_as_host_trace_on_insensitive_filesystems(self):
        alias = self.retained / "RESULT.txt"
        if not alias.exists() or not alias.samefile(self.artifact):
            self.skipTest("retained filesystem is case-sensitive")
        self.with_selection()
        self.document["reported_selection"]["trace_path"] = alias.name
        self.document["reported_selection"]["trace_sha256"] = self.document["artifact"]["sha256"]
        self.assertEqual(self.run_check()["record_consistency"], "REJECTED")

    def test_invalid_source_url_port_rejects(self):
        self.document["source"]["repository_url"] = "https://github.com:badport/szl-holdings/szl-skills"
        self.assertEqual(self.run_check()["record_consistency"], "REJECTED")

    def test_duplicate_keys_and_nonfinite_json_reject(self):
        for raw in ('{"schema_version":1,"schema_version":1}', '{"value":NaN}'):
            self.record.write_text(raw, encoding="utf-8")
            self.assertEqual(kernel.check_use_record(self.source, self.record, self.retained)["record_consistency"], "REJECTED")

    def test_cli_exit_states_and_schema_file(self):
        schema = json.loads((ROOT / "pilots/skill-conformance/use-record.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        command = [sys.executable, "-B", str(SKILL / "scripts/use_record.py"), "--source",
                   str(self.source), "--record", str(self.record), "--retained", str(self.retained)]
        self.run_check()
        absent = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(absent.returncode, 2, absent.stderr)
        self.assertEqual(json.loads(absent.stdout)["actual_host_invocation"], "UNKNOWN")
        self.with_selection(); self.run_check()
        supplied = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(supplied.returncode, 0, supplied.stderr)
        self.assertEqual(json.loads(supplied.stdout)["actual_host_invocation"], "NOT_VERIFIED")


if __name__ == "__main__":
    main()
