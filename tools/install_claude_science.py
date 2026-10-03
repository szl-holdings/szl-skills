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
         "szl-clustered-replication"]
AGENT = "SZL_SCIENCE"
DESIGN_NAMES = ["szl-experiment-contract"]
REPLAY_NAMES = ["szl-experiment-replay", "szl-measurement-harmonizer"]
PAPER_NAMES = ["szl-paper-evidence-audit"]
ASSAY_NAMES = ["szl-assay-measurement-audit"]
CHANGE_NAMES = ["szl-research-change-impact"]
MULTIPLICITY_NAMES = ["szl-multiplicity-audit"]
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
    raise ValueError("Unknown reviewed science family")


def bundle(root, family="core"):
    root = pathlib.Path(root).resolve(strict=True)
    result = {}
    for name in family_names(family):
        directory = root / "skills" / name
        entry = (directory / "SKILL.md").read_text(encoding="utf-8")
        if not re.search(r"(?m)^name: " + re.escape(name) + r"$", entry):
            raise ValueError("Skill name mismatch")
        files = {}
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("Symlink in bundle")
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(directory).as_posix()] = path.read_text(encoding="utf-8")
        for license_name in ("LICENSE", "NOTICE"):
            files[license_name] = (root / license_name).read_text(encoding="utf-8")
        result[name] = files
    if sum(len(v.encode()) for files in result.values() for v in files.values()) > 1000000:
        raise ValueError("Bundle exceeds 1 MB")
    return result


def install(host, resources, receipt_path, update=False, family="core"):
    """Preflight the complete bundle; publish and read back through the actual SDK."""
    receipt_path = pathlib.Path(receipt_path)
    if receipt_path.exists():
        raise FileExistsError("Retain the previous receipt; choose a new receipt path")
    names = family_names(family)
    if set(resources) != set(names):
        raise ValueError("Expected the complete reviewed science skill inventory")
    if sum(len(content.encode()) for files in resources.values() for content in files.values()) > 1000000:
        raise ValueError("Bundle exceeds 1 MB")
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
               "agent": None, "family": family, "runtime_task_evaluation": "NOT_EXECUTED", "signed": False}
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
        with receipt_path.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, allow_nan=False)
            handle.write("\n")
