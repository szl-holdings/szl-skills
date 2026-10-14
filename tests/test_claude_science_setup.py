"""Documented SDK contract tests only. This test double is NOT the real application."""
import pathlib
import copy
import hashlib
import json
import runpy
import subprocess
import tempfile
import types
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP = runpy.run_path(str(ROOT / "tools" / "install_claude_science.py"))
REVISION = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
BINDINGS = {}


def bound(family):
    """Binding at the checked-out commit; staged working-tree resources must equal it."""
    if family not in BINDINGS:
        BINDINGS[family] = SETUP["revision_binding"](ROOT, REVISION, family)
    return copy.deepcopy(BINDINGS[family])


def flattened(batches):
    return {name: files for batch in batches for name, files in batch.items()}


def resource_bytes(files):
    return sum(len(text.encode("utf-8")) for text in files.values())


class SkillsDouble:
    def __init__(self):
        self.files, self.origins, self.published = {}, {}, []
        self.reject_gate = False
        self.corrupt_published_readback = False

    def list(self):
        return [{"name": n, "origin": origin} for n, origin in self.origins.items()]

    def read(self, name, path="SKILL.md"):
        if (name, path) not in self.files:
            raise FileNotFoundError("file not found")
        content = self.files[name, path]
        if self.corrupt_published_readback and name in self.published:
            content += "\nreadback drift"
        return {"content": content}

    def edit(self, name, path, content, old_string=None):
        previous = self.files.get((name, path))
        if old_string is None and previous is not None:
            raise ValueError("file exists")
        if old_string is not None and (previous is None or previous.count(old_string) != 1):
            raise ValueError("single exact match required")
        self.files[name, path] = content if old_string is None else previous.replace(old_string, content, 1)
        self.origins[name] = "draft"
        return {"action": "edit", "sidecar_gate": {"ok": not self.reject_gate}} if path == "kernel.py" else {"action": "edit"}

    def publish(self, name, overwrite=False):
        self.origins[name] = "personal"
        self.published.append(name)
        return {"status": "published", "name": name}


