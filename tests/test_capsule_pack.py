"""Deterministic capsule pack and fail-closed unpack-verify; synthetic temporary files only."""
# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import pathlib
import runpy
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-reproducibility-capsule"
PACK = SKILL / "scripts" / "pack.py"
RUN = SKILL / "scripts" / "run.py"
# Reports produced by main (b8e259c) for the unchanged examples, LF-normalized stdout bytes.
MAIN_REPORTS = {
    "example.json": ("b19d684d2892b05483d7bf4552e869db4e11270f036a99173b97d800ecff4815",
                     "99f8eb31232019a2682739f40628c359a23f201b988c63e0330a294d6eb26a09"),
    "replay-example.json": ("d3e37a8a26f56c1d036fb917189348758596a417c55dd96eada9baaf5058b4e6",
                            "03ee46bf9d237256f7b6dca8ee83e4e46f3bdf3bd211569fc06df547191e4b9b"),
}
# Archive of assets/pack-match.json; measured identical on CPython 3.12 Windows and Linux zipfile.
PACK_MATCH_ARCHIVE_SHA256 = "01ad5cec7a248d3bb80da2bab84ba17fa5a8934176e83971f9b408dbc0a2d4a2"


def cli(*args, cwd=None):
    process = subprocess.run([sys.executable, "-B", *map(str, args)], capture_output=True, cwd=cwd)
    return process.returncode, process.stdout.replace(b"\r\n", b"\n"), process.stderr


