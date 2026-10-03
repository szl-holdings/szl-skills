#!/usr/bin/env python3
"""Use only inside Claude Science's documented control-plane repl with its real host SDK.
This script never opens a database, reads credentials or emulates the application's host.
"""
import hashlib
import json
import pathlib
import re

NAMES = ["szl-science-workbench", "szl-research-anatomy", "szl-math-claim-check", "szl-dataset-readiness",
         "szl-model-evaluation", "szl-kernel-comparison", "szl-reproducibility-capsule", "szl-paired-science",
         "szl-artifact-lineage", "szl-unit-invariants", "szl-negative-control-audit", "szl-analysis-plan-audit",
         "szl-evidence-gate", "szl-cross-implementation-check", "szl-analysis-mutation-test",
         "szl-compute-energy-receipt", "szl-session-receipt", "szl-reviewer-pack",
         "szl-refutation-ledger", "szl-retrieval-eval", "szl-quantization-check", "szl-repo-pin", "szl-result-fragility",
         "szl-clustered-replication", "szl-outcome-preservation", "szl-release-continuity",
         "szl-skill-update-review"]
AGENT = "SZL_SCIENCE"
DESIGN_NAMES = ["szl-experiment-contract"]
REPLAY_NAMES = ["szl-experiment-replay"]
PAPER_NAMES = ["szl-paper-evidence-audit"]
ASSAY_NAMES = ["szl-assay-measurement-audit"]
CHANGE_NAMES = ["szl-research-change-impact"]
MULTIPLICITY_NAMES = ["szl-multiplicity-audit"]
MAX_BATCH_BYTES = 1000000
MAX_SKILL_BYTES = 200000
MAX_BATCHES = 8
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
PROMPT = """You are SZL Science Workbench, a scientific workflow assistant. Connect the scientist's
question to inspectable artifacts, selected calculations, actual outputs and retained
project memory. Prefer the integrated workbench for a project that should survive a session;
use an individual check when that is the task. Keep scientific findings, file integrity,
model binding, formal proof, and independent replication distinct. Preserve negative results,
unavailable measurements and contradictory evidence. A changed source requires reassessment
of dependent conclusions. Numerical examples never establish Lambda uniqueness:
Lambda is Conjecture 1 (OPEN). Propose the next useful experiment with its assumptions and
source ids, and leave scientific judgments with the researcher."""


def family_names(family):
    if family == "core":
        return NAMES
    if family == "design":
        return DESIGN_NAMES
    if family == "replay":
        return REPLAY_NAMES
    if family == "paper":
        return PAPER_NAMES
    if family == "assay":
        return ASSAY_NAMES
    if family == "change":
        return CHANGE_NAMES
    if family == "multiplicity":
        return MULTIPLICITY_NAMES
    if family == "all":
        return (NAMES + DESIGN_NAMES + REPLAY_NAMES + PAPER_NAMES + ASSAY_NAMES +
                MULTIPLICITY_NAMES + CHANGE_NAMES)
    raise ValueError("Unknown reviewed science family")


def _skill_bytes(files):
    if not isinstance(files, dict) or not {"SKILL.md", "LICENSE", "NOTICE"} <= set(files):
        raise ValueError("Expected skill resources with SKILL.md, LICENSE and NOTICE")
    size = 0
    for path, content in files.items():
        if (not isinstance(path, str) or not path or "\\" in path or ":" in path
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or any(part in ("", ".", "..") for part in path.split("/"))
                or pathlib.PurePosixPath(path).is_absolute()):
            raise ValueError("Expected a canonical relative resource path")
        if not isinstance(content, str):
            raise ValueError("Expected UTF-8 text resources")
        size += len(content.encode("utf-8"))
    if size > MAX_SKILL_BYTES:
        raise ValueError("Skill exceeds 200 KB")
    return size


def _declares_name(entry, name):
    match = FRONTMATTER.match(entry)
    if match is None:
        return False
    declarations = [line for line in match.group(1).splitlines() if line.startswith("name:")]
    return (len(declarations) == 1 and
            re.fullmatch(r"name:[ \t]*" + re.escape(name) + r"[ \t]*", declarations[0]) is not None)