class AgentsDouble:
    def __init__(self):
        self.profiles = {}

    def list(self):
        return list(self.profiles.values())

    def create(self, name, display_name, description, system_prompt="", skill_names=None):
        self.profiles[name] = {"name": name, "displayName": display_name, "systemPrompt": system_prompt,
                               "skillNames": list(skill_names), "connectors": [], "unrestricted": False}

    def attach_skill(self, name, skill):
        self.profiles[name]["skillNames"].append(skill)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temp.name) / "receipt.json"
        self.host = types.SimpleNamespace(skills=SkillsDouble(), agents=AgentsDouble())
        self.resources = SETUP["bundle_batches"](ROOT, family="core")

    def tearDown(self):
        self.temp.cleanup()

    def test_supported_calls_publish_readback_and_curated_profile(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        science = next(p for p in market["plugins"] if p["name"] == "szl-science-skills")
        evidence = next(p for p in market["plugins"] if p["name"] == "szl-evidence-skills")
        expected = {pathlib.PurePosixPath(p).name for p in science["skills"]}
        staged = flattened(self.resources)
        self.assertEqual(set(staged), expected)
        self.assertEqual(len(SETUP["NAMES"]), len(expected))
        self.assertTrue(expected.isdisjoint(pathlib.PurePosixPath(p).name for p in evidence["skills"]))
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        notice_text = (ROOT / "NOTICE").read_text(encoding="utf-8")
        for files in staged.values():
            self.assertEqual(files["LICENSE"], license_text)
            self.assertEqual(files["NOTICE"], notice_text)
            self.assertLessEqual(resource_bytes(files), SETUP["MAX_SKILL_BYTES"])
        self.assertLessEqual(len(self.resources), SETUP["MAX_BATCHES"])
        self.assertTrue(all(sum(map(resource_bytes, batch.values())) <= SETUP["MAX_BATCH_BYTES"]
                            for batch in self.resources))
        result = SETUP["install"](self.host, self.resources, self.path, family="core", binding=bound("core"))
        self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(result["family"], "core")
        self.assertEqual(len(result["skills"]), len(SETUP["NAMES"]))
        self.assertEqual(result["agent"]["connectors"], [])
        self.assertFalse(result["agent"]["unrestricted"])
        self.assertEqual(result["runtime_task_evaluation"], "NOT_EXECUTED")
        self.assertEqual(len(result["staging"]["batches"]), len(self.resources))
        self.assertTrue(all(batch["resource_bytes"] <= 1000000 for batch in result["staging"]["batches"]))
        self.assertEqual(result["staging"]["total_resource_bytes"], sum(
            len(text.encode("utf-8")) for batch in self.resources for files in batch.values() for text in files.values()))
        for name, files in staged.items():
            hashes = result["skills"][name]["readback_sha256"]
            self.assertEqual(set(hashes), set(files))
            for path, content in files.items():
                self.assertEqual(hashes[path], hashlib.sha256(content.encode("utf-8")).hexdigest())
        second = SETUP["install"](self.host, self.resources, self.path.with_name("second.json"), family="core", binding=bound("core"))
        self.assertEqual(second["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(len(self.host.skills.published), len(SETUP["NAMES"]))

    def test_collision_preflight_does_not_modify_other_resources(self):
        name = SETUP["NAMES"][-1]
        self.assertGreater(len(self.resources), 1)
        self.assertIn(name, self.resources[-1])
        self.host.skills.origins[name] = "personal"
        self.host.skills.files[name, "SKILL.md"] = "retain my personal skill"
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, self.resources, self.path, binding=bound("core"))
        self.assertEqual(len(self.host.skills.files), 1)
        self.assertEqual(self.host.skills.published, [])

    def test_all_science_batches_match_every_non_evidence_family(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        expected = {pathlib.PurePosixPath(path).name
                    for plugin in market["plugins"] if plugin["name"] != "szl-evidence-skills"
                    for path in plugin["skills"]}
        all_batches = SETUP["bundle_batches"](ROOT, family="all")
        names = [name for batch in all_batches for name in batch]
        self.assertEqual(names, SETUP["family_names"]("all"))
        self.assertEqual(set(names), expected)
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(len(all_batches), SETUP["MAX_BATCHES"])
        for batch in all_batches:
            self.assertLessEqual(sum(map(resource_bytes, batch.values())), SETUP["MAX_BATCH_BYTES"])
            for files in batch.values():
                self.assertLessEqual(resource_bytes(files), SETUP["MAX_SKILL_BYTES"])
                self.assertEqual(files["LICENSE"], (ROOT / "LICENSE").read_text(encoding="utf-8"))
                self.assertEqual(files["NOTICE"], (ROOT / "NOTICE").read_text(encoding="utf-8"))
        result = SETUP["install"](self.host, all_batches, self.path, family="all", binding=bound("all"))
        self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(result["family"], "all")
        self.assertEqual(set(result["skills"]), expected)
        self.assertEqual(set(result["agent"]["skillNames"]), expected)
        self.assertEqual(result["agent"]["connectors"], [])

    def test_synthetic_evidence_map_remains_outside_the_science_installer(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        evidence = next(plugin for plugin in market["plugins"]
                        if plugin["name"] == "szl-evidence-skills")
        self.assertIn("./skills/szl-rare-disease-evidence-map", evidence["skills"])
        self.assertNotIn("szl-rare-disease-evidence-map", SETUP["family_names"]("all"))
        self.assertIn("szl-rare-disease-evidence-replay", SETUP["family_names"]("all"))

    def test_eleven_bounded_families_attach_all_tools_without_replacing_profile(self):
        design = SETUP["bundle"](ROOT, family="design")
        replay = SETUP["bundle"](ROOT, family="replay")
        rare_replay = SETUP["bundle"](ROOT, family="rare-disease-replay")
        paper = SETUP["bundle"](ROOT, family="paper")
        assay = SETUP["bundle"](ROOT, family="assay")
        change = SETUP["bundle"](ROOT, family="change")
        multiplicity = SETUP["bundle"](ROOT, family="multiplicity")
        uncertainty = SETUP["bundle"](ROOT, family="uncertainty")
        reporting = SETUP["bundle"](ROOT, family="reporting")
        sharing = SETUP["bundle"](ROOT, family="sharing")
        self.assertEqual(set(design), {"szl-experiment-contract"})
        self.assertEqual(set(replay), {"szl-experiment-replay", "szl-figure-data-contract", "szl-measurement-harmonizer"})
        self.assertEqual(set(rare_replay), {"szl-rare-disease-evidence-replay"})
        self.assertEqual(set(paper), {"szl-paper-evidence-audit"})
        self.assertEqual(set(assay), {"szl-assay-measurement-audit"})
        self.assertEqual(set(change), {"szl-research-change-impact"})
        self.assertEqual(set(multiplicity), {"szl-multiplicity-audit"})
        self.assertEqual(set(uncertainty), {"szl-uncertainty-lineage"})
        self.assertEqual(set(reporting), {"szl-reporting-guideline-audit"})
        self.assertNotIn("kernel.py", reporting["szl-reporting-guideline-audit"])
        self.assertEqual(set(sharing), {"szl-skill-conformance"})
        self.assertIn("scripts/run.py", sharing["szl-skill-conformance"])
        self.assertIn("scripts/conformance.py", sharing["szl-skill-conformance"])
        self.assertNotIn("kernel.py", sharing["szl-skill-conformance"])
        for family, resources in (("design", design), ("replay", replay),
                                  ("rare-disease-replay", rare_replay), ("paper", paper),
                                  ("assay", assay), ("multiplicity", multiplicity),
                                  ("change", change), ("uncertainty", uncertainty),
                                  ("reporting", reporting), ("sharing", sharing)):
            self.assertLessEqual(sum(map(resource_bytes, resources.values())), SETUP["MAX_BATCH_BYTES"])
            self.assertEqual(SETUP["bundle_batches"](ROOT, family=family), [resources])
        SETUP["install"](self.host, self.resources, self.path, family="core", binding=bound("core"))
        design_result = SETUP["install"](self.host, design, self.path.with_name("design.json"), family="design", binding=bound("design"))
        self.assertEqual(design_result["family"], "design")
        replay_result = SETUP["install"](self.host, replay, self.path.with_name("replay.json"), family="replay", binding=bound("replay"))
        self.assertEqual(replay_result["family"], "replay")
        rare_result = SETUP["install"](self.host, rare_replay,
                                       self.path.with_name("rare-replay.json"), family="rare-disease-replay", binding=bound("rare-disease-replay"))
        self.assertEqual(rare_result["family"], "rare-disease-replay")
        paper_result = SETUP["install"](self.host, paper, self.path.with_name("paper.json"), family="paper", binding=bound("paper"))
        self.assertEqual(paper_result["family"], "paper")
        SETUP["install"](self.host, assay, self.path.with_name("assay.json"), family="assay", binding=bound("assay"))
        multiplicity_result = SETUP["install"](self.host, multiplicity, self.path.with_name("multiplicity.json"), family="multiplicity", binding=bound("multiplicity"))
        self.assertEqual(multiplicity_result["family"], "multiplicity")
        uncertainty_result = SETUP["install"](self.host, uncertainty, self.path.with_name("uncertainty.json"), family="uncertainty", binding=bound("uncertainty"))
        self.assertEqual(uncertainty_result["family"], "uncertainty")
        reporting_result = SETUP["install"](self.host, reporting, self.path.with_name("reporting.json"), family="reporting", binding=bound("reporting"))
        self.assertEqual(reporting_result["family"], "reporting")
        sharing_result = SETUP["install"](self.host, sharing, self.path.with_name("sharing.json"), family="sharing", binding=bound("sharing"))
        self.assertEqual(sharing_result["family"], "sharing")
        self.assertEqual(sharing_result["runtime_task_evaluation"], "NOT_EXECUTED")
        self.assertNotIn("sidecar_gate", sharing_result["skills"]["szl-skill-conformance"])
        result = SETUP["install"](self.host, change, self.path.with_name("change.json"), family="change", binding=bound("change"))
        self.assertEqual(set(result["agent"]["skillNames"]),
                         set(SETUP["NAMES"] + SETUP["DESIGN_NAMES"] + SETUP["REPLAY_NAMES"] +
                             SETUP["RARE_DISEASE_REPLAY_NAMES"] + SETUP["PAPER_NAMES"] +
                             SETUP["ASSAY_NAMES"] + SETUP["MULTIPLICITY_NAMES"] +
                             SETUP["CHANGE_NAMES"] + SETUP["UNCERTAINTY_NAMES"] +
                             SETUP["REPORTING_NAMES"] + SETUP["SHARING_NAMES"]))
        self.assertEqual(result["family"], "change")
        self.assertEqual(result["skills"]["szl-research-change-impact"]["sidecar_gate"]["ok"], True)
        self.assertFalse(result["agent"]["unrestricted"])
        self.assertEqual(result["agent"]["connectors"], [])

    def test_uncertainty_family_is_cli_only_opt_in_and_bounded(self):
        resources = SETUP["bundle"](ROOT, family="uncertainty")
        self.assertEqual(set(resources), {"szl-uncertainty-lineage"})
        self.assertNotIn("szl-uncertainty-lineage", SETUP["family_names"]("core"))
        self.assertEqual(SETUP["bundle_batches"](ROOT, family="uncertainty"), [resources])
        files = resources["szl-uncertainty-lineage"]
        self.assertIn("scripts/run.py", files)
        self.assertNotIn("kernel.py", files)
        self.assertLessEqual(resource_bytes(files), SETUP["MAX_SKILL_BYTES"])
        for malformed in ({}, {"szl-uncertainty-lineage": {"huge": "x" * 1000001}}):
            with self.assertRaises(ValueError):
                SETUP["install"](self.host, malformed, self.path, family="uncertainty", binding=bound("uncertainty"))
        self.assertEqual(self.host.skills.files, {})
        self.assertFalse(self.path.exists())
        result = SETUP["install"](self.host, resources, self.path, family="uncertainty", binding=bound("uncertainty"))
        self.assertEqual(result["family"], "uncertainty")
        self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(result["runtime_task_evaluation"], "NOT_EXECUTED")
        self.assertEqual(set(result["skills"]), {"szl-uncertainty-lineage"})
        self.assertEqual(result["agent"]["connectors"], [])

    def test_unknown_partial_or_oversized_family_never_writes(self):
        with self.assertRaises(ValueError):
            SETUP["bundle"](ROOT, family="unreviewed")
        with self.assertRaises(ValueError):
            SETUP["bundle_batches"](ROOT, family="unreviewed")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="replay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="rare-disease-replay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-experiment-replay": {},
                                          "szl-rare-disease-evidence-replay": {}},
                             self.path, family="replay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="design")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="assay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="change")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="sharing")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-research-change-impact": {"huge": "x" * 1000001}}, self.path, family="change")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-assay-measurement-audit": {"huge": "x" * 1000001}}, self.path, family="assay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-experiment-replay": {"huge": "x" * 1000001}},
                             self.path, family="replay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-rare-disease-evidence-replay": {"huge": "x" * 1000001}},
                             self.path, family="rare-disease-replay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-experiment-replay": {"huge": "x" * 1000001},
                                             "szl-measurement-harmonizer": {}}, self.path, family="replay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="paper")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-paper-evidence-audit": {"huge": "x" * 1000001}}, self.path, family="paper")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="multiplicity")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-multiplicity-audit": {"huge": "x" * 1000001}}, self.path, family="multiplicity")
        self.assertEqual(self.host.skills.files, {})
        self.assertEqual(self.host.agents.profiles, {})

    def test_gate_rejection_is_not_a_publication_success(self):
        self.host.skills.reject_gate = True
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, self.resources, self.path, family="core", binding=bound("core"))
        self.assertEqual(self.host.skills.published, [])
        self.assertIn("FAILED_OR_INCOMPLETE", self.path.read_text())

    def test_mismatched_readback_is_not_publication_success(self):
        self.host.skills.corrupt_published_readback = True
        with self.assertRaisesRegex(ValueError, "readback mismatch"):
            SETUP["install"](self.host, self.resources, self.path, family="core", binding=bound("core"))
        self.assertIn("FAILED_OR_INCOMPLETE", self.path.read_text())

    def test_invalid_receipt_parent_rejected_before_host_access(self):
        file_parent = self.path.parent / "not-a-directory"
        file_parent.write_text("keep", encoding="utf-8")
        for receipt_path in (self.path.parent / "missing" / "receipt.json",
                             file_parent / "receipt.json"):
            with self.subTest(receipt_path=receipt_path):
                with self.assertRaisesRegex(ValueError, "receipt"):
                    SETUP["install"](None, self.resources, receipt_path, family="core", binding=bound("core"))
                self.assertFalse(receipt_path.exists())
        self.assertEqual(file_parent.read_text(encoding="utf-8"), "keep")

    def test_update_retains_unstaged_personal_file_and_marks_completeness_unverified(self):
        SETUP["install"](self.host, self.resources, self.path, family="core", binding=bound("core"))
        name = SETUP["NAMES"][-1]
        staged = flattened(self.resources)
        self.host.skills.files[name, "scripts/stale.py"] = "print('personal')\n"
        self.host.skills.files[name, "SKILL.md"] += "\nold personal revision\n"
        updated = SETUP["install"](self.host, self.resources, self.path.with_name("updated.json"),
                                   update=True, family="core", binding=bound("core"))
        self.assertEqual(updated["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(updated["resource_set_completeness"], "UNVERIFIED")
        self.assertEqual(self.host.skills.files[name, "scripts/stale.py"], "print('personal')\n")
        self.assertEqual(self.host.skills.files[name, "SKILL.md"], staged[name]["SKILL.md"])
        for skill_name, files in staged.items():
            hashes = updated["skills"][skill_name]["readback_sha256"]
            self.assertEqual(set(hashes), set(files))
            for path, content in files.items():
                self.assertEqual(hashes[path], hashlib.sha256(content.encode("utf-8")).hexdigest())

    def reject_before_host(self, resources, family="core"):
        # A malformed plan must fail before even looking up host inventory.
        with self.assertRaises(ValueError):
            SETUP["install"](None, resources, self.path, family=family)
        self.assertFalse(self.path.exists())

    def test_single_bundle_guard_remains_and_batch_plan_is_complete(self):
        all_batches = SETUP["bundle_batches"](ROOT, family="all")
        with self.assertRaisesRegex(ValueError, "1 MB"):
            SETUP["bundle"](ROOT, family="all")
        core_total = sum(resource_bytes(files) for files in flattened(self.resources).values())
        if core_total > SETUP["MAX_BATCH_BYTES"]:
            with self.assertRaisesRegex(ValueError, "1 MB"):
                SETUP["bundle"](ROOT, family="core")
        else:
            self.assertEqual(set(SETUP["bundle"](ROOT, family="core")), set(SETUP["NAMES"]))
        single = SETUP["bundle"](ROOT, family="replay")
        self.assertEqual(list(single), SETUP["REPLAY_NAMES"])
        seen = [name for batch in self.resources for name in batch]
        self.assertEqual(seen, SETUP["NAMES"])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual([name for batch in all_batches for name in batch], SETUP["family_names"]("all"))

    def test_flattening_cannot_bypass_the_single_batch_guard(self):
        flat = flattened(SETUP["bundle_batches"](ROOT, family="all"))
        self.reject_before_host(flat, family="all")

    def test_duplicate_missing_unknown_or_excessive_batches_rejected(self):
        all_batches = SETUP["bundle_batches"](ROOT, family="all")
        self.assertGreater(len(all_batches), 1)
        duplicate = copy.deepcopy(all_batches)
        duplicate[-1][SETUP["NAMES"][0]] = duplicate[0][SETUP["NAMES"][0]]
        self.reject_before_host(duplicate, family="all")
        missing = copy.deepcopy(all_batches)
        del missing[-1][SETUP["family_names"]("all")[-1]]
        self.reject_before_host(missing, family="all")
        unknown = copy.deepcopy(all_batches)
        unknown[-1]["unreviewed"] = {"SKILL.md": "name: unreviewed"}
        self.reject_before_host(unknown, family="all")
        self.reject_before_host(all_batches * 9, family="all")

    def test_oversize_skill_and_noncanonical_paths_rejected(self):
        for path, content in [("large.txt", "x" * 200001), ("../escape.txt", "x"),
                              ("a//b", "x"), ("a\\b", "x"), ("a:b", "x"),
                              ("/absolute", "x"), ("nontext", 1), ("unicode.txt", "\u03bb" * 100001),
                              ("nul\x00path", "x"), ("new\nline", "x")]:
            with self.subTest(path=path):
                bad = copy.deepcopy(self.resources)
                bad[-1][SETUP["NAMES"][-1]][path] = content
                self.reject_before_host(bad)

    def test_wrong_declared_name_rejected_before_host(self):
        bad = copy.deepcopy(self.resources)
        bad[-1][SETUP["NAMES"][-1]]["SKILL.md"] = "name: collision"
        self.reject_before_host(bad)

    def test_body_name_cannot_disguise_wrong_frontmatter_identity(self):
        bad = copy.deepcopy(self.resources)
        name = SETUP["NAMES"][-1]
        skill_md = bad[-1][name]["SKILL.md"]
        self.assertIn("\nname: " + name + "\n", skill_md)
        bad[-1][name]["SKILL.md"] = skill_md.replace("\nname: " + name + "\n",
                                                      "\nname: collision\n", 1) + "\nname: " + name + "\n"
        self.reject_before_host(bad)

    def test_missing_license_or_notice_rejected_before_host(self):
        for required in ("SKILL.md", "LICENSE", "NOTICE"):
            with self.subTest(required=required):
                bad = copy.deepcopy(self.resources)
                del bad[-1][SETUP["NAMES"][-1]][required]
                self.reject_before_host(bad)

    def test_late_batch_protected_collision_rejected_before_any_edit(self):
        all_batches = SETUP["bundle_batches"](ROOT, family="all")
        name = SETUP["family_names"]("all")[-1]
        self.assertIn(name, all_batches[-1])
        self.host.skills.origins[name] = "anthropic"
        self.host.skills.files[name, "SKILL.md"] = "protected"
        with self.assertRaisesRegex(ValueError, "Protected skill name collision"):
            SETUP["install"](self.host, all_batches, self.path, family="all", binding=bound("all"))
        self.assertEqual(self.host.skills.files, {(name, "SKILL.md"): "protected"})
        self.assertEqual(self.host.skills.published, [])
        self.assertFalse(self.path.exists())

    def test_staging_mode_is_declared_for_every_selected_skill(self):
        selected = SETUP["family_names"]("all")
        cli_only = SETUP["CLI_ONLY_NAMES"]
        self.assertEqual(len(cli_only), len(set(cli_only)))
        self.assertLessEqual(set(cli_only), set(selected))
        self.assertIn("szl-skill-conformance", cli_only)
        staged = flattened(SETUP["bundle_batches"](ROOT, family="all"))
        for name in selected:
            with self.subTest(skill=name):
                self.assertEqual("kernel.py" in staged[name], name not in cli_only)

    def test_staging_mode_contradictions_refused_before_host(self):
        sharing = SETUP["bundle"](ROOT, family="sharing")
        planted = copy.deepcopy(sharing)
        engine = planted["szl-skill-conformance"]["scripts/conformance.py"]
        planted["szl-skill-conformance"]["kernel.py"] = engine
        with self.assertRaisesRegex(ValueError, "CLI-only skill must not stage a root kernel.py"):
            SETUP["install"](None, planted, self.path, family="sharing", binding=bound("sharing"))
        change = SETUP["bundle"](ROOT, family="change")
        name = "szl-research-change-impact"
        for kernel, message in ((None, "lacks a root kernel.py"),
                                ("def check(document):\n    return document\n", r"exports no szl_\* entrypoint"),
                                ("def szl_plan(:\n", "does not parse")):
            with self.subTest(message=message):
                bad = copy.deepcopy(change)
                if kernel is None:
                    del bad[name]["kernel.py"]
                else:
                    bad[name]["kernel.py"] = kernel
                with self.assertRaisesRegex(ValueError, message):
                    SETUP["install"](None, bad, self.path, family="change", binding=bound("change"))
        self.assertFalse(self.path.exists())

    def test_binding_covers_every_staged_resource_including_legal_files(self):
        binding = bound("all")
        self.assertEqual((binding["schema"], binding["source_commit"], binding["family"]),
                         (SETUP["BINDING_SCHEMA"], REVISION, "all"))
        legal = {name: hashlib.sha256(subprocess.run(["git", "show", REVISION + ":" + name], cwd=ROOT,
                                                     check=True, capture_output=True).stdout).hexdigest()
                 for name in ("LICENSE", "NOTICE")}
        staged = flattened(SETUP["bundle_batches"](ROOT, family="all"))
        self.assertEqual(set(binding["skills"]), set(staged))
        for name, files in staged.items():
            with self.subTest(skill=name):
                expected = binding["skills"][name]
                self.assertEqual(set(expected), set(files))
                self.assertEqual({path: expected[path] for path in legal}, legal)
                for path, content in files.items():
                    self.assertEqual(expected[path], hashlib.sha256(content.encode("utf-8")).hexdigest())
        checked = SETUP["verify_binding"](staged, binding, "all")
        self.assertEqual(checked["status"], "STAGED_RESOURCES_MATCH_REVISION")
        self.assertEqual(checked["resource_sha256"], binding["skills"])
        for revision in ("main", "a" * 39, "A" * 40, None):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                SETUP["revision_binding"](ROOT, revision, "sharing")
        tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
        with self.assertRaisesRegex(ValueError, "commit object"):
            SETUP["revision_binding"](ROOT, tree, "sharing")

    def test_receipt_binds_readback_to_source_revision(self):
        change = SETUP["bundle"](ROOT, family="change")
        result = SETUP["install"](self.host, change, self.path, family="change", binding=bound("change"))
        self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
        source = result["source_binding"]
        self.assertEqual((source["source_commit"], source["status"]),
                         (REVISION, "STAGED_RESOURCES_MATCH_REVISION"))
        name = "szl-research-change-impact"
        self.assertEqual(result["skills"][name]["readback_sha256"], source["resource_sha256"][name])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["source_binding"], source)

    def test_unbound_or_mismatched_sdk_staging_refused_before_host(self):
        for family, name, payload in (("change", "szl-research-change-impact", "kernel.py"),
                                      ("sharing", "szl-skill-conformance", "scripts/conformance.py")):
            resources = SETUP["bundle"](ROOT, family=family)
            binding = bound(family)
            tampered = copy.deepcopy(binding)
            tampered["skills"][name]["SKILL.md"] = "0" * 64

            def mutate(change):
                bad = copy.deepcopy(resources)
                change(bad[name])
                return bad

            def flip(files):
                files[payload] = files[payload].replace("def ", "def  ", 1)

            cases = {
                "missing LICENSE": (mutate(lambda files: files.pop("LICENSE")), binding, "LICENSE"),
                "modified NOTICE": (mutate(lambda files: files.update(NOTICE=files["NOTICE"] + "\n")),
                                    binding, "differs from the bound revision: " + name + "/NOTICE"),
                "unexpected resource": (mutate(lambda files: files.update({"scripts/extra.py": "print(1)\n"})),
                                        binding, "not bound to the revision: " + name + "/scripts/extra.py"),
                "mutated payload byte": (mutate(flip), binding,
                                         "differs from the bound revision: " + name + "/" + payload),
                "no binding": (resources, None, "revision_binding"),
                "other family binding": (resources, bound("design"), "selected family"),
                "tampered binding digest": (resources, tampered, "differs from the bound revision"),
            }
            for label, (staged, used, message) in cases.items():
                with self.subTest(family=family, case=label):
                    with self.assertRaisesRegex(ValueError, message):
                        SETUP["install"](None, staged, self.path, family=family, binding=used)
                    self.assertFalse(self.path.exists())
            missing = mutate(lambda files: files.pop("LICENSE"))
            with self.assertRaisesRegex(ValueError, "Bound resource missing from staging: " + name + "/LICENSE"):
                SETUP["verify_binding"](missing, binding, family)

    def test_zip_round_trip_into_sdk_staging(self):
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        names = {"szl-research-change-impact": ("change", "kernel.py"),
                 "szl-skill-conformance": ("sharing", "scripts/conformance.py")}
        with tempfile.TemporaryDirectory() as temp:
            archives = pathlib.Path(temp) / "archives"
            package["package_skills"](archives, REVISION, manifest=True)
            self.assertEqual(package["verify_archives"](archives)["status"], "ARCHIVES_MATCH_MANIFEST")
            for name, (family, payload) in names.items():
                mutations = {
                    "missing LICENSE": (lambda files: files.pop("LICENSE"), "LICENSE"),
                    "modified NOTICE": (lambda files: files.update(NOTICE=files["NOTICE"] + " "),
                                        "differs from the bound revision: " + name + "/NOTICE"),
                    "unexpected resource": (lambda files: files.update({"assets/extra.txt": "x"}),
                                            "not bound to the revision: " + name + "/assets/extra.txt"),
                    "mutated payload byte": (lambda files: files.update(
                        {payload: files[payload].replace("def ", "def  ", 1)}),
                        "differs from the bound revision: " + name + "/" + payload),
                }
                with zipfile.ZipFile(archives / (name + ".zip")) as archive:
                    staged = {name: {info.filename[len(name) + 1:]: archive.read(info).decode("utf-8")
                                     for info in archive.infolist()}}
                with self.subTest(skill=name):
                    result = SETUP["install"](self.host, staged, self.path.with_name(family + "-zip.json"),
                                              family=family, binding=bound(family))
                    self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
                    self.assertEqual(result["skills"][name]["readback_sha256"], bound(family)["skills"][name])
                for label, (change, message) in mutations.items():
                    bad = copy.deepcopy(staged)
                    change(bad[name])
                    receipt = self.path.with_name(family + "-" + label.replace(" ", "-") + ".json")
                    with self.subTest(skill=name, case=label):
                        with self.assertRaisesRegex(ValueError, message):
                            SETUP["install"](None, bad, receipt, family=family, binding=bound(family))
                        self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
