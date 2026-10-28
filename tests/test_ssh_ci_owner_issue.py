"""Offline owner issue-flow tests; gh, ssh, and the VM are faked."""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

from tests.test_ssh_ci_broker import config as broker_config
from tools import ssh_ci_contract as contract
from tools import ssh_ci_owner_issue as owner_issue
from tools import ssh_ci_owner_signature as signature
from tools import ssh_ci_plan as plan


SHA = "c" * 40
HOST = {"hostname": "0136-ict-prxmx50112", "boot_id": "6912b71a-bd5c-4ef2-b65f-76322fbb50c1"}
OWNER_CONFIG = {
    "schema": owner_issue.OWNER_CONFIG_SCHEMA,
    "provider_instance_uuid": "bd6e0a2e-c7d1-4423-bfc2-99b575c6a233",
    "hf_revision": "dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f",
    "budget": {"ledger_ref": "ledger://massed-compute/2026-10-28", "remaining_seconds": 7200,
               "remaining_usd": 25.0},
    "checkpoint_manifest_sha256": "d" * 64,
    "owner_receipt_ref": "owner://vm2/cpu-window/2026-10-28",
}
ROOT_POSIX = os.name == "posix" and os.geteuid() == 0
SSH_KEYGEN = shutil.which("ssh-keygen")


RUN_METADATA = {"id": 777, "status": "waiting", "event": "workflow_dispatch",
                "head_branch": "main", "head_sha": SHA, "run_attempt": 1,
                "path": plan.WORKFLOW_PATH,
                "repository": {"id": plan.REPOSITORY_ID, "full_name": owner_issue.REPOSITORY}}


def envelope_for(request, result="PASS"):
    """A contract-valid v1 response, as the VM broker would send it."""
    digest = contract.sha256(contract.canonical(request))
    files = {name: name.encode() for name in sorted(contract.REQUIRED_FILES)}
    runtime = dict(HOST, pid=445607, start_ticks=98765)
    receipt = {field: request[field] for field in contract.BINDING_FIELDS}
    receipt.update(schema=contract.RECEIPT_SCHEMA, request_sha256=digest, result=result,
                   evidence_class="DECLARED", signed=False, independent_witness=False,
                   gpu_requests=0, actual_cpu_profile=dict(contract.CPU_PROFILE),
                   isolation_verified=True, runtime_before=runtime, runtime_after=dict(runtime),
                   steps=[{"name": name, "returncode": 0 if result == "PASS" else 1}
                          for name in contract.STEP_NAMES],
                   files={name: {"sha256": contract.sha256(data), "bytes": len(data)}
                          for name, data in files.items()})
    return contract.canonical({"schema": contract.RESPONSE_SCHEMA, "request_sha256": digest,
                               "receipt": receipt,
                               "files": {name: base64.b64encode(data).decode()
                                         for name, data in files.items()}})


