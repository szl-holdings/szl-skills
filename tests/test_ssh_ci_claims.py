"""Offline claim tests; real root/POSIX integration never contacts a VM.

Unprivileged POSIX unit tests mock ownership metadata only. The root-only
integration class exercises the production checks without metadata mocks.
"""

import json
from datetime import datetime, timezone
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools import ssh_ci_claims as claims


REQUEST = "68958802-ceca-49e1-aef0-874608eb78dd"
OTHER_REQUEST = "3df8be70-99eb-4dca-beaf-730f28bf21b4"
PERMIT = "449ef997-8dd1-4f9e-8c62-32d65b343546"
DIGEST = "4" * 64
POSIX = os.name == "posix" and claims.fcntl is not None
ROOT_POSIX = POSIX and hasattr(os, "geteuid") and os.geteuid() == 0


def _competing_process(directory, result):
    """Spawn target: use only the production helper and ordinary root metadata."""
    try:
        with claims.ClaimStore(Path(directory)):
            result.put("ACQUIRED")
    except claims.ClaimError:
        result.put("BLOCKED")


class IdentityTests(unittest.TestCase):
    def test_canonical_identifiers(self):
        self.assertIsNone(claims.validate_identity(REQUEST, PERMIT, DIGEST))

    def test_bad_ids_before_storage(self):
        for bad in (None, True, 1, b"x", "", REQUEST.upper(), "../permit",
                    "{" + REQUEST + "}", REQUEST.replace("-", ""), "x" * 4097):
            for index in (0, 1):
                arguments = [REQUEST, PERMIT, DIGEST]
                arguments[index] = bad
                with self.subTest(bad=bad, index=index), self.assertRaises(claims.ClaimError):
                    claims.validate_identity(*arguments)

    def test_bad_digest(self):
        for bad in (None, True, 1, b"4" * 64, "", "a" * 63, "a" * 65,
                    "A" * 64, "z" * 64, "4" * 64 + "\n"):
            with self.subTest(bad=bad), self.assertRaises(claims.ClaimError):
                claims.validate_identity(REQUEST, PERMIT, bad)

    def test_claim_requires_entered_context(self):
        with self.assertRaises(claims.ClaimError):
            claims.ClaimStore(Path("unprovisioned")).claim(REQUEST, PERMIT, DIGEST)

    def test_windows_is_explicitly_unsupported(self):
        with mock.patch.object(claims.os, "name", "nt"), \
                self.assertRaisesRegex(claims.ClaimError, "POSIX"):
            with claims.ClaimStore("unprovisioned"):
                self.fail("unsupported platform must not enter")

    def test_missing_flock_is_explicitly_unsupported(self):
        with mock.patch.object(claims, "fcntl", None), \
                self.assertRaisesRegex(claims.ClaimError, "POSIX"):
            with claims.ClaimStore("unprovisioned"):
                self.fail("unsupported platform must not enter")


