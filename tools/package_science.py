#!/usr/bin/env python3
"""Create individual import ZIPs. Refuses overwrite; includes only explicit skill resources."""
import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import zipfile
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
GIT_ENV = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}
MANIFEST = "science-package-manifest.json"
# v2 binds every archive member, root LICENSE and NOTICE included, to the source commit.
MANIFEST_SCHEMA = "szl.science-package-manifest.v2"


def git_bytes(revision, relative):
    return subprocess.run(["git", "show", revision + ":" + relative], cwd=ROOT,
                          env=GIT_ENV, check=True, capture_output=True).stdout


def package_skills(destination, revision=None, manifest=False):
    if revision is not None and not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Release revision must be a full immutable 40-character commit")
    if revision is not None:
        try:
            object_type = subprocess.run(
                ["git", "cat-file", "-t", revision], cwd=ROOT, check=True,
                capture_output=True, text=True, env=GIT_ENV,
            ).stdout.strip()
        except subprocess.CalledProcessError as error:
            raise ValueError("Release revision must resolve to a commit object") from error
        if object_type != "commit":
            raise ValueError("Release revision must be a commit object")
    if manifest and revision is None:
        raise ValueError("A manifest requires --revision at a full immutable commit")
    manifest_path = destination / MANIFEST if manifest else None
    if manifest_path is not None and manifest_path.exists():
        raise FileExistsError(manifest_path)
    market = json.loads(git_bytes(revision, ".claude-plugin/marketplace.json") if revision else (ROOT / ".claude-plugin" / "marketplace.json").read_bytes())
    selected = [skill for plugin in market["plugins"]
                if plugin["name"] in {"szl-science-skills", "szl-science-design-skills", "szl-science-replay-skills",
                                       "szl-science-rare-disease-replay-skills",
                                       "szl-paper-evidence-skills", "szl-science-assay-skills",
                                       "szl-science-multiplicity-skills", "szl-science-change-impact-skills",
                                       "szl-science-uncertainty-skills", "szl-science-reporting-skills",
                                       "szl-science-sharing-skills"}
                for skill in plugin["skills"]]
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate skill across science families")
    local_contents = {}
    if revision is None:
        # Validate every local skill before creating any archive. Local edits to
        # tracked resources are allowed, but unrelated files must never leak.
        for relative in selected:
            skill = ROOT / relative
            if (ROOT / "skills").is_symlink() or skill.is_symlink():
                raise ValueError(f"Symlinked skill resource in {relative}")
            prefix = skill.relative_to(ROOT).as_posix() + "/"
            tracked = subprocess.run(
                ["git", "ls-files", "--cached", "-z", "--", prefix],
                cwd=ROOT, env=GIT_ENV, check=True, capture_output=True,
            ).stdout
            tracked_paths = {os.fsdecode(path) for path in tracked.split(b"\0") if path}
            entries = tuple(skill.rglob("*"))
            if any(path.is_symlink() for path in entries):
                raise ValueError(f"Symlinked skill resource in {relative}")
            members = sorted(path for path in entries
                             if path.is_file() and "__pycache__" not in path.parts)
            member_paths = {path.relative_to(ROOT).as_posix() for path in members}
            if member_paths - tracked_paths:
                raise ValueError(f"Untracked skill resources in {relative}")
            if tracked_paths - member_paths:
                raise ValueError(f"Missing tracked skill resources in {relative}")
            contents = {
                path.relative_to(skill).as_posix(): path.read_bytes() for path in members
            }
            if "SKILL.md" not in contents:
                raise ValueError(f"Missing skill entrypoint in {relative}")
            local_contents[relative] = contents
    destination.mkdir(parents=True, exist_ok=True)
    reports = []
    for relative in selected:
        skill = ROOT / relative
        contents = {}
        if revision:
            prefix = skill.relative_to(ROOT).as_posix() + "/"
            tree = subprocess.run(["git", "ls-tree", "-r", revision, "--", prefix], cwd=ROOT,
                                  env=GIT_ENV, check=True, capture_output=True, text=True).stdout
            for line in tree.splitlines():
                attributes, path = line.split("\t", 1)
                if attributes.split()[0] not in {"100644", "100755"} or not path.startswith(prefix):
                    raise ValueError("Only tracked regular skill files may be packaged")
                contents[path[len(prefix):]] = git_bytes(revision, path)
        else:
            contents = local_contents[relative]
        if "SKILL.md" not in contents:
            raise ValueError("Missing skill entrypoint")
        for name in ("LICENSE", "NOTICE"):
            contents[name] = git_bytes(revision, name) if revision else (ROOT / name).read_bytes()
        archive = destination / (skill.name + ".zip")
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as z:
            for name, data in sorted(contents.items()):
                # Manual claude.ai upload requires one enclosing skill directory.
                member = zipfile.ZipInfo(skill.name + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED
                member.external_attr = 0o100644 << 16
                z.writestr(member, data)
        with zipfile.ZipFile(archive) as z:
            reports.append({"skill": skill.name, "archive": archive.name,
                            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                            "compressed_bytes": archive.stat().st_size,
                            "uncompressed_bytes": sum(i.file_size for i in z.infolist()),
                            "resources": {name: hashlib.sha256(data).hexdigest()
                                          for name, data in sorted(contents.items())}})
    if manifest_path is not None:
        record = {"schema": MANIFEST_SCHEMA,
                  "source_commit": revision, "archives": reports}
        with manifest_path.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(record, output, sort_keys=True, indent=2)
            output.write("\n")
    return reports


def verify_archives(directory):
    """Round-trip check against the directory's source-bound manifest. Every member,
    LICENSE and NOTICE included, must match by path and SHA-256; a missing, extra or
    changed member, a corrupt or re-encoded archive, or an unlisted archive fails."""
    directory = pathlib.Path(directory)
    record = json.loads((directory / MANIFEST).read_bytes())
    if (not isinstance(record, dict) or set(record) != {"schema", "source_commit", "archives"}
            or record["schema"] != MANIFEST_SCHEMA or not isinstance(record["source_commit"], str)
            or not re.fullmatch(r"[a-f0-9]{40}", record["source_commit"])
            or not isinstance(record["archives"], list) or not record["archives"]):
        raise ValueError("Expected a source-bound " + MANIFEST_SCHEMA + " manifest")
    entries = {}
    for entry in record["archives"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("skill"), str)
                or entry.get("archive") != entry["skill"] + ".zip" or entry["archive"] in entries
                or not isinstance(entry.get("resources"), dict) or not entry["resources"]):
            raise ValueError("Malformed or duplicate manifest archive entry")
        entries[entry["archive"]] = entry
    if sorted(path.name for path in directory.iterdir() if path.name != MANIFEST) != sorted(entries):
        raise ValueError("Archive directory differs from the manifest")
    for name, entry in sorted(entries.items()):
        data = (directory / name).read_bytes()
        prefix, members = entry["skill"] + "/", {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    relative = info.filename[len(prefix):] if info.filename.startswith(prefix) else ""
                    if not relative or info.is_dir() or relative in members:
                        raise ValueError("Unexpected archive member in " + name + ": " + info.filename)
                    members[relative] = hashlib.sha256(archive.read(info)).hexdigest()
        except (zipfile.BadZipFile, zlib.error, EOFError) as error:
            raise ValueError("Corrupt archive " + name) from error
        expected = entry["resources"]
        missing, extra = sorted(set(expected) - set(members)), sorted(set(members) - set(expected))
        if missing:
            raise ValueError("Bound member missing from " + name + ": " + missing[0])
        if extra:
            raise ValueError("Unexpected archive member in " + name + ": " + extra[0])
        changed = sorted(path for path in expected if members[path] != expected[path])
        if changed:
            raise ValueError("Archive member differs from the bound revision in " + name + ": " + changed[0])
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Archive bytes differ from the manifest: " + name)
    return {"status": "ARCHIVES_MATCH_MANIFEST", "source_commit": record["source_commit"],
            "archives": len(entries)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--revision", help="Full immutable commit; package Git blobs, excluding working-copy changes")
    parser.add_argument("--manifest", action="store_true", help="Write a source-bound ZIP hash manifest; requires --revision")
    parser.add_argument("--verify", action="store_true",
                        help="Verify --output-dir archives against its manifest instead of packaging")
    args = parser.parse_args()
    if args.verify:
        if args.revision or args.manifest:
            parser.error("--verify takes only --output-dir")
        print(json.dumps(verify_archives(args.output_dir), indent=2))
    else:
        print(json.dumps(package_skills(args.output_dir, args.revision, args.manifest), indent=2))
