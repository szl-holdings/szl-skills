#!/usr/bin/env python3
"""Create individual import ZIPs. Refuses overwrite; includes only explicit skill resources."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def git_bytes(revision, relative):
    return subprocess.run(["git", "show", revision + ":" + relative], cwd=ROOT, check=True, capture_output=True).stdout


def package_skills(destination, revision=None):
    if revision is not None and not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Release revision must be a full immutable 40-character commit")
    market = json.loads(git_bytes(revision, ".claude-plugin/marketplace.json") if revision else (ROOT / ".claude-plugin" / "marketplace.json").read_bytes())
    selected = [skill for plugin in market["plugins"]
                if plugin["name"] in {"szl-science-skills", "szl-science-replay-skills",
                                       "szl-paper-evidence-skills", "szl-science-assay-skills",
                                       "szl-science-multiplicity-skills"}
                for skill in plugin["skills"]]
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate skill across science families")
    destination.mkdir(parents=True, exist_ok=True)
    reports = []
    for relative in selected:
        skill = ROOT / relative
        contents = {}
        if revision:
            prefix = skill.relative_to(ROOT).as_posix() + "/"
            tree = subprocess.run(["git", "ls-tree", "-r", revision, "--", prefix], cwd=ROOT, check=True, capture_output=True, text=True).stdout
            for line in tree.splitlines():
                attributes, path = line.split("\t", 1)
                if attributes.split()[0] not in {"100644", "100755"} or not path.startswith(prefix):
                    raise ValueError("Only tracked regular skill files may be packaged")
                contents[path[len(prefix):]] = git_bytes(revision, path)
        else:
            members = sorted(p for p in skill.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
            if any(not p.is_file() or p.is_symlink() for p in members):
                raise ValueError("Missing or symlinked skill resource")
            contents = {p.relative_to(skill).as_posix(): p.read_bytes() for p in members}
        if "SKILL.md" not in contents:
            raise ValueError("Missing skill entrypoint")
        for name in ("LICENSE", "NOTICE"):
            contents[name] = git_bytes(revision, name) if revision else (ROOT / name).read_bytes()
        archive = destination / (skill.name + ".zip")
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as z:
            for name, data in sorted(contents.items()):
                member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED
                member.external_attr = 0o100644 << 16
                z.writestr(member, data)
        with zipfile.ZipFile(archive) as z:
            reports.append({"skill": skill.name, "archive": archive.name,
                            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                            "compressed_bytes": archive.stat().st_size,
                            "uncompressed_bytes": sum(i.file_size for i in z.infolist())})
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--revision", help="Full immutable commit; package Git blobs, excluding working-copy changes")
    args = parser.parse_args()
    print(json.dumps(package_skills(args.output_dir, args.revision), indent=2))
