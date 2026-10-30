#!/usr/bin/env python3
"""Owner-run issue flow for one CPU build: dispatch, bind, sign, install, approve.

Run on the owner's workstation with ``gh`` (owner login), ``ssh`` (verified
admin key and host pin), and ``ssh-keygen`` (owner permit-signing key). It
reserves a fresh permit UUID, dispatches the manual workflow, waits for the
protected-environment gate, reconstructs the exact request from that waiting
run, observes the VM read-only, builds a <=900-second permit and owner window
from that observation and the owner's own attestations, signs them, installs
them through the VM's root verifier, and approves only that run. It never
retries, never reuses a permit, and never touches the GPU or the model. It
checks the owner config, the protected branch, other unfinished runs, and
non-interactive VM access before dispatch, and cancels its own run if it stops
before approval is attempted. A lost or failed approval response does not
withdraw that run: the run id is kept, the outcome is UNKNOWN, and another
attempt has to wait until that run is reconciled. An interruption after an
observed approval does not withdraw it either. This does not provide crash
durability or a cross-session lock.

``observe`` and ``install-permit`` are the VM-side halves of this flow.
"""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ssh_ci_admission import PERMIT_SCHEMA, validate_permit
from tools.ssh_ci_contract import (CPU_PROFILE, MAX_PINNED_PROCESSES, MAX_RESPONSE_BYTES,
                                   RECEIPT_SCHEMA_V2, ContractError, canonical, sha256,
                                   strict_json_loads, verify_response)
from tools.ssh_ci_owner_signature import (BUNDLE_SUFFIX, MAX_ALLOWED_SIGNERS_BYTES, NAMESPACE,
                                          SIGNATURE_SUFFIX, bundle, read_protected,
                                          verify_bundle)
from tools.ssh_ci_owner_window import (OWNER_ATTESTATION_FIELDS, WINDOW_SCHEMA,
                                       validate_owner_attestations, validate_owner_window)
from tools.ssh_ci_plan import plan_request


REPOSITORY = "szl-holdings/szl-skills"
WORKFLOW = "isolated-ssh-build.yml"
ENVIRONMENT = "massed-cpu-build"
OWNER_CONFIG_SCHEMA = "szl.ssh-cpu-build.owner-config.v1"
REMOTE_TOOLS = "/opt/szl-cpu-broker/current/tools"
PUBLIC_RECEIPTS = "/var/lib/szl-cpu-broker/public"
WINDOW_SECONDS = 900
# 900 s window - 450 s claim floor - 180 s approval = 270 s left for runner pickup,
# checkout, setup-python, and staging.
MAX_APPROVAL_DELAY_SECONDS = 180
WATCH_SECONDS = 1200  # Job timeout-minutes 15 (900 s) plus 300 s for pickup and upload.
ACTIVE_STATUSES = ("waiting", "pending", "queued", "requested", "in_progress")
REQUIRED_VARIABLES = {"SZL_CPU_CI_ENABLED", "SZL_CPU_SSH_HOST", "SZL_CPU_SSH_PORT",
                      "SZL_CPU_SSH_USER", "SZL_CPU_KNOWN_HOSTS"}
OWNER_FIELDS = OWNER_ATTESTATION_FIELDS | {"schema"}
FIND_RUN = ("find it with: gh run list -R %s --workflow %s --json databaseId,displayTitle,status"
            " (title 'CPU build permit %%s'), then cancel it with: gh run cancel <id> -R %s"
            % (REPOSITORY, WORKFLOW, REPOSITORY))


class IssueError(RuntimeError):
    """The issue flow stopped; nothing after the failing stage happened."""


def utc_now():
    return datetime.now(timezone.utc)


def stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_owner_config(data):
    """The owner's own attestations; this tool never invents them."""
    value = strict_json_loads(data, 8192)
    if type(value) is not dict or set(value) != OWNER_FIELDS:
        raise IssueError("owner config has missing or unknown fields")
    if value["schema"] != OWNER_CONFIG_SCHEMA:
        raise IssueError("owner config schema does not match")
    budget = value["budget"]
    if type(budget) is not dict or set(budget) != {"ledger_ref", "remaining_seconds", "remaining_usd"}:
        raise IssueError("owner budget needs ledger_ref, remaining_seconds, remaining_usd")
    # The broker's own value checks, before anything is dispatched.
    validate_owner_attestations({field: value[field] for field in OWNER_ATTESTATION_FIELDS})
    return value


