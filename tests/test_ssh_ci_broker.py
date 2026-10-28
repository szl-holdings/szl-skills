"""Offline broker tests; the systemd unit, mount, and git staging are faked.

The root-only integration case uses the real owner-signature verifier, the real
root-owned claim store, and real file collection, with no VM or network.
"""

import base64
import copy
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import uuid

from tests.test_ssh_ci_executor import FakeStore, fixtures
from tools import ssh_ci_broker as broker
from tools import ssh_ci_contract as contract
from tools import ssh_ci_executor as executor
from tools import ssh_ci_owner_signature as signature


POSIX = os.name == "posix" and hasattr(os, "O_NOFOLLOW")
ROOT_POSIX = POSIX and os.geteuid() == 0
SSH_KEYGEN = shutil.which("ssh-keygen")


def json_load(path):
    return contract.strict_json_loads(Path(path).read_bytes())


def config(root):
    return {
        "schema": broker.CONFIG_SCHEMA, "owner_principal": "stephen",
        "allowed_signers": str(root / "allowed_signers"), "permit_dir": str(root / "permits"),
        "claim_dir": str(root / "claims"), "receipt_dir": str(root / "receipts"),
        "public_receipt_dir": str(root / "public"),
        "run_root": str(root / "run"), "build_user": "szl-build",
        "build_python": "/usr/bin/python3",
        "runner": "/opt/szl-cpu-broker/current/tools/ssh_ci_step_runner.py",
        "nvidia_smi": "/usr/bin/nvidia-smi", "hidden_paths": ["/opt/models"],
    }


def write_outputs(out, codes=(0, 0, 0, 0), extra=None):
    for name in contract.STEP_NAMES:
        (out / (name + ".stdout")).write_bytes(name.encode() + b" ok\n")
        (out / (name + ".stderr")).write_bytes(b"")
    packages = out / "packages"
    packages.mkdir()
    (packages / "szl-evidence-gate.zip").write_bytes(b"PK-synthetic-archive")
    (packages / broker.MANIFEST_NAME).write_bytes(b'{"schema":"szl.science-package-manifest.v1"}\n')
    if extra:
        extra(out)
    return ('{"failure":null,"prepared":true,"schema":"szl.ssh-cpu-build.runner.v1",'
            '"started":true,"steps":[%s],"truncated":[]}\n' % ",".join(
                '{"name":"%s","returncode":%d}' % pair
                for pair in zip(contract.STEP_NAMES, codes))).encode()


class TestBuild(broker.Build):
    scan_result = ([], [])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scan = lambda: TestBuild.scan_result

    def _build_ids(self):
        return os.getuid(), os.getgid()