def _read_skill(root, name):
    directory = root / "skills" / name
    if directory.is_symlink() or not directory.is_dir() or (root / "skills").is_symlink():
        raise ValueError("Expected a regular skill directory")
    files, size = {}, 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in bundle")
        if path.is_file() and "__pycache__" not in path.parts:
            with path.open("rb") as handle:
                data = handle.read(MAX_SKILL_BYTES + 1)
            if len(data) > MAX_SKILL_BYTES:
                raise ValueError("Resource exceeds 200 KB")
            files[path.relative_to(directory).as_posix()] = data.decode("utf-8")
            size += len(data)
            if size > MAX_SKILL_BYTES:
                raise ValueError("Skill exceeds 200 KB")
    for license_name in ("LICENSE", "NOTICE"):
        path = root / license_name
        if path.is_symlink():
            raise ValueError("Symlink in bundle")
        with path.open("rb") as handle:
            data = handle.read(MAX_SKILL_BYTES + 1)
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("License resource exceeds 200 KB")
        files[license_name] = data.decode("utf-8")
        size += len(data)
        if size > MAX_SKILL_BYTES:
            raise ValueError("Skill exceeds 200 KB")
    _skill_bytes(files)
    if not _declares_name(files["SKILL.md"], name):
        raise ValueError("Skill name mismatch")
    return files


def bundle(root, family="core"):
    """Return one reviewed family as a single batch, subject to the 1 MB limit."""
    root = pathlib.Path(root).resolve(strict=True)
    result = {name: _read_skill(root, name) for name in family_names(family)}
    if sum(_skill_bytes(files) for files in result.values()) > MAX_BATCH_BYTES:
        raise ValueError("Bundle exceeds 1 MB; use bundle_batches")
    return result


def bundle_batches(root, family="all"):
    """Stage the complete selected family, or all science skills, in bounded batches."""
    root = pathlib.Path(root).resolve(strict=True)
    names = family_names(family)
    batches, current, size = [], {}, 0
    for name in names:
        files = _read_skill(root, name)
        added = _skill_bytes(files)
        if current and size + added > MAX_BATCH_BYTES:
            batches.append(current)
            current, size = {}, 0
        current[name], size = files, size + added
    if current:
        batches.append(current)
    _review_batches(batches, names)
    return batches


def _review_batches(resources, selected_names):
    batches = [resources] if isinstance(resources, dict) else resources
    if not isinstance(batches, list) or not 1 <= len(batches) <= MAX_BATCHES:
        raise ValueError("Expected one to eight bounded staging batches")
    flattened, receipts = {}, []
    for batch in batches:
        if not isinstance(batch, dict) or not batch:
            raise ValueError("Expected a nonempty staging batch")
        size = 0
        for name, files in batch.items():
            if name not in selected_names or name in flattened:
                raise ValueError("Unknown or duplicate staged skill")
            size += _skill_bytes(files)
            if not _declares_name(files["SKILL.md"], name):
                raise ValueError("Skill name mismatch")
            flattened[name] = files
        if size > MAX_BATCH_BYTES:
            raise ValueError("Staging batch exceeds 1 MB")
        receipts.append({"skill_names": list(batch), "resource_bytes": size})
    if set(flattened) != set(selected_names):
        raise ValueError("Expected the complete reviewed science skill inventory")
    return flattened, receipts


