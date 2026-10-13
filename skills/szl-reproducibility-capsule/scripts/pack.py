#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Original 2026-10-06 SZL contribution (base b8e259c); see references/provenance.md.
"""Deterministic capsule ZIP pack and fail-closed unpack-verify; nothing is executed.
Exit 0 MATCH, 1 MISMATCH/EXTRA_MEMBERS/REFUSED, 2 usage or strict-JSON input error."""
import argparse, hashlib, io, json, math, pathlib, re, runpy, stat, sys, zipfile, zlib

K = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1] / "kernel.py"))
EMBED, MIB, INPUT = "capsule.json", 1 << 20, "Strict JSON input: "
UNPORTABLE = re.compile(r'(?i)[<>"|?*]|~\d|[. ]$|^(con|prn|aux|nul|com\d|lpt\d) *(\.|$)')  # per segment; ~N: 8.3 alias
FAIL = (ValueError, TypeError, KeyError, OSError, RuntimeError, EOFError, zipfile.BadZipFile, zlib.error)


def strict_json(raw):
    def pairs(items):
        if len(dict(items)) != len(items):
            raise ValueError("duplicate key")
        return dict(items)

    def finite(token):  # also 1e400
        if math.isfinite(value := float(token)):
            return value
        raise ValueError("non-finite " + token)
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=finite, parse_float=finite)
    except (ValueError, RecursionError) as error:
        raise ValueError(INPUT + str(error)) from None
    if not isinstance(value, dict):
        raise ValueError(INPUT + "not an object")
    return value


def name_problem(name):
    try:
        K["szl_capsule_path"](name)
    except (ValueError, TypeError) as error:
        return str(error)
    return "Not portable to Windows" if any(UNPORTABLE.search(part) for part in name.split("/")) else None


def check_paths(paths, label):
    folded = [path.casefold() for path in paths]
    parents = {path[:i] for path in folded for i, char in enumerate(path) if char == "/"}
    for path, key in zip(paths, folded):
        problem = name_problem(path) or ("file or directory clash ignoring case"
                                         if key in parents or folded.count(key) > 1 else None)
        if problem:
            raise ValueError("%s %r: %s" % (label, path, problem))


def report(kind, status, reason=None, **fields):
    return dict(schema="szl.capsule-%s.v1" % kind, status=status, reason=reason, signed=False, authentic=False,
                execution="NOT_RUN", scientific_performance="NOT_MEASURED", **fields)


def pack(capsule, root, output):
    out, verdict = pathlib.Path(output), None
    try:
        if out.exists() or out.is_symlink():
            raise ValueError("Output exists")
        verdict = K["szl_verify_capsule"](str(root), capsule)
        if verdict["integrity"] != "MATCH":
            raise ValueError("Source integrity " + verdict["integrity"])
        check_paths([entry["path"] for entry in capsule["files"]] + [EMBED], "Path")
        members = {EMBED: (json.dumps(capsule, separators=(",", ":"), sort_keys=True, ensure_ascii=False) + "\n").encode()}
        for entry in capsule["files"]:
            K["szl_capsule_file"](str(root), entry["path"])  # repeat the symlink/reparse/containment walk
            with pathlib.Path(root).resolve().joinpath(*entry["path"].split("/")).open("rb") as source:
                data = source.read(8 * MIB + 1)
            if [len(data), hashlib.sha256(data).hexdigest()] != [entry["size_bytes"], entry["sha256"]]:
                raise ValueError("Changed after verification: " + entry["path"])
            members[entry["path"]] = data
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in sorted(members):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_STORED, 3, 0o100644 << 16
                archive.writestr(info, members[name])
        if len(members[EMBED]) > MIB or len(buffer.getvalue()) > 34 * MIB:  # unpack-verify limits
            raise ValueError("Embedded capsule over 1 MiB or archive over 34 MiB")
        with out.open("xb") as target:
            target.write(buffer.getvalue())
    except FAIL as error:
        return report("pack", "REFUSED", str(error), verification=verdict)
    return report("pack", "MATCH", archive_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
                  members=sorted(members), verification=verdict)