@unittest.skipUnless(POSIX, "broker file collection requires POSIX no-follow opens")
class BrokerHandleTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        (self.root / "run").mkdir()
        self.config = config(self.root)
        self.request, self.permit, self.observation, self.window, self.now = fixtures()
        self.commands = []
        self.after = copy.deepcopy(self.observation)
        self.codes = (0, 0, 0, 0)
        self.readback = {"verified": True}
        self.extra = None
        self.claims = []

    def fake_run(self, argv, **_kwargs):
        self.commands.append(argv)
        output = b""
        if argv[0] == broker.GIT and "rev-parse" in argv:
            output = self.request["source_sha"].encode()
        elif argv[0] == broker.GIT and "cat-file" in argv:
            output = b"commit"
        return subprocess.CompletedProcess(argv, 0, output, b"")

    def fake_unit(self, request_id, argv, user, **_kwargs):
        self.assertEqual(request_id, self.request["request_id"])
        self.assertEqual(user, "szl-build")
        out = Path(next(item for item in argv if item.startswith("--property=BindPaths="))
                   .split("=", 2)[2].split(":")[0])
        stdout = write_outputs(out, self.codes, self.extra)
        return {"unit": "u", "readback": self.readback, "go_sent": self.readback["verified"],
                "timed_out": False, "exit_code": 0 if all(c == 0 for c in self.codes) else 1,
                "runner_stdout": stdout, "runner_stderr": b"", "truncated": [], "unit_gone": True}

    def handle(self, request_bytes=None):
        observations = iter([self.observation, self.observation, self.after])

        def store():
            return FakeStore(self.claims)
        original = broker.Build
        broker.Build = TestBuild
        try:
            return broker.handle(
                contract.canonical(self.request) if request_bytes is None else request_bytes,
                self.config, clock=lambda: self.now, run=self.fake_run,
                observer=lambda: copy.deepcopy(next(observations)),
                claim_store_factory=store,
                permit_loader=lambda _request: executor.OwnerPermit(
                    self.permit, self.window, True),
                run_unit=self.fake_unit)
        finally:
            broker.Build = original

    def test_pass_emits_contract_verified_envelope_and_cleans_run_root(self):
        status, body, record = self.handle()
        self.assertEqual(status, b"0")
        envelope = contract.strict_json_loads(body)
        files = contract.verify_response(self.request, envelope)
        self.assertEqual(envelope["receipt"]["result"], "PASS")
        self.assertEqual(envelope["receipt"]["runtime_before"]["pid"], 123)
        with tarfile.open(fileobj=io.BytesIO(files["packages.tar"])) as archive:
            self.assertEqual(archive.getnames(), ["szl-evidence-gate.zip"])
        self.assertEqual(self.claims, ["lock", "claim", "unlock"])
        self.assertEqual(record["execution"]["result"], "PASS")
        self.assertEqual(record["response_sha256"], contract.sha256(body))
        self.assertEqual(list((self.root / "run").iterdir()), [])
        mounts = [argv for argv in self.commands if argv[0] == broker.MOUNT]
        self.assertEqual(mounts[0][-1], str(self.root / "run" / self.request["request_id"]))
        fetch = next(argv for argv in self.commands if "fetch" in argv)
        self.assertEqual(fetch[-2:], [broker.SOURCE_URL, self.request["source_sha"]])
        self.assertIn("core.hooksPath=/dev/null", fetch)

    def test_failed_step_is_a_retained_blocked_envelope(self):
        self.codes = (0, 0, 1, 0)
        status, body, _record = self.handle()
        self.assertEqual(status, b"0")
        envelope = contract.strict_json_loads(body)
        contract.verify_response(self.request, envelope)
        self.assertEqual(envelope["receipt"]["result"], "BLOCKED")

    def test_model_identity_change_blocks_without_envelope(self):
        self.after["gpu_compute_processes"] = [{"pid": 123, "start_ticks": 9999}]
        status, body, record = self.handle()
        self.assertEqual(status, b"1")
        notice = contract.strict_json_loads(body)
        self.assertEqual(notice["detail"], "MODEL_IDENTITY_CHANGED")
        self.assertEqual(notice["claim_status"], "WRITTEN")
        self.assertIs(notice["retry"], False)
        self.assertEqual(record["build"]["failure"], "MODEL_IDENTITY_CHANGED")

    def test_unverified_isolation_blocks_without_envelope(self):
        self.readback = {"verified": False, "failure": "READBACK_MISMATCH"}
        status, body, _record = self.handle()
        self.assertEqual(status, b"1")
        self.assertEqual(contract.strict_json_loads(body)["detail"], "ISOLATION_READBACK_FAILED")

    def test_unexpected_package_entry_blocks_result(self):
        self.extra = lambda out: os.symlink("/etc/passwd", out / "packages" / "evil.zip")
        status, body, _record = self.handle()
        self.assertEqual(status, b"1")
        self.assertEqual(contract.strict_json_loads(body)["detail"], "OUTPUT_FILE_REJECTED")

    def test_unhideable_host_path_blocks_before_the_unit_starts(self):
        TestBuild.scan_result = ([], ["unhideable host path name"])
        try:
            status, body, _record = self.handle()
        finally:
            TestBuild.scan_result = ([], [])
        self.assertEqual(status, b"1")
        self.assertEqual(contract.strict_json_loads(body)["detail"], "UNHIDEABLE_HOST_PATH")

    def test_scanned_paths_are_hidden_in_the_unit(self):
        TestBuild.scan_result = (["/data", "/run/snapd.socket"], [])
        seen = []
        original = self.fake_unit

        def capture(request_id, argv, user, **kwargs):
            seen.extend(argv)
            return original(request_id, argv, user, **kwargs)
        self.fake_unit = capture
        try:
            status, _body, _record = self.handle()
        finally:
            TestBuild.scan_result = ([], [])
        self.assertEqual(status, b"0")
        self.assertIn("--property=InaccessiblePaths=-/data", seen)
        self.assertIn("--property=InaccessiblePaths=-/run/snapd.socket", seen)

    def test_stale_run_directories_from_a_killed_broker_are_removed(self):
        stale = self.root / "run" / "11111111-2222-4333-8444-555555555555"
        (stale / "out").mkdir(parents=True)
        status, _body, _record = self.handle()
        self.assertEqual(status, b"0")
        self.assertFalse(stale.exists())
        self.assertIn([broker.UMOUNT, str(stale)], [argv[:2] for argv in self.commands])

    def test_package_entry_flood_is_rejected(self):
        def flood(out):
            for index in range(broker.MAX_PACKAGE_ENTRIES + 5):
                (out / "packages" / ("a%d.zip" % index)).write_bytes(b"")
        self.extra = flood
        status, body, _record = self.handle()
        self.assertEqual(status, b"1")
        self.assertEqual(contract.strict_json_loads(body)["detail"], "OUTPUT_FILE_REJECTED")

    def test_receipts_are_written_and_a_receipt_failure_withholds_the_envelope(self):
        for name in ("receipts", "public"):
            (self.root / name).mkdir()
        status, body, record = self.handle()
        broker.write_receipts(self.config, record)
        public = json_load(self.root / "public" / (self.request["request_id"] + ".json"))
        self.assertEqual(public["response_sha256"], contract.sha256(body))
        self.assertEqual(public["execution"]["result"], "PASS")
        self.assertNotIn("files", public)
        broker.write_receipts(self.config, record)  # A replay never overwrites the first.
        self.assertEqual(len(list((self.root / "public").iterdir())), 2)
        output = io.BytesIO()
        original_load, original_handle = broker.load_config, broker.handle
        broker.load_config = lambda _path: dict(self.config, receipt_dir=str(self.root / "missing"))
        broker.handle = lambda *_args, **_kwargs: (status, body, record)
        try:
            code = broker.main([], stdin=io.BytesIO(b"{}"), stdout=output)
        finally:
            broker.load_config, broker.handle = original_load, original_handle
        self.assertEqual(code, 1)
        notice = contract.strict_json_loads(output.getvalue()[1:])
        self.assertEqual(notice["reason"], "BROKER_RECEIPT_UNAVAILABLE")

    def test_invalid_request_is_blocked_before_any_component(self):
        for bad in (b"", b"{}", contract.canonical(dict(self.request, operation="shell")),
                    b"x" * (contract.MAX_REQUEST_BYTES + 1)):
            with self.subTest(bad=bad[:20]):
                status, body, record = self.handle(bad)
                self.assertEqual(status, b"1")
                self.assertEqual(contract.strict_json_loads(body)["reason"], "INVALID_REQUEST")
                self.assertEqual(self.commands, [])
                self.assertIsNone(record["request_id"])


