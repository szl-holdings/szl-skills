#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect original science packages as data; never import their helpers."""
import argparse
import ast
import builtins
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAMES = ['szl-dataset-readiness', 'szl-paired-science', 'szl-model-evaluation',
         'szl-reproducibility-capsule', 'szl-math-claim-check', 'szl-research-anatomy',
         'szl-artifact-lineage', 'szl-unit-invariants', 'szl-negative-control-audit',
         'szl-analysis-plan-audit', 'szl-outcome-preservation', 'szl-release-continuity']
MIRRORS = {'anatomy.py': 'skills/szl-research-anatomy/kernel.py',
           'math_claim.py': 'skills/szl-math-claim-check/kernel.py',
           'dataset.py': 'skills/szl-dataset-readiness/kernel.py',
           'model.py': 'skills/szl-model-evaluation/kernel.py',
           'kernel_compare.py': 'skills/szl-kernel-comparison/kernel.py',
           'capsule.py': 'skills/szl-reproducibility-capsule/kernel.py',
           'paired.py': 'skills/szl-paired-science/scripts/qualify.py',
           'outcome_preservation.py': 'skills/szl-outcome-preservation/kernel.py',
           'release_continuity.py': 'skills/szl-release-continuity/kernel.py'}


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def main(sync_mirrors=False, scope='all'):
    findings, inventory = [], []
    chosen = NAMES[:6] if scope == 'upgrades' else NAMES
    for name in chosen:
        directory = ROOT / 'skills' / name
        text = (directory / 'SKILL.md').read_text(encoding='utf-8')
        match = re.match(r'^---\s*\n(.*?)\n---', text, re.S)
        front = {} if not match else dict(re.findall(r'(?m)^(name|description|license):\s*(.+)$', match.group(1)))
        if front.get('name') != name or not re.fullmatch('[a-z0-9-]{1,64}', name):
            findings.append(name + ': invalid name/frontmatter')
        if not 1 <= len(front.get('description', '')) <= 1024 or front.get('license') != 'Apache-2.0':
            findings.append(name + ': description/license')
        if len(text.splitlines()) > 160:
            findings.append(name + ': entrypoint exceeds concise bound')
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if '://' not in target and not target.startswith('#') and not (directory / target.split('#')[0]).is_file():
                findings.append(name + ': missing reference ' + target)
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if path.is_symlink():
                findings.append(name + ': symlinked resource')
            data = path.read_bytes()
            if path.suffix == '.py':
                tree = ast.parse(data, filename=str(path))
                if path.name == 'kernel.py':
                    for node in tree.body:
                        if not isinstance(node, (ast.Expr, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign)):
                            findings.append(name + ': unsupported sidecar AST statement')
                        if isinstance(node, ast.FunctionDef):
                            if node.name.startswith('_') or node.name in dir(builtins) or node.decorator_list:
                                findings.append(name + ': unsupported sidecar function ' + node.name)
                            for default in node.args.defaults:
                                ast.literal_eval(default)
            if path.suffix == '.json':
                json.loads(data, object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            inventory.append({'path': path.relative_to(ROOT).as_posix(),
                              'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    records = []
    workbench = ROOT / 'skills/szl-science-workbench'
    for target, source in MIRRORS.items():
        content = (ROOT / source).read_bytes()
        path = workbench / 'scripts/library' / target
        if sync_mirrors:
            path.write_bytes(content)
        elif path.read_bytes() != content:
            findings.append('workbench drift: ' + target)
        records.append({'path': 'scripts/library/' + target, 'source': source,
                        'sha256': hashlib.sha256(content).hexdigest()})
    calibration = (ROOT / 'references/szl_calibration_metrics.py').read_bytes()
    records.append({'path': 'scripts/library/calibration_reference.py',
                    'source': 'szl-holdings/szl-calibration@b2e317877abed98e70f9cf6730944a797837faf1/src/szl_calibration/metrics.py',
                    'sha256': hashlib.sha256(calibration).hexdigest()})
    expected = (json.dumps(records, indent=2) + '\n').encode()
    manifest = workbench / 'references/implementations.json'
    if sync_mirrors:
        manifest.write_bytes(expected)
    elif manifest.read_bytes() != expected:
        findings.append('workbench manifest drift')
    print(json.dumps({'status': 'PASS' if not findings else 'FAIL', 'packages': len(chosen),
                      'findings': sorted(findings), 'inventory': inventory}, indent=2))
    return int(bool(findings))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sync-mirrors', action='store_true')
    parser.add_argument('--scope', choices=('all', 'upgrades'), default='all')
    args = parser.parse_args()
    sys.exit(main(args.sync_mirrors, args.scope))