@unittest.skipUnless(POSIX, "requires POSIX descriptor semantics; Windows fails closed")
class PosixClaimUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="szl-claims-unit-")
        self.root = Path(self.temp.name)
        self.directory = self.root / "claims"
        self.directory.mkdir(mode=0o700)
        self.real_fstat = os.fstat
        self.controlled = {self._identity(self.root), self._identity(self.directory)}
        self.wrong_uid = set()
        self.metadata_patch = mock.patch.object(claims.os, "fstat", self._root_metadata)
        self.metadata_patch.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.metadata_patch.stop)

    @staticmethod
    def _identity(path):
        info = path.stat()
        return info.st_dev, info.st_ino

    def _root_metadata(self, descriptor):
        """Test-only owner substitution; preserve actual controlled permissions."""
        original = self.real_fstat(descriptor)
        key = original.st_dev, original.st_ino
        fields = list(original)
        fields[4] = 12345 if key in self.wrong_uid else 0
        if stat.S_ISDIR(original.st_mode) and key not in self.controlled:
            # Temporary-directory ancestors may be sticky shared directories.
            # Root integration below does not use this metadata substitution.
            fields[0] = stat.S_IFDIR | 0o755
        return os.stat_result(fields)

    def claim_file(self):
        return self.directory / (PERMIT + ".execution-claim.json")

    def test_claim_persists_after_context_and_refuses_new_request_replay(self):
        with claims.ClaimStore(self.directory) as store:
            receipt = store.claim(REQUEST, PERMIT, DIGEST)
            self.assertEqual(json.loads(self.claim_file().read_bytes()), receipt)
            self.assertEqual(receipt["schema"], claims.CLAIM_SCHEMA)
            self.assertEqual(receipt["evidence_class"], "MEASURED")
            self.assertFalse(receipt["signed"])
            self.assertFalse(receipt["independent_witness"])
            self.assertTrue(receipt["claimed_at_utc"].endswith("Z"))
            self.assertLessEqual(self.claim_file().stat().st_size, claims.MAX_CLAIM_BYTES)
        original = self.claim_file().read_bytes()
        with claims.ClaimStore(self.directory) as store:
            with self.assertRaisesRegex(claims.ClaimError, "already claimed"):
                store.claim(OTHER_REQUEST, PERMIT, "5" * 64)
        self.assertEqual(original, self.claim_file().read_bytes())
        self.assertTrue((self.directory / claims.LOCK_NAME).is_file())

    def test_context_lock_is_nonblocking_and_persistent(self):
        with claims.ClaimStore(self.directory):
            lock_identity = self._identity(self.directory / claims.LOCK_NAME)
            with self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(self.directory):
                    self.fail("second context must not enter")
        with claims.ClaimStore(self.directory):
            self.assertEqual(self._identity(self.directory / claims.LOCK_NAME), lock_identity)

    def test_missing_directory_not_created(self):
        missing = self.root / "missing"
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(missing):
                self.fail("missing directory must not enter")
        self.assertFalse(missing.exists())

    def test_relative_parent_traversal_and_root_rejected(self):
        for path in (Path("claims"), Path("/"), self.directory / ".." / "claims"):
            with self.subTest(path=path), self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(path):
                    self.fail("unsafe path must not enter")

    def test_unsafe_final_mode_rejected(self):
        for mode in (0o755, 0o770, 0o707, 0o777, 0o1700):
            self.directory.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(self.directory):
                    self.fail("unsafe directory mode must not enter")
        self.directory.chmod(0o700)

    def test_mode_0750_accepted(self):
        self.directory.chmod(0o750)
        with claims.ClaimStore(self.directory) as store:
            store.claim(REQUEST, PERMIT, DIGEST)

    def test_writable_ancestor_rejected(self):
        self.root.chmod(0o777)
        try:
            with self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(self.directory):
                    self.fail("writable ancestry must not enter")
        finally:
            self.root.chmod(0o700)

    def test_nonroot_directory_and_ancestor_rejected(self):
        for path in (self.root, self.directory):
            self.wrong_uid.add(self._identity(path))
            with self.subTest(path=path), self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(self.directory):
                    self.fail("untrusted owner must not enter")
            self.wrong_uid.clear()

    def test_symlink_directory_and_ancestor_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        parent_alias = self.root / "parent-alias"
        parent_alias.symlink_to(self.root, target_is_directory=True)
        for path in (alias, parent_alias / "claims"):
            with self.subTest(path=path), self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(path):
                    self.fail("symlink path must not enter")

    def test_symlink_lock_rejected_without_target_mutation(self):
        target = self.root / "target"
        target.write_bytes(b"preserve me")
        (self.directory / claims.LOCK_NAME).symlink_to(target)
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(self.directory):
                self.fail("symlink lock must not enter")
        self.assertEqual(target.read_bytes(), b"preserve me")

    def test_hardlinked_lock_rejected(self):
        target = self.root / "target"
        target.touch(mode=0o600)
        os.link(target, self.directory / claims.LOCK_NAME)
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(self.directory):
                self.fail("hardlinked lock must not enter")

    def test_fifo_lock_rejected_without_blocking(self):
        os.mkfifo(self.directory / claims.LOCK_NAME, 0o600)
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(self.directory):
                self.fail("FIFO lock must not enter")

    def test_wrong_lock_owner_mode_and_contents_rejected(self):
        lock = self.directory / claims.LOCK_NAME
        lock.touch(mode=0o600)
        self.wrong_uid.add(self._identity(lock))
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(self.directory):
                self.fail("untrusted lock owner must not enter")
        self.wrong_uid.clear()
        lock.chmod(0o640)
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(self.directory):
                self.fail("readable lock must not enter")
        lock.chmod(0o600)
        lock.write_bytes(b"not empty")
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(self.directory):
                self.fail("nonempty lock must not enter")

    def test_existing_claim_symlink_and_hardlink_never_followed_or_removed(self):
        target = self.root / "target"
        target.write_bytes(b"preserve me")
        target.chmod(0o600)
        self.claim_file().symlink_to(target)
        with claims.ClaimStore(self.directory) as store:
            with self.assertRaises(claims.ClaimError):
                store.claim(REQUEST, PERMIT, DIGEST)
        self.assertTrue(self.claim_file().is_symlink())
        self.assertEqual(target.read_bytes(), b"preserve me")
        # Test fixture replacement only; production helper never unlinks.
        self.claim_file().unlink()
        os.link(target, self.claim_file())
        with claims.ClaimStore(self.directory) as store:
            with self.assertRaises(claims.ClaimError):
                store.claim(REQUEST, PERMIT, DIGEST)
        self.assertEqual(target.read_bytes(), b"preserve me")
        self.assertEqual(target.stat().st_nlink, 2)

    def test_bad_input_creates_no_claim(self):
        with claims.ClaimStore(self.directory) as store:
            with self.assertRaises(claims.ClaimError):
                store.claim("../request", PERMIT, DIGEST)
        self.assertFalse(self.claim_file().exists())

    def test_reentrant_and_post_exit_claim_rejected(self):
        store = claims.ClaimStore(self.directory)
        with store:
            with self.assertRaises(claims.ClaimError):
                store.__enter__()
        with self.assertRaises(claims.ClaimError):
            store.claim(REQUEST, PERMIT, DIGEST)

    def test_claim_from_inherited_context_rejected(self):
        with claims.ClaimStore(self.directory) as store:
            with mock.patch.object(claims.os, "getpid", return_value=store._pid + 1):
                with self.assertRaises(claims.ClaimError):
                    store.claim(REQUEST, PERMIT, DIGEST)

    def test_write_failure_preserves_consumed_entry(self):
        with claims.ClaimStore(self.directory) as store:
            with mock.patch.object(claims.os, "write", side_effect=OSError("disk error")):
                with self.assertRaises(claims.ClaimError):
                    store.claim(REQUEST, PERMIT, DIGEST)
            self.assertTrue(self.claim_file().exists())
            with self.assertRaisesRegex(claims.ClaimError, "already claimed"):
                store.claim(REQUEST, PERMIT, DIGEST)

    def test_zero_length_write_preserves_consumed_entry(self):
        with claims.ClaimStore(self.directory) as store:
            with mock.patch.object(claims.os, "write", return_value=0):
                with self.assertRaises(claims.ClaimError):
                    store.claim(REQUEST, PERMIT, DIGEST)
        self.assertTrue(self.claim_file().exists())

    def test_partial_write_loop_and_fsync_both_descriptors(self):
        real_write, real_fsync = os.write, os.fsync
        with claims.ClaimStore(self.directory) as store:
            with mock.patch.object(claims.os, "write", side_effect=lambda fd, data:
                                   real_write(fd, data[:7])) as writer, \
                    mock.patch.object(claims.os, "fsync", wraps=real_fsync) as sync:
                receipt = store.claim(REQUEST, PERMIT, DIGEST)
            self.assertGreater(writer.call_count, 1)
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(sync.call_args_list[-1].args, (store._directory_fd,))
            self.assertNotEqual(sync.call_args_list[0].args, (store._directory_fd,))
        self.assertEqual(json.loads(self.claim_file().read_bytes()), receipt)

    def test_file_fsync_failure_still_attempts_directory_and_denies(self):
        with claims.ClaimStore(self.directory) as store:
            with mock.patch.object(claims.os, "fsync", side_effect=[OSError("disk error"), None]) as sync:
                with self.assertRaises(claims.ClaimError):
                    store.claim(REQUEST, PERMIT, DIGEST)
                self.assertEqual(sync.call_count, 2)
                self.assertEqual(sync.call_args_list[-1].args, (store._directory_fd,))
        self.assertTrue(self.claim_file().exists())

    def test_directory_fsync_failure_preserves_claim_and_denies(self):
        with claims.ClaimStore(self.directory) as store:
            with mock.patch.object(claims.os, "fsync", side_effect=[None, OSError("disk error")]):
                with self.assertRaises(claims.ClaimError):
                    store.claim(REQUEST, PERMIT, DIGEST)
        self.assertTrue(self.claim_file().exists())

    def test_callers_exception_leaves_claim_and_releases_lock(self):
        with self.assertRaisesRegex(RuntimeError, "caller failed"):
            with claims.ClaimStore(self.directory) as store:
                store.claim(REQUEST, PERMIT, DIGEST)
                raise RuntimeError("caller failed")
        with claims.ClaimStore(self.directory) as store:
            with self.assertRaises(claims.ClaimError):
                store.claim(REQUEST, PERMIT, DIGEST)