def unpack_verify(path, dest):
    dest, state = pathlib.Path(dest), {"archive_sha256": None, "destination_created": False}

    def done(status, reason=None, **fields):
        return report("unpack-verify", status, reason, **state, **fields)
    try:
        if dest.exists() or dest.is_symlink() or not dest.parent.is_dir():
            raise ValueError("Destination must be new, inside an existing directory")
        with open(path, "rb") as source:
            raw = source.read(34 * MIB + 1)
        if len(raw) > 34 * MIB:
            raise ValueError("Archive exceeds 34 MiB")
        state["archive_sha256"] = hashlib.sha256(raw).hexdigest()
        archive, total = zipfile.ZipFile(io.BytesIO(raw)), 0
        for count, info in enumerate(archive.infolist(), 1):
            name, mode = info.filename, info.external_attr
            if (name != info.orig_filename or info.flag_bits & 0x41 or mode & 0x10
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or stat.S_IFMT(mode >> 16) not in (0, stat.S_IFREG)):
                raise ValueError("Renamed by decoding, encrypted, unsupported or non-regular member %r" % name)
            total += info.file_size
            if info.file_size > (MIB if name == EMBED else 8 * MIB) or total > 33 * MIB or count > 129:
                raise ValueError("Oversize member, total or count at %r" % name)
        check_paths(archive.namelist(), "Member")
        infos = {info.filename: info for info in archive.infolist()}
        if EMBED not in infos:
            raise ValueError("Missing " + EMBED)
        capsule = strict_json(archive.read(infos[EMBED]))  # bounded by checked header sizes
        pre = K["szl_verify_capsule"](str(dest), capsule)  # dest absent: structure and self-digest only
        if pre["integrity"] == "MANIFEST_CHANGED":
            return done("MISMATCH", "Embedded capsule self-digest disagrees", verification=pre)
        declared = [entry["path"] for entry in capsule["files"]]
        check_paths(declared + [EMBED], "Declared path")
        extras = sorted(set(infos) - set(declared) - {EMBED})
        dest.mkdir()
        state["destination_created"] = True
        for name in sorted(set(declared) & set(infos)):
            target = dest.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve().is_relative_to(dest.resolve()):
                raise ValueError("Member escapes destination: " + name)
            with target.open("xb") as handle:
                handle.write(archive.read(infos[name]))
        verdict = K["szl_verify_capsule"](str(dest), capsule)
    except FAIL as error:
        return done("REFUSED", str(error))
    status = "MISMATCH" if verdict["integrity"] != "MATCH" else "EXTRA_MEMBERS" if extras else "MATCH"
    return done(status, extra_members=extras, verification=verdict)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    packer = commands.add_parser("pack")
    packer.add_argument("capsule", type=pathlib.Path)
    packer.add_argument("--root", default=".")
    packer.add_argument("--output", required=True)
    unpacker = commands.add_parser("unpack-verify")
    unpacker.add_argument("archive")
    unpacker.add_argument("--dest", required=True)
    args = parser.parse_args(argv)
    if args.command == "unpack-verify":
        result = unpack_verify(args.archive, args.dest)
    else:
        try:
            with args.capsule.open("rb") as source:
                raw = source.read(MIB + 1)
            if len(raw) > MIB:
                raise ValueError("capsule exceeds 1 MiB")
            capsule = strict_json(raw)
        except (ValueError, OSError) as error:
            result = report("pack", "REFUSED", INPUT + str(error).removeprefix(INPUT))
        else:
            result = pack(capsule, args.root, args.output)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")  # ASCII: any stdout encoding
    return 0 if result["status"] == "MATCH" else 2 if str(result["reason"]).startswith(INPUT) else 1


if __name__ == "__main__":
    sys.exit(main())
