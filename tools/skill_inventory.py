#!/usr/bin/env python3
"""Generate/check the source skill inventory without importing skill payloads.

The marketplace assigns skills to plugins; SKILL.md files establish which skill
packages actually exist. This report describes the current source tree, not a
release tag, Claude Science registration, or measured skill effectiveness.
"""
import argparse
import ast
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = "SKILL_INVENTORY.json"
SKILL_PATH = re.compile(r"\./skills/(szl-[a-z0-9-]+)\Z")
FRONTMATTER_NAME = re.compile(r"\A---\s*\n(.*?)\n---", re.S)


def installer_names(root, variable="NAMES", required=True):
    """Read the SDK selection as a literal, without running installer code."""
    source = (pathlib.Path(root) / "tools" / "install_claude_science.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = [node.value for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == variable for target in node.targets)]
    if not assignments and not required:
        return []
    if len(assignments) != 1:
        raise ValueError("installer must have exactly one literal NAMES assignment")
    names = ast.literal_eval(assignments[0])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("installer NAMES must be a list of skill names")
    if len(names) != len(set(names)):
        raise ValueError("duplicate installer skill name")
    return names


def build(root):
    root = pathlib.Path(root)
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugins = market["plugins"]
    if not isinstance(plugins, list) or not plugins:
        raise ValueError("marketplace has no plugins")

    skills_root = root / "skills"
    if not skills_root.is_dir() or skills_root.is_symlink():
        raise ValueError("skills/ must be a regular directory")
    actual = set()
    for directory in skills_root.iterdir():
        if directory.name == "__pycache__":
            continue
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("unexpected skills/ entry: " + directory.name)
        entry = directory / "SKILL.md"
        if not entry.is_file() or entry.is_symlink():
            raise ValueError("missing regular SKILL.md: " + directory.name)
        match = FRONTMATTER_NAME.match(entry.read_text(encoding="utf-8"))
        declarations = [] if match is None else re.findall(r"(?m)^name:[^\r\n]*$", match.group(1))
        if len(declarations) != 1 or re.fullmatch(r"name:[ \t]*" + re.escape(directory.name) + r"[ \t]*",
                                                    declarations[0]) is None:
            raise ValueError("SKILL.md frontmatter name mismatch: " + directory.name)
        actual.add(directory.name)

    grouped = {}
    catalog = set()
    for plugin in plugins:
        plugin_name = plugin["name"]
        if not isinstance(plugin_name, str) or plugin_name in grouped:
            raise ValueError("duplicate or invalid marketplace plugin name")
        names = []
        for path in plugin["skills"]:
            match = SKILL_PATH.fullmatch(path) if isinstance(path, str) else None
            if match is None:
                raise ValueError("invalid marketplace skill path: " + repr(path))
            name = match.group(1)
            if name in catalog:
                raise ValueError("duplicate marketplace skill: " + name)
            catalog.add(name)
            names.append(name)
        grouped[plugin_name] = sorted(names)

    if actual != catalog:
        raise ValueError("marketplace/SKILL.md mismatch: uncataloged=%s, missing=%s" %
                         (sorted(actual - catalog), sorted(catalog - actual)))
    if not {"szl-science-skills", "szl-evidence-skills"} <= set(grouped):
        raise ValueError("science or evidence marketplace plugin missing")
    selected = set(installer_names(root))
    science = set(grouped["szl-science-skills"])
    if selected != science:
        raise ValueError("installer/science catalog mismatch: unselected=%s, unlisted=%s" %
                         (sorted(science - selected), sorted(selected - science)))
    design = set(installer_names(root, "DESIGN_NAMES", required=False))
    if design != set(grouped.get("szl-science-design-skills", [])) or design & selected:
        raise ValueError("installer/design catalog mismatch")
    replay = set(installer_names(root, "REPLAY_NAMES", required=False))
    if replay != set(grouped.get("szl-science-replay-skills", [])) or replay & (selected | design):
        raise ValueError("installer/replay catalog mismatch")
    rare_replay = set(installer_names(root, "RARE_DISEASE_REPLAY_NAMES", required=False))
    if (rare_replay != set(grouped.get("szl-science-rare-disease-replay-skills", [])) or
            rare_replay & (selected | design | replay)):
        raise ValueError("installer/rare-disease-replay catalog mismatch")
    paper = set(installer_names(root, "PAPER_NAMES", required=False))
    if paper != set(grouped.get("szl-paper-evidence-skills", [])) or paper & (selected | design | replay | rare_replay):
        raise ValueError("installer/paper catalog mismatch")
    assay = set(installer_names(root, "ASSAY_NAMES", required=False))
    if assay != set(grouped.get("szl-science-assay-skills", [])) or assay & (selected | design | replay | rare_replay | paper):
        raise ValueError("installer/assay catalog mismatch")
    change = set(installer_names(root, "CHANGE_NAMES", required=False))
    if change != set(grouped.get("szl-science-change-impact-skills", [])) or change & (selected | design | replay | rare_replay | paper | assay):
        raise ValueError("installer/change catalog mismatch")
    multiplicity = set(installer_names(root, "MULTIPLICITY_NAMES", required=False))
    if multiplicity != set(grouped.get("szl-science-multiplicity-skills", [])) or multiplicity & (selected | design | replay | rare_replay | paper | assay | change):
        raise ValueError("installer/multiplicity catalog mismatch")
    uncertainty = set(installer_names(root, "UNCERTAINTY_NAMES", required=False))
    if uncertainty != set(grouped.get("szl-science-uncertainty-skills", [])) or uncertainty & (selected | design | replay | rare_replay | paper | assay | change | multiplicity):
        raise ValueError("installer/uncertainty catalog mismatch")
    reporting = set(installer_names(root, "REPORTING_NAMES", required=False))
    if reporting != set(grouped.get("szl-science-reporting-skills", [])) or reporting & (selected | design | replay | rare_replay | paper | assay | change | multiplicity | uncertainty):
        raise ValueError("installer/reporting catalog mismatch")
    grouped = dict(sorted(grouped.items()))
    return {
        "schema": "szl.source-skill-inventory.v1",
        "scope": "source-tree",
        "counts": {"total": len(actual), "by_plugin": {name: len(names) for name, names in grouped.items()}},
        "plugins": grouped,
    }


def render(root):
    return json.dumps(build(root), indent=2, ensure_ascii=False) + "\n"


def inventory_path(root):
    path = pathlib.Path(root) / OUTPUT
    if path.is_symlink():
        raise ValueError(OUTPUT + " must not be a symlink")
    return path


def check(root):
    expected = render(root)
    path = inventory_path(root)
    if not path.is_file() or path.read_text(encoding="utf-8") != expected:
        raise ValueError(OUTPUT + " is stale; run python -B tools/skill_inventory.py --write")


def write(root):
    root = pathlib.Path(root)
    expected = render(root)
    path = inventory_path(root)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=root,
                                     prefix=".skill-inventory-", suffix=".tmp", delete=False) as stream:
        stream.write(expected)
        temporary = pathlib.Path(stream.name)
    try:
        inventory_path(root)  # Recheck before atomically replacing the destination.
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate the source inventory")
    args = parser.parse_args()
    try:
        if args.write:
            write(ROOT)
        else:
            check(ROOT)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("skill inventory: FAIL: " + str(error), file=sys.stderr)
        return 1
    print("skill inventory: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