def build_grant(request, observation, owner, new_uuid=uuid.uuid4):
    """Return (permit, window) bound to one request, host, and the pinned GPU process set.

    Every GPU compute process observed now (vLLM, training, or none) is pinned;
    the broker refuses to build, or to report success, if that set changes.
    """
    processes = sorted(({"pid": item["pid"], "start_ticks": item["start_ticks"]}
                        for item in observation["gpu_compute_processes"]),
                       key=lambda item: item["pid"])
    if len(processes) > MAX_PINNED_PROCESSES:
        raise IssueError("at most %d GPU compute processes can be pinned; observed %d"
                         % (MAX_PINNED_PROCESSES, len(processes)))
    start = datetime.strptime(observation["observed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    window_bounds = {"not_before": stamp(start),
                     "expires_at": stamp(start + timedelta(seconds=WINDOW_SECONDS))}
    digest = sha256(canonical(request))
    permit = {"schema": PERMIT_SCHEMA, "permit_id": request["permit_id"], "request_sha256": digest,
              "max_runs": 1, "cpu_profile": dict(CPU_PROFILE), "host": dict(observation["host"]),
              "workload": {"mode": "pinned_set", "processes": processes}, **window_bounds}
    execution_id, run_nonce = str(new_uuid()), str(new_uuid())
    window = {"schema": WINDOW_SCHEMA, "window_id": str(new_uuid()),
              "permit_id": request["permit_id"], "request_sha256": digest,
              "provider": {"name": "Massed Compute", "instance_uuid": owner["provider_instance_uuid"]},
              "host": dict(observation["host"]),
              "execution": {"execution_id": execution_id, "run_nonce": run_nonce},
              "source": {"github_sha": request["source_sha"], "hf_revision": owner["hf_revision"]},
              "cpu_profile": dict(CPU_PROFILE), "budget": dict(owner["budget"]),
              "workload": {"mode": "cpu_build", "gpu_devices": False, "model_interaction": "none",
                           "training": False, "inference": False},
              "checkpoint_manifest_sha256": owner["checkpoint_manifest_sha256"],
              "owner_receipt_ref": owner["owner_receipt_ref"], **window_bounds}
    # Same validators the broker runs, at the observation instant.
    validate_permit(request, permit, observation, start)
    validate_owner_window(request, window, start)
    return permit, window


class Owner:
    """Owner-side command runner; every external call goes through ``run``."""

    def __init__(self, args, *, run=subprocess.run, sleep=time.sleep, clock=time.monotonic):
        self.args, self.run, self.sleep, self.clock = args, run, sleep, clock

    def call(self, argv, stage, timeout, **options):
        """``run``, with subprocess failures as IssueError so the record stays clean."""
        try:
            return self.run(argv, timeout=timeout, check=False, **options)
        except subprocess.TimeoutExpired as error:
            raise IssueError("%s timed out after %d s" % (stage, timeout)) from error
        except subprocess.SubprocessError as error:
            raise IssueError("%s failed: %s" % (stage, type(error).__name__)) from error

    def command(self, argv, stage, *, stdin=None, check=True, timeout=600):
        completed = self.call(argv, stage, timeout, input=stdin, capture_output=True)
        if check and completed.returncode != 0:
            raise IssueError(stage + " failed: " + completed.stderr.decode("utf-8", "replace")[-300:])
        return completed

    def gh_json(self, argv, stage):
        output = self.command([self.args.gh, *argv], stage).stdout
        try:
            return json.loads(output.decode("utf-8"))
        except ValueError as error:
            raise IssueError(stage + " returned non-JSON output") from error

    def ssh_argv(self, remote, *, tty=False):
        port = self.args.port
        if type(port) is not int or not 1 <= port <= 65535:
            raise IssueError("ssh port must be an integer from 1 to 65535")
        argv = [self.args.ssh, "-o", "StrictHostKeyChecking=yes", "-o", "ClearAllForwardings=yes"]
        if self.args.admin_known_hosts:
            argv += ["-o", "UserKnownHostsFile=" + self.args.admin_known_hosts]
        if self.args.admin_key:
            argv += ["-o", "IdentitiesOnly=yes", "-i", self.args.admin_key]
        argv += ["-p", str(port)]
        argv += ["-t"] if tty else ["-T", "-o", "BatchMode=yes"]
        return argv + [self.args.admin_target, remote]

    def ssh(self, remote, stage, *, tty=False):
        argv = self.ssh_argv(remote, tty=tty)
        if tty:  # sudo may prompt on the owner's own terminal.
            completed = self.call(argv, stage, 600)
            if completed.returncode != 0:
                raise IssueError(stage + " failed with exit %s" % completed.returncode)
            return completed
        return self.command(argv, stage)

    def reachable(self):
        """One BatchMode no-op before dispatch, so a locked admin key stops nothing midway."""
        completed = self.command(self.ssh_argv("true"), "reach VM", check=False)
        if completed.returncode != 0:
            raise IssueError("the VM is not reachable without a prompt (ssh exit %d: %s); load the "
                             "admin key with ssh-add (Windows: start the ssh-agent service first) "
                             "and check the host pin, then rerun"
                             % (completed.returncode,
                                completed.stderr.decode("utf-8", "replace")[-200:].strip()))

    def list_runs(self, extra, stage):
        runs = self.gh_json(["run", "list", "-R", REPOSITORY, "--workflow", WORKFLOW, *extra], stage)
        if type(runs) is not list or any(type(item) is not dict
                                         or type(item.get("databaseId")) is not int
                                         for item in runs):
            raise IssueError(stage + " returned an unexpected shape")
        return runs

    def refuse_active_runs(self):
        """A run not yet completed holds the workflow's concurrency group; never queue behind it."""
        busy = {}
        for status in ACTIVE_STATUSES:
            for item in self.list_runs(["--status", status, "--json", "databaseId,status,url"],
                                       "list %s runs" % status):
                busy.setdefault(item["databaseId"], (item.get("status") or status, item.get("url")))
        if busy:
            raise IssueError("other %s runs are not completed and would hold the concurrency group; "
                             "cancel each first: %s" % (WORKFLOW, "; ".join(
                                 "run %d (%s, %s): gh run cancel %d -R %s"
                                 % (run_id, status, url, run_id, REPOSITORY)
                                 for run_id, (status, url) in sorted(busy.items()))))

    def preflight_environment(self):
        environment = self.gh_json(["api", "repos/%s/environments/%s" % (REPOSITORY, ENVIRONMENT)],
                                   "read protected environment")
        rules = environment.get("protection_rules") or []
        reviewers = [rule for rule in rules if rule.get("type") == "required_reviewers"
                     and rule.get("reviewers")]
        policy = environment.get("deployment_branch_policy") or {}
        if not reviewers:
            raise IssueError("environment has no required reviewer; the approval gate is absent")
        if not (policy.get("protected_branches") or policy.get("custom_branch_policies")):
            raise IssueError("environment is not restricted to protected branches")
        # With no protected branch at all, a protected-branches policy admits every branch.
        branch = self.gh_json(["api", "repos/%s/branches/main" % REPOSITORY], "read main branch")
        protected = type(branch) is dict and branch.get("protected") is True
        rulesets = 0
        if not protected:
            rules = self.gh_json(["api", "repos/%s/rules/branches/main" % REPOSITORY],
                                 "read main rulesets")
            rulesets = len(rules) if type(rules) is list else 0
        if not (protected or rulesets):
            raise IssueError("main has no branch protection rule and no ruleset, so the "
                             "environment's branch policy does not restrict deployments")
        variables = self.gh_json(["variable", "list", "--env", ENVIRONMENT, "-R", REPOSITORY,
                                  "--json", "name"], "list environment variables")
        secrets = self.gh_json(["secret", "list", "--env", ENVIRONMENT, "-R", REPOSITORY,
                                "--json", "name"], "list environment secrets")
        missing = sorted(REQUIRED_VARIABLES - {item["name"] for item in variables})
        if missing or "SZL_CPU_SSH_KEY" not in {item["name"] for item in secrets}:
            raise IssueError("environment configuration missing: %s"
                             % ", ".join(missing + (["SZL_CPU_SSH_KEY"] if "SZL_CPU_SSH_KEY" not in
                                                    {item["name"] for item in secrets} else [])))
        return {"required_reviewers": len(reviewers), "branch_policy": policy,
                "can_admins_bypass": environment.get("can_admins_bypass"),
                "main_protected": protected, "main_rulesets": rulesets}

    def runs_titled(self, permit_id):
        runs = self.list_runs(["--event", "workflow_dispatch", "--limit", "20",
                               "--json", "databaseId,displayTitle"], "list runs")
        title = "CPU build permit " + permit_id
        return [item["databaseId"] for item in runs if item.get("displayTitle") == title]

    def dispatch(self, permit_id):
        self.command([self.args.gh, "workflow", "run", WORKFLOW, "-R", REPOSITORY, "--ref", "main",
                      "-f", "permit_id=" + permit_id], "dispatch workflow")
        deadline = self.clock() + 120
        while self.clock() < deadline:
            matches = self.runs_titled(permit_id)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise IssueError("more than one run carries this permit; refusing")
            self.sleep(3)
        raise IssueError("dispatched run did not appear")

    def waiting_run(self, run_id):
        deadline = self.clock() + 180
        status = None
        while self.clock() < deadline:
            raw = self.command([self.args.gh, "api", "repos/%s/actions/runs/%d" % (REPOSITORY, run_id)],
                               "read run").stdout
            status = json.loads(raw.decode("utf-8")).get("status")
            if status == "waiting":
                return raw
            if status == "completed":
                raise IssueError("run completed before approval (configuration BLOCKED?)")
            self.sleep(3)
        raise IssueError("run never reached the environment approval gate (last status %s; a "
                         "pending run may be queued behind another run in the concurrency group)"
                         % status)

    def withdraw(self, permit_id, run_id, environment_id):
        """Cancel a dispatched, unapproved run so it cannot hold the concurrency group.

        Never raises. Returns what was done and, for anything left behind, how to
        cancel it by hand.
        """
        def attempt(argv, stage):
            try:
                return self.command(argv, stage, check=False).returncode == 0
            except Exception:
                return False
        try:
            ids = [run_id] if run_id is not None else self.runs_titled(permit_id)
        except Exception:
            ids = []
        done = {"cancelled": [], "rejected": [], "by_hand": None}
        left = []
        for item in ids:
            if attempt([self.args.gh, "run", "cancel", str(item), "-R", REPOSITORY],
                       "cancel unapproved run"):
                done["cancelled"].append(item)
            elif item == run_id and environment_id is not None and attempt(
                    [self.args.gh, "api", "-X", "POST",
                     "repos/%s/actions/runs/%d/pending_deployments" % (REPOSITORY, item),
                     "-F", "environment_ids[]=%d" % environment_id, "-f", "state=rejected",
                     "-f", "comment=CPU permit %s withdrawn before approval" % permit_id],
                    "reject unapproved deployment"):
                done["rejected"].append(item)
            else:
                left.append(item)
        if not ids:
            done["by_hand"] = "the dispatched run was not found; " + FIND_RUN % permit_id
        elif left:
            done["by_hand"] = "cancel by hand: " + "; ".join(
                "gh run cancel %s -R %s" % (item, REPOSITORY) for item in left)
        return done

    def pending_environment_id(self, run_id):
        pending = self.gh_json(["api", "repos/%s/actions/runs/%d/pending_deployments"
                                % (REPOSITORY, run_id)], "read pending deployments")
        ids = [item["environment"]["id"] for item in pending
               if item.get("environment", {}).get("name") == ENVIRONMENT
               and item.get("current_user_can_approve") is True]
        if len(ids) != 1 or type(ids[0]) is not int:
            raise IssueError("the owner cannot approve exactly one %s gate" % ENVIRONMENT)
        return ids[0]

    def observe(self):
        output = self.ssh("python3 -I -B %s/ssh_ci_observer.py" % REMOTE_TOOLS, "observe VM").stdout
        return strict_json_loads(output.strip(), 4096)

    def sign(self, data):
        with tempfile.TemporaryDirectory(prefix="szl-permit-sign-") as scratch:
            path = Path(scratch) / "bundle.json"
            path.write_bytes(data)
            completed = self.call([self.args.ssh_keygen, "-Y", "sign", "-f", self.args.signing_key,
                                   "-n", NAMESPACE, str(path)], "owner signing", 300)
            if completed.returncode != 0:
                raise IssueError("owner signing failed")
            return (Path(scratch) / "bundle.json.sig").read_bytes()

    def install(self, data, signature):
        self.ssh("sudo python3 -I -B %s/ssh_ci_owner_issue.py install-permit --bundle-b64 %s "
                 "--signature-b64 %s" % (REMOTE_TOOLS, base64.b64encode(data).decode("ascii"),
                                         base64.b64encode(signature).decode("ascii")),
                 "install permit", tty=True)

    def approve(self, run_id, environment_id, permit_id, digest):
        self.command([self.args.gh, "api", "-X", "POST",
                      "repos/%s/actions/runs/%d/pending_deployments" % (REPOSITORY, run_id),
                      "-F", "environment_ids[]=%d" % environment_id, "-f", "state=approved",
                      "-f", "comment=CPU permit %s bound to request %s" % (permit_id, digest)],
                     "approve deployment")

    def collect(self, run_id, out):
        """Download evidence; return the raw broker response bytes only (bounded)."""
        self.command([self.args.gh, "run", "watch", str(run_id), "-R", REPOSITORY, "--interval", "10"],
                     "watch run", check=False, timeout=WATCH_SECONDS)
        self.command([self.args.gh, "run", "download", str(run_id), "-R", REPOSITORY,
                      "-n", "ssh-cpu-build-%d-1" % run_id, "-D", str(out)], "download evidence")
        path = out / "response.raw.json"
        if path.is_symlink() or not path.is_file():
            raise IssueError("evidence has no regular response.raw.json")
        with open(path, "rb") as stream:
            raw = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise IssueError("response.raw.json exceeds the response limit")
        return raw

    def public_receipt(self, request_id):
        """The broker's own summary, read from the VM rather than from GitHub."""
        if str(uuid.UUID(request_id)) != request_id:
            raise IssueError("invalid request_id")
        output = self.ssh("cat %s/%s.json" % (PUBLIC_RECEIPTS, request_id),
                          "read broker public receipt").stdout
        return strict_json_loads(output.strip(), 65536)


def verify_outcome(request, raw, public, permit=None):
    """Owner-side verdict from the owner's own contract code, not the job's.

    The GitHub job runs code from the commit under test, so its client receipt
    and artifact list are not trusted here. PASS needs the raw response to pass
    ``verify_response`` for the request the owner signed, a PASS remote receipt,
    and the broker's public receipt on the VM naming the same request and the
    same response hash with a PASS execution.
    """
    digest = sha256(canonical(request))
    files = verify_response(request, strict_json_loads(raw))
    remote = strict_json_loads(raw)["receipt"]
    failures = []
    if remote["result"] != "PASS":
        failures.append("remote receipt is BLOCKED")
    if permit is not None and permit["workload"]["mode"] == "pinned_set":
        expected = {"hostname": permit["host"]["hostname"], "boot_id": permit["host"]["boot_id"],
                    "gpu_processes": permit["workload"]["processes"]}
        if remote["schema"] != RECEIPT_SCHEMA_V2 or remote["runtime_before"] != expected:
            failures.append("remote receipt does not pin the GPU process set you signed")
    if public.get("request_sha256") != digest:
        failures.append("VM receipt names another request")
    if public.get("response_sha256") != sha256(raw):
        failures.append("VM receipt response hash differs from the downloaded response")
    if (public.get("execution") or {}).get("result") != "PASS":
        failures.append("VM receipt execution is not PASS")
    return files, remote, failures


def new_record():
    return {"schema": "szl.ssh-cpu-build.owner-issue.v1", "evidence_class": "MEASURED",
            "result": "BLOCKED", "stages": [], "failure": None, "run_id": None,
            "permit_id": None, "signed_bundle_sha256": None, "retry": False,
            "approval_outcome": None, "reconciliation_required": False}


def commit_approval(record):
    """Record an approval response this process observed. Does not call GitHub."""
    record["approval_outcome"] = "ACCEPTED"
    record["reconciliation_required"] = False
    record["stages"].append("approved")


def uncertain_approval(record, run_id, error):
    """The approval response was lost or failed. The run may already be approved.

    Does not withdraw or retry. The run id stays on the record so a person can
    reconcile that run before another attempt. This memory dies with the process.
    """
    record["run_id"] = run_id
    record["approval_outcome"] = "UNKNOWN"
    record["result"] = "UNKNOWN"
    record["retry"] = False
    record["reconciliation_required"] = True
    detail = ("approval outcome UNKNOWN for run %s; reconcile that run before retrying"
              % run_id)
    if not isinstance(error, Exception):
        print("CPU BUILD STOPPED: " + detail, file=sys.stderr)
        raise
    raise IssueError("%s: %s; %s" % (type(error).__name__, error, detail)) from error


def issue(args, record, owner=None, new_uuid=uuid.uuid4):
    """Fill ``record`` stage by stage, so a stop still leaves a receipt."""
    owner = owner or Owner(args)
    config = load_owner_config(Path(args.owner_config).read_bytes())
    record["environment"] = owner.preflight_environment()
    record["stages"].append("environment")
    owner.refuse_active_runs()
    owner.reachable()
    permit_id = str(new_uuid())
    record["permit_id"] = permit_id
    environment_id = None
    approval_attempted = False
    approval_accepted = False
    # Until approval is attempted, any stop (Ctrl+C included) withdraws the run,
    # which would otherwise wait at the gate and hold the concurrency group.
    # Once approval is attempted, a lost response must not withdraw it.
    try:
        run_id = owner.dispatch(permit_id)
        record["run_id"] = run_id
        plan = plan_request(owner.waiting_run(run_id), permit_id)
        request, digest = plan["request"], plan["request_sha256"]
        if request["source_sha"] != args.source_sha:
            raise IssueError("waiting run builds %s, not the commit you named (%s); not signing"
                             % (request["source_sha"], args.source_sha))
        environment_id = owner.pending_environment_id(run_id)
        record.update(request_sha256=digest, source_sha=request["source_sha"])
        record["stages"].append("waiting-run-bound")
        observed_at = owner.clock()
        observation = owner.observe()
        permit, window = build_grant(request, observation, config, new_uuid)
        data = bundle(permit, window)
        record.update(observation=observation, window_id=window["window_id"],
                      not_before=permit["not_before"], expires_at=permit["expires_at"],
                      signed_bundle_sha256=sha256(data))
        signature = owner.sign(data)
        owner.install(data, signature)
        record["stages"].append("permit-installed")
        if owner.clock() - observed_at > MAX_APPROVAL_DELAY_SECONDS:
            raise IssueError("too little window remains; not approving (permit stays unused)")
        approval_attempted = True
        owner.approve(run_id, environment_id, permit_id, digest)
        approval_accepted = True
        commit_approval(record)
    except BaseException as error:
        if approval_accepted:
            record["run_id"] = run_id
            record["approval_outcome"] = "ACCEPTED"
            record["retry"] = False
            record["reconciliation_required"] = False
            if not isinstance(error, Exception):
                print("CPU BUILD STOPPED: run %s approval was accepted; not withdrawing"
                      % run_id, file=sys.stderr)
                raise
            raise IssueError("%s: %s" % (type(error).__name__, error)) from error
        if approval_attempted:
            uncertain_approval(record, run_id, error)
        withdrawn = owner.withdraw(permit_id, record.get("run_id"), environment_id)
        record["unapproved_run"] = withdrawn
        if withdrawn["by_hand"] is None:
            raise
        if not isinstance(error, Exception):
            print("CPU BUILD STOPPED: " + withdrawn["by_hand"], file=sys.stderr)
            raise
        raise IssueError("%s: %s; %s" % (type(error).__name__, error, withdrawn["by_hand"])) \
            from error
    out = Path(args.evidence_dir) / ("run-%d" % run_id)
    out.mkdir(parents=True, exist_ok=False)
    raw = owner.collect(run_id, out)
    record["stages"].append("collected")
    try:
        files, remote, failures = verify_outcome(request, raw,
                                                 owner.public_receipt(request["request_id"]),
                                                 permit)
    except ContractError as error:
        raise IssueError("downloaded response failed owner-side verification: " + str(error))
    verified = out / "owner-verified"
    verified.mkdir()
    for name, content in sorted(files.items()):  # Fixed contract names only.
        (verified / name).write_bytes(content)
    record.update(response_sha256=sha256(raw), remote_result=remote["result"],
                  remote_steps=remote["steps"], runtime_before=remote["runtime_before"],
                  runtime_after=remote["runtime_after"], verification_failures=failures,
                  verified_files={name: sha256(data) for name, data in sorted(files.items())})
    record["result"] = "PASS" if not failures else "BLOCKED"
    record["stages"].append("owner-verified")
    return record


def install_permit(bundle_b64, signature_b64, config_path):
    """VM side, as root: verify the owner signature, then write once, never overwrite."""
    from tools.ssh_ci_broker import load_config
    config = load_config(config_path)
    data = base64.b64decode(bundle_b64, validate=True)
    signature = base64.b64decode(signature_b64, validate=True)
    read_protected(Path(config["allowed_signers"]), MAX_ALLOWED_SIGNERS_BYTES)
    value = verify_bundle(data, signature, config["allowed_signers"], config["owner_principal"])
    permit_id = value["permit"]["permit_id"]
    if type(permit_id) is not str or str(uuid.UUID(permit_id)) != permit_id \
            or value["window"].get("permit_id") != permit_id:
        raise ContractError("signed bundle permit identity is invalid")
    directory = Path(config["permit_dir"])
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for suffix, content in ((BUNDLE_SUFFIX, data), (SIGNATURE_SUFFIX, signature)):
        descriptor = os.open(directory / (permit_id + suffix), flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    return {"installed": permit_id, "bundle_sha256": sha256(data)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="owner workstation: issue and run one CPU build")
    run.add_argument("--owner-config", required=True)
    run.add_argument("--source-sha", required=True,
                     help="the full main commit you intend to build; any other is refused")
    run.add_argument("--signing-key", required=True, help="owner permit-signing private key")
    run.add_argument("--admin-target", required=True, help="for example Ubuntu@209.137.198.191")
    run.add_argument("--admin-known-hosts", help="independently verified host-key pin file")
    run.add_argument("--admin-key")
    run.add_argument("--port", type=int, default=22, help="VM ssh port for every ssh call")
    run.add_argument("--evidence-dir", default="szl-cpu-build-evidence")
    run.add_argument("--gh", default=shutil.which("gh") or "gh")
    run.add_argument("--ssh", default=shutil.which("ssh") or "ssh")
    run.add_argument("--ssh-keygen", default=shutil.which("ssh-keygen") or "ssh-keygen")
    install = sub.add_parser("install-permit", help="VM side, as root")
    install.add_argument("--bundle-b64", required=True)
    install.add_argument("--signature-b64", required=True)
    install.add_argument("--config", default="/etc/szl-cpu-broker/broker.json")
    args = parser.parse_args(argv)
    if args.action == "run" and re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is None:
        parser.error("--source-sha must be a full lowercase commit")
    if args.action == "run" and not 1 <= args.port <= 65535:
        parser.error("--port must be from 1 to 65535")
    try:
        if args.action == "install-permit":
            result = install_permit(args.bundle_b64, args.signature_b64, args.config)
        else:
            result = new_record()
            try:
                issue(args, result)
            finally:
                if sys.exc_info()[1] is not None:
                    result["failure"] = type(sys.exc_info()[1]).__name__ + ": " + str(sys.exc_info()[1])
                Path(args.evidence_dir).mkdir(parents=True, exist_ok=True)
                name = "owner-issue-%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                (Path(args.evidence_dir) / name).write_bytes(canonical(result) + b"\n")
    except (IssueError, ContractError, OSError, ValueError, subprocess.SubprocessError) as error:
        print("CPU BUILD BLOCKED: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("result", "PASS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