def write_zip(path, members):
    """Write members as (name, data[, external_mode]) with raw, unsanitized names."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w") as archive:
            for item in members:
                name, data = item[0], item[1]
                info = zipfile.ZipInfo("placeholder", date_time=(1980, 1, 1, 0, 0, 0))
                info.filename = name
                info.create_system = 3
                info.external_attr = (item[2] if len(item) > 2 else 0o100644) << 16
                archive.writestr(info, data)


class CapsulePackTests(unittest.TestCase):
    def setUp(self):
        self.mod = runpy.run_path(str(PACK))
        self.kernel = self.mod["K"]
        self.tmp = tempfile.TemporaryDirectory(prefix="szl-capsule-pack-")
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.root = self.base / "project"
        (self.root / "data").mkdir(parents=True)
        self.contents = {"data/input.csv": b"x,y\n1,2\n", "analysis.py": b"raise RuntimeError('never executed')\n",
                         "result.txt": b"SYNTHETIC result\n"}
        for name, data in self.contents.items():
            (self.root / name).write_bytes(data)
        self.capsule = self.kernel["szl_make_capsule"](str(self.root), sorted(self.contents), {"evidence_class": "SYNTHETIC"})
        self.capsule_path = self.base / "capsule.json"
        self.capsule_path.write_text(json.dumps(self.capsule, indent=2), encoding="utf-8")

    def pack(self, name="out.zip", capsule_path=None, root=None):
        output = self.base / name
        code, out, err = cli(PACK, "pack", capsule_path or self.capsule_path, "--root", root or self.root, "--output", output)
        return code, json.loads(out), output

    def unpack(self, archive, dest="restored"):
        code, out, err = cli(PACK, "unpack-verify", archive, "--dest", self.base / dest)
        return code, json.loads(out), self.base / dest

    def embedded(self, capsule=None):
        return (json.dumps(capsule or self.capsule, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()

    def test_pack_twice_is_byte_identical_with_fixed_member_metadata(self):
        code, first, a = self.pack("a.zip")
        self.assertEqual((code, first["status"], first["reason"]), (0, "MATCH", None))
        compact = self.base / "compact.json"
        compact.write_text(json.dumps(self.capsule, separators=(",", ":")), encoding="utf-8")
        code, second, b = self.pack("b.zip", capsule_path=compact)
        self.assertEqual(code, 0)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(first["archive_sha256"], hashlib.sha256(a.read_bytes()).hexdigest())
        self.assertEqual(first["archive_sha256"], second["archive_sha256"])
        with zipfile.ZipFile(a) as archive:
            infos = archive.infolist()
            self.assertEqual([i.filename for i in infos], sorted(list(self.contents) + ["capsule.json"]))
            for info in infos:
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.external_attr >> 16, 0o100644)
                self.assertEqual((info.compress_type, info.create_system), (zipfile.ZIP_STORED, 3))
            self.assertEqual(archive.read("capsule.json"), self.embedded())
            for name, data in self.contents.items():
                self.assertEqual(archive.read(name), data)
        self.assertFalse(first["signed"] or first["authentic"])
        self.assertEqual(first["execution"], "NOT_RUN")

    def test_bundled_positive_and_negative_fixtures(self):
        code, out, _ = cli(PACK, "pack", "assets/pack-match.json", "--output", self.base / "fixture.zip", cwd=SKILL)
        report = json.loads(out)
        self.assertEqual((code, report["status"]), (0, "MATCH"))
        self.assertEqual(report["archive_sha256"], PACK_MATCH_ARCHIVE_SHA256)
        code, out, _ = cli(PACK, "pack", "assets/pack-mismatch.json", "--output", self.base / "refused.zip", cwd=SKILL)
        report = json.loads(out)
        self.assertEqual((code, report["status"]), (1, "REFUSED"))
        self.assertEqual(report["verification"]["files"][0]["status"], "CHANGED")
        self.assertFalse((self.base / "refused.zip").exists())
        code, report, dest = self.unpack(self.base / "fixture.zip")
        self.assertEqual((code, report["status"]), (0, "MATCH"))
        self.assertEqual((dest / "assets" / "protocol.txt").read_bytes(), (SKILL / "assets" / "protocol.txt").read_bytes())

    def test_pack_refuses_existing_output_and_any_non_match(self):
        existing = self.base / "exists.zip"
        existing.write_bytes(b"retained")
        code, report, _ = self.pack("exists.zip")
        self.assertEqual((code, report["status"]), (1, "REFUSED"))
        self.assertEqual(existing.read_bytes(), b"retained")
        (self.root / "result.txt").write_bytes(b"changed\n")
        code, report, output = self.pack("changed.zip")
        self.assertEqual((code, report["status"], report["verification"]["integrity"]), (1, "REFUSED", "MISMATCH"))
        self.assertFalse(output.exists())
        (self.root / "result.txt").unlink()
        self.assertEqual(self.pack("missing.zip")[1]["status"], "REFUSED")
        tampered = dict(self.capsule, metadata={"evidence_class": "EDITED"})
        self.capsule_path.write_text(json.dumps(tampered), encoding="utf-8")
        code, report, _ = self.pack("manifest.zip")
        self.assertEqual((code, report["verification"]["integrity"]), (1, "MANIFEST_CHANGED"))

    def test_pack_binds_bytes_read_after_verification(self):
        (self.root / "result.txt").write_bytes(b"swapped after verification\n")
        self.kernel["szl_verify_capsule"] = lambda root, capsule: {"integrity": "MATCH"}
        report = self.mod["pack"](self.capsule, self.root, self.base / "race.zip")
        self.assertEqual(report["status"], "REFUSED")
        self.assertIn("Changed after verification", report["reason"])
        self.assertFalse((self.base / "race.zip").exists())

    def test_pack_refuses_reserved_and_nonportable_names(self):
        (self.root / "capsule.json").write_bytes(b"{}")
        capsule = self.kernel["szl_make_capsule"](str(self.root), ["capsule.json", "result.txt"])
        report = self.mod["pack"](capsule, self.root, self.base / "reserved.zip")
        self.assertEqual(report["status"], "REFUSED")
        self.assertFalse((self.base / "reserved.zip").exists())
        for name in ("aux.txt", "data/NUL", "com1.log", "trail.", "space ", "q?.txt", 'a"b', "a|b"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.mod["name_problem"](name))
        for name in ("data/input.csv", "auxiliary.txt", "notes.v2.txt"):
            with self.subTest(name=name):
                self.assertIsNone(self.mod["name_problem"](name))

    def test_pack_input_rejects_duplicate_keys_nonfinite_and_non_objects_with_exit_2(self):
        for text in ('{"schema": "a", "schema": "b"}', '{"files": NaN}', '{"files": Infinity}', "[]", "{"):
            with self.subTest(text=text):
                self.capsule_path.write_text(text, encoding="utf-8")
                code, report, output = self.pack("strict.zip")
                self.assertEqual((code, report["status"]), (2, "REFUSED"))
                self.assertTrue(report["reason"].startswith("Strict JSON input: "))
                self.assertFalse(output.exists())

    def test_unpack_roundtrip_restores_exact_bytes_into_a_new_directory(self):
        _, _, archive = self.pack()
        code, report, dest = self.unpack(archive)
        self.assertEqual((code, report["status"], report["extra_members"]), (0, "MATCH", []))
        self.assertTrue(report["destination_created"])
        self.assertEqual(report["verification"]["integrity"], "MATCH")
        for name, data in self.contents.items():
            self.assertEqual((dest / name).read_bytes(), data)
        self.assertFalse((dest / "capsule.json").exists())
        code, report, _ = self.unpack(archive)
        self.assertEqual((code, report["status"], report["destination_created"]), (1, "REFUSED", False))

    def test_tampered_member_is_mismatch(self):
        members = [(name, data) for name, data in sorted(self.contents.items())]
        members[0] = (members[0][0], b"TAMPERED")
        write_zip(self.base / "tampered.zip", members + [("capsule.json", self.embedded())])
        code, report, _ = self.unpack(self.base / "tampered.zip")
        self.assertEqual((code, report["status"]), (1, "MISMATCH"))
        statuses = {item["path"]: item["status"] for item in report["verification"]["files"]}
        self.assertEqual(statuses[members[0][0]], "CHANGED")
        write_zip(self.base / "short.zip", members[1:] + [("capsule.json", self.embedded())])
        code, report, _ = self.unpack(self.base / "short.zip", "short")
        self.assertEqual(report["status"], "MISMATCH")
        self.assertIn("MISSING", {item["status"] for item in report["verification"]["files"]})

    def test_extra_member_is_reported_and_not_extracted(self):
        members = sorted(self.contents.items()) + [("capsule.json", self.embedded()), ("notes/extra.txt", b"unlisted")]
        write_zip(self.base / "extra.zip", members)
        code, report, dest = self.unpack(self.base / "extra.zip")
        self.assertEqual((code, report["status"], report["extra_members"]), (1, "EXTRA_MEMBERS", ["notes/extra.txt"]))
        self.assertEqual(report["verification"]["integrity"], "MATCH")
        self.assertFalse((dest / "notes").exists())
        members[0] = (members[0][0], b"TAMPERED")
        write_zip(self.base / "both.zip", members)
        self.assertEqual(self.unpack(self.base / "both.zip", "both")[1]["status"], "MISMATCH")

    def test_zip_slip_and_unsafe_member_names_are_refused_before_writing(self):
        unsafe = ["../escape.txt", "data/../../escape.txt", "/absolute.txt", "C:/drive.txt", "C:drive.txt",
                  "data\\..\\..\\escape.txt", "a\\b.txt", "./result.txt", "data//input.csv", "data/",
                  "nul\x00.txt", "aux.txt", "trail.", ".env", "q?.txt"]
        for index, name in enumerate(unsafe):
            with self.subTest(name=name):
                archive = self.base / ("slip%d.zip" % index)
                write_zip(archive, [("capsule.json", self.embedded()), (name, b"payload")])
                code, report, dest = self.unpack(archive, "slip%d" % index)
                self.assertEqual((code, report["status"], report["destination_created"]), (1, "REFUSED", False))
                self.assertFalse(dest.exists())
        self.assertFalse((self.base / "escape.txt").exists())
        self.assertFalse(pathlib.Path(self.base.anchor, "absolute.txt").exists())

    def test_duplicate_case_colliding_symlink_and_oversize_members_are_refused(self):
        cases = {
            "duplicate": [("capsule.json", self.embedded()), ("result.txt", b"a"), ("result.txt", b"b")],
            "case": [("capsule.json", self.embedded()), ("result.txt", b"a"), ("RESULT.txt", b"b")],
            "embedded-case": [("capsule.json", self.embedded()), ("Capsule.JSON", b"{}")],
            "symlink": [("capsule.json", self.embedded()), ("result.txt", b"/etc/passwd", stat.S_IFLNK | 0o777)],
            "directory": [("capsule.json", self.embedded()), ("result.txt", b"", stat.S_IFDIR | 0o755)],
            "missing-capsule": [("result.txt", self.contents["result.txt"])],
        }
        for label, members in cases.items():
            with self.subTest(label=label):
                write_zip(self.base / (label + ".zip"), members)
                code, report, dest = self.unpack(self.base / (label + ".zip"), label)
                self.assertEqual((code, report["status"]), (1, "REFUSED"))
                self.assertFalse(dest.exists())
        with zipfile.ZipFile(self.base / "big.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("capsule.json", self.embedded())
            archive.writestr("result.txt", bytes(8 * 1024 * 1024 + 1))
        with zipfile.ZipFile(self.base / "big-capsule.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("capsule.json", b" " * (1024 * 1024 + 1))
        for label in ("big", "big-capsule"):
            with self.subTest(label=label):
                code, report, dest = self.unpack(self.base / (label + ".zip"), label)
                self.assertEqual((code, report["status"]), (1, "REFUSED"))
                self.assertIn("Oversize", report["reason"])
                self.assertFalse(dest.exists())

    def test_embedded_capsule_is_strict_and_self_digest_bound(self):
        for label, text in (("dup", b'{"schema": 1, "schema": 2}'), ("nan", b'{"files": NaN}')):
            with self.subTest(label=label):
                write_zip(self.base / (label + ".zip"), [("capsule.json", text)])
                code, report, dest = self.unpack(self.base / (label + ".zip"), label)
                self.assertEqual((code, report["status"]), (2, "REFUSED"))
                self.assertFalse(dest.exists())
        edited = dict(self.capsule, metadata={"evidence_class": "EDITED"})
        write_zip(self.base / "edited.zip", sorted(self.contents.items()) + [("capsule.json", self.embedded(edited))])
        code, report, dest = self.unpack(self.base / "edited.zip", "edited")
        self.assertEqual((code, report["status"], report["verification"]["integrity"]), (1, "MISMATCH", "MANIFEST_CHANGED"))
        self.assertFalse(dest.exists())
        (self.base / "not-a-zip.zip").write_bytes(b"not a zip archive")
        code, report, dest = self.unpack(self.base / "not-a-zip.zip", "not-a-zip")
        self.assertEqual((code, report["status"]), (1, "REFUSED"))
        self.assertFalse(dest.exists())

    def test_existing_examples_report_exactly_as_on_main(self):
        for name, (made_digest, verified_digest) in MAIN_REPORTS.items():
            with self.subTest(example=name):
                code, made, err = cli(RUN, "assets/" + name, "--root", ".", cwd=SKILL)
                self.assertEqual(code, 0, err)
                self.assertEqual(hashlib.sha256(made).hexdigest(), made_digest)
                capsule = self.base / ("made-" + name)
                capsule.write_bytes(made)
                code, verified, err = cli(RUN, capsule, "--root", ".", cwd=SKILL)
                self.assertEqual(code, 0, err)
                self.assertEqual(hashlib.sha256(verified).hexdigest(), verified_digest)


if __name__ == "__main__":
    unittest.main()
