"""Synthetic offline run planning; no GitHub, permit issuance, or SSH."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools import ssh_ci_plan as plan
from tools.ssh_ci_client import build_request, WORKFLOW_REF
from tools.ssh_ci_contract import canonical, sha256


PERMIT = '17684090-9765-4d32-baa1-ec95e3213bdb'


def metadata():
    return {'id': 123, 'run_attempt': 1, 'event': 'workflow_dispatch',
            'path': plan.WORKFLOW_PATH, 'head_branch': 'main', 'head_sha': '1' * 40,
            'status': 'waiting', 'repository': {'id': plan.REPOSITORY_ID,
                                               'full_name': plan.REPOSITORY}}


class RequestPlanTests(unittest.TestCase):
    def test_metadata_reader_rejects_directories_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                plan.read_metadata(root)
            source = root / 'large.json'
            source.write_bytes(b' ' * (plan.MAX_METADATA_BYTES + 1))
            with self.assertRaises(ValueError):
                plan.read_metadata(source)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink and FIFO checks')
    def test_metadata_reader_rejects_symlinks_and_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_bytes(canonical(metadata()))
            link = root / 'link.json'
            link.symlink_to(source)
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            for path in (link, fifo):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    plan.read_metadata(path)

    def test_digest_matches_actual_controller_request(self):
        result = plan.plan_request(metadata(), PERMIT)
        request = build_request({
            'GITHUB_REPOSITORY': plan.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_WORKFLOW_REF': WORKFLOW_REF, 'GITHUB_RUN_ID': '123',
            'GITHUB_SHA': '1' * 40,
        }, PERMIT)
        self.assertEqual(result['request'], request)
        self.assertEqual(result['request_sha256'], sha256(canonical(request)))
        self.assertEqual(result['evidence_class'], 'DECLARED')
        for field in ('owner_authenticated', 'environment_protection_verified',
                      'permit_issued', 'execution_authorized'):
            self.assertIs(result[field], False)
        self.assertEqual(result['remote_calls'], 0)
        self.assertEqual(result['claims_written'], 0)

    def test_rejects_wrong_repository_name_or_numeric_id(self):
        for patch in ({'id': True}, {'id': str(plan.REPOSITORY_ID)}, {'id': 12},
                      {'full_name': 'another/repository'}):
            run = metadata()
            run['repository'].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                plan.plan_request(run, PERMIT)

    def test_rejects_unapproved_context_and_nonwaiting_runs(self):
        for field, values in {
            'event': ['push', 'pull_request', None],
            'head_branch': ['branch', None], 'path': ['arbitrary.py', None],
            'status': ['queued', 'in_progress', 'completed', None],
            'head_sha': ['short', 'G' * 40, None, True],
            'run_attempt': [True, 0, 2, '1'], 'id': [True, -1, 0, 1 << 63, '123'],
        }.items():
            for value in values:
                run = metadata()
                run[field] = value
                with self.subTest(field=field, value=value), self.assertRaises((ValueError, TypeError)):
                    plan.plan_request(run, PERMIT)

    def test_duplicate_oversized_and_nonobject_input_rejected(self):
        for raw in (b'{"id":1,"id":2}', b'[]', b' ' * (plan.MAX_METADATA_BYTES + 1)):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                plan.plan_request(raw, PERMIT)

    def test_invalid_permit_id_rejected(self):
        for value in ('../grant', PERMIT.upper(), 'old-gpu-grant'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan.plan_request(metadata(), value)

    def test_extra_api_fields_only_affect_metadata_hash(self):
        run = metadata()
        previous = copy.deepcopy(run)
        a = plan.plan_request(run, PERMIT)
        run['display_title'] = 'untrusted descriptive text, never returned or executed'
        b = plan.plan_request(run, PERMIT)
        self.assertEqual(a['request_sha256'], b['request_sha256'])
        self.assertNotEqual(a['metadata_sha256'], b['metadata_sha256'])
        self.assertNotIn('display_title', canonical(b).decode())
        self.assertEqual(previous, metadata())

    def test_cli_emits_plan_only_and_does_not_modify_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'run.json'
            raw = canonical(metadata())
            source.write_bytes(raw)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = plan.main(['--run-metadata', str(source), '--permit-id', PERMIT])
            self.assertEqual(result, 0)
            self.assertFalse(json.loads(stdout.getvalue())['execution_authorized'])
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_cli_rejects_invalid_input_without_echoing_its_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'run.json'
            source.write_bytes(b'synthetic-sensitive-canary')
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = plan.main(['--run-metadata', str(source), '--permit-id', PERMIT])
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), '')
            self.assertNotIn('synthetic-sensitive-canary', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
