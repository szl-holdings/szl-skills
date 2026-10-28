#!/usr/bin/env python3
"""Owner-run bring-up for the szl-skills isolated CPU build lane at one release.

Run on the workstation that holds the verified admin SSH key and host-key pin.
Stages run in order; each one that changes something asks first:

  keys       create the dedicated CI key and a separate permit-signing key (local)
  provision  fetch the merged commit on the VM, print the plan, apply on "APPLY"
  qualify    sandbox negative controls plus a real build rehearsal on the VM
  github     protected environment, secret, variables; SZL_CPU_CI_ENABLED last
  run        one owner-issued build (tools/ssh_ci_owner_issue.py) and its verdict

Nothing here touches the model process or the GPU. ``--dry-run`` prints every
command instead of running it. Requires ssh, ssh-keygen, git, and gh.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ssh_ci_contract import (ContractError, STEP_NAMES, canonical,
                                   strict_json_loads, validate_pinned_processes)

REPO = "szl-holdings/szl-skills"
ENVIRONMENT = "massed-cpu-build"
STAGES = ("keys", "provision", "qualify", "github", "run")
PLAIN_PATH = r"/[A-Za-z0-9._/@+-]{1,254}"
MAX_EVIDENCE_BYTES = 256 * 1024
QUALIFICATION_MAX_AGE_SECONDS = 900
QUALIFICATION_SCHEMA = "szl.ssh-cpu-build.qualification.v1"
ACTIVATION_SCHEMA = "szl.ssh-cpu-build.activation-state.v1"
CONTROLS = {"isolation", "tasks", "memory", "tmpfs", "deadline"}


class Stop(RuntimeError):
    """A stage refused or failed; later stages do not run."""


def evidence(data):
    """Decode bounded evidence without accepting duplicate keys or nonfinite JSON."""
    try:
        return strict_json_loads(data if isinstance(data, (str, bytes)) else canonical(data),
                                 MAX_EVIDENCE_BYTES)
    except (ContractError, TypeError, ValueError) as error:
        raise Stop("invalid qualification evidence") from error


def validate_qualification(receipt, state, release_sha, window_ref, now):
    """Check same-invocation host evidence; not a signature or independent witness."""
    receipt, state = evidence(receipt), evidence(state)
    try:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("clock must be aware UTC")
        if (receipt["schema"] != QUALIFICATION_SCHEMA or receipt["result"] != "QUALIFIED"
                or receipt["failures"] != [] or receipt["evidence_class"] != "MEASURED"
                or receipt["signed"] is not False or receipt["independent_witness"] is not False
                or state["schema"] != ACTIVATION_SCHEMA
                or receipt["release_sha"] != release_sha or state["release_sha"] != release_sha
                or receipt["owner_window_ref"] != window_ref or not window_ref):
            raise ValueError("qualification binding mismatch")
        digest = receipt["config_sha256"]
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None \
                or state["config_sha256"] != digest:
            raise ValueError("configuration drift")
        completed = datetime.fromisoformat(receipt["completed_at"].replace("Z", "+00:00"))
        if completed.utcoffset() != timedelta(0) or not timedelta(0) <= now - completed <= \
                timedelta(seconds=QUALIFICATION_MAX_AGE_SECONDS):
            raise ValueError("qualification is stale or future-dated")
        observation = state["observation"]
        if (set(observation) != {"schema", "observed_at", "host", "gpu_compute_processes",
                                 "competing_cpu_jobs"}
                or observation["schema"] != "szl.ssh-cpu-build.observation.v1"
                or type(observation["competing_cpu_jobs"]) is not int
                or observation["competing_cpu_jobs"] != 0):
            raise ValueError("observation unavailable or competing job")
        observed = datetime.strptime(observation["observed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        if not timedelta(0) <= now - observed <= timedelta(seconds=5):
            raise ValueError("observation is stale or future-dated")
        host = observation["host"]
        if (set(host) != {"hostname", "boot_id"}
                or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host["hostname"]) is None
                or str(uuid.UUID(host["boot_id"])) != host["boot_id"] or receipt["host"] != host):
            raise ValueError("host/boot identity mismatch")
        processes = observation["gpu_compute_processes"]
        validate_pinned_processes(processes, "activation observation")
        if receipt["model_before"] != processes or receipt["model_after"] != processes:
            raise ValueError("protected workload changed")
        controls = receipt["controls"]
        if (type(controls) is not list or len(controls) != len(CONTROLS)
                or {item["control"] for item in controls} != CONTROLS
                or any(item["failures"] != [] or item["readback"]["verified"] is not True
                       for item in controls)):
            raise ValueError("isolation controls did not all pass")
        rehearsal = receipt["rehearsal"]
        runner = rehearsal["runner"]
        steps = runner["steps"]
        if (rehearsal["source_sha"] != release_sha or rehearsal["failures"] != []
                or rehearsal["collection_problems"] != []
                or runner["schema"] != "szl.ssh-cpu-build.runner.v1"
                or runner["started"] is not True or runner["prepared"] is not True
                or runner["truncated"] != [] or type(steps) is not list
                or len(steps) != len(STEP_NAMES)
                or any(step["name"] != name or type(step["returncode"]) is not int
                       or step["returncode"] != 0 for step, name in zip(steps, STEP_NAMES))):
            raise ValueError("exact-source rehearsal did not pass")
    except (KeyError, TypeError, ValueError, AttributeError, ContractError) as error:
        raise Stop("qualification evidence rejected: " + str(error)) from error
    return receipt


class Lane:
    def __init__(self, args):
        self.args = args
        self.sha = args.release_sha
        self.work = Path(args.workdir).absolute()
        self.ci_key = self.work / "szl-cpu-ci"
        self.owner_key = self.work / "szl-owner-permit"
        self.host = args.vm.split("@", 1)[-1]
        self.qualification = None
        self.clock = lambda: datetime.now(timezone.utc)

    # -- plumbing -------------------------------------------------------------
    def run(self, argv, *, stdin=None, capture=False, check=True, timeout=60):
        print(("SIMULATED + " if self.args.dry_run else "+ ")
              + " ".join(shlex.quote(str(part)) for part in argv), flush=True)
        if self.args.dry_run:
            return subprocess.CompletedProcess(argv, 0, b"" if capture else None, b"")
        completed = subprocess.run([str(part) for part in argv], input=stdin,
                                   stdout=subprocess.PIPE if capture else None, check=False,
                                   timeout=timeout)
        if capture and len(completed.stdout or b"") > MAX_EVIDENCE_BYTES:
            raise Stop("command evidence exceeds byte limit")
        if check and completed.returncode != 0:
            raise Stop("command failed with exit %d: %s" % (completed.returncode, argv[0]))
        return completed

    def ssh_options(self):
        options = ["-o", "StrictHostKeyChecking=yes",
                   "-o", "UserKnownHostsFile=" + str(Path(self.args.known_hosts).absolute())]
        if self.args.admin_key:
            options += ["-o", "IdentitiesOnly=yes", "-i", self.args.admin_key]
        if self.args.port != 22:
            options += ["-o", "Port=%d" % self.args.port]
        return options

    def remote(self, command, *, tty=False, check=True, stdin=None, capture=False, timeout=60):
        flag = ["-t"] if tty else ["-T", "-o", "BatchMode=yes"]
        return self.run(["ssh", *self.ssh_options(), *flag, self.args.vm, command], check=check,
                        stdin=stdin, capture=capture, timeout=timeout)

    def confirm(self, question, word):
        if self.args.dry_run:
            print("SIMULATED (would ask): " + question)
            return True
        return input(question + " Type %s to continue: " % word).strip() == word

    # -- stages ---------------------------------------------------------------
    def keys(self):
        if not self.confirm("Create any missing dedicated CI and permit-signing keys?", "KEYS"):
            raise Stop("keys not created")
        if not self.args.dry_run:
            self.work.mkdir(parents=True, exist_ok=True)
        if not self.ci_key.exists():
            self.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "szl-cpu-ci",
                      "-f", self.ci_key])
        if not self.owner_key.exists():
            print("Choose a passphrase for your permit-signing key; it signs every build permit.")
            self.run(["ssh-keygen", "-t", "ed25519", "-C", "stephen-szl-permit",
                      "-f", self.owner_key])
        if not self.args.dry_run:
            ci, owner = (Path(str(key) + ".pub").read_text().split()[1]
                         for key in (self.ci_key, self.owner_key))
            if ci == owner:
                raise Stop("the CI key and the permit-signing key must differ")
        self.report("keys", "CI key %s, permit-signing key %s" % (self.ci_key, self.owner_key))

    def provision(self):
        self.qualification = None
        self.require_window()
        hidden = []
        for path in self.args.hidden_path:
            if re.fullmatch(PLAIN_PATH, path) is None:
                raise Stop("hidden path must be a plain absolute path: " + path)
            hidden += ["--hidden-path", path]
        if not self.confirm("Stage public keys and exact source in a NEW private VM directory "
                            "(retained, never deleted automatically)?", "STAGE"):
            raise Stop("provisioning not staged")
        # No existing directory is reused, overwritten or removed, even on failure.
        staged = self.remote("umask 077; mktemp -d /tmp/szl-cpu-bringup-XXXXXXXXXX", capture=True)
        directory = ("/tmp/szl-cpu-bringup-SIMULATED" if self.args.dry_run else
                     staged.stdout.decode("ascii").strip())
        if re.fullmatch(r"/tmp/szl-cpu-bringup-[A-Za-z0-9]{8,32}", directory) is None:
            raise Stop("unexpected staging path; no further writes attempted")
        self.report("staging", "retained directory " + directory)
        # Public halves only, over plain ssh stdin (no scp/SFTP subsystem needed).
        for key, name in ((self.ci_key, "szl-cpu-ci.pub"), (self.owner_key, "szl-owner-permit.pub")):
            data = b"" if self.args.dry_run else Path(str(key) + ".pub").read_bytes()
            self.remote("umask 077; set -C; cat > %s/%s" % (directory, name), stdin=data)
        source = directory + "/src"
        self.remote(" && ".join([
            "git init -q " + source,
            "git -C %s fetch -q --depth=1 --no-tags https://github.com/%s %s" % (source, REPO, self.sha),
            "git -C %s -c advice.detachedHead=false checkout -q --detach %s" % (source, self.sha),
            'test "$(git -C %s rev-parse HEAD)" = %s' % (source, self.sha), "echo SOURCE_OK " + self.sha]))
        base = ("sudo python3 -I -B %s/tools/ssh_ci_provision.py {action} --release-sha %s "
                "--ci-public-key %s/szl-cpu-ci.pub --owner-public-key %s/szl-owner-permit.pub "
                '--admin-user "$USER" %s' % (source, self.sha, directory, directory,
                                            " ".join(shlex.quote(part) for part in hidden)))
        plan = self.remote(base.format(action="plan"), tty=True, check=False)
        if plan.returncode != 0:
            raise Stop("the provisioning plan is not ready; read its failures above")
        if not self.confirm("The plan above is ready. Apply it to the VM now?", "APPLY"):
            raise Stop("provisioning not applied")
        self.remote(base.format(action="apply") + " --owner-window-ref "
                    + shlex.quote(self.args.window_ref), tty=True, timeout=300)
        self.report("provision", "applied; broker socket enabled for szl-ci")

    def qualify(self):
        self.qualification = None
        self.require_window()
        if not self.confirm("Run bounded CPU isolation controls and the exact-source rehearsal?",
                            "QUALIFY"):
            raise Stop("qualification not run")
        result = self.remote(self.qualifier_command() + " --rehearse-build %s --owner-window-ref %s"
                             % (self.sha, shlex.quote(self.args.window_ref)), capture=True,
                             check=False, timeout=900)
        if result.returncode != 0:
            raise Stop("qualification is not QUALIFIED; do not enable the lane")
        if not self.args.dry_run:
            receipt = evidence(result.stdout)
            self.qualification = validate_qualification(
                receipt, self.activation_state(), self.sha, self.args.window_ref, self.clock())
        self.report("qualify", "QUALIFIED")

    def qualifier_command(self):
        return ("sudo -n python3 -I -B /opt/szl-cpu-broker/releases/%s/tools/ssh_ci_qualify.py"
                " --expected-release %s" % (self.sha, self.sha))

    def activation_state(self):
        return evidence(self.remote(self.qualifier_command() + " --readiness",
                                    capture=True).stdout)

    def require_qualification(self):
        if self.args.dry_run:
            print("SIMULATED: would require a fresh exact-source qualification receipt")
            return
        if self.qualification is None:
            raise Stop("run qualify,github together: no live qualification in this invocation")
        validate_qualification(self.qualification, self.activation_state(), self.sha,
                               self.args.window_ref, self.clock())

    def github(self):
        self.require_qualification()
        if not self.confirm("Configure the protected GitHub environment and dedicated CI credential?",
                            "CONFIGURE"):
            raise Stop("GitHub configuration not changed")
        user = json.loads(self.run(["gh", "api", "user"], capture=True).stdout or b'{"id": 0}')
        environment = {"reviewers": [{"type": "User", "id": user["id"]}],
                       "prevent_self_review": False,
                       "deployment_branch_policy": {"protected_branches": True,
                                                    "custom_branch_policies": False}}
        pattern = self.host if self.args.port == 22 else "[%s]:%d" % (self.host, self.args.port)
        found = self.run(["ssh-keygen", "-F", pattern, "-f", self.args.known_hosts],
                         capture=True, check=False).stdout or b""
        pins = [line for line in found.decode("ascii", "replace").splitlines()
                if line.strip() and not line.startswith("#")]
        if not pins and not self.args.dry_run:
            raise Stop("no verified host-key pin for %s in %s" % (pattern, self.args.known_hosts))
        if not self.args.dry_run:
            secret = self.ci_key.read_bytes()
        else:
            secret = b""
        # Configuration writes start only after qualification, consent and pin checks.
        self.require_qualification()
        self.run(["gh", "api", "-X", "PUT", "repos/%s/environments/%s" % (REPO, ENVIRONMENT),
                  "--input", "-"], stdin=json.dumps(environment).encode(), capture=True)
        self.run(["gh", "secret", "set", "SZL_CPU_SSH_KEY", "--env", ENVIRONMENT, "-R", REPO],
                 stdin=secret)
        for name, value in (("SZL_CPU_SSH_HOST", self.host), ("SZL_CPU_SSH_PORT", str(self.args.port)),
                            ("SZL_CPU_SSH_USER", "szl-ci"), ("SZL_CPU_KNOWN_HOSTS", "\n".join(pins))):
            self.run(["gh", "variable", "set", name, "--env", ENVIRONMENT, "-R", REPO, "--body", value])
        if self.confirm("Enable the CPU lane after a fresh qualification/identity recheck?", "ENABLE"):
            self.require_qualification()
            self.run(["gh", "variable", "set", "SZL_CPU_CI_ENABLED", "--env", ENVIRONMENT, "-R",
                      REPO, "--body", "true"])
        if self.confirm("The CI private key is now only needed in GitHub. Delete the local copy?",
                        "DELETE") and not self.args.dry_run:
            self.ci_key.unlink()
        self.report("github", "environment %s configured" % ENVIRONMENT)

    def build(self):
        if not self.confirm("Prepare owner attestations and issue one signed CPU build?", "RUN"):
            raise Stop("build not issued")
        config = Path(self.args.owner_config or self.work / "owner-cpu-window.json")
        if not config.exists() and self.args.dry_run:
            print("(dry run: would write an owner-config template at %s)" % config)
        elif not config.exists():
            config.write_text(json.dumps({
                "schema": "szl.ssh-cpu-build.owner-config.v1",
                "provider_instance_uuid": "bd6e0a2e-c7d1-4423-bfc2-99b575c6a233",
                "hf_revision": "FILL: admitted Hugging Face revision",
                "budget": {"ledger_ref": "FILL: your budget ledger reference",
                           "remaining_seconds": 0, "remaining_usd": 0},
                "checkpoint_manifest_sha256": "FILL: 64 lowercase hex characters",
                "owner_receipt_ref": "FILL: your approval reference"}, indent=2) + "\n")
            raise Stop("wrote %s; fill in your own attestations, then rerun --stages run" % config)
        source = self.work / "src"
        if not (source / ".git").exists():
            self.run(["git", "init", "-q", source])
        self.run(["git", "-C", source, "fetch", "-q", "--depth=1", "--no-tags",
                  "https://github.com/" + REPO, self.sha])
        self.run(["git", "-C", source, "-c", "advice.detachedHead=false", "checkout", "-q",
                  "--detach", self.sha])
        command = [sys.executable, "-B", source / "tools" / "ssh_ci_owner_issue.py", "run",
                   "--owner-config", config, "--source-sha", self.sha, "--signing-key",
                   self.owner_key,
                   "--admin-target", self.args.vm, "--admin-known-hosts", self.args.known_hosts,
                   "--evidence-dir", self.work / "evidence"]
        if self.args.admin_key:
            command += ["--admin-key", self.args.admin_key]
        # Owner issuance already bounds approval, signing, SSH and collection.
        # A short outer timeout could kill its receipt writer after dispatch.
        self.run(command, timeout=None)
        self.report("run", "owner-verified PASS; evidence under %s" % (self.work / "evidence"))

    def report(self, stage, message):
        if self.args.dry_run:
            print("SIMULATED %s: planned only; no state or outcome established" % stage)
        else:
            print("%s: %s" % (stage, message))

    def require_window(self):
        if not self.args.window_ref:
            raise Stop("--window-ref (your approval reference for this VM window) is required")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release-sha", required=True,
                        help="the full merged main commit to install, qualify, and build")
    parser.add_argument("--vm", required=True, help="admin login, for example Ubuntu@203.0.113.10")
    parser.add_argument("--known-hosts", required=True, help="your verified host-key pin file")
    parser.add_argument("--admin-key", help="admin SSH private key, if not your default")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--workdir", default="szl-cpu-lane")
    parser.add_argument("--window-ref", help="approval reference recorded in VM receipts")
    parser.add_argument("--hidden-path", action="append", default=[],
                        help="extra model or data directory to hide from builds (repeatable)")
    parser.add_argument("--owner-config", help="owner attestations for the run stage")
    parser.add_argument("--stages", default="keys,provision,qualify,github")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    stages = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
    unknown = sorted(set(stages) - set(STAGES))
    if unknown:
        parser.error("unknown stage(s): " + ", ".join(unknown))
    if "@" not in args.vm or re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.:-]+", args.vm) is None:
        parser.error("--vm must be a plain user@host")
    if re.fullmatch(r"[0-9a-f]{40}", args.release_sha) is None:
        parser.error("--release-sha must be a full lowercase commit")
    lane = Lane(args)
    handlers = {"keys": lane.keys, "provision": lane.provision, "qualify": lane.qualify,
                "github": lane.github, "run": lane.build}
    for stage in STAGES:
        if stage in stages:
            print("== " + stage, flush=True)
            try:
                handlers[stage]()
            except (Stop, OSError, ValueError, subprocess.SubprocessError) as error:
                print("STOPPED at %s: %s" % (stage, error), file=sys.stderr)
                return 1
    print(("SIMULATED: no commands executed; planned stages: " if args.dry_run else "done: ")
          + ", ".join(stage for stage in STAGES if stage in stages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
