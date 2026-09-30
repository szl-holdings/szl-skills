#!/usr/bin/env python3
"""Create individual import ZIPs. Refuses overwrite; includes only explicit skill resources."""
import argparse
import hashlib
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def package_skills(destination):
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = next(p for p in market["plugins"] if p["name"] == "szl-science-skills")
    destination.mkdir(parents=True, exist_ok=True)
    reports = []
    for relative in plugin["skills"]:
        skill = ROOT / relative
        members = sorted(p for p in skill.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        if not (skill / "SKILL.md").is_file():
            raise ValueError("Missing skill entrypoint")
        if any(not p.is_file() or p.is_symlink() for p in members):
            raise ValueError("Missing or symlinked skill resource")
        archive = destination / (skill.name + ".zip")
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as z:
            for file in members:
                z.write(file, file.relative_to(skill).as_posix())
            for name in ("LICENSE", "NOTICE"):
                z.write(ROOT / name, name)
        with zipfile.ZipFile(archive) as z:
            reports.append({"skill": skill.name, "archive": archive.name,
                            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                            "compressed_bytes": archive.stat().st_size,
                            "uncompressed_bytes": sum(i.file_size for i in z.infolist())})
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package_skills(args.output_dir), indent=2))
