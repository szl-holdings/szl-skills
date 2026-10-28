#!/usr/bin/env python3
"""Owner-run issue flow for one CPU build: dispatch, bind, sign, install, approve.

Run on the owner's workstation with ``gh`` (owner login), ``ssh`` (verified
admin key and host pin), and ``ssh-keygen`` (owner permit-signing key). It
reserves a fresh permit UUID, dispatches the manual workflow, waits for the
protected-environment gate, reconstructs the exact request from that waiting
run, observes the VM read-only, builds a <=900-second permit and owner window
from that observation and the owner's own attestations, signs them, installs
them through the VM's root verifier, and approves only that run. It never
retries, never reuses a permit, and never touches the GPU or the model.

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
from tools.ssh_ci_contract import CPU_PROFILE, ContractError, canonical, sha256, strict_json_loads
from tools.ssh_ci_owner_signature import (BUNDLE_SUFFIX, MAX_ALLOWED_SIGNERS_BYTES, NAMESPACE,
                                          SIGNATURE_SUFFIX, bundle, read_protected,
                                          verify_bundle)
from tools.ssh_ci_owner_window import WINDOW_SCHEMA, validate_owner_window
from tools.ssh_ci_plan import plan_request


REPOSITORY = "szl-holdings/szl-skills"
WORKFLOW = "isolated-ssh-build.yml"
ENVIRONMENT = "massed-cpu-build"
OWNER_CONFIG_SCHEMA = "szl.ssh-cpu-build.owner-config.v1"
REMOTE_TOOLS = "/opt/szl-cpu-broker/current/tools"
WINDOW_SECONDS = 900
MAX_APPROVAL_DELAY_SECONDS = 300  # Leaves >=600s for runner pickup and the >=450s claim floor.
REQUIRED_VARIABLES = {"SZL_CPU_CI_ENABLED", "SZL_CPU_SSH_HOST", "SZL_CPU_SSH_PORT",
                      "SZL_CPU_SSH_USER", "SZL_CPU_KNOWN_HOSTS"}
OWNER_FIELDS = frozenset({"schema", "provider_instance_uuid", "hf_revision", "budget",
                          "checkpoint_manifest_sha256", "owner_receipt_ref"})


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
    return value


def build_grant(request, observation, owner, new_uuid=uuid.uuid4):
    """Return (permit, window) bound to one request, host, and pinned model process."""
    processes = observation["gpu_compute_processes"]
    if len(processes) != 1:
        raise IssueError("exactly one pinned GPU process is required by the v1 receipt; observed %d"
                         % len(processes))
    start = datetime.strptime(observation["observed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    window_bounds = {"not_before": stamp(start),
                     "expires_at": stamp(start + timedelta(seconds=WINDOW_SECONDS))}
    digest = sha256(canonical(request))
    permit = {"schema": PERMIT_SCHEMA, "permit_id": request["permit_id"], "request_sha256": digest,
              "max_runs": 1, "cpu_profile": dict(CPU_PROFILE), "host": dict(observation["host"]),
              "workload": {"mode": "pinned", "pid": processes[0]["pid"],
                           "start_ticks": processes[0]["start_ticks"]}, **window_bounds}
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

    def command(self, argv, stage, *, stdin=None, check=True):
        completed = self.run(argv, input=stdin, capture_output=True, timeout=600, check=False)
        if check and completed.returncode != 0:
            raise IssueError(stage + " failed: " + completed.stderr.decode("utf-8", "replace")[-300:])
        return completed

    def gh_json(self, argv, stage):
        output = self.command([self.args.gh, *argv], stage).stdout
        try:
            return json.loads(output.decode("utf-8"))
        except ValueError as error:
            raise IssueError(stage + " returned non-JSON output") from error

    def ssh(self, remote, stage, *, tty=False):
        argv = [self.args.ssh, "-o", "StrictHostKeyChecking=yes", "-o", "ClearAllForwardings=yes"]
        if self.args.admin_known_hosts:
            argv += ["-o", "UserKnownHostsFile=" + self.args.admin_known_hosts]
        if self.args.admin_key:
            argv += ["-o", "IdentitiesOnly=yes", "-i", self.args.admin_key]
        argv += ["-t"] if tty else ["-T", "-o", "BatchMode=yes"]
        argv += [self.args.admin_target, remote]
        if tty:  # sudo may prompt on the owner's own terminal.
            completed = self.run(argv, timeout=600, check=False)
            if completed.returncode != 0:
                raise IssueError(stage + " failed with exit %s" % completed.returncode)
            return completed
        return self.command(argv, stage)

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
                "can_admins_bypass": environment.get("can_admins_bypass")}

    def dispatch(self, permit_id):
        self.command([self.args.gh, "workflow", "run", WORKFLOW, "-R", REPOSITORY, "--ref", "main",
                      "-f", "permit_id=" + permit_id], "dispatch workflow")
        deadline = self.clock() + 120
        title = "CPU build permit " + permit_id
        while self.clock() < deadline:
            runs = self.gh_json(["run", "list", "-R", REPOSITORY, "--workflow", WORKFLOW,
                                 "--event", "workflow_dispatch", "--limit", "20",
                                 "--json", "databaseId,displayTitle"], "list runs")
            matches = [item["databaseId"] for item in runs if item.get("displayTitle") == title]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise IssueError("more than one run carries this permit; refusing")
            self.sleep(3)
        raise IssueError("dispatched run did not appear")

    def waiting_run(self, run_id):
        deadline = self.clock() + 180
        while self.clock() < deadline:
            raw = self.command([self.args.gh, "api", "repos/%s/actions/runs/%d" % (REPOSITORY, run_id)],
                               "read run").stdout
            status = json.loads(raw.decode("utf-8")).get("status")
            if status == "waiting":
                return raw
            if status == "completed":
                raise IssueError("run completed before approval (configuration BLOCKED?)")
            self.sleep(3)
        raise IssueError("run never reached the environment approval gate")

    def pending_environment_id(self, run_id):
        pending = self.gh_json(["api", "repos/%s/actions/runs/%d/pending_deployments"
                                % (REPOSITORY, run_id)], "read pending deployments")
        ids = [item["environment"]["id"] for item in pending
               if item.get("environment", {}).get("name") == ENVIRONMENT
               and item.get("current_user_can_approve") is True]
        if len(ids) != 1:
            raise IssueError("the owner cannot approve exactly one %s gate" % ENVIRONMENT)
        return ids[0]

    def observe(self):
        output = self.ssh("python3 -I -B %s/ssh_ci_observer.py" % REMOTE_TOOLS, "observe VM").stdout
        return strict_json_loads(output.strip(), 4096)

    def sign(self, data):
        with tempfile.TemporaryDirectory(prefix="szl-permit-sign-") as scratch:
            path = Path(scratch) / "bundle.json"
            path.write_bytes(data)
            completed = self.run([self.args.ssh_keygen, "-Y", "sign", "-f", self.args.signing_key,
                                  "-n", NAMESPACE, str(path)], timeout=300, check=False)
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
        self.command([self.args.gh, "run", "watch", str(run_id), "-R", REPOSITORY, "--interval", "10"],
                     "watch run", check=False)
        self.command([self.args.gh, "run", "download", str(run_id), "-R", REPOSITORY,
                      "-n", "ssh-cpu-build-%d-1" % run_id, "-D", str(out)], "download evidence")
        receipt = json.loads((out / "client-receipt.json").read_text(encoding="utf-8"))
        mismatched = [name for name, meta in receipt.get("verified_files", {}).items()
                      if sha256((out / "artifacts" / name).read_bytes()) != meta["sha256"]]
        return receipt, mismatched


def new_record():
    return {"schema": "szl.ssh-cpu-build.owner-issue.v1", "evidence_class": "MEASURED",
            "result": "BLOCKED", "stages": [], "failure": None, "run_id": None,
            "permit_id": None, "signed_bundle_sha256": None, "retry": False}


def issue(args, record, owner=None, new_uuid=uuid.uuid4):
    """Fill ``record`` stage by stage, so a stop still leaves a receipt."""
    owner = owner or Owner(args)
    config = load_owner_config(Path(args.owner_config).read_bytes())
    record["environment"] = owner.preflight_environment()
    record["stages"].append("environment")
    permit_id = str(new_uuid())
    run_id = owner.dispatch(permit_id)
    record.update(permit_id=permit_id, run_id=run_id)
    plan = plan_request(owner.waiting_run(run_id), permit_id)
    request, digest = plan["request"], plan["request_sha256"]
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
    owner.approve(run_id, environment_id, permit_id, digest)
    record["stages"].append("approved")
    out = Path(args.evidence_dir) / ("run-%d" % run_id)
    out.mkdir(parents=True, exist_ok=False)
    receipt, mismatched = owner.collect(run_id, out)
    record.update(client_result=receipt.get("result"), client_failures=receipt.get("failures"),
                  artifact_hash_mismatches=mismatched,
                  remote_result=(receipt.get("remote_receipt") or {}).get("result"))
    record["result"] = "PASS" if receipt.get("result") == "PASS" and not mismatched else "BLOCKED"
    record["stages"].append("collected")
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
    run.add_argument("--signing-key", required=True, help="owner permit-signing private key")
    run.add_argument("--admin-target", required=True, help="for example Ubuntu@209.137.198.191")
    run.add_argument("--admin-known-hosts", help="independently verified host-key pin file")
    run.add_argument("--admin-key")
    run.add_argument("--evidence-dir", default="szl-cpu-build-evidence")
    run.add_argument("--gh", default=shutil.which("gh") or "gh")
    run.add_argument("--ssh", default=shutil.which("ssh") or "ssh")
    run.add_argument("--ssh-keygen", default=shutil.which("ssh-keygen") or "ssh-keygen")
    install = sub.add_parser("install-permit", help="VM side, as root")
    install.add_argument("--bundle-b64", required=True)
    install.add_argument("--signature-b64", required=True)
    install.add_argument("--config", default="/etc/szl-cpu-broker/broker.json")
    args = parser.parse_args(argv)
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
    except (IssueError, ContractError, OSError, ValueError) as error:
        print("CPU BUILD BLOCKED: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("result", "PASS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