class FakeHost:
    """Emulates gh and ssh for one dispatched run and its VM."""

    def __init__(self, root):
        self.root, self.calls, self.permit_id = root, [], None
        self.reviewers = [{"type": "User", "reviewer": {"login": "owner"}}]
        self.processes = [{"pid": 445607, "start_ticks": 98765}]
        self.remote_result = "PASS"
        self.github_response = None  # Replaces what the GitHub artifact carries.
        self.time = 0.0
        self.observe_cost = 0.0

    def request(self):
        return plan.plan_request(RUN_METADATA, self.permit_id)["request"]

    def clock(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds

    def __call__(self, argv, input=None, **_kwargs):
        self.calls.append(argv)
        head = argv[0]
        out = b""
        if head == "gh":
            out = self.gh(argv[1:])
        elif head == "ssh" and argv[-1].startswith("cat "):
            raw = envelope_for(self.request(), self.remote_result)
            out = json.dumps({"request_sha256": contract.sha256(contract.canonical(self.request())),
                              "response_sha256": contract.sha256(raw),
                              "execution": {"result": "PASS"}}).encode()
        elif head == "ssh":
            self.time += self.observe_cost
            out = json.dumps({"schema": "szl.ssh-cpu-build.observation.v1",
                              "observed_at": "2030-01-02T03:04:05Z", "host": HOST,
                              "gpu_compute_processes": self.processes,
                              "competing_cpu_jobs": 0}).encode()
        return subprocess.CompletedProcess(argv, 0, out, b"")

    def gh(self, args):
        if args[:2] == ["api", "repos/%s/environments/massed-cpu-build" % owner_issue.REPOSITORY]:
            return json.dumps({"protection_rules": [
                {"type": "required_reviewers", "reviewers": self.reviewers}],
                "deployment_branch_policy": {"protected_branches": True,
                                             "custom_branch_policies": False},
                "can_admins_bypass": True}).encode()
        if args[:2] == ["variable", "list"]:
            return json.dumps([{"name": n} for n in owner_issue.REQUIRED_VARIABLES]).encode()
        if args[:2] == ["secret", "list"]:
            return json.dumps([{"name": "SZL_CPU_SSH_KEY"}]).encode()
        if args[:2] == ["workflow", "run"]:
            self.permit_id = args[-1].split("=", 1)[1]
            return b""
        if args[:2] == ["run", "list"]:
            return json.dumps([{"databaseId": 777, "displayTitle": "CPU build permit " + self.permit_id},
                               {"databaseId": 776, "displayTitle": "CPU build permit other"}]).encode()
        if args[:2] == ["api", "repos/%s/actions/runs/777" % owner_issue.REPOSITORY]:
            return json.dumps(RUN_METADATA).encode()
        if args[:2] == ["api", "repos/%s/actions/runs/777/pending_deployments" % owner_issue.REPOSITORY]:
            return json.dumps([{"environment": {"id": 55, "name": "massed-cpu-build"},
                                "current_user_can_approve": True}]).encode()
        if args[:3] == ["api", "-X", "POST"]:
            return b"[]"
        if args[:2] == ["run", "watch"]:
            return b""
        if args[:2] == ["run", "download"]:
            out = Path(args[args.index("-D") + 1])
            (out / "artifacts").mkdir(parents=True)
            raw = self.github_response or envelope_for(self.request(), self.remote_result)
            (out / "response.raw.json").write_bytes(raw)
            # The job's own client receipt always claims PASS; the owner ignores it.
            (out / "client-receipt.json").write_text(json.dumps({"result": "PASS"}))
            return b""
        raise AssertionError("unexpected gh call %r" % args)


class FakeOwner(owner_issue.Owner):
    def sign(self, data):
        self.signed = data
        return b"-----BEGIN SSH SIGNATURE-----\nsynthetic\n"


class IssueFlowTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        (self.root / "owner.json").write_text(json.dumps(OWNER_CONFIG))
        self.host = FakeHost(self.root)
        self.args = argparse.Namespace(
            owner_config=str(self.root / "owner.json"), source_sha=SHA, signing_key="key",
            gh="gh", ssh="ssh",
            ssh_keygen="ssh-keygen", admin_target="Ubuntu@203.0.113.10",
            admin_known_hosts=str(self.root / "known_hosts"), admin_key=None,
            evidence_dir=str(self.root / "evidence"))

    def issue(self):
        owner = FakeOwner(self.args, run=self.host, sleep=self.host.sleep, clock=self.host.clock)
        record = owner_issue.new_record()
        try:
            owner_issue.issue(self.args, record, owner=owner)
        except owner_issue.IssueError as error:
            record["failure"] = str(error)
        return record, owner

    def approvals(self):
        return [argv for argv in self.host.calls if argv[1:4] == ["api", "-X", "POST"]]

    def test_happy_path_binds_signs_installs_and_approves_exactly_one_run(self):
        record, owner = self.issue()
        self.assertEqual(record["result"], "PASS", record)
        self.assertEqual(record["stages"], ["environment", "waiting-run-bound", "permit-installed",
                                            "approved", "collected", "owner-verified"])
        self.assertEqual(sorted(record["verified_files"]), sorted(contract.REQUIRED_FILES))
        self.assertTrue((self.root / "evidence" / "run-777" / "owner-verified" /
                         "packages.tar").is_file())
        value = signature.parse_bundle(owner.signed)
        self.assertEqual(value["permit"]["permit_id"], self.host.permit_id)
        self.assertEqual(value["permit"]["workload"], {"mode": "pinned", "pid": 445607,
                                                       "start_ticks": 98765})
        self.assertEqual(value["permit"]["not_before"], "2030-01-02T03:04:05Z")
        self.assertEqual(value["permit"]["expires_at"], "2030-01-02T03:19:05Z")
        self.assertEqual(value["window"]["source"]["github_sha"], SHA)
        self.assertEqual(record["request_sha256"], value["permit"]["request_sha256"])
        approvals = self.approvals()
        self.assertEqual(len(approvals), 1)
        self.assertIn("environment_ids[]=55", approvals[0])
        self.assertIn("comment=CPU permit %s bound to request %s"
                      % (self.host.permit_id, record["request_sha256"]), approvals[0])
        install = next(argv for argv in self.host.calls if argv[0] == "ssh" and "-t" in argv)
        self.assertIn("sudo python3 -I -B /opt/szl-cpu-broker/current/tools/ssh_ci_owner_issue.py "
                      "install-permit --bundle-b64 " + base64.b64encode(owner.signed).decode(),
                      install[-1])
        for argv in (a for a in self.host.calls if a[0] == "ssh"):
            self.assertIn("StrictHostKeyChecking=yes", argv)

    def test_missing_reviewer_gate_stops_before_dispatch(self):
        self.host.reviewers = []
        record, _owner = self.issue()
        self.assertIn("required reviewer", record["failure"])
        self.assertFalse(any(argv[1:3] == ["workflow", "run"] for argv in self.host.calls))

    def test_idle_or_ambiguous_gpu_stops_before_signing_or_approval(self):
        for processes in ([], [{"pid": 1, "start_ticks": 2}, {"pid": 3, "start_ticks": 4}]):
            with self.subTest(processes=processes):
                self.setUp()
                self.host.processes = processes
                record, owner = self.issue()
                self.assertIn("exactly one pinned GPU process", record["failure"])
                self.assertFalse(hasattr(owner, "signed"))
                self.assertEqual(self.approvals(), [])

    def test_slow_owner_never_approves_a_short_window(self):
        self.host.observe_cost = owner_issue.MAX_APPROVAL_DELAY_SECONDS + 1
        record, _owner = self.issue()
        self.assertIn("too little window", record["failure"])
        self.assertEqual(self.approvals(), [])
        self.assertIn("permit-installed", record["stages"])

    def test_forged_github_side_envelope_is_rejected(self):
        # A client from the commit under test could ship its own self-consistent
        # PASS envelope; it is bound to another request, so owner checks reject it.
        self.host.github_response = envelope_for(
            plan.plan_request(RUN_METADATA, "00000000-0000-4000-8000-000000000000")["request"])
        record, _owner = self.issue()
        self.assertEqual(record["result"], "BLOCKED")
        self.assertIn("owner-side verification", record["failure"])

    def test_remote_blocked_is_blocked_even_if_the_client_says_pass(self):
        self.host.remote_result = "BLOCKED"
        record, _owner = self.issue()
        self.assertEqual(record["result"], "BLOCKED")
        self.assertIn("remote receipt is BLOCKED", record["verification_failures"])

    def test_downloaded_bytes_must_hash_to_the_vm_receipt(self):
        original_gh = self.host.gh

        def gh(args):
            output = original_gh(args)
            if args[:2] == ["run", "download"]:
                path = Path(args[args.index("-D") + 1]) / "response.raw.json"
                value = json.loads(path.read_bytes())
                path.write_bytes(json.dumps(value).encode())  # Same content, other bytes.
            return output
        self.host.gh = gh
        record, _owner = self.issue()
        self.assertEqual(record["result"], "BLOCKED")
        self.assertTrue(record["failure"] or record["verification_failures"])

    def test_tampered_response_bytes_fail_the_vm_hash(self):
        original_gh = self.host.gh

        def gh(args):
            output = original_gh(args)
            if args[:2] == ["run", "download"]:
                path = Path(args[args.index("-D") + 1]) / "response.raw.json"
                path.write_bytes(path.read_bytes() + b"\n")
            return output
        self.host.gh = gh
        record, _owner = self.issue()
        self.assertEqual(record["result"], "BLOCKED")
        self.assertIn("VM receipt response hash differs from the downloaded response",
                      record["verification_failures"])

    @unittest.skipUnless(os.name == "posix", "symlink creation is POSIX-only here")
    def test_symlinked_response_is_refused(self):
        original_gh = self.host.gh

        def gh(args):
            output = original_gh(args)
            if args[:2] == ["run", "download"]:
                path = Path(args[args.index("-D") + 1]) / "response.raw.json"
                path.unlink()
                os.symlink("/dev/zero", path)
            return output
        self.host.gh = gh
        record, _owner = self.issue()
        self.assertIn("no regular response.raw.json", record["failure"])

    def test_waiting_run_for_another_commit_is_never_signed(self):
        self.args.source_sha = "e" * 40
        record, owner = self.issue()
        self.assertIn("not the commit you named", record["failure"])
        self.assertFalse(hasattr(owner, "signed"))
        self.assertEqual(self.approvals(), [])

    def test_owner_attestations_are_required_not_invented(self):
        for change in ({"hf_revision": "UNKNOWN"}, {"checkpoint_manifest_sha256": "x"},
                       {"budget": {"ledger_ref": "l", "remaining_seconds": 10, "remaining_usd": 1}}):
            with self.subTest(change=change):
                self.setUp()
                (self.root / "owner.json").write_text(json.dumps(dict(OWNER_CONFIG, **change)))
                owner = FakeOwner(self.args, run=self.host, sleep=self.host.sleep,
                                  clock=self.host.clock)
                with self.assertRaises(contract.ContractError):
                    owner_issue.issue(self.args, owner_issue.new_record(), owner=owner)
                self.assertEqual(self.approvals(), [])
        with self.assertRaises(owner_issue.IssueError):
            owner_issue.load_owner_config(contract.canonical(dict(OWNER_CONFIG, extra=1)))


@unittest.skipUnless(ROOT_POSIX and SSH_KEYGEN, "VM-side install needs POSIX root and ssh-keygen")
class InstallPermitTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/run") / ("szl-install-test-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o755)
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "permits").mkdir(mode=0o700)
        key = self.root / "owner"
        subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       check=True, capture_output=True, timeout=30)
        public = (self.root / "owner.pub").read_text().split()
        (self.root / "allowed_signers").write_text(
            'stephen namespaces="%s" %s %s\n' % (signature.NAMESPACE, public[0], public[1]))
        config_path = self.root / "broker.json"
        config_path.write_bytes(contract.canonical(broker_config(self.root)))
        self.config_path = config_path
        from tests.test_ssh_ci_executor import fixtures
        values = fixtures()
        self.permit, self.window = values[1], values[3]
        self.data = signature.bundle(self.permit, self.window)
        bundle_path = self.root / "bundle.json"
        bundle_path.write_bytes(self.data)
        subprocess.run([SSH_KEYGEN, "-q", "-Y", "sign", "-f", str(key), "-n", signature.NAMESPACE,
                        str(bundle_path)], check=True, capture_output=True, timeout=30)
        self.signature = (self.root / "bundle.json.sig").read_bytes()

    def install(self, data=None):
        return owner_issue.install_permit(base64.b64encode(data or self.data).decode(),
                                          base64.b64encode(self.signature).decode(),
                                          str(self.config_path))

    def test_verified_permit_is_written_once_and_never_overwritten(self):
        result = self.install()
        self.assertEqual(result["installed"], self.permit["permit_id"])
        written = self.root / "permits" / (self.permit["permit_id"] + signature.BUNDLE_SUFFIX)
        self.assertEqual(written.read_bytes(), self.data)
        self.assertEqual(oct(written.stat().st_mode & 0o777), "0o600")
        with self.assertRaises(FileExistsError):
            self.install()

    def test_unsigned_change_is_refused_before_any_write(self):
        altered = signature.bundle(dict(self.permit, expires_at="2030-01-02T03:18:00Z"), self.window)
        with self.assertRaises(contract.ContractError):
            self.install(altered)
        self.assertEqual(list((self.root / "permits").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
