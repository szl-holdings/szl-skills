"""Offline owner-signature tests; real ssh-keygen when present, never a VM."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

from tests.test_ssh_ci_executor import fixtures
from tools import ssh_ci_contract as contract
from tools import ssh_ci_owner_signature as signature


SSH_KEYGEN = shutil.which("ssh-keygen")
ROOT_POSIX = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0


def keypair(directory, comment="owner"):
    key = Path(directory) / ("key-" + uuid.uuid4().hex)
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)],
                   check=True, capture_output=True, timeout=30)
    return key


def sign(key, data, namespace=signature.NAMESPACE):
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "bundle.json"
        path.write_bytes(data)
        subprocess.run([SSH_KEYGEN, "-q", "-Y", "sign", "-f", str(key), "-n", namespace, str(path)],
                       check=True, capture_output=True, timeout=30)
        return (Path(scratch) / "bundle.json.sig").read_bytes()


class BundleTests(unittest.TestCase):
    def test_bundle_is_canonical_and_round_trips(self):
        request, permit, _observation, window, _now = fixtures()
        data = signature.bundle(permit, window)
        self.assertEqual(data, contract.canonical(signature.parse_bundle(data)))
        self.assertEqual(signature.parse_bundle(data)["permit"]["permit_id"], request["permit_id"])

    def test_noncanonical_duplicate_or_extra_bundles_are_rejected(self):
        _request, permit, _observation, window, _now = fixtures()
        data = signature.bundle(permit, window)
        spaced = data.replace(b'{"permit"', b'{ "permit"', 1)
        duplicate = data[:-1] + b',"schema":"x"}'
        extra = contract.canonical({"schema": signature.BUNDLE_SCHEMA, "permit": permit,
                                    "window": window, "gpu_grant": True})
        wrong = contract.canonical({"schema": "szl.gpu-grant.v1", "permit": permit,
                                    "window": window})
        for bad in (spaced, duplicate, extra, wrong, b"[]", b""):
            with self.subTest(bad=bad[:40]), self.assertRaises(contract.ContractError):
                signature.parse_bundle(bad)


@unittest.skipUnless(os.name == "posix" and SSH_KEYGEN, "POSIX ssh-keygen is not installed")
class RealSignatureTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.key = keypair(self.root)
        public = (self.root / (self.key.name + ".pub")).read_text(encoding="ascii").split()
        self.allowed = self.root / "allowed_signers"
        self.allowed.write_text('stephen namespaces="%s" %s %s\n'
                                % (signature.NAMESPACE, public[0], public[1]), encoding="ascii")
        _request, self.permit, _observation, self.window, _now = fixtures()
        self.data = signature.bundle(self.permit, self.window)

    def test_owner_signature_verifies_exact_bytes(self):
        value = signature.verify_bundle(self.data, sign(self.key, self.data), self.allowed, "stephen",
                                        ssh_keygen=SSH_KEYGEN)
        self.assertEqual(value["permit"], self.permit)

    def test_tampered_bytes_wrong_principal_namespace_and_key_are_rejected(self):
        good = sign(self.key, self.data)
        altered = dict(self.permit, expires_at="2030-01-02T03:19:01Z")
        cases = [
            (signature.bundle(altered, self.window), good, "stephen"),
            (self.data, good, "mallory"),
            (self.data, sign(self.key, self.data, namespace="git"), "stephen"),
            (self.data, sign(keypair(self.root, "other"), self.data), "stephen"),
        ]
        for data, sig, principal in cases:
            with self.subTest(principal=principal), self.assertRaises(contract.ContractError):
                signature.verify_bundle(data, sig, self.allowed, principal, ssh_keygen=SSH_KEYGEN)

    def test_malformed_signature_inputs_never_reach_verifier(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("verifier must not run")
        for bad in (b"", b"x" * (signature.MAX_SIGNATURE_BYTES + 1), b"not armored", "text"):
            with self.subTest(bad=str(bad)[:20]), self.assertRaises(contract.ContractError):
                signature.verify_bundle(self.data, bad, self.allowed, "stephen", run=forbidden)
        for principal in ("", "a b", "-I", "x" * 65, None):
            with self.subTest(principal=principal), self.assertRaises(contract.ContractError):
                signature.verify_bundle(self.data, b"-----BEGIN SSH SIGNATURE-----\n",
                                        self.allowed, principal, run=forbidden)

    def test_verifier_failure_is_rejection(self):
        def unavailable(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("ssh-keygen", 15)
        with self.assertRaisesRegex(contract.ContractError, "unavailable"):
            signature.verify_bundle(self.data, sign(self.key, self.data), self.allowed,
                                    "stephen", run=unavailable)

    def test_loader_returns_authenticated_owner_permit_only_after_verification(self):
        request = fixtures()[0]
        spool = self.root / "permits"
        spool.mkdir()
        (spool / (request["permit_id"] + signature.BUNDLE_SUFFIX)).write_bytes(self.data)
        (spool / (request["permit_id"] + signature.SIGNATURE_SUFFIX)).write_bytes(
            sign(self.key, self.data))

        def reader(path, limit):
            return Path(path).read_bytes()[:limit + 1]
        loader = signature.make_permit_loader(spool, self.allowed, "stephen", reader=reader,
                                               ssh_keygen=SSH_KEYGEN)
        loaded = loader(request)
        self.assertIs(loaded.owner_authenticated, True)
        self.assertEqual(loaded.window, self.window)
        other = dict(request, permit_id="11111111-2222-4333-8444-555555555555")
        with self.assertRaises(FileNotFoundError):
            loader(other)

    def test_loader_rejects_bundle_signed_for_another_permit(self):
        request = fixtures()[0]
        foreign = dict(self.permit, permit_id="11111111-2222-4333-8444-555555555555")
        data = signature.bundle(foreign, self.window)
        spool = self.root / "permits"
        spool.mkdir()
        (spool / (request["permit_id"] + signature.BUNDLE_SUFFIX)).write_bytes(data)
        (spool / (request["permit_id"] + signature.SIGNATURE_SUFFIX)).write_bytes(sign(self.key, data))
        loader = signature.make_permit_loader(
            spool, self.allowed, "stephen", reader=lambda path, limit: Path(path).read_bytes(),
            ssh_keygen=SSH_KEYGEN)
        with self.assertRaisesRegex(contract.ContractError, "another permit"):
            loader(request)


@unittest.skipUnless(ROOT_POSIX, "root-owned protected-file checks need POSIX root")
class ProtectedReadTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/run") / ("szl-signature-test-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree, self.root)

    def write(self, name, data=b"{}", mode=0o600):
        path = self.root / name
        path.write_bytes(data)
        os.chmod(path, mode)
        return path

    def test_reads_bounded_root_owned_regular_file(self):
        self.assertEqual(signature.read_protected(self.write("ok"), 16), b"{}")

    def test_rejects_symlink_writable_hardlinked_oversized_and_relative(self):
        target = self.write("target")
        os.symlink(target, self.root / "link")
        hard = self.write("hard")
        os.link(hard, self.root / "hard2")
        cases = [self.root / "link", self.write("writable", mode=0o666), hard,
                 self.write("large", b"x" * 17), Path("relative"), self.root / "missing"]
        for path in cases:
            with self.subTest(path=path.name), self.assertRaises((contract.ContractError, OSError)):
                signature.read_protected(path, 16)

    def test_rejects_symlinked_or_writable_ancestor(self):
        inner = self.root / "inner"
        inner.mkdir(mode=0o755)
        (inner / "file").write_bytes(b"{}")
        os.chmod(inner / "file", 0o600)
        os.symlink(inner, self.root / "alias")
        with self.assertRaises(OSError):
            signature.read_protected(self.root / "alias" / "file", 16)
        os.chmod(inner, 0o777)
        with self.assertRaisesRegex(contract.ContractError, "ancestry"):
            signature.read_protected(inner / "file", 16)

    def test_non_root_owner_is_rejected(self):
        path = self.write("owned")
        os.chown(path, 65534, 65534)
        with self.assertRaisesRegex(contract.ContractError, "root-owned"):
            signature.read_protected(path, 16)


if __name__ == "__main__":
    unittest.main()