@unittest.skipUnless(ROOT_POSIX, "requires root POSIX integration; not a VM test")
class RootPosixIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Explicit, trusted test parent: no production path or shared cache is
        # ever removed. TemporaryDirectory cleans only its own unique child.
        self.temp = tempfile.TemporaryDirectory(prefix="szl-claims-integration-", dir="/run")
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_planned_request_validated_and_claimed_under_real_lock(self):
        from tools.ssh_ci_admission import validate_permit
        from tools.ssh_ci_contract import CPU_PROFILE
        from tools.ssh_ci_plan import plan_request, REPOSITORY_ID, WORKFLOW_PATH

        plan = plan_request({
            'id': 123, 'run_attempt': 1, 'event': 'workflow_dispatch',
            'path': WORKFLOW_PATH, 'head_branch': 'main', 'head_sha': '1' * 40,
            'status': 'waiting', 'repository': {
                'id': REPOSITORY_ID, 'full_name': 'szl-holdings/szl-skills'},
        }, PERMIT)
        host = {'hostname': 'synthetic-host', 'boot_id': REQUEST}
        permit = {
            'schema': 'szl.ssh-cpu-build.permit.v1', 'permit_id': PERMIT,
            'request_sha256': plan['request_sha256'], 'max_runs': 1,
            'not_before': '2026-01-01T00:00:00Z',
            'expires_at': '2026-01-01T00:15:00Z',
            'cpu_profile': dict(CPU_PROFILE), 'host': host,
            'workload': {'mode': 'pinned', 'pid': 123, 'start_ticks': 456},
        }
        observation = {
            'schema': 'szl.ssh-cpu-build.observation.v1',
            'observed_at': '2026-01-01T00:00:00Z', 'host': host,
            'gpu_compute_processes': [{'pid': 123, 'start_ticks': 456}],
            'competing_cpu_jobs': 0,
        }
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with claims.ClaimStore(self.directory) as store:
            invalid = dict(permit, request_sha256='f' * 64)
            with self.assertRaises(ValueError):
                validate_permit(plan['request'], invalid, observation, now)
            self.assertEqual(list(self.directory.glob('*.execution-claim.json')), [])
            validate_permit(plan['request'], permit, observation, now)
            receipt = store.claim(plan['request']['request_id'], PERMIT,
                                  plan['request_sha256'])
            self.assertEqual(receipt['request_sha256'], plan['request_sha256'])
            with self.assertRaises(claims.ClaimError):
                store.claim(plan['request']['request_id'], PERMIT, plan['request_sha256'])
        self.assertFalse(plan['execution_authorized'])

    def test_real_root_ownership_durable_replay(self):
        with claims.ClaimStore(self.directory) as store:
            receipt = store.claim(REQUEST, PERMIT, DIGEST)
        claim = self.directory / (PERMIT + ".execution-claim.json")
        self.assertEqual(claim.stat().st_uid, 0)
        self.assertEqual(stat.S_IMODE(claim.stat().st_mode), 0o600)
        self.assertEqual(json.loads(claim.read_bytes()), receipt)
        with claims.ClaimStore(self.directory) as store:
            with self.assertRaises(claims.ClaimError):
                store.claim(OTHER_REQUEST, PERMIT, "5" * 64)
        self.assertEqual(json.loads(claim.read_bytes()), receipt)

    def _contender(self):
        context = multiprocessing.get_context("spawn")
        result = context.Queue()
        process = context.Process(target=_competing_process,
                                  args=(str(self.directory), result))
        process.start()
        try:
            observed = result.get(timeout=10)
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "claim contender must not hang")
            self.assertEqual(process.exitcode, 0)
            return observed
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            result.close()
            result.join_thread()

    def test_real_multiprocess_contention_and_release(self):
        with claims.ClaimStore(self.directory):
            self.assertEqual(self._contender(), "BLOCKED")
        self.assertEqual(self._contender(), "ACQUIRED")

    def test_real_untrusted_owner_is_rejected(self):
        os.chown(self.directory, 65534, -1)
        try:
            with self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(self.directory):
                    self.fail("nonroot directory must not enter")
        finally:
            os.chown(self.directory, 0, -1)

    def test_real_unsafe_mode_and_symlink_rejected(self):
        self.directory.chmod(0o777)
        try:
            with self.assertRaises(claims.ClaimError):
                with claims.ClaimStore(self.directory):
                    self.fail("writable directory must not enter")
        finally:
            self.directory.chmod(0o700)
        target = self.directory / "safe"
        target.mkdir(mode=0o700)
        alias = self.directory / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(claims.ClaimError):
            with claims.ClaimStore(alias):
                self.fail("symlink directory must not enter")


if __name__ == "__main__":
    unittest.main()
