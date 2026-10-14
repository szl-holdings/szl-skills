"""Release ZIP manifests are source-bound and byte-verifiable."""

import hashlib
import json
import os
import pathlib
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = "science-package-manifest.json"


def head():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def rewrite(path, skill, change, compression=zipfile.ZIP_DEFLATED):
    """Rebuild one archive in the packager's layout after changing its members."""
    with zipfile.ZipFile(path) as archive:
        members = {info.filename[len(skill) + 1:]: archive.read(info) for info in archive.infolist()}
    change(members)
    path.unlink()
    with zipfile.ZipFile(path, "x", compression=compression) as archive:
        for name, data in sorted(members.items()):
            member = zipfile.ZipInfo(skill + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = compression
            member.external_attr = 0o100644 << 16
            archive.writestr(member, data)


class PackageScienceManifestTests(unittest.TestCase):
    def test_manifest_matches_zip_bytes_and_is_deterministic(self):
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory() as temp:
            first_dir = pathlib.Path(temp) / "first"
            second_dir = pathlib.Path(temp) / "second"
            first = package["package_skills"](first_dir, revision, manifest=True)
            second = package["package_skills"](second_dir, revision, manifest=True)
            self.assertEqual(first, second)
            manifest_name = "science-package-manifest.json"
            first_bytes = (first_dir / manifest_name).read_bytes()
            self.assertEqual(first_bytes, (second_dir / manifest_name).read_bytes())
            record = json.loads(first_bytes)
            self.assertEqual(record["schema"], "szl.science-package-manifest.v2")
            self.assertEqual(record["source_commit"], revision)
            self.assertEqual(record["archives"], first)
            self.assertTrue(first)
            setup = runpy.run_path(str(ROOT / "tools" / "install_claude_science.py"))
            binding = setup["revision_binding"](ROOT, revision, "all")
            self.assertEqual({archive["skill"] for archive in first}, set(binding["skills"]))
            for archive in record["archives"]:
                path = first_dir / archive["archive"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), archive["sha256"])
                self.assertEqual(path.stat().st_size, archive["compressed_bytes"])
                # ZIP members and SDK staging share one revision binding, legal files included.
                self.assertEqual(archive["resources"], binding["skills"][archive["skill"]])
                self.assertLessEqual({"SKILL.md", "LICENSE", "NOTICE"}, set(archive["resources"]))
                with zipfile.ZipFile(path) as opened:
                    self.assertEqual({info.filename: hashlib.sha256(opened.read(info)).hexdigest()
                                      for info in opened.infolist()},
                                     {archive["skill"] + "/" + name: digest
                                      for name, digest in archive["resources"].items()})
            with self.assertRaises(FileExistsError):
                package["package_skills"](first_dir, revision, manifest=True)
            self.assertEqual(first_bytes, (first_dir / manifest_name).read_bytes())

    def test_manifest_requires_full_revision_before_writing(self):
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory() as temp:
            destination = pathlib.Path(temp) / "packages"
            for revision in (None, "main", "a" * 39, tree):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    package["package_skills"](destination, revision, manifest=True)
                self.assertFalse(destination.exists())

    def test_cli_rejects_manifest_without_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = pathlib.Path(temp) / "packages"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "package_science.py"),
                 "--output-dir", str(destination), "--manifest"],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires --revision", result.stderr)
            self.assertFalse(destination.exists())

    def test_git_blob_read_ignores_local_replacement_refs(self):
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        with tempfile.TemporaryDirectory() as temp:
            repo = pathlib.Path(temp) / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
            for value in ("original", "replacement"):
                (repo / "payload.txt").write_text(value, encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "add", "payload.txt"], check=True)
                subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false",
                                "commit", "-qm", value], check=True)
                commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                        check=True, capture_output=True, text=True).stdout.strip()
                if value == "original":
                    original = commit
                else:
                    replacement = commit
            subprocess.run(["git", "-C", str(repo), "replace", original, replacement], check=True)
            replace_env = os.environ.copy()
            replace_env.pop("GIT_NO_REPLACE_OBJECTS", None)
            replaced = subprocess.run(["git", "show", original + ":payload.txt"], cwd=repo,
                                      env=replace_env, check=True, capture_output=True).stdout
            self.assertEqual(replaced, b"replacement")
            package["git_bytes"].__globals__["ROOT"] = repo
            self.assertEqual(package["git_bytes"](original, "payload.txt"), b"original")