def install(host, resources, receipt_path, update=False, family="core"):
    """Preflight every batch and collision before SDK edits; publish and read back."""
    receipt_path = pathlib.Path(receipt_path)
    if receipt_path.exists():
        raise FileExistsError("Retain the previous receipt; choose a new receipt path")
    names = family_names(family)
    resources, batches = _review_batches(resources, names)
    if not receipt_path.parent.is_dir():
        raise ValueError("receipt parent must be an existing directory")
    inventory = {s["name"]: s for s in host.skills.list()}
    profiles = {a["name"]: a for a in host.agents.list()}
    if AGENT in profiles and profiles[AGENT].get("systemPrompt") != PROMPT:
        raise ValueError("Existing specialist name collision: " + AGENT)
    edits, readbacks = [], {}
    for name in names:
        existing = inventory.get(name)
        if existing and existing.get("origin") == "anthropic":
            raise ValueError("Protected skill name collision: " + name)
        readbacks[name] = {}
        for path, content in resources[name].items():
            previous = None
            if existing:
                try:
                    previous = host.skills.read(name, path)["content"]
                except (ValueError, FileNotFoundError) as error:
                    if not any(marker in str(error).lower() for marker in ("not found", "does not exist", "no such")):
                        raise
            if previous == content:
                continue
            if existing and not update:
                raise ValueError("SKILL_NAME_COLLISION: explicit update required for " + name)
            if previous == "":
                raise ValueError("Empty existing resource cannot be replaced with a nonempty exact-match SDK edit")
            edits.append((name, path, content, previous))
    # All collisions are assessed before any write.
    receipt = {"schema": "szl.claude-science-install.v1", "status": "IN_PROGRESS", "skills": {},
               "agent": None, "family": family, "runtime_task_evaluation": "NOT_EXECUTED", "signed": False,
               "readback_scope": "STAGED_RESOURCES_ONLY", "resource_set_completeness": "UNVERIFIED",
               "staging": {"batches": batches, "max_batch_bytes": MAX_BATCH_BYTES,
                           "max_skill_bytes": MAX_SKILL_BYTES, "max_batches": MAX_BATCHES,
                           "total_resource_bytes": sum(batch["resource_bytes"] for batch in batches)}}
    receipt_handle = receipt_path.open("x", encoding="utf-8")
    try:
        json.dump(receipt, receipt_handle, indent=2, allow_nan=False)
        receipt_handle.write("\n")
        receipt_handle.flush()
    except Exception:
        receipt_handle.close()
        raise
    try:
        for name, path, content, previous in edits:
            edited = host.skills.edit(name, path, content, old_string=previous)
            gate = edited.get("sidecar_gate")
            if path == "kernel.py":
                receipt["skills"].setdefault(name, {})["sidecar_gate"] = gate if gate is not None else {"status": "PROBE_UNAVAILABLE"}
                if gate is not None and gate.get("ok") is not True:
                    raise ValueError("Claude Science sidecar gate rejected " + name)
        for name in names:
            changed = any(edit[0] == name for edit in edits)
            if changed or inventory.get(name, {}).get("origin") == "draft":
                published = host.skills.publish(name, overwrite=bool(inventory.get(name)))
                if published.get("status") != "published":
                    raise ValueError("Publish did not confirm success for " + name)
                receipt["skills"].setdefault(name, {})["publication"] = published
            for path, expected in resources[name].items():
                actual = host.skills.read(name, path)["content"]
                if actual != expected:
                    raise ValueError("Application readback mismatch: " + name + "/" + path)
                readbacks[name][path] = hashlib.sha256(actual.encode()).hexdigest()
            receipt["skills"].setdefault(name, {})["readback_sha256"] = readbacks[name]
        live = {s["name"]: s for s in host.skills.list()}
        if any(n not in live or live[n].get("origin") == "draft" for n in names):
            raise ValueError("Skills did not appear in the live catalog")
        agents = {a["name"]: a for a in host.agents.list()}
        existing = agents.get(AGENT)
        if existing:
            if existing.get("systemPrompt") != PROMPT:
                raise ValueError("Existing specialist name collision: " + AGENT)
            for name in names:
                if name not in existing.get("skillNames", []):
                    host.agents.attach_skill(AGENT, name)
        else:
            host.agents.create(AGENT, "SZL Science Workbench",
                               "Reproducible scientific checks and living project evidence with the SZL Science Pack",
                               system_prompt=PROMPT, skill_names=names)
        agent = next(a for a in host.agents.list() if a["name"] == AGENT)
        if not set(names) <= set(agent.get("skillNames", [])):
            raise ValueError("Specialist skill readback mismatch")
        receipt["agent"] = {k: agent.get(k) for k in ("name", "displayName", "skillNames", "connectors", "unrestricted")}
        receipt["status"] = "PUBLISHED_AND_READ_BACK"
        return receipt
    except Exception as error:
        receipt["status"] = "FAILED_OR_INCOMPLETE"
        receipt["error_type"] = type(error).__name__
        receipt["error"] = str(error)
        raise
    finally:
        try:
            receipt_handle.seek(0)
            json.dump(receipt, receipt_handle, indent=2, allow_nan=False)
            receipt_handle.write("\n")
            receipt_handle.truncate()
            receipt_handle.flush()
        finally:
            receipt_handle.close()
