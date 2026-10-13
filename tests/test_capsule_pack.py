"""Deterministic capsule pack and fail-closed unpack-verify; synthetic temporary files only."""
# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import os
import pathlib
import runpy
import stat
import struct
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
# Archive of assets/pack-match.json (compact embedded capsule.json); measured identical with
# CPython 3.12.10 and 3.11.9 on Windows and CPython 3.12.3 on Ubuntu (WSL).
PACK_MATCH_ARCHIVE_SHA256 = "12ed91e91cfcee64c1bba38d04203f64703953e9ded7288a17b9bd31b12b2502"
MIB = 1 << 20


def cli(*args, cwd=None, env=None):
    process = subprocess.run([sys.executable, "-B", *map(str, args)], capture_output=True, cwd=cwd, env=env)
    return process.returncode, process.stdout.replace(b"\r\n", b"\n"), process.stderr


def write_zip(path, members, compression=zipfile.ZIP_STORED):
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
                info.compress_type = compression
                archive.writestr(info, data)


def set_central_field(path, name, offset, fmt, *values):
    """Patch one field of NAME's central-directory record (zipfile resets some fields when writing)."""
    raw, encoded = bytearray(path.read_bytes()), name.encode()
    start = raw.find(b"PK\x01\x02")
    while start >= 0:
        if raw[start + 46:start + 46 + struct.unpack_from("<H", raw, start + 28)[0]] == encoded:
            struct.pack_into(fmt, raw, start + offset, *values)
            path.write_bytes(bytes(raw))
            return
        start = raw.find(b"PK\x01\x02", start + 4)
    raise AssertionError("central record not found: " + name)


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
        text = json.dumps(capsule or self.capsule, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        return (text + "\n").encode()

    def forged(self, paths, data=b"SYNTHETIC result\n", **fields):
        """A capsule with a valid self-digest that szl_make_capsule would never produce."""
        body = dict({key: value for key, value in self.capsule.items() if key != "capsule_sha256"}, **fields)
        body["files"] = [{"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                         for path in paths]
        return dict(body, capsule_sha256=self.kernel["szl_capsule_digest"](body))

    def assert_refused_before_writing(self, archive, dest, fragment=None):
        code, report, dest = self.unpack(archive, dest)
        self.assertEqual((code, report["status"], report["destination_created"]), (1, "REFUSED", False), report)
        self.assertFalse(dest.exists())
        if fragment:
            self.assertIn(fragment, report["reason"])
        return report

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
        for name in ("aux.txt", "data/NUL", "com1.log", "trail.", "space ", "q?.txt", 'a"b', "a|b", "LONGRE~1.TXT",
                     "data~2/x.csv", "com0", "COM\u00b9.txt", "lpt\u00b2", "LPT\u00b3.log", "CONIN$", "conout$.txt",
                     "Conin$ .x", "a" * 252 + ".txt", "\u30c7" * 86, "data/" + "\u30c7" * 200 + ".txt"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.mod["name_problem"](name))
        # Segments of at most 255 UTF-8 bytes (Linux NAME_MAX; never more than 255 UTF-16 units on Windows).
        for name in ("data/input.csv", "auxiliary.txt", "notes.v2.txt", "backup~.txt", "a~b.txt", "com10.txt",
                     "conin.txt", "console.log", "lpt$.txt", "a" * 251 + ".txt", "\u30c7" * 85, "d/" + "e" * 255):
            with self.subTest(name=name):
                self.assertIsNone(self.mod["name_problem"](name))

    def test_pack_applies_portability_and_collision_rules_to_declared_paths(self):
        # Verification is stubbed to MATCH so the path rules run on every host, including Windows,
        # where files such as aux.txt or trail. cannot be created to begin with.
        self.kernel["szl_verify_capsule"] = lambda root, capsule: {"integrity": "MATCH"}
        cases = {"device": ["aux.txt"], "trailing-dot": ["trail."], "short-name": ["LONGRE~1.TXT"],
                 "case": ["result.txt", "RESULT.txt"], "dot-alias": ["result.txt", "result.txt."],
                 "file-directory": ["Data/x.txt", "data"], "reserved": ["Capsule.JSON"],
                 "under-reserved": ["capsule.json/x.txt"], "superscript-device": ["COM\u00b9.txt"],
                 "console": ["data/CONOUT$"], "long-segment": ["\u30c7" * 200 + ".txt"]}
        for label, paths in cases.items():
            with self.subTest(label=label):
                output = self.base / (label + ".zip")
                report = self.mod["pack"](self.forged(paths), self.root, output)
                self.assertEqual(report["status"], "REFUSED")
                self.assertTrue(report["reason"].startswith("Path "), report["reason"])
                self.assertFalse(output.exists())

    def test_pack_input_rejects_duplicate_keys_nonfinite_and_non_objects_with_exit_2(self):
        for text in ('{"schema": "a", "schema": "b"}', '{"files": NaN}', '{"files": Infinity}', '{"files": -Infinity}',
                     '{"metadata": {"x": 1e400}}', '{"metadata": {"x": -1E+999}}', "[]", "{"):
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
                  "nul\x00.txt", "aux.txt", "trail.", ".env", "q?.txt",
                  # zipfile cuts names at NUL on every host; only the renamed-by-decoding check refuses these.
                  "result.txt\x00.sh", "x\x00.txt", "LPT\u00b3.log", "conin$", "a" * 252 + ".txt"]
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
            "under-embedded": [("capsule.json", self.embedded()), ("capsule.json/x.txt", b"a")],
            "file-directory": [("capsule.json", self.embedded()), ("Data/x.txt", b"a"), ("data", b"b")],
            "file-directory-exact": [("capsule.json", self.embedded()), ("data", b"b"), ("data/x.txt", b"a")],
            "short-name": [("capsule.json", self.embedded()), ("LONGRE~1.TXT", b"a")],
            "symlink": [("capsule.json", self.embedded()), ("result.txt", b"/etc/passwd", stat.S_IFLNK | 0o777)],
            "directory": [("capsule.json", self.embedded()), ("result.txt", b"", stat.S_IFDIR | 0o755)],
            "missing-capsule": [("result.txt", self.contents["result.txt"])],
        }
        for label, members in cases.items():
            with self.subTest(label=label):
                write_zip(self.base / (label + ".zip"), members)
                self.assert_refused_before_writing(self.base / (label + ".zip"), label)
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
        for label, text in (("dup", b'{"schema": 1, "schema": 2}'), ("nan", b'{"files": NaN}'),
                            ("overflow", b'{"metadata": {"x": 1e400}}')):
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

    def test_unpack_applies_pack_path_rules_to_declared_paths_before_writing(self):
        # One member can satisfy two declared paths on Windows (case folding, trailing-dot stripping,
        # 8.3 short names) while Linux reports MISSING; such manifests are refused on every host.
        cases = {"case": ["result.txt", "RESULT.txt"], "dot-alias": ["result.txt", "result.txt."],
                 "short-name": ["result.txt", "RESULT~1.TXT"], "traversal": ["../escape.txt"],
                 "device": ["result.txt", "aux.txt"], "file-directory": ["result.txt", "Result.txt/x"],
                 "reserved": ["result.txt", "capsule.json"], "reserved-case": ["result.txt", "Capsule.JSON"],
                 "superscript-device": ["result.txt", "com\u00b2.txt"], "console": ["result.txt", "CONIN$"],
                 "long-segment": ["result.txt", "\u30c7" * 200 + ".txt"]}
        for label, paths in cases.items():
            with self.subTest(label=label):
                archive = self.base / ("declared-%s.zip" % label)
                capsule = self.forged(paths)
                self.assertEqual(self.kernel["szl_verify_capsule"](str(self.base / "absent"), capsule)["integrity"],
                                 "MISMATCH")  # well-formed with a valid self-digest; only the path rules refuse it
                write_zip(archive, [("capsule.json", self.embedded(capsule)), ("result.txt", b"SYNTHETIC result\n")])
                report = self.assert_refused_before_writing(archive, "declared-" + label, "Declared path ")
                self.assertEqual(report["archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertFalse((self.base / "escape.txt").exists())

    def test_encrypted_unsupported_and_directory_attribute_members_are_refused(self):
        members = sorted(self.contents.items()) + [("capsule.json", self.embedded())]
        write_zip(self.base / "encrypted.zip", members)
        set_central_field(self.base / "encrypted.zip", "result.txt", 8, "<H", 0x1)
        write_zip(self.base / "bzip2.zip", members, compression=zipfile.ZIP_BZIP2)
        write_zip(self.base / "dos-directory.zip", members)
        set_central_field(self.base / "dos-directory.zip", "result.txt", 38, "<I", 0x10)  # external attributes
        for label in ("encrypted", "bzip2", "dos-directory"):
            with self.subTest(label=label):
                self.assert_refused_before_writing(self.base / (label + ".zip"), label,
                                                   "encrypted, unsupported or non-regular")
        write_zip(self.base / "plain.zip", members)  # the same members without the patch restore cleanly
        self.assertEqual(self.unpack(self.base / "plain.zip", "plain")[1]["status"], "MATCH")

    def test_zip64_offset_overflow_and_unreadable_members_are_refused_before_writing(self):
        # A ZIP64 header offset of 2**63 or more makes zipfile's seek raise OverflowError, and a bad
        # CRC raises only when the member is read. Both must give a JSON REFUSED report, no traceback
        # and no destination, also when the member is a declared file (not only capsule.json).
        members = sorted(self.contents.items()) + [("capsule.json", self.embedded())]
        cases = [(target, offset) for target in ("capsule.json", "result.txt") for offset in (2**63 + 5, 2**64 - 2)]
        for index, (target, offset) in enumerate(cases + [("result.txt", None)]):
            with self.subTest(target=target, offset=offset):
                archive, dest = self.base / ("unreadable%d.zip" % index), self.base / ("unreadable%d" % index)
                with zipfile.ZipFile(archive, "w") as out:
                    for name, data in members:
                        info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                        info.extra = struct.pack("<HHQ", 0xCAFE, 8, 0) if name == target else b""
                        out.writestr(info, data)
                if offset is None:
                    set_central_field(archive, target, 16, "<I", 0)  # CRC-32 that no data matches
                else:  # header offset 0xFFFFFFFF defers to the ZIP64 extra (tag 1) that replaces the placeholder
                    set_central_field(archive, target, 42, "<I", 0xFFFFFFFF)
                    set_central_field(archive, target, 46 + len(target), "<HHQ", 1, 8, offset)
                code, out, err = cli(PACK, "unpack-verify", archive, "--dest", dest)
                self.assertEqual(err, b"")
                report = json.loads(out)
                self.assertEqual((code, report["status"], report["destination_created"]), (1, "REFUSED", False), report)
                self.assertFalse(dest.exists())

    def test_member_count_total_and_archive_size_limits(self):
        members = sorted(self.contents.items()) + [("capsule.json", self.embedded())]
        extras = [("extra/%03d.txt" % index, b"x") for index in range(125)]  # 129 members in all
        write_zip(self.base / "count-129.zip", members + extras)
        code, report, _ = self.unpack(self.base / "count-129.zip", "count-129")
        self.assertEqual((code, report["status"], len(report["extra_members"])), (1, "EXTRA_MEMBERS", 125))
        write_zip(self.base / "count-130.zip", members + extras + [("extra/125.txt", b"x")])
        self.assert_refused_before_writing(self.base / "count-130.zip", "count-130", "Oversize")
        zeros = bytes(8 * MIB)  # each member is within 8 MiB; together they exceed 33 MiB
        write_zip(self.base / "total.zip", members + [("big/%d.bin" % index, zeros) for index in range(5)],
                  compression=zipfile.ZIP_DEFLATED)
        self.assertLess((self.base / "total.zip").stat().st_size, MIB)
        self.assert_refused_before_writing(self.base / "total.zip", "total", "Oversize")
        with open(self.base / "huge.zip", "wb") as handle:
            handle.truncate(34 * MIB + 1)
        report = self.assert_refused_before_writing(self.base / "huge.zip", "huge", "Archive exceeds 34 MiB")
        self.assertIsNone(report["archive_sha256"])  # no digest of a truncated prefix is reported

    def test_embedded_capsule_is_compact_and_pack_enforces_unpack_limits(self):
        body = {key: value for key, value in self.capsule.items() if key != "capsule_sha256"}
        nested = [0] * 8000
        for _ in range(100):
            nested = [nested]
        deep = dict(body, scope=nested)
        deep["capsule_sha256"] = self.kernel["szl_capsule_digest"](deep)
        self.assertGreater(len(json.dumps(deep, indent=2)), MIB)  # an indented copy would exceed the 1 MiB limit
        self.capsule_path.write_text(json.dumps(deep), encoding="utf-8")
        code, report, archive = self.pack("deep.zip")
        self.assertEqual((code, report["status"]), (0, "MATCH"))
        with zipfile.ZipFile(archive) as opened:
            self.assertEqual(opened.read("capsule.json"), self.embedded(deep))
            self.assertLess(opened.getinfo("capsule.json").file_size, 64 * 1024)
        self.assertEqual(self.unpack(archive, "deep")[1]["status"], "MATCH")
        # Under 1 MiB of input, but each 1e15 literal re-serializes as 1000000000000000.0.
        floats = dict(body, scope=[1e15] * 200_000)
        digest = self.kernel["szl_capsule_digest"](floats)
        text = json.dumps(dict(body, scope="@", capsule_sha256=digest))
        text = text.replace('"@"', "[" + ",".join(["1e15"] * 200_000) + "]")
        self.assertLess(len(text), MIB)
        self.capsule_path.write_text(text, encoding="utf-8")
        code, report, output = self.pack("floats.zip")
        self.assertEqual((code, report["status"], report["verification"]["integrity"]), (1, "REFUSED", "MATCH"))
        self.assertIn("Embedded capsule over 1 MiB", report["reason"])
        self.assertFalse(output.exists())

    def test_cli_reports_non_ascii_names_without_utf8_mode(self):
        name = "\u30c7\u30fc\u30bf.csv"
        (self.root / name).write_bytes(b"a,b\n")
        capsule = self.kernel["szl_make_capsule"](str(self.root), [name])
        self.capsule_path.write_text(json.dumps(capsule), encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        output = self.base / "unicode.zip"
        code, out, err = cli(PACK, "pack", self.capsule_path, "--root", self.root, "--output", output, env=env)
        self.assertEqual(code, 0, err)
        self.assertTrue(out.isascii())
        self.assertEqual(json.loads(out)["members"], ["capsule.json", name])
        code, out, err = cli(PACK, "unpack-verify", output, "--dest", self.base / "unicode", env=env)
        self.assertEqual((code, json.loads(out)["status"]), (0, "MATCH"), err)
        self.assertEqual((self.base / "unicode" / name).read_bytes(), b"a,b\n")

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
