"""Release ZIP manifests are source-bound and byte-verifiable."""

import hashlib
import json
import os
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


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
            self.assertEqual(record["schema"], "szl.science-package-manifest.v1")
            self.assertEqual(record["source_commit"], revision)
            self.assertEqual(record["archives"], first)
            self.assertTrue(first)
            for archive in record["archives"]:
                path = first_dir / archive["archive"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), archive["sha256"])
                self.assertEqual(path.stat().st_size, archive["compressed_bytes"])
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


if __name__ == "__main__":
    unittest.main()