class ConfigAndCollectionTests(unittest.TestCase):
    def test_config_is_closed_and_paths_are_plain(self):
        root = Path("/etc/szl-cpu-broker")
        good = config(root)
        self.assertEqual(broker.parse_config(contract.canonical(good)), good)
        for change in ({"extra": 1}, {"build_user": "root"}, {"runner": "relative.py"},
                       {"hidden_paths": ["/ok", "/../etc"]}, {"schema": "x"},
                       {"owner_principal": ""}):
            with self.subTest(change=change), self.assertRaises(contract.ContractError):
                broker.parse_config(contract.canonical(dict(good, **change)))

    def test_deterministic_tar_has_fixed_metadata(self):
        first = broker.deterministic_tar({"b.zip": b"2", "a.zip": b"1"})
        self.assertEqual(first, broker.deterministic_tar({"a.zip": b"1", "b.zip": b"2"}))
        with tarfile.open(fileobj=io.BytesIO(first)) as archive:
            members = archive.getmembers()
        self.assertEqual([member.name for member in members], ["a.zip", "b.zip"])
        self.assertTrue(all(member.mtime == 0 and member.uid == 0 and member.isreg()
                            for member in members))

    def test_runner_output_must_be_one_exact_line(self):
        good = ('{"schema":"szl.ssh-cpu-build.runner.v1","started":true,"prepared":true,'
                '"steps":[%s],"truncated":[]}' % ",".join(
                    '{"name":"%s","returncode":0}' % name for name in contract.STEP_NAMES)).encode()
        self.assertIsNotNone(broker.parse_runner(good + b"\n"))
        for bad in (b"", good + b"\n" + good, good.replace(b"selfcheck", b"shell"), b"[]"):
            with self.subTest(bad=bad[:30]):
                self.assertIsNone(broker.parse_runner(bad))


