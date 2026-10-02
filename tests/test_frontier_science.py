"""Behavioral fixtures, not application registration or real model qualification."""
import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(name):
    path = ROOT / 'skills' / name
    spec = importlib.util.spec_from_file_location(name, path / 'kernel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, json.loads((path / 'assets/example.json').read_text())

class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.k, self.doc = load('szl-outcome-preservation')

    def passing(self):
        self.doc['cases'][0]['candidate']['scientific_metric'] = 0.4998
        return self.doc

    def test_small_numeric_error_changes_decision(self):
        r = self.k.szl_outcome_preservation(self.doc)
        self.assertEqual(r['cases'][0]['numerical_mismatches'], 0)
        self.assertIn('SCIENTIFIC_DECISION_CHANGED', r['findings'])
        self.assertIsNone(r['cases'][0]['supplied_median_speedup'])

    def test_preserved_supplied_fixture_ratios(self):
        r = self.k.szl_outcome_preservation(self.passing())
        self.assertEqual(r['status'], 'PRESERVED_ON_SUPPLIED_COHORT')
        self.assertEqual(r['cases'][0]['supplied_median_speedup'], 4)
        self.assertEqual(r['cases'][0]['supplied_memory_reduction_ratio'], 2)
        self.assertEqual(r['scientific_generalization'], 'NOT_ESTABLISHED')

    def test_missing_case_suppresses_survivor_performance(self):
        doc = self.passing(); doc['expected_case_ids'].append('failed-large-case')
        r = self.k.szl_outcome_preservation(doc)
        self.assertEqual(r['missing_case_ids'], ['failed-large-case'])
        self.assertIsNone(r['cases'][0]['supplied_median_speedup'])

    def test_failed_case_suppresses_complete_cohort_performance(self):
        doc = self.passing(); doc['expected_case_ids'].append('error')
        doc['cases'].append({'id': 'error', 'reference': {'status': 'SUCCESS'}, 'candidate': {'status': 'ERROR'}})
        r = self.k.szl_outcome_preservation(doc)
        self.assertIn('EXECUTION_FAILED_OR_UNOBSERVED', r['findings'])
        self.assertTrue(all(c['supplied_median_speedup'] is None for c in r['cases']))

    def test_stock_fallback_is_not_acceleration(self):
        doc = self.passing(); doc['cases'][0]['candidate']['activated_mode'] = 'off'
        self.assertIn('OPTIMIZATION_NOT_ACTIVE', self.k.szl_outcome_preservation(doc)['findings'])

    def test_exact_missing_bytes_and_memory_method_remain_unobserved(self):
        doc = self.passing(); doc['mode'] = 'exact'
        doc['cases'][0]['candidate']['activated_mode'] = 'exact'
        self.assertIn('EXACT_BYTES_UNOBSERVED', self.k.szl_outcome_preservation(doc)['findings'])
        doc['mode'] = 'big'; doc['cases'][0]['candidate']['activated_mode'] = 'big'
        for r in (doc['cases'][0]['reference'], doc['cases'][0]['candidate']):
            del r['benchmark_context']['memory_measurement_method']
        self.assertIsNone(self.k.szl_outcome_preservation(doc)['cases'][0]['supplied_memory_reduction_ratio'])

    def test_unmatched_hardware_withholds_ratios(self):
        doc = self.passing(); doc['cases'][0]['candidate']['benchmark_context']['hardware'] = 'other'
        r = self.k.szl_outcome_preservation(doc)
        self.assertFalse(r['cases'][0]['benchmark_contexts_match'])
        self.assertIsNone(r['cases'][0]['supplied_median_speedup'])

    def test_exact_checks_byte_digests_and_numbers(self):
        doc = self.passing(); doc['mode'] = 'exact'; c = doc['cases'][0]
        c['candidate']['activated_mode'] = 'exact'
        for r in (c['reference'], c['candidate']): r['output_sha256'] = 'a' * 64
        self.assertIn('EXACT_BYTES_MISMATCH', self.k.szl_outcome_preservation(doc)['findings'])
        c['candidate']['output'] = copy.deepcopy(c['reference']['output'])
        self.assertEqual(self.k.szl_outcome_preservation(doc)['status'], 'PRESERVED_ON_SUPPLIED_COHORT')

    def test_boundaries_bool_nan_duplicate_and_extra_cases(self):
        for value in (True, float('nan'), float('inf')):
            doc = copy.deepcopy(self.passing()); doc['atol'] = value
            with self.assertRaises(ValueError): self.k.szl_outcome_preservation(doc)
        doc = self.passing(); doc['cases'].append(copy.deepcopy(doc['cases'][0]))
        with self.assertRaises(ValueError): self.k.szl_outcome_preservation(doc)
        doc['cases'][-1]['id'] = 'unexpected'
        self.assertIn('INCOMPLETE_COHORT', self.k.szl_outcome_preservation(doc)['findings'])

class ContinuityTests(unittest.TestCase):
    def setUp(self): self.k, self.doc = load('szl-release-continuity')

    def passing(self):
        for s in self.doc['surfaces']: s['observed']['source_revision'] = self.doc['source_revision']
        self.doc['surfaces'][-1]['observed']['ready'] = True
        return self.doc

    def test_wrong_source_and_refused_runtime_retained(self):
        r = self.k.szl_release_continuity(self.doc)
        self.assertIn('MISMATCH_SOURCE_REVISION', r['surfaces'][1]['findings'])
        self.assertIn('READINESS_REFUSED', r['surfaces'][2]['findings'])

    def test_agreement_does_not_assert_verification_or_authority(self):
        r = self.k.szl_release_continuity(self.passing())
        self.assertEqual(r['status'], 'IDENTITY_AGREEMENT_ON_SUPPLIED_EVIDENCE')
        self.assertEqual(r['signature_verification'], 'NOT_PERFORMED')
        self.assertEqual(r['release_authorization'], 'NOT_GRANTED')

    def test_missing_surface_and_short_pin_rejected(self):
        doc = self.passing(); doc['surfaces'][0]['observed'] = None
        self.assertEqual(self.k.szl_release_continuity(doc)['surfaces'][0]['findings'], ['UNOBSERVED'])
        doc['source_revision'] = 'main'
        with self.assertRaises(ValueError): self.k.szl_release_continuity(doc)

    def test_running_label_cannot_supply_readiness(self):
        for ready in (None, 'RUNNING', 1):
            doc = self.passing(); doc['surfaces'][-1]['observed']['ready'] = ready
            self.assertIn('READINESS_UNOBSERVED', self.k.szl_release_continuity(doc)['surfaces'][-1]['findings'])

    def test_different_per_surface_artifacts_allowed_and_changed_hash_fails(self):
        doc = self.passing()
        self.assertNotEqual(doc['surfaces'][0]['expected']['artifact_sha256'], doc['surfaces'][1]['expected']['artifact_sha256'])
        doc['surfaces'][0]['observed']['artifact_sha256'] = 'c' * 64
        self.assertIn('MISMATCH_ARTIFACT_SHA256', self.k.szl_release_continuity(doc)['surfaces'][0]['findings'])

    def test_native_kernel_is_an_explicit_pinned_repository_type(self):
        doc = self.passing(); surface = doc['surfaces'][1]
        surface['kind'] = 'hf-kernel'
        self.assertEqual(self.k.szl_release_continuity(doc)['surfaces'][1]['kind'], 'hf-kernel')
        surface['expected']['revision'] = 'main'
        with self.assertRaises(ValueError): self.k.szl_release_continuity(doc)

class CLITests(unittest.TestCase):
    def test_cli_findings_and_malformed_json_are_nonzero(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = pathlib.Path(folder) / 'bad.json'; bad.write_text('{"schema":1,"schema":2}')
            for name in ('szl-outcome-preservation', 'szl-release-continuity'):
                root = ROOT / 'skills' / name
                result = subprocess.run([sys.executable, '-B', str(root / 'scripts/run.py'), str(root / 'assets/example.json')], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                invalid = subprocess.run([sys.executable, '-B', str(root / 'scripts/run.py'), str(bad)], capture_output=True, text=True)
                self.assertEqual(invalid.returncode, 2)
                self.assertEqual(json.loads(invalid.stdout)['status'], 'ERROR')

    def test_cli_rejects_size_and_wrong_nested_types_without_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'input.json'
            for name in ('szl-outcome-preservation', 'szl-release-continuity'):
                root = ROOT / 'skills' / name
                for payload in (' ' * 262145, '[]'):
                    path.write_text(payload)
                    result = subprocess.run([sys.executable, '-B', str(root / 'scripts/run.py'), str(path)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2); self.assertEqual(result.stderr, '')
                _, doc = load(name)
                if name == 'szl-outcome-preservation': doc['cases'][0]['candidate'] = []
                else: doc['surfaces'][0] = []
                path.write_text(json.dumps(doc))
                result = subprocess.run([sys.executable, '-B', str(root / 'scripts/run.py'), str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2); self.assertEqual(result.stderr, '')

class WorkbenchFrontierTests(unittest.TestCase):
    def test_checks_capsule_and_changed_source_invalidation(self):
        import runpy
        w = runpy.run_path(str(ROOT / 'skills/szl-science-workbench/scripts/workbench.py'))
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder) / 'project'
            files = {name: load('szl-' + name)[1] for name in ('outcome-preservation', 'release-continuity')}
            checks = [{'id': n + '-check', 'type': n, 'input': n, 'depends_on': [n]} for n in files]
            w['start_project'](root, files, checks, 'SYNTHETIC')
            report = w['run_project'](root)
            self.assertEqual(set(report['findings']), {n + '-check' for n in files})
            old = w['latest_run'](root)
            self.assertEqual(w['assess_snapshot'](root, old)['capsule']['integrity'], 'MATCH')
            path = root / 'inputs/outcome-preservation.json'
            doc = json.loads(path.read_text()); doc['candidate_revision'] = '3' * 40
            path.write_text(json.dumps(doc))
            assessment = w['assess_snapshot'](root, old)
            self.assertIn('outcome-preservation', assessment['changed_sources'])
            self.assertIn('outcome-preservation-check', assessment['recheck'])
            self.assertIn('conclusion', assessment['recheck'])

if __name__ == '__main__': unittest.main()
