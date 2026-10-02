#!/usr/bin/env python3
"""Generate the workbench's self-contained helpers from the reviewed skill sources."""
import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "skills" / "szl-science-workbench"
SOURCES = {
    "anatomy.py": "skills/szl-research-anatomy/kernel.py",
    "math_claim.py": "skills/szl-math-claim-check/kernel.py",
    "dataset.py": "skills/szl-dataset-readiness/kernel.py",
    "model.py": "skills/szl-model-evaluation/kernel.py",
    "kernel_compare.py": "skills/szl-kernel-comparison/kernel.py",
    "capsule.py": "skills/szl-reproducibility-capsule/kernel.py",
    "paired.py": "skills/szl-paired-science/scripts/qualify.py",
    "outcome_preservation.py": "skills/szl-outcome-preservation/kernel.py",
    "release_continuity.py": "skills/szl-release-continuity/kernel.py",
}


def sync(check=False):
    destination = WORKBENCH / "scripts" / "library"
    expected = {}
    records = []
    for name, relative in SOURCES.items():
        content = (ROOT / relative).read_bytes()
        expected[destination / name] = content
        records.append({"path": "scripts/library/" + name, "source": relative,
                        "sha256": hashlib.sha256(content).hexdigest()})
    reference = ROOT / "references" / "szl_calibration_metrics.py"
    content = reference.read_bytes()
    expected[destination / "calibration_reference.py"] = content
    records.append({"path": "scripts/library/calibration_reference.py",
                    "source": "szl-holdings/szl-calibration@b2e317877abed98e70f9cf6730944a797837faf1/src/szl_calibration/metrics.py",
                    "sha256": hashlib.sha256(content).hexdigest()})
    expected[WORKBENCH / "references" / "implementations.json"] = (json.dumps(records, indent=2) + "\n").encode()
    failures = []
    for path, data in expected.items():
        if check:
            if not path.is_file() or path.read_bytes() != data:
                failures.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if failures:
        print("WORKBENCH DRIFT: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("workbench helpers: " + ("in sync" if check else "generated"))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    sys.exit(sync(parser.parse_args().check))
