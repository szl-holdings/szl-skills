"""Offline owner issue-flow tests; gh, ssh, and the VM are faked."""

import argparse
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
import uuid

from tests.test_ssh_ci_broker import config as broker_config
from tools import ssh_ci_bring_up as bring_up
from tools import ssh_ci_contract as contract
from tools import ssh_ci_owner_issue as owner_issue
from tools import ssh_ci_owner_signature as signature
from tools import ssh_ci_owner_window as owner_window
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


def envelope_for(request, result="PASS", processes=({"pid": 445607, "start_ticks": 98765},)):
    """A contract-valid v2 response pinning ``processes``, as the VM broker sends it."""
    digest = contract.sha256(contract.canonical(request))
    files = {name: name.encode() for name in sorted(contract.REQUIRED_FILES)}
    runtime = dict(HOST, gpu_processes=sorted((dict(item) for item in processes),
                                              key=lambda item: item["pid"]))
    receipt = {field: request[field] for field in contract.BINDING_FIELDS}
    receipt.update(schema=contract.RECEIPT_SCHEMA_V2, request_sha256=digest, result=result,
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
        self.root, self.calls, self.options, self.permit_id = root, [], [], None
        self.reviewers = [{"type": "User", "reviewer": {"login": "owner"}}]
        self.processes = [{"pid": 445607, "start_ticks": 98765}]
        self.remote_result = "PASS"
        self.github_response = None  # Replaces what the GitHub artifact carries.
        self.broker_processes = None
        self.time = 0.0
        self.observe_cost = 0.0
        self.main_branch = {"name": "main", "protected": True}
        self.main_rules = []
        self.active = {}  # --status -> unfinished runs of the workflow.
        self.run_ids = [777]  # Runs titled with the dispatched permit.
        self.appear_at = 0.0  # Fake time from which those runs are listed.
        self.refuse = lambda argv: False  # True: the command exits 1.
        self.interrupt = lambda argv: None  # An exception raised instead of running.

    def request(self):
        return plan.plan_request(RUN_METADATA, self.permit_id)["request"]

    def remote_processes(self):
        """What the broker pins: the observed set unless a test makes it drift."""
        return self.broker_processes if self.broker_processes is not None else self.processes

    def clock(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds

    def __call__(self, argv, input=None, **kwargs):
        self.calls.append(argv)
        self.options.append(kwargs)
        error = self.interrupt(argv)
        if error is not None:
            raise error
        if self.refuse(argv):
            return subprocess.CompletedProcess(argv, 1, b"", b"refused by the test")
        head = argv[0]
        out = b""
        if head == "gh":
            out = self.gh(argv[1:])
        elif head == "ssh" and argv[-1] == "true":
            out = b""
        elif head == "ssh" and argv[-1].startswith("cat "):
            raw = envelope_for(self.request(), self.remote_result, self.remote_processes())
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
        if args[:2] == ["api", "repos/%s/branches/main" % owner_issue.REPOSITORY]:
            return json.dumps(self.main_branch).encode()
        if args[:2] == ["api", "repos/%s/rules/branches/main" % owner_issue.REPOSITORY]:
            return json.dumps(self.main_rules).encode()
        if args[:2] == ["variable", "list"]:
            return json.dumps([{"name": n} for n in owner_issue.REQUIRED_VARIABLES]).encode()
        if args[:2] == ["secret", "list"]:
            return json.dumps([{"name": "SZL_CPU_SSH_KEY"}]).encode()
        if args[:2] == ["workflow", "run"]:
            self.permit_id = args[-1].split("=", 1)[1]
            return b""
        if args[:2] == ["run", "list"] and "--status" in args:
            return json.dumps(self.active.get(args[args.index("--status") + 1], [])).encode()
        if args[:2] == ["run", "list"]:
            titled = [{"databaseId": run_id, "displayTitle": "CPU build permit " + self.permit_id}
                      for run_id in self.run_ids if self.time >= self.appear_at]
            return json.dumps(titled + [{"databaseId": 776,
                                         "displayTitle": "CPU build permit other"}]).encode()
        if args[:2] == ["run", "cancel"]:
            return b""
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
            raw = self.github_response or envelope_for(self.request(), self.remote_result,
                                                       self.remote_processes())
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
            admin_known_hosts=str(self.root / "known_hosts"), admin_key=None, port=22,
            evidence_dir=str(self.root / "evidence"))

    def owner(self):
        return FakeOwner(self.args, run=self.host, sleep=self.host.sleep, clock=self.host.clock)

    def issue(self):
        owner = self.owner()
        record = owner_issue.new_record()
        try:
            owner_issue.issue(self.args, record, owner=owner)
        except owner_issue.IssueError as error:
            record["failure"] = str(error)
        return record, owner

    def approvals(self):
        return [argv for argv in self.host.calls if argv[1:4] == ["api", "-X", "POST"]]

    def dispatched(self):
        return any(argv[1:3] == ["workflow", "run"] for argv in self.host.calls)

    def cancels(self):
        return [argv[1:] for argv in self.host.calls if argv[1:3] == ["run", "cancel"]]

    def write_config(self, value):
        (self.root / "owner.json").write_text(json.dumps(value))

    def bring_up_template(self):
        """The owner-config template exactly as tools/ssh_ci_bring_up.py writes it."""
        path = self.root / "template.json"
        args = argparse.Namespace(
            release_sha=SHA, vm="Ubuntu@203.0.113.10", known_hosts="known_hosts",
            admin_key=None, port=22, workdir=str(self.root / "lane"), window_ref="w",
            hidden_path=[], owner_config=str(path), stages="run", dry_run=False)
        with self.assertRaises(bring_up.Stop), \
                unittest.mock.patch("builtins.input", return_value="RUN"):
            bring_up.Lane(args).build()
        return path.read_bytes()

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
        self.assertEqual(value["permit"]["workload"], {
            "mode": "pinned_set", "processes": [{"pid": 445607, "start_ticks": 98765}]})
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
            self.assertEqual(argv[argv.index("-p"):argv.index("-p") + 2], ["-p", "22"])
        self.assertEqual(self.cancels(), [])
        self.assertNotIn("unapproved_run", record)
        dispatch = next(i for i, argv in enumerate(self.host.calls) if argv[1:3] == ["workflow", "run"])
        before = self.host.calls[:dispatch]
        self.assertEqual([argv[argv.index("--status") + 1] for argv in before
                          if argv[1:3] == ["run", "list"] and "--status" in argv],
                         list(owner_issue.ACTIVE_STATUSES))
        reach = [argv for argv in before if argv[0] == "ssh" and argv[-1] == "true"]
        self.assertEqual(len(reach), 1)
        self.assertIn("BatchMode=yes", reach[0])
        self.assertTrue(record["environment"]["main_protected"])

    def test_missing_reviewer_gate_stops_before_dispatch(self):
        self.host.reviewers = []
        record, _owner = self.issue()
        self.assertIn("required reviewer", record["failure"])
        self.assertFalse(any(argv[1:3] == ["workflow", "run"] for argv in self.host.calls))

    def test_idle_and_multi_process_gpus_are_pinned_as_a_set(self):
        # vLLM plus a training job, or no GPU work at all: both are pinned exactly.
        for processes in ([], [{"pid": 3, "start_ticks": 4}, {"pid": 1, "start_ticks": 2}]):
            with self.subTest(processes=processes):
                self.setUp()
                self.host.processes = processes
                record, owner = self.issue()
                self.assertEqual(record["result"], "PASS", record)
                pinned = signature.parse_bundle(owner.signed)["permit"]["workload"]
                self.assertEqual(pinned, {"mode": "pinned_set", "processes": sorted(
                    processes, key=lambda item: item["pid"])})

    def test_too_many_gpu_processes_stop_before_signing_or_approval(self):
        self.host.processes = [{"pid": pid, "start_ticks": pid} for pid in range(1, 10)]
        record, owner = self.issue()
        self.assertIn("at most 8 GPU compute processes", record["failure"])
        self.assertFalse(hasattr(owner, "signed"))
        self.assertEqual(self.approvals(), [])

    def test_remote_receipt_must_pin_the_signed_process_set(self):
        self.host.processes = [{"pid": 1, "start_ticks": 2}, {"pid": 3, "start_ticks": 4}]
        self.host.broker_processes = [{"pid": 1, "start_ticks": 2}]
        record, _owner = self.issue()
        self.assertEqual(record["result"], "BLOCKED")
        self.assertIn("remote receipt does not pin the GPU process set you signed",
                      record["verification_failures"])

    def test_slow_owner_never_approves_a_short_window(self):
        self.host.observe_cost = owner_issue.MAX_APPROVAL_DELAY_SECONDS + 1
        record, _owner = self.issue()
        self.assertIn("too little window", record["failure"])
        self.assertEqual(self.approvals(), [])
        self.assertIn("permit-installed", record["stages"])
        self.assertEqual(self.cancels(), [["run", "cancel", "777", "-R", owner_issue.REPOSITORY]])
        self.assertEqual(record["unapproved_run"], {"cancelled": [777], "rejected": [],
                                                    "by_hand": None})

    def test_approval_cap_leaves_runner_pickup_time_before_the_claim_floor(self):
        self.assertEqual(owner_issue.WINDOW_SECONDS, owner_window.MAX_WINDOW_SECONDS)
        self.assertGreaterEqual(owner_issue.WINDOW_SECONDS - owner_window.MIN_REMAINING_SECONDS
                                - owner_issue.MAX_APPROVAL_DELAY_SECONDS, 270)

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
        self.assertEqual(self.cancels(), [["run", "cancel", "777", "-R", owner_issue.REPOSITORY]])

    def test_owner_attestations_are_required_not_invented(self):
        for change in ({"hf_revision": "UNKNOWN"}, {"checkpoint_manifest_sha256": "x"},
                       {"budget": {"ledger_ref": "l", "remaining_seconds": 10, "remaining_usd": 1}},
                       {"provider_instance_uuid": "BD6E0A2E-C7D1-4423-BFC2-99B575C6A233"},
                       {"owner_receipt_ref": ""}):
            with self.subTest(change=change):
                self.setUp()
                self.write_config(dict(OWNER_CONFIG, **change))
                with self.assertRaises(contract.ContractError):
                    owner_issue.issue(self.args, owner_issue.new_record(), owner=self.owner())
                self.assertEqual(self.approvals(), [])
                self.assertEqual(self.host.calls, [])  # Refused before any gh or ssh call.
        with self.assertRaises(owner_issue.IssueError):
            owner_issue.load_owner_config(contract.canonical(dict(OWNER_CONFIG, extra=1)))

    def test_bring_up_template_is_refused_before_anything_is_dispatched(self):
        template = self.bring_up_template()
        with self.assertRaises((contract.ContractError, owner_issue.IssueError)):
            owner_issue.load_owner_config(template)
        (self.root / "owner.json").write_bytes(template)
        with self.assertRaises((contract.ContractError, owner_issue.IssueError)):
            owner_issue.issue(self.args, owner_issue.new_record(), owner=self.owner())
        self.assertEqual(self.host.calls, [])

    def test_corrected_bring_up_template_is_accepted_and_signed_as_written(self):
        value = json.loads(self.bring_up_template())
        value.update(provider_instance_uuid="5f0c7d2e-8a41-4c3b-9e6d-2b7f1a0c4d93",
                     hf_revision="dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f",
                     checkpoint_manifest_sha256="e" * 64,
                     owner_receipt_ref="owner://vm2/cpu-window/2026-10-28")
        value["budget"].update(ledger_ref="ledger://massed-compute/2026-10-28",
                               remaining_seconds=7200, remaining_usd=25.0)
        self.assertEqual(owner_issue.load_owner_config(json.dumps(value, indent=2).encode()), value)
        self.write_config(value)
        record, owner = self.issue()
        self.assertEqual(record["result"], "PASS", record)
        window = signature.parse_bundle(owner.signed)["window"]
        self.assertEqual(window["provider"]["instance_uuid"], value["provider_instance_uuid"])
        self.assertEqual(window["budget"], value["budget"])
        self.assertEqual(window["checkpoint_manifest_sha256"], "e" * 64)

    def test_placeholders_and_uppercase_digests_are_refused_not_normalized(self):
        texts = ("FILL: admitted Hugging Face revision", "fill me in", "  Fill", "FILL")
        fields = ("provider_instance_uuid", "hf_revision", "checkpoint_manifest_sha256",
                  "owner_receipt_ref")
        changes = [{field: text} for field in fields for text in texts]
        changes += [{"budget": dict(OWNER_CONFIG["budget"], ledger_ref=text)} for text in texts]
        for change in changes:
            with self.subTest(change=change), \
                    self.assertRaisesRegex(contract.ContractError, "template placeholder"):
                owner_issue.load_owner_config(json.dumps(dict(OWNER_CONFIG, **change)).encode())
        for digest in ("D" * 64, "d" * 63 + "D"):
            with self.subTest(digest=digest), self.assertRaisesRegex(contract.ContractError,
                                                                     "uppercase"):
                owner_issue.load_owner_config(json.dumps(dict(
                    OWNER_CONFIG, checkpoint_manifest_sha256=digest)).encode())
        self.write_config(dict(OWNER_CONFIG, hf_revision="FILL: admitted Hugging Face revision"))
        with self.assertRaises(contract.ContractError):
            owner_issue.issue(self.args, owner_issue.new_record(), owner=self.owner())
        self.assertEqual(self.host.calls, [])

    def test_unfinished_runs_of_the_workflow_block_dispatch(self):
        for status in owner_issue.ACTIVE_STATUSES:
            with self.subTest(status=status):
                self.setUp()
                self.host.active = {status: [
                    {"databaseId": 701, "status": status,
                     "url": "https://github.com/%s/actions/runs/701" % owner_issue.REPOSITORY},
                    {"databaseId": 702, "status": status, "url": None}]}
                record, _owner = self.issue()
                for run_id in (701, 702):
                    self.assertIn("gh run cancel %d -R %s" % (run_id, owner_issue.REPOSITORY),
                                  record["failure"])
                self.assertIn(status, record["failure"])
                self.assertIn("actions/runs/701", record["failure"])
                self.assertFalse(self.dispatched())
        for listing in ({"runs": []}, [{"databaseId": "701"}], [{"databaseId": True}], ["701"]):
            with self.subTest(listing=listing):
                self.setUp()
                self.host.active = {"waiting": listing}
                record, _owner = self.issue()
                self.assertIn("unexpected shape", record["failure"])
                self.assertFalse(self.dispatched())

    def test_failed_active_run_listing_stops_before_dispatch(self):
        self.host.refuse = lambda argv: argv[1:3] == ["run", "list"] and "pending" in argv
        record, _owner = self.issue()
        self.assertIn("list pending runs failed", record["failure"])
        self.assertFalse(self.dispatched())

    def test_vm_unreachable_without_a_prompt_stops_before_dispatch(self):
        self.host.refuse = lambda argv: argv[0] == "ssh" and argv[-1] == "true"
        record, _owner = self.issue()
        self.assertIn("ssh-add", record["failure"])
        self.assertIn("ssh-agent service", record["failure"])
        self.assertFalse(self.dispatched())
        self.assertEqual(self.cancels(), [])

    def test_main_must_be_protected_for_the_branch_policy_to_mean_anything(self):
        for branch, rules in (({"name": "main", "protected": False}, []),
                              ({"name": "main"}, []),
                              ({"name": "main", "protected": "true"}, {"message": "Not Found"}),
                              ([], [])):
            with self.subTest(branch=branch, rules=rules):
                self.setUp()
                self.host.main_branch, self.host.main_rules = branch, rules
                record, _owner = self.issue()
                self.assertIn("main has no branch protection rule and no ruleset", record["failure"])
                self.assertFalse(self.dispatched())
        self.setUp()
        self.host.main_branch = {"name": "main", "protected": False}
        self.host.main_rules = [{"type": "pull_request", "ruleset_id": 9}]
        record, _owner = self.issue()
        self.assertEqual(record["result"], "PASS", record)
        self.assertEqual(record["environment"]["main_rulesets"], 1)
        self.setUp()
        self.host.refuse = lambda argv: argv[1:3] == ["api", "repos/%s/branches/main"
                                                      % owner_issue.REPOSITORY]
        record, _owner = self.issue()
        self.assertIn("read main branch failed", record["failure"])
        self.assertFalse(self.dispatched())

    def test_stop_before_approval_cancels_or_rejects_the_dispatched_run(self):
        # After the gate is known: a refused cancel falls back to rejecting that gate.
        self.host.processes = [{"pid": pid, "start_ticks": pid} for pid in range(1, 10)]
        self.host.refuse = lambda argv: argv[1:3] == ["run", "cancel"]
        record, _owner = self.issue()
        self.assertIn("at most 8 GPU compute processes", record["failure"])
        self.assertEqual(record["unapproved_run"], {"cancelled": [], "rejected": [777],
                                                    "by_hand": None})
        self.assertEqual(len(self.approvals()), 1)
        self.assertIn("state=rejected", self.approvals()[0])
        self.assertIn("environment_ids[]=55", self.approvals()[0])
        self.assertFalse(any("state=approved" in argv for argv in self.host.calls))
        # Neither works: the error says exactly what to run by hand.
        self.setUp()
        self.host.processes = [{"pid": pid, "start_ticks": pid} for pid in range(1, 10)]
        self.host.refuse = lambda argv: argv[1:3] == ["run", "cancel"] or argv[1:4] == [
            "api", "-X", "POST"]
        record, _owner = self.issue()
        self.assertIn("at most 8 GPU compute processes", record["failure"])
        self.assertIn("cancel by hand: gh run cancel 777 -R " + owner_issue.REPOSITORY,
                      record["failure"])
        self.assertEqual(record["unapproved_run"]["cancelled"], [])
        # Before the gate is known there is nothing to reject.
        self.setUp()
        self.args.source_sha = "e" * 40
        self.host.refuse = lambda argv: argv[1:3] == ["run", "cancel"]
        record, _owner = self.issue()
        self.assertIn("gh run cancel 777 -R", record["failure"])
        self.assertEqual(self.approvals(), [])

    def test_interrupt_at_the_passphrase_prompt_cancels_the_run_and_propagates(self):
        owner = self.owner()

        def sign(_data):
            raise KeyboardInterrupt
        owner.sign = sign
        record = owner_issue.new_record()
        with self.assertRaises(KeyboardInterrupt):
            owner_issue.issue(self.args, record, owner=owner)
        self.assertEqual(record["unapproved_run"]["cancelled"], [777])
        self.assertEqual(self.approvals(), [])

    def test_run_that_cannot_be_found_is_named_with_how_to_find_and_cancel_it(self):
        self.host.run_ids = []
        record, _owner = self.issue()
        self.assertIn("dispatched run did not appear", record["failure"])
        self.assertIn("gh run list -R %s --workflow %s" % (owner_issue.REPOSITORY,
                                                           owner_issue.WORKFLOW), record["failure"])
        self.assertIn("CPU build permit " + record["permit_id"], record["failure"])
        self.assertIn("gh run cancel <id> -R " + owner_issue.REPOSITORY, record["failure"])
        self.assertEqual(self.cancels(), [])
        # A run that shows up only after the search, or twice, is still cancelled.
        for run_ids, appear_at in (([777], 120.0), ([777, 778], 0.0)):
            with self.subTest(run_ids=run_ids):
                self.setUp()
                self.host.run_ids, self.host.appear_at = run_ids, appear_at
                record, _owner = self.issue()
                self.assertEqual(sorted(record["unapproved_run"]["cancelled"]), run_ids)
                self.assertIsNone(record["unapproved_run"]["by_hand"])
                self.assertEqual(self.approvals(), [])

    def test_subprocess_timeouts_become_clean_issue_errors(self):
        for name, match, stage in (
                ("install", lambda argv: argv[0] == "ssh" and "-t" in argv, "install permit"),
                ("observe", lambda argv: argv[0] == "ssh" and "observer" in argv[-1], "observe VM"),
                ("pending", lambda argv: argv[-1].endswith("/pending_deployments"),
                 "read pending deployments")):
            with self.subTest(name=name):
                self.setUp()
                self.host.interrupt = lambda argv, match=match: (
                    subprocess.TimeoutExpired(argv, 600) if match(argv) else None)
                record, _owner = self.issue()
                self.assertIn(stage + " timed out after 600 s", record["failure"])
                self.assertEqual(self.cancels(), [["run", "cancel", "777", "-R",
                                                   owner_issue.REPOSITORY]])
                self.assertEqual(self.approvals(), [])

    def test_watch_outlasts_the_job_and_a_watch_timeout_never_cancels_an_approved_run(self):
        record, _owner = self.issue()
        self.assertEqual(record["result"], "PASS", record)
        watch = next(options for argv, options in zip(self.host.calls, self.host.options)
                     if argv[1:3] == ["run", "watch"])
        self.assertGreaterEqual(watch["timeout"], 1200)
        self.assertEqual(watch["timeout"], owner_issue.WATCH_SECONDS)
        self.setUp()
        self.host.interrupt = lambda argv: (subprocess.TimeoutExpired(argv, owner_issue.WATCH_SECONDS)
                                            if argv[1:3] == ["run", "watch"] else None)
        record, _owner = self.issue()
        self.assertIn("watch run timed out after %d s" % owner_issue.WATCH_SECONDS, record["failure"])
        self.assertIn("approved", record["stages"])
        self.assertEqual(self.cancels(), [])

    def main(self, *extra):
        argv = ["run", "--owner-config", str(self.root / "owner.json"), "--source-sha", SHA,
                "--signing-key", "key", "--admin-target", "Ubuntu@203.0.113.10",
                "--admin-known-hosts", str(self.root / "known_hosts"),
                "--evidence-dir", str(self.root / "evidence"), "--gh", "gh", "--ssh", "ssh",
                "--ssh-keygen", "ssh-keygen", *extra]
        output, errors = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(owner_issue, "Owner", lambda args: FakeOwner(
                args, run=self.host, sleep=self.host.sleep, clock=self.host.clock)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = owner_issue.main(argv)
        return code, output.getvalue(), errors.getvalue()

    def test_port_option_reaches_every_ssh_call(self):
        code, output, _errors = self.main("--port", "2222")
        self.assertEqual(code, 0, output)
        ssh = [argv for argv in self.host.calls if argv[0] == "ssh"]
        self.assertGreaterEqual(len(ssh), 4)  # Reach, observe, install, public receipt.
        for argv in ssh:
            self.assertEqual(argv[argv.index("-p"):argv.index("-p") + 2], ["-p", "2222"])
        for port in ("0", "65536", "-22", "22x"):
            with self.subTest(port=port), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                owner_issue.main(["run", "--owner-config", "x", "--source-sha", SHA,
                                  "--signing-key", "k", "--admin-target", "u@h", "--port", port])
        for port in (True, "22", 0, 70000):
            with self.subTest(port=port), self.assertRaises(owner_issue.IssueError):
                self.args.port = port
                self.owner().ssh_argv("true")

    def test_timeout_after_approval_leaves_a_clean_receipt_and_message(self):
        self.host.interrupt = lambda argv: (subprocess.TimeoutExpired(argv, owner_issue.WATCH_SECONDS)
                                            if argv[1:3] == ["run", "watch"] else None)
        code, _output, errors = self.main()
        self.assertEqual(code, 1)
        self.assertIn("CPU BUILD BLOCKED: IssueError: watch run timed out", errors)
        self.assertNotIn("Traceback", errors)
        receipt = json.loads(next((self.root / "evidence").glob("owner-issue-*.json")).read_text())
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertIn("watch run timed out", receipt["failure"])
        self.assertEqual(receipt["run_id"], 777)


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
