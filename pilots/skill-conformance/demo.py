#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run an explicitly synthetic conformance example; no host or model is contacted."""
import hashlib
import importlib.util
import json
import pathlib
import shutil
import tempfile

PILOT = pathlib.Path(__file__).resolve().parent / "szl-skill-conformance"
spec = importlib.util.spec_from_file_location("conformance_demo", PILOT / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)
with tempfile.TemporaryDirectory(prefix="szl-conformance-fixture-") as temporary:
    root = pathlib.Path(temporary)
    source, exported, retained = [root / name for name in ("source", "exported", "retained")]
    source.mkdir(); retained.mkdir()
    (source / "SKILL.md").write_text("---\nname: szl-unit-example\ndescription: Synthetic unit-check example\n---\nCheck declared dimensional units.\n", encoding="utf-8")
    (source / "units.txt").write_text("time: second\nlength: metre\n", encoding="utf-8")
    shutil.copytree(source, exported)
    digest = kernel.bundle(source)["bundle_sha256"]
    document = {"schema_version": 1, "evidence_kind": "synthetic_fixture", "source_bundle_sha256": digest, "host_bundle_sha256": digest, "imported_skill": "szl-unit-example", "cases": []}
    for kind, task, result in (
        ("positive", "Check the dimensions of distance/time.", "Synthetic declaration only: speed has units metre/second."),
        ("negative", "Translate hello into French.", "Synthetic declaration only: bonjour; target skill not invoked.")
    ):
        case = {"case_id": kind, "kind": kind, "invocation_reported": kind == "positive", "outcome": "PASS"}
        for field, content in (("task", task), ("result", result)):
            filename = f"{kind}-{field}.txt"
            data = content.encode("utf-8")
            (retained / filename).write_bytes(data)
            case[field + "_path"] = filename
            case[field + "_sha256"] = hashlib.sha256(data).hexdigest()
        document["cases"].append(case)
    trials = retained / "trials.json"
    trials.write_text(json.dumps(document), encoding="utf-8")
    report = kernel.check(source, exported, trials, retained)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    if report["offline_conformance"] != "CONSISTENT_SUPPLIED_EVIDENCE" or report["actual_host_invocation"] != "NOT_VERIFIED":
        raise SystemExit(1)