class ArchiveRoundTripTests(unittest.TestCase):
    """Packaged archives are refused unless every member matches the bound revision."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.clean = pathlib.Path(cls.temp.name) / "clean"
        package = runpy.run_path(str(ROOT / "tools" / "package_science.py"))
        cls.verify = staticmethod(package["verify_archives"])
        cls.revision = head()
        package["package_skills"](cls.clean, cls.revision, manifest=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def copy(self, label):
        target = pathlib.Path(self.temp.name) / label.replace(" ", "-")
        shutil.copytree(self.clean, target)
        return target

    def test_untouched_archives_verify_against_manifest(self):
        result = self.verify(self.clean)
        record = json.loads((self.clean / MANIFEST).read_bytes())
        self.assertEqual(result, {"status": "ARCHIVES_MATCH_MANIFEST", "source_commit": self.revision,
                                  "archives": len(record["archives"])})
        cli = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "package_science.py"),
                              "--output-dir", str(self.clean), "--verify"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["status"], "ARCHIVES_MATCH_MANIFEST")

    def test_missing_modified_unexpected_or_mutated_members_are_refused(self):
        for skill, payload in (("szl-research-change-impact", "kernel.py"),
                               ("szl-skill-conformance", "scripts/conformance.py")):
            archive = skill + ".zip"

            def flip(members):
                data = bytearray(members[payload])
                data[len(data) // 2] ^= 0x01
                members[payload] = bytes(data)

            cases = {
                "missing LICENSE": (lambda members: members.pop("LICENSE"),
                                    "Bound member missing from " + archive + ": LICENSE"),
                "modified NOTICE": (lambda members: members.update(NOTICE=members["NOTICE"] + b"\n"),
                                    "differs from the bound revision in " + archive + ": NOTICE"),
                "unexpected resource": (lambda members: members.update({"scripts/extra.py": b"print(1)\n"}),
                                        "Unexpected archive member in " + archive + ": scripts/extra.py"),
                "mutated payload byte": (flip, "differs from the bound revision in " + archive + ": " + payload),
            }
            for label, (change, message) in cases.items():
                with self.subTest(skill=skill, case=label):
                    directory = self.copy(skill + "-" + label)
                    rewrite(directory / archive, skill, change)
                    with self.assertRaisesRegex(ValueError, message):
                        self.verify(directory)
                    cli = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "package_science.py"),
                                          "--output-dir", str(directory), "--verify"],
                                         cwd=ROOT, capture_output=True, text=True)
                    self.assertNotEqual(cli.returncode, 0)
                    self.assertIn(message, cli.stderr)

    def test_archive_level_changes_are_refused(self):
        skill = "szl-skill-conformance"
        archive = skill + ".zip"
        reencoded = self.copy("reencoded")
        rewrite(reencoded / archive, skill, lambda members: None, compression=zipfile.ZIP_STORED)
        with self.assertRaisesRegex(ValueError, "Archive bytes differ from the manifest: " + archive):
            self.verify(reencoded)
        corrupt = self.copy("corrupt")
        data = bytearray((corrupt / archive).read_bytes())
        with zipfile.ZipFile(self.clean / archive) as opened:
            first = opened.infolist()[0]
        offset = first.header_offset
        start = (offset + 30 + int.from_bytes(data[offset + 26:offset + 28], "little")
                 + int.from_bytes(data[offset + 28:offset + 30], "little"))
        data[start + first.compress_size // 2] ^= 0xFF
        (corrupt / archive).write_bytes(bytes(data))
        with self.assertRaisesRegex(ValueError, "Corrupt archive " + archive):
            self.verify(corrupt)
        extra = self.copy("extra archive")
        shutil.copyfile(extra / archive, extra / "szl-unlisted.zip")
        with self.assertRaisesRegex(ValueError, "Archive directory differs from the manifest"):
            self.verify(extra)
        missing = self.copy("missing archive")
        (missing / archive).unlink()
        with self.assertRaisesRegex(ValueError, "Archive directory differs from the manifest"):
            self.verify(missing)
        stale = self.copy("v1 manifest")
        record = json.loads((stale / MANIFEST).read_bytes())
        record["schema"] = "szl.science-package-manifest.v1"
        (stale / MANIFEST).write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source-bound"):
            self.verify(stale)


if __name__ == "__main__":
    unittest.main()
