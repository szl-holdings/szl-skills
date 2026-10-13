"""Post-#62 repair checks for szl-skill-conformance; offline and stdlib only.

The SDK doubles below are NOT the real Claude Science host. They inspect the submitted
kernel only for the local szl_* entrypoint convention, so host acceptance stays unverified.
"""
import ast
import copy
import hashlib
import importlib.util
import json
import pathlib
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAME = "szl-skill-conformance"
SKILL = ROOT / "skills" / NAME
SETUP = runpy.run_path(str(ROOT / "tools" / "install_claude_science.py"))
spec = importlib.util.spec_from_file_location("skill_conformance_staging", SKILL / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


def has_entrypoint(content):
    return any(isinstance(node, ast.FunctionDef) and node.name.startswith("szl_")
               for node in ast.parse(content).body)


def write_tree(directory, files):
    for path, data in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


class GateSkills:
    """Minimal skills double; gate is 'inspect', 'absent' or reports what it saw."""

    def __init__(self, gate="inspect"):
        self.gate, self.files, self.origins, self.published, self.inspected = gate, {}, {}, [], []

    def list(self):
        return [{"name": name, "origin": origin} for name, origin in self.origins.items()]

    def read(self, name, path="SKILL.md"):
        if (name, path) not in self.files:
            raise FileNotFoundError("file not found")
        return {"content": self.files[name, path]}

    def edit(self, name, path, content, old_string=None):
        self.files[name, path] = content
        self.origins[name] = "draft"
        if path != "kernel.py" or self.gate == "absent":
            return {"action": "edit"}
        self.inspected.append(name)
        return {"action": "edit", "sidecar_gate": {"ok": has_entrypoint(content)}}

    def publish(self, name, overwrite=False):
        self.origins[name] = "personal"
        self.published.append(name)
        return {"status": "published", "name": name}


class GateAgents:
    def __init__(self):
        self.profiles = {}

    def list(self):
        return list(self.profiles.values())

    def create(self, name, display_name, description, system_prompt="", skill_names=None):
        self.profiles[name] = {"name": name, "displayName": display_name, "systemPrompt": system_prompt,
                               "skillNames": list(skill_names), "connectors": [], "unrestricted": False}

    def attach_skill(self, name, skill):
        self.profiles[name]["skillNames"].append(skill)


def native_kernels(batches):
    return {name for batch in batches for name, files in batch.items() if "kernel.py" in files}


def without_entrypoint(content):
    legacy = content.replace("def szl_check_skill_conformance(", "def check(", 1)
    legacy = legacy.replace("\ncheck = szl_check_skill_conformance\n", "\n")
    assert not has_entrypoint(legacy)
    return legacy


class EntrypointGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.receipts = pathlib.Path(temporary.name)

    def host(self, gate="inspect"):
        return types.SimpleNamespace(skills=GateSkills(gate), agents=GateAgents())

    def plans(self):
        return {"sharing": [SETUP["bundle"](ROOT, family="sharing")],
                "all": SETUP["bundle_batches"](ROOT, family="all")}

    def test_kernel_exposes_convention_entrypoint_and_keeps_cli_alias(self):
        self.assertTrue(has_entrypoint((SKILL / "kernel.py").read_text(encoding="utf-8")))
        self.assertIs(kernel.check, kernel.szl_check_skill_conformance)
        self.assertNotIn("kernel.check(", (SKILL / "scripts" / "run.py").read_text(encoding="utf-8"))

    def test_sharing_and_all_pass_an_entrypoint_inspecting_gate(self):
        for family, batches in self.plans().items():
            with self.subTest(family=family):
                host = self.host()
                result = SETUP["install"](host, batches, self.receipts / (family + ".json"), family=family)
                self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
                self.assertIn(NAME, native_kernels(batches))
                self.assertEqual(set(host.skills.inspected), native_kernels(batches))
                gated = {name for name, entry in result["skills"].items() if "sidecar_gate" in entry}
                self.assertEqual(gated, native_kernels(batches))
                self.assertTrue(all(result["skills"][name]["sidecar_gate"]["ok"] is True for name in gated))
                self.assertEqual(result["runtime_task_evaluation"], "NOT_EXECUTED")

    def test_kernel_without_entrypoint_is_rejected_before_publication(self):
        for family, batches in self.plans().items():
            with self.subTest(family=family):
                bad = copy.deepcopy(batches)
                batch = next(item for item in bad if NAME in item)
                batch[NAME]["kernel.py"] = without_entrypoint(batch[NAME]["kernel.py"])
                host, receipt = self.host(), self.receipts / ("rejected-" + family + ".json")
                with self.assertRaisesRegex(ValueError, "sidecar gate rejected " + NAME):
                    SETUP["install"](host, bad, receipt, family=family)
                self.assertEqual(host.skills.published, [])
                recorded = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(recorded["status"], "FAILED_OR_INCOMPLETE")
                self.assertIs(recorded["skills"][NAME]["sidecar_gate"]["ok"], False)

    def test_absent_gate_is_recorded_as_probe_unavailable_not_success(self):
        for family, batches in self.plans().items():
            with self.subTest(family=family):
                host = self.host("absent")
                result = SETUP["install"](host, batches, self.receipts / ("absent-" + family + ".json"), family=family)
                self.assertEqual(host.skills.inspected, [])
                for name in native_kernels(batches):
                    self.assertEqual(result["skills"][name]["sidecar_gate"], {"status": "PROBE_UNAVAILABLE"})
                self.assertEqual(result["runtime_task_evaluation"], "NOT_EXECUTED")


class StagedResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.temporary.name)
        cls.source = cls.root / "source"
        shutil.copytree(SKILL, cls.source, ignore=shutil.ignore_patterns("__pycache__"))
        cls.legal = cls.root / "legal"
        cls.legal.mkdir()
        for name in kernel.LEGAL:
            shutil.copyfile(ROOT / name, cls.legal / name)
        cls.sdk = cls.root / "sdk-export"
        write_tree(cls.sdk, SETUP["bundle"](ROOT, family="sharing")[NAME])
        cls.revision_source, cls.revision_legal, cls.zip_export = cls.package_at_head()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def package_at_head(cls):
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        original = package["git_bytes"]
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                  capture_output=True, text=True).stdout.strip()
        market = json.loads(original(revision, ".claude-plugin/marketplace.json"))
        market["plugins"] = [p for p in market["plugins"] if p["name"] == "szl-science-sharing-skills"]
        # Package only this skill through the real revision path; other bytes are Git blobs.
        # run_path returns a copy of the globals, so patch the namespace the function uses.
        package["package_skills"].__globals__["git_bytes"] = lambda rev, rel: (
            json.dumps(market).encode("utf-8") if rel == ".claude-plugin/marketplace.json" else original(rev, rel))
        reports = package["package_skills"](cls.root / "zips", revision)
        assert [report["skill"] for report in reports] == [NAME]
        with zipfile.ZipFile(cls.root / "zips" / (NAME + ".zip")) as archive:
            archive.extractall(cls.root / "zip-export")
        listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", revision, "--", "skills/" + NAME + "/"],
                                cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
        source, legal = cls.root / "revision-source", cls.root / "revision-legal"
        write_tree(source, {path[len("skills/" + NAME + "/"):]: original(revision, path) for path in listed})
        write_tree(legal, {name: original(revision, name) for name in kernel.LEGAL})
        return source, legal, cls.root / "zip-export" / NAME

    def export_copy(self, origin=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = pathlib.Path(temporary.name) / "export"
        shutil.copytree(origin or self.sdk, target)
        return target

    def assert_boundaries(self, report):
        self.assertEqual(report["actual_host_invocation"], "NOT_VERIFIED")
        self.assertEqual(report["scientific_result_validity"], "NOT_EVALUATED")

    def test_raw_source_against_staged_export_without_contract_still_mismatches(self):
        for export in (self.sdk, self.zip_export):
            with self.subTest(export=export.name):
                report = kernel.check(self.source, export)
                self.assertEqual((report["offline_conformance"], report["supplied_export_binding"]),
                                 ("REJECTED", "MISMATCH"))
                self.assertNotIn("staged_resources", report)
                self.assert_boundaries(report)

    def test_sdk_staging_and_zip_round_trip_match_the_staged_contract(self):
        for source, legal, export in ((self.source, self.legal, self.sdk),
                                      (self.revision_source, self.revision_legal, self.zip_export)):
            with self.subTest(export=export.name):
                report = kernel.szl_check_skill_conformance(source, export, legal_root=legal)
                self.assertEqual(report["supplied_export_binding"], "MATCHED_SUPPLIED_BYTES", report["issues"])
                self.assertEqual(report["offline_conformance"], "INCOMPLETE")
                self.assertEqual(report["issues"], ["no complete supplied trial evidence"])
                staged = report["staged_resources"]
                self.assertEqual(staged["contract"], "szl.skill-staged-resources.v1")
                self.assertEqual(staged["expected_bundle_sha256"], report["supplied_export_bundle_sha256"])
                self.assertNotEqual(staged["expected_bundle_sha256"], report["source"]["bundle_sha256"])
                self.assertEqual(staged["legal_source_revision"], "NOT_VERIFIED")
                for name in kernel.LEGAL:
                    digest = hashlib.sha256((legal / name).read_bytes()).hexdigest()
                    self.assertEqual(staged["legal_resources"][name]["sha256"], digest)
                    self.assertNotIn(name, report["source"]["files"])
                self.assert_boundaries(report)

    def test_staged_export_negatives_name_the_exact_difference(self):
        cases = (("missing LICENSE", lambda e: (e / "LICENSE").unlink(), "missing", "LICENSE"),
                 ("missing NOTICE", lambda e: (e / "NOTICE").unlink(), "missing", "NOTICE"),
                 ("modified LICENSE", lambda e: (e / "LICENSE").write_bytes(b"other license\n"), "changed", "LICENSE"),
                 ("modified NOTICE", lambda e: (e / "NOTICE").write_bytes(b"other notice\n"), "changed", "NOTICE"),
                 ("unexpected resource", lambda e: (e / "extra.txt").write_bytes(b"x"), "unexpected", "extra.txt"),
                 ("nested legal copy", lambda e: (e / "references" / "LICENSE").write_bytes(b"x"),
                  "unexpected", "references/LICENSE"),
                 ("changed payload", lambda e: (e / "kernel.py").write_bytes(
                     (e / "kernel.py").read_bytes() + b"\n"), "changed", "kernel.py"),
                 ("missing payload", lambda e: (e / "references" / "contract.md").unlink(),
                  "missing", "references/contract.md"))
        for label, mutate, bucket, path in cases:
            for origin in (self.sdk, self.zip_export):
                with self.subTest(case=label, export=origin.name):
                    export = self.export_copy(origin)
                    mutate(export)
                    source, legal = ((self.source, self.legal) if origin == self.sdk
                                     else (self.revision_source, self.revision_legal))
                    report = kernel.check(source, export, legal_root=legal)
                    self.assertEqual((report["offline_conformance"], report["supplied_export_binding"]),
                                     ("REJECTED", "MISMATCH"))
                    found = report["staged_resources"]["export_differences"]
                    self.assertEqual(found[bucket], [path])
                    self.assertEqual(sum(map(len, found.values())), 1)
                    self.assertIn("expected staged resources", report["issues"][-1])
                    self.assert_boundaries(report)

    def test_legal_inputs_and_unclean_sources_fail_closed(self):
        other = self.export_copy(self.legal)
        (other / "LICENSE").write_bytes(b"license from another revision\n")
        report = kernel.check(self.source, self.sdk, legal_root=other)
        self.assertEqual(report["staged_resources"]["export_differences"]["changed"], ["LICENSE"])
        missing = self.export_copy(self.legal)
        (missing / "NOTICE").unlink()
        self.assertEqual(kernel.check(self.source, self.sdk, legal_root=missing)["offline_conformance"], "REJECTED")
        for root in (self.root / "absent", ""):
            with self.subTest(legal_root=str(root)):
                self.assertEqual(kernel.check(self.source, self.sdk, legal_root=root)["offline_conformance"], "REJECTED")
        doubled = kernel.check(self.sdk, self.sdk, legal_root=self.legal)
        self.assertEqual(doubled["offline_conformance"], "REJECTED")
        self.assertIn("already contains a staged legal resource", doubled["issues"][-1])
        for extra, reason in (("license", "already contains"), ("Notice", "already contains"),
                              ("__pycache__/kernel.cpython-312.pyc", "generated cache")):
            with self.subTest(extra=extra):
                source = self.export_copy(self.source)
                write_tree(source, {extra: b"x"})
                report = kernel.check(source, legal_root=self.legal)
                self.assertEqual(report["offline_conformance"], "REJECTED")
                self.assertIn(reason, report["issues"][-1])
                self.assert_boundaries(report)

    def trials(self, source_digest, host_digest):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        retained = pathlib.Path(temporary.name)
        document = {"schema_version": 1, "evidence_kind": "synthetic_fixture", "source_bundle_sha256": source_digest,
                    "host_bundle_sha256": host_digest, "imported_skill": NAME, "cases": []}
        for kind, task in (("positive", "Check retained skill-sharing evidence."), ("negative", "Name a colour.")):
            case = {"case_id": kind, "kind": kind, "invocation_reported": kind == "positive", "outcome": "PASS"}
            for prefix, text in (("task", task), ("result", "Synthetic " + kind + " declaration.")):
                (retained / (kind + "-" + prefix + ".txt")).write_bytes(text.encode("utf-8"))
                case[prefix + "_path"] = kind + "-" + prefix + ".txt"
                case[prefix + "_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            document["cases"].append(case)
        (retained / "trials.json").write_text(json.dumps(document), encoding="utf-8")
        return retained / "trials.json", retained

    def test_trials_bind_raw_source_and_staged_export_identities(self):
        matched = kernel.check(self.source, self.sdk, legal_root=self.legal)
        raw, staged = matched["source"]["bundle_sha256"], matched["staged_resources"]["expected_bundle_sha256"]
        trials, retained = self.trials(raw, staged)
        report = kernel.check(self.source, self.sdk, trials, retained, legal_root=self.legal)
        self.assertEqual(report["offline_conformance"], "CONSISTENT_SUPPLIED_EVIDENCE")
        self.assert_boundaries(report)
        for source_digest, host_digest in ((staged, staged), (raw, raw)):
            with self.subTest(source=source_digest[:8], host=host_digest[:8]):
                trials, retained = self.trials(source_digest, host_digest)
                report = kernel.check(self.source, self.sdk, trials, retained, legal_root=self.legal)
                self.assertEqual(report["offline_conformance"], "REJECTED")
                self.assert_boundaries(report)
        self.assertEqual(kernel.check(self.source, self.sdk, trials, None, legal_root=self.legal)["offline_conformance"],
                         "INCOMPLETE")

    def test_cli_staged_comparison_matches_kernel_and_exit_codes(self):
        base = [sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"), "--source", str(self.source),
                "--host-export", str(self.sdk)]
        staged = base + ["--legal-root", str(self.legal)]
        matched = subprocess.run(staged, capture_output=True, text=True, check=False)
        self.assertEqual(matched.returncode, 2, matched.stdout + matched.stderr)
        self.assertEqual(json.loads(matched.stdout), kernel.check(self.source, self.sdk, legal_root=self.legal))
        self.assertEqual(subprocess.run(base, capture_output=True, text=True, check=False).returncode, 1)
        report = json.loads(matched.stdout)
        trials, retained = self.trials(report["source"]["bundle_sha256"],
                                       report["staged_resources"]["expected_bundle_sha256"])
        complete = subprocess.run(staged + ["--trials", str(trials), "--trial-root", str(retained)],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(complete.returncode, 0, complete.stdout + complete.stderr)
        self.assertEqual(json.loads(complete.stdout)["actual_host_invocation"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
