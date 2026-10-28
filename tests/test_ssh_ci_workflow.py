"""Offline workflow controls and actual inline bootstrap-receipt regression tests."""
import contextlib
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap
import unittest
from unittest import mock


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/isolated-ssh-build.yml'
TEXT = WORKFLOW.read_text(encoding='utf-8')
BOOTSTRAP_NAME = 'Retain a failure receipt when the client did not produce one'


def step(name):
    """Extract a fixed, uniquely named step without requiring a YAML dependency."""
    marker = '      - name: ' + name + '\n'
    if TEXT.count(marker) != 1:
        raise AssertionError('Expected exactly one workflow step: ' + name)
    return TEXT.split(marker, 1)[1].split('\n      - ', 1)[0]


@contextlib.contextmanager
def working_directory(path):
    """contextlib.chdir is 3.11+; the VM build runs this suite on Ubuntu 22.04's 3.10."""
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def bootstrap_source():
    block = step(BOOTSTRAP_NAME)
    match = re.search(r"          python - <<'PY'\n(.*?)\n          PY(?:\n|$)", block, re.S)
    if not match:
        raise AssertionError('Bootstrap inline Python block is absent')
    return textwrap.dedent(match.group(1))


class WorkflowControlsTests(unittest.TestCase):
    def test_real_claim_storage_test_is_hosted_linux_only_without_host_secrets(self):
        contract = WORKFLOW.with_name('ssh-build-contract.yml').read_text(encoding='utf-8')
        self.assertIn('os: [ubuntu-latest, windows-latest]', contract)
        self.assertIn('runs-on: ${{ matrix.os }}', contract)
        root_test = contract.split(
            '      - name: Exercise root-owned claim storage on the disposable hosted Linux runner\n', 1)[1]
        self.assertIn("if: matrix.os == 'ubuntu-latest'", root_test)
        self.assertIn('sudo "$(command -v python)" -B -m unittest discover', root_test)
        self.assertIn('-p "test_ssh_ci_claims.py"', root_test)
        for forbidden in ('secrets.', 'environment:', 'self-hosted', 'SZL_CPU_SSH', 'continue-on-error:'):
            self.assertNotIn(forbidden, contract)

    def test_remote_workflow_is_manual_only_with_required_cpu_permit(self):
        triggers = TEXT.split('\non:\n', 1)[1].split('\npermissions:\n', 1)[0]
        self.assertEqual(re.findall(r'^  ([a-z_]+):', triggers, re.M), ['workflow_dispatch'])
        self.assertIn('      permit_id:', triggers)
        self.assertIn('        required: true', triggers)
        self.assertIn('never a GPU grant', triggers)

    def test_trusted_context_gates_cannot_be_removed(self):
        for gate in ("github.repository_id == '1389873753'",
                     "github.repository == 'szl-holdings/szl-skills'",
                     "github.ref == 'refs/heads/main'",
                     "github.event_name == 'workflow_dispatch'",
                     'github.run_attempt == 1'):
            self.assertIn(gate, TEXT)
        self.assertIn('    environment: massed-cpu-build\n', TEXT)
        self.assertIn('    runs-on: ubuntu-latest\n', TEXT)
        self.assertIn('    timeout-minutes: 15\n', TEXT)
        self.assertNotIn('self-hosted', TEXT)

    def test_permissions_action_pins_and_non_cancellation(self):
        permissions = TEXT.split('\npermissions:\n', 1)[1].split('\nconcurrency:\n', 1)[0]
        self.assertEqual(permissions.strip(), 'contents: read')
        self.assertIn('  group: szl-skills-massed-cpu-build\n', TEXT)
        self.assertIn('  cancel-in-progress: false\n', TEXT)
        actions = re.findall(r'^\s+(?:- )?uses: (.+)$', TEXT, re.M)
        self.assertEqual(len(actions), 3)
        for action in actions:
            self.assertRegex(action, r'^[\w-]+/[\w-]+@[a-f0-9]{40}$')
        self.assertIn('          persist-credentials: false\n', TEXT)
        self.assertNotIn('continue-on-error:', TEXT)

    def test_disabled_activation_precedes_secrets_and_submit(self):
        admission = step('Require separately provisioned and owner-qualified CPU boundary')
        self.assertIn('CPU_CI_ENABLED: ${{ vars.SZL_CPU_CI_ENABLED }}', admission)
        self.assertIn('if [ "$CPU_CI_ENABLED" != "true" ]; then', admission)
        self.assertIn('exit 1', admission)
        self.assertLess(TEXT.index('name: Require separately'), TEXT.index('name: Materialize'))
        self.assertLess(TEXT.index('name: Materialize'), TEXT.index('id: submit'))

    def test_submit_input_is_environment_bound_not_interpolated_into_shell(self):
        submit = step('Submit one owner-permitted CPU build and verify its response')
        self.assertIn('        id: submit\n', submit)
        self.assertIn('PERMIT_ID: ${{ inputs.permit_id }}', submit)
        self.assertIn('--permit-id "$PERMIT_ID" --output-dir ssh-ci-evidence', submit)
        run = submit.split('        run: ', 1)[1]
        self.assertNotIn('${{', run)
        self.assertEqual(TEXT.count('tools/ssh_ci_client.py'), 1)

    def test_bootstrap_then_upload_then_cleanup_always_run(self):
        names = (BOOTSTRAP_NAME, 'Preserve available success or failure evidence',
                 "Remove this job's temporary credential files")
        for name in names:
            self.assertIn('        if: always()\n', step(name))
        self.assertLess(TEXT.index(names[0]), TEXT.index(names[1]))
        self.assertLess(TEXT.index(names[1]), TEXT.index(names[2]))
        self.assertIn('CLIENT_STEP_OUTCOME: ${{ steps.submit.outcome }}', step(names[0]))
        self.assertIn('if-no-files-found: error', step(names[1]))
        self.assertNotIn('if-no-files-found: warn', TEXT)


class BootstrapReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / 'ssh-ci-evidence'
        self.receipt = self.output / 'client-receipt.json'

    def tearDown(self):
        self.temp.cleanup()

    def run_bootstrap(self, outcome, failure=True):
        with working_directory(self.root), mock.patch.dict(
                os.environ, {'CLIENT_STEP_OUTCOME': outcome}, clear=True):
            code = compile(bootstrap_source(), str(WORKFLOW) + ':bootstrap', 'exec')
            if failure:
                with self.assertRaises(SystemExit) as error:
                    exec(code, {'__name__': '__main__'})
                self.assertTrue(error.exception.code)
            else:
                exec(code, {'__name__': '__main__'})
        return json.loads(self.receipt.read_text(encoding='utf-8')) if self.receipt.is_file() else None

    def test_skipped_client_produces_explicit_zero_transport_failure_receipt(self):
        receipt = self.run_bootstrap('skipped')
        self.assertEqual(receipt['schema'], 'szl.ssh-cpu-build.bootstrap-receipt.v1')
        self.assertEqual(receipt['result'], 'BLOCKED')
        self.assertEqual(receipt['evidence_class'], 'MEASURED')
        self.assertEqual(receipt['transport'], {'attempts': 0, 'started': False,
                                               'remote_completion': 'UNKNOWN'})
        self.assertFalse(receipt['signed'])
        self.assertFalse(receipt['independent_witness'])
        self.assertFalse(receipt['publication_eligible'])
        self.assertEqual(receipt['authorized_inference_requests'], 0)

    def test_started_or_unavailable_step_outcome_cannot_claim_zero_attempts(self):
        for outcome in ('success', 'failure', 'cancelled', '', 'unrecognized'):
            with self.subTest(outcome=outcome):
                receipt = self.run_bootstrap(outcome)
                self.assertIsNone(receipt['transport']['attempts'])
                self.assertIsNone(receipt['transport']['started'])
                self.assertEqual(receipt['transport']['remote_completion'], 'UNKNOWN')
                self.receipt.unlink()

    def test_partial_client_evidence_overrides_skipped_zero_claim_and_is_preserved(self):
        self.output.mkdir()
        for name in ('request.json', 'response.raw.json', 'ssh.stderr', 'artifacts'):
            with self.subTest(name=name):
                evidence = self.output / name
                evidence.write_bytes(b'partial evidence preserved')
                receipt = self.run_bootstrap('skipped')
                self.assertIsNone(receipt['transport']['attempts'])
                self.assertIsNone(receipt['transport']['started'])
                self.assertEqual(evidence.read_bytes(), b'partial evidence preserved')
                evidence.unlink()
                self.receipt.unlink()

    def test_existing_client_receipt_is_never_parsed_rewritten_or_relabelled(self):
        self.output.mkdir()
        original = b'{"owner":"existing", "spaces":  true}\n'
        self.receipt.write_bytes(original)
        self.run_bootstrap('failure', failure=False)
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_repeat_bootstrap_preserves_first_receipt(self):
        self.run_bootstrap('skipped')
        original = self.receipt.read_bytes()
        self.run_bootstrap('failure', failure=False)
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_directory_at_receipt_path_is_rejected_without_overwrite(self):
        self.output.mkdir()
        self.receipt.mkdir()
        self.run_bootstrap('skipped')
        self.assertTrue(self.receipt.is_dir())


if __name__ == '__main__':
    unittest.main()