@unittest.skipUnless(ROOT_POSIX and SSH_KEYGEN, "root integration needs POSIX root and ssh-keygen")
class RootIntegrationTests(unittest.TestCase):
    """Real signature, real root-owned claim store, faked unit/mount/git only."""

    def setUp(self):
        self.root = Path("/run") / ("szl-broker-test-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o755)
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("permits", "claims", "receipts", "run"):
            (self.root / name).mkdir(mode=0o700)
        key = self.root / "owner-key"
        subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       check=True, capture_output=True, timeout=30)
        public = (self.root / "owner-key.pub").read_text().split()
        allowed = self.root / "allowed_signers"
        allowed.write_text('stephen namespaces="%s" %s %s\n' % (signature.NAMESPACE, *public[:2]))
        os.chmod(allowed, 0o644)
        self.request, self.permit, self.observation, self.window, self.now = fixtures()
        self.bundle = self.root / "permits" / (self.request["permit_id"] + signature.BUNDLE_SUFFIX)
        self.bundle.write_bytes(signature.bundle(self.permit, self.window))
        subprocess.run([SSH_KEYGEN, "-q", "-Y", "sign", "-f", str(key), "-n", signature.NAMESPACE,
                        str(self.bundle)], check=True, capture_output=True, timeout=30)
        for path in (self.bundle, Path(str(self.bundle) + ".sig")):
            os.chmod(path, 0o600)
        key.unlink()
        self.config = config(self.root)
        self.harness = BrokerHandleTests("test_pass_emits_contract_verified_envelope_and_cleans_run_root")
        self.harness.request, self.harness.commands = self.request, []
        self.harness.codes, self.harness.readback, self.harness.extra = (0, 0, 0, 0), {"verified": True}, None

    def handle(self, run=None):
        original = broker.Build
        broker.Build = TestBuild
        try:
            return broker.handle(
                contract.canonical(self.request), self.config, clock=lambda: self.now,
                run=run or self.harness.fake_run,
                observer=lambda: copy.deepcopy(self.observation),
                permit_loader=signature.make_permit_loader(
                    self.config["permit_dir"], self.config["allowed_signers"], "stephen",
                    ssh_keygen=SSH_KEYGEN),
                run_unit=self.harness.fake_unit)
        finally:
            broker.Build = original

    def test_signed_permit_runs_once_and_replay_is_blocked(self):
        status, body, record = self.handle()
        self.assertEqual(status, b"0", body[:300])
        self.assertEqual(contract.strict_json_loads(body)["receipt"]["result"], "PASS")
        self.assertEqual(record["execution"]["claim_status"], "WRITTEN")
        self.assertEqual(len(list((self.root / "claims").glob("*.execution-claim.json"))), 1)
        replay_status, replay_body, _ = self.handle()
        self.assertEqual(replay_status, b"1")
        self.assertEqual(contract.strict_json_loads(replay_body)["reason"], "CLAIM_UNAVAILABLE")
        self.assertEqual(len(list((self.root / "claims").glob("*.execution-claim.json"))), 1)

    def test_tampered_permit_is_not_owner_authenticated(self):
        self.bundle.write_bytes(signature.bundle(
            dict(self.permit, expires_at="2030-01-02T03:18:59Z"), self.window))
        status, body, _ = self.handle(run=lambda *a, **k: self.fail("no command may run"))
        self.assertEqual(status, b"1")
        self.assertEqual(contract.strict_json_loads(body)["reason"], "PERMIT_LOADER_UNAVAILABLE")
        self.assertEqual(sorted(path.name for path in (self.root / "claims").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
