#!/usr/bin/env python3
"""Owner-run bring-up for the szl-skills isolated CPU build lane at one release.

Run on the workstation that holds the verified admin SSH key and host-key pin.
Stages run in order; each one that changes something asks first:

  plan       live read-only preflight; pins source, host and GitHub preimage
  keys       verify separately provisioned CI and permit-signing keys (read-only)
  provision  after STAGE, prove a BatchMode login, stage into a new private
             /tmp directory that is never deleted or reused, plan, apply on
             APPLY, then retest a fresh admin login
  qualify    sandbox negative controls plus a real build rehearsal on the VM
  github     fresh qualification plus unchanged live plan before variable
             writes; the environment and secret must already exist;
             SZL_CPU_CI_ENABLED is written last
  run        one owner-issued build (tools/ssh_ci_owner_issue.py) and its verdict

Stages that use unattended (BatchMode) ssh first prove one such login works.
Nothing here touches the model process or the GPU. ``--dry-run`` prints every
command instead of running it. Requires ssh, ssh-keygen, git, and gh.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
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
from tools.ssh_ci_environment import (environment_variables_preimage,
                                      required_environment_preimage)
from tools.ssh_ci_broker import overlaps_unit_mount
from tools.ssh_ci_provision import ProvisionError, public_key as normalize_public_key

REPO = "szl-holdings/szl-skills"
ENVIRONMENT = "massed-cpu-build"
STAGES = ("plan", "keys", "provision", "qualify", "github", "run")
PLAIN_PATH = r"/[A-Za-z0-9._/@+-]{1,254}"
MAX_EVIDENCE_BYTES = 256 * 1024
QUALIFICATION_MAX_AGE_SECONDS = 900
QUALIFICATION_SCHEMA = "szl.ssh-cpu-build.qualification.v1"
ACTIVATION_SCHEMA = "szl.ssh-cpu-build.activation-state.v1"
CONTROLS = {"isolation", "tasks", "memory", "tmpfs", "deadline"}
UNINSTALL = "sudo python3 -I -B /opt/szl-cpu-broker/current/tools/ssh_ci_provision.py uninstall"


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
        self.admin = args.vm.split("@", 1)[0]
        self.qualification = None
        self.plan_state = None
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

    def login(self):
        """One fresh BatchMode admin login (no reused connection), as unattended calls make."""
        return self.run(["ssh", *self.ssh_options(), "-T", "-o", "BatchMode=yes",
                         "-o", "ControlPath=none", self.args.vm, "true"], check=False)

    def require_login(self):
        if self.login().returncode != 0:
            raise Stop("a BatchMode admin login to %s failed; load the admin key with ssh-add "
                       "(Windows: start the ssh-agent service), then rerun" % self.args.vm)

    def recovery(self):
        """What to run from a second admin session when a fresh admin login fails after apply."""
        return ("%s; if fresh admin logins still fail, apply's sshd reload may have activated a "
                "pending sshd_config or sshd_config.d edit: check sudo sshd -T -C "
                "user=%s,host=localhost,addr=127.0.0.1 and again with addr= your workstation's "
                "address (the first field of $SSH_CLIENT), since Match Address/Host blocks only "
                "apply to their own address; compare file times under /etc/ssh, revert any edit "
                "szl did not make, then run sudo systemctl reload ssh" % (UNINSTALL, self.admin))

    def gh_json(self, argv):
        output = self.run(["gh", *argv], capture=True).stdout
        if self.args.dry_run:
            return None
        try:
            return json.loads(output.decode("utf-8"))
        except ValueError as error:
            raise Stop("gh %s returned non-JSON output" % " ".join(argv)) from error

    def confirm(self, question, word):
        if self.args.dry_run:
            print("SIMULATED (would ask for %s): %s" % (word, question))
            return True
        return input(question + " Type %s to continue: " % word).strip() == word

    def public_key(self, private_path, label):
        """Validate public material and private-file presence without reading private bytes."""
        private_path = Path(private_path)
        public_path = Path(str(private_path) + ".pub")
        if (private_path.is_symlink() or public_path.is_symlink()
                or not private_path.is_file() or not public_path.is_file()):
            raise Stop("pre-provision %s private/public key files before bring-up" % label)
        try:
            text = public_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise Stop("%s public key is unavailable or not ASCII" % label) from error
        try:
            normalized = normalize_public_key(text, label + " public key")
        except ProvisionError as error:
            raise Stop(str(error)) from error
        fields = normalized.split()
        if fields[0] != "ssh-ed25519":
            raise Stop("%s public key is not Ed25519" % label)
        return fields[1], text

    def preprovisioned_keys(self):
        ci, ci_text = self.public_key(self.ci_key, "CI")
        owner, owner_text = self.public_key(self.owner_key, "owner permit-signing")
        if ci == owner:
            raise Stop("the CI key and the permit-signing key must differ")
        return ci_text, owner_text

    def public_key_snapshot(self):
        """Return the exact public-key bytes the live plan will later stage."""
        if self.args.dry_run:
            return {
                "szl-cpu-ci.pub": "ssh-ed25519 SIMULATED-CI-PUBLIC-KEY",
                "szl-owner-permit.pub": "ssh-ed25519 SIMULATED-OWNER-PUBLIC-KEY",
            }
        ci, owner = self.preprovisioned_keys()
        return {"szl-cpu-ci.pub": ci, "szl-owner-permit.pub": owner}

    def host_pins(self):
        pattern = self.host if self.args.port == 22 else "[%s]:%d" % (self.host, self.args.port)
        found = self.run(["ssh-keygen", "-F", pattern, "-f", self.args.known_hosts],
                         capture=True, check=False).stdout or b""
        if self.args.dry_run:
            return [pattern + " ssh-ed25519 SIMULATED-PIN"]
        pins = [line for line in found.decode("ascii", "replace").splitlines()
                if line.strip() and not line.startswith("#")]
        if not pins:
            raise Stop("no verified host-key pin for %s in %s" % (pattern, self.args.known_hosts))
        return pins

    def require_release_is_current_main(self):
        commit = self.gh_json(["api", "repos/%s/commits/main" % REPO])
        if self.args.dry_run:
            self.report("plan", "would require release SHA to equal current main")
            return
        if type(commit) is not dict or commit.get("sha") != self.sha:
            raise Stop("release SHA is not the current main commit; re-read and re-plan")

    def environment_variable_snapshot(self, *, allow_enabled=False):
        endpoint = "repos/%s/environments/%s/variables?per_page=100" % (REPO, ENVIRONMENT)
        if self.args.dry_run:
            return {"variables_sha256": "0" * 64, "variables": {}}
        variables = self.gh_json(["api", endpoint])
        try:
            preimage, values = environment_variables_preimage(variables)
            digest = hashlib.sha256(canonical(preimage)).hexdigest()
        except (ContractError, TypeError, ValueError) as error:
            raise Stop("GitHub environment variable preimage rejected: " + str(error)) from error
        enabled = values.get("SZL_CPU_CI_ENABLED")
        if not allow_enabled and enabled not in (None, "false"):
            raise Stop("SZL_CPU_CI_ENABLED must be absent or exactly false before configuration")
        return {"variables_sha256": digest, "variables": values}

    def environment_snapshot(self, *, allow_enabled=False):
        endpoint = "repos/%s/environments/%s" % (REPO, ENVIRONMENT)
        policies = endpoint + "/deployment-branch-policies"
        environments = self.gh_json(["api", "repos/%s/environments?per_page=100" % REPO])
        if self.args.dry_run:
            return {"environment_sha256": "0" * 64,
                    "secret_names": ["SZL_CPU_SSH_KEY"],
                    "variables_sha256": "0" * 64, "variables": {}}
        rows = environments.get("environments") if type(environments) is dict else None
        count = environments.get("total_count") if type(environments) is dict else None
        if (type(count) is not int or type(rows) is not list or count != len(rows)
                or any(type(row) is not dict or type(row.get("name")) is not str
                       for row in rows)):
            raise Stop("environment listing is incomplete; no configuration changed")
        if sum(row["name"] == ENVIRONMENT for row in rows) != 1:
            raise Stop("pre-provision the protected %s environment before bring-up" % ENVIRONMENT)
        environment = self.gh_json(["api", endpoint])
        branch_policies = self.gh_json(["api", policies + "?per_page=100"])
        details = {}

        def lookup(policy_id):
            details[policy_id] = self.gh_json(["api", policies + "/" + str(policy_id)])
            return details[policy_id]

        secrets = self.gh_json(["api", endpoint + "/secrets?per_page=100"])
        try:
            preimage = required_environment_preimage(
                environment, branch_policies, secrets, expected_name=ENVIRONMENT,
                required_secret="SZL_CPU_SSH_KEY", lookup=lookup)
            digest = hashlib.sha256(canonical(preimage)).hexdigest()
        except (ContractError, TypeError, ValueError) as error:
            raise Stop("GitHub environment preimage rejected: " + str(error)) from error
        variable_snapshot = self.environment_variable_snapshot(allow_enabled=allow_enabled)
        return {"environment_sha256": digest,
                "secret_names": [row["name"] for row in preimage["secrets"]],
                **variable_snapshot}

    def verify_plan(self, *, expected_variables=None, allow_enabled=False):
        if self.plan_state is None:
            raise Stop("run plan in this invocation before any mutating stage")
        public_keys = self.public_key_snapshot()
        if public_keys != self.plan_state["public_keys"]:
            raise Stop("planned public key material changed; activation refused")
        pins = self.host_pins()
        self.require_protected_main()
        self.require_release_is_current_main()
        current = self.environment_snapshot(allow_enabled=allow_enabled)
        if (pins != self.plan_state["pins"]
                or current["environment_sha256"] != self.plan_state["environment_sha256"]):
            raise Stop("live plan authority preimage changed; activation refused")
        if expected_variables is None:
            if current["variables_sha256"] != self.plan_state["variables_sha256"]:
                raise Stop("live plan variable preimage changed; activation refused")
        elif self.args.dry_run:
            return current
        elif current["variables"] != expected_variables:
            raise Stop("environment variables do not match the exact planned values")
        return current

    def connection_variables(self, pins):
        """The exact ordered non-secret connection values proposed by the live plan."""
        return {
            "SZL_CPU_SSH_HOST": self.host,
            "SZL_CPU_SSH_PORT": str(self.args.port),
            "SZL_CPU_SSH_USER": "szl-ci",
            "SZL_CPU_KNOWN_HOSTS": "\n".join(pins),
        }

    # -- stages ---------------------------------------------------------------
    def plan(self):
        """Perform live reads only and bind every later write to their preimage."""
        self.plan_state = None
        self.require_window()
        if self.args.dry_run:
            print("SIMULATED: live plan would validate pre-provisioned keys and read host/GitHub state")
        public_keys = self.public_key_snapshot()
        pins = self.host_pins()
        self.require_login()
        self.require_protected_main()
        self.require_release_is_current_main()
        snapshot = self.environment_snapshot()
        self.plan_state = {"pins": pins, "public_keys": public_keys, **snapshot}
        connection = self.connection_variables(pins)
        plan = {"schema": "szl.ssh-cpu-build.live-plan.v1", "repo": REPO,
                "release_sha": self.sha, "vm": self.args.vm, "port": self.args.port,
                "environment": ENVIRONMENT,
                "environment_sha256": snapshot["environment_sha256"],
                "variables_sha256": snapshot["variables_sha256"],
                 "public_keys_sha256": hashlib.sha256(canonical(public_keys)).hexdigest(),
                "preprovisioned_secret": "SZL_CPU_SSH_KEY",
                "writes": [{"name": name, "value": value}
                           for name, value in connection.items()]
                          + [{"name": "SZL_CPU_CI_ENABLED", "value": "true"}]}
        print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
        self.report("plan", "live read-only preimage recorded")

    def keys(self):
        if self.args.dry_run:
            print("SIMULATED: would verify distinct public keys and private-file presence")
        else:
            self.preprovisioned_keys()
        self.report("keys", "distinct public keys and private-file presence verified")

    def provision(self):
        self.qualification = None
        self.require_window()
        hidden = []
        for path in self.args.hidden_path:
            if re.fullmatch(PLAIN_PATH, path) is None:
                raise Stop("hidden path must be a plain absolute path: " + path)
            if overlaps_unit_mount(path):  # The VM plan refuses it too, after staging.
                raise Stop("hidden path must not cover a build unit mount point: " + path)
            hidden += ["--hidden-path", path]
        if not self.confirm("Stage public keys and exact source in a NEW private VM directory "
                            "(retained, never deleted automatically)?", "STAGE"):
            raise Stop("provisioning not staged; nothing was written to the VM")
        self.verify_plan()
        self.require_login()
        # No existing directory is reused, overwritten or removed, even on failure.
        staged = self.remote("umask 077; mktemp -d /tmp/szl-cpu-bringup-XXXXXXXXXX", capture=True)
        directory = ("/tmp/szl-cpu-bringup-SIMULATED" if self.args.dry_run else
                     staged.stdout.decode("ascii").strip())
        if re.fullmatch(r"/tmp/szl-cpu-bringup-[A-Za-z0-9]{8,32}", directory) is None:
            raise Stop("unexpected staging path; no further writes attempted")
        self.report("staging", "retained directory " + directory)
        keys = []
        for name in ("szl-cpu-ci.pub", "szl-owner-permit.pub"):
            text = self.plan_state["public_keys"][name]
            keys.append((name, b"" if self.args.dry_run else text.encode("ascii"), text))
        print("Staging writes only under %s on %s (a new directory; an existing directory is "
              "never deleted or reused):" % (directory, self.args.vm))
        for name, _, text in keys:
            print("  %s/%s: %s" % (directory, name, text))
        source = directory + "/src"
        print("  %s: fresh fetch of https://github.com/%s %s" % (source, REPO, self.sha))
        # Public halves only, over plain ssh stdin (no scp/SFTP subsystem needed).
        # set -C refuses to overwrite a file that is already there.
        for name, data, _ in keys:
            self.remote("umask 077; set -C; cat > %s/%s" % (directory, name), stdin=data)
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
            raise Stop("the provisioning plan is not ready; read its failures above; "
                       "retained directory %s was not deleted" % directory)
        print("Keep a second admin SSH session (or the provider's web terminal) open until "
              "provision ends; if a fresh admin login then fails, run there: " + self.recovery())
        if not self.confirm("The plan above is ready. Apply it to the VM now?", "APPLY"):
            raise Stop("provisioning not applied; retained directory %s was not deleted" % directory)
        applied = self.remote(base.format(action="apply") + " --owner-window-ref "
                              + shlex.quote(self.args.window_ref), tty=True, check=False,
                              timeout=300)
        if self.login().returncode != 0:
            raise Stop("a fresh admin login to %s fails after apply; from your second admin session "
                       "or the provider's web terminal run: %s; retained directory %s was not deleted"
                       % (self.args.vm, self.recovery(), directory))
        if applied.returncode != 0:
            raise Stop("provisioning apply failed; read its output above; "
                       "retained directory %s was not deleted" % directory)
        self.report("provision",
                    "applied; broker socket enabled for szl-ci; a fresh admin login works")

    def qualify(self):
        self.qualification = None
        self.require_window()
        if not self.confirm("Run bounded CPU isolation controls and the exact-source rehearsal?",
                            "QUALIFY"):
            raise Stop("qualification not run")
        self.verify_plan()
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
        # Every check before the first GitHub write, so a refusal changes nothing.
        self.require_qualification()
        if self.plan_state is None:
            raise Stop("run plan,qualify,github together: no live plan in this invocation")
        if not self.confirm("Write the planned GitHub variables to the pre-provisioned environment?",
                            "CONFIGURE"):
            raise Stop("GitHub configuration not changed")
        # The environment, its reviewers/policy and the private secret are owner-
        # provisioned. This controller never creates/replaces them or reads the key.
        self.verify_plan()
        pins = self.plan_state["pins"]
        connection = self.connection_variables(pins)
        # Variable writes start only after qualification, consent and exact preimage checks.
        self.require_qualification()
        for name, value in connection.items():
            self.run(["gh", "variable", "set", name, "--env", ENVIRONMENT, "-R", REPO, "--body", value])
        if self.confirm("Enable the CPU lane after a fresh qualification/identity recheck?", "ENABLE"):
            self.require_qualification()
            expected = {**self.plan_state["variables"], **connection}
            self.verify_plan(expected_variables=expected)
            enabled = {**expected, "SZL_CPU_CI_ENABLED": "true"}
            try:
                self.run(["gh", "variable", "set", "SZL_CPU_CI_ENABLED", "--env", ENVIRONMENT,
                          "-R", REPO, "--body", "true"])
                self.verify_plan(expected_variables=enabled, allow_enabled=True)
            except BaseException as activation_error:
                recovery_notes = []
                try:
                    self.run(["gh", "variable", "set", "SZL_CPU_CI_ENABLED", "--env",
                              ENVIRONMENT, "-R", REPO, "--body", "false"])
                except BaseException as error:
                    recovery_notes.append("deactivation command: " + str(error))
                try:
                    disabled = self.environment_variable_snapshot()
                    if disabled["variables"].get("SZL_CPU_CI_ENABLED") not in (None, "false"):
                        raise Stop("enabled value remains true")
                except BaseException as error:
                    details = recovery_notes + ["disabled readback: " + str(error)]
                    raise Stop("activation failed and disabled state was not proven; inspect the "
                               "environment immediately (" + "; ".join(details) + ")") \
                        from activation_error
                note = ("; " + "; ".join(recovery_notes)) if recovery_notes else ""
                raise Stop("activation was not admitted; SZL_CPU_CI_ENABLED=false readback verified"
                           + note) \
                    from activation_error
        self.report("github", "variables configured in unchanged environment %s" % ENVIRONMENT)

    def require_protected_main(self):
        """The explicit main deployment rule does not replace main's source protection."""
        branch = self.gh_json(["api", "repos/%s/branches/main" % REPO])
        if self.args.dry_run:
            self.report("github", "would require protected main before any GitHub write")
            return
        if type(branch) is dict and branch.get("protected") is True:
            return
        rules = self.gh_json(["api", "repos/%s/rules/branches/main" % REPO])
        if type(rules) is not list or not rules \
                or not all(type(rule) is dict and rule.get("type") for rule in rules):
            raise Stop("main has no branch protection rule or active ruleset; "
                       "the deployment source must remain protected")

    def build(self):
        if not self.confirm("Prepare owner attestations and issue one signed CPU build?", "RUN"):
            raise Stop("build not issued")
        config = Path(self.args.owner_config or self.work / "owner-cpu-window.json")
        if not config.exists() and self.args.dry_run:
            print("(dry run: would write an owner-config template at %s)" % config)
        elif not config.exists():
            # Every attestation is a FILL placeholder, which the owner-config loader refuses.
            config.write_text(json.dumps({
                "schema": "szl.ssh-cpu-build.owner-config.v1",
                "provider_instance_uuid": "FILL: Massed Compute instance UUID from the provider console",
                "hf_revision": "FILL: admitted Hugging Face revision",
                "budget": {"ledger_ref": "FILL: your budget ledger reference",
                           "remaining_seconds": "FILL: remaining budget seconds, an integer",
                           "remaining_usd": "FILL: remaining budget USD, a number"},
                "checkpoint_manifest_sha256": "FILL: 64 lowercase hex characters",
                "owner_receipt_ref": "FILL: your approval reference"}, indent=2) + "\n")
            raise Stop("wrote %s; replace every FILL value with your own attestation (numbers "
                       "unquoted), then rerun --stages run" % config)
        self.require_login()
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
                   "--port", str(self.args.port), "--evidence-dir", self.work / "evidence"]
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
    parser.add_argument("--stages", default="plan,keys,provision,qualify,github")
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
    if not 1 <= args.port <= 65535:
        parser.error("--port must be 1-65535")
    lane = Lane(args)
    handlers = {"plan": lane.plan, "keys": lane.keys, "provision": lane.provision,
                "qualify": lane.qualify,
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
