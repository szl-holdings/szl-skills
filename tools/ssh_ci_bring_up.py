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
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

REPO = "szl-holdings/szl-skills"
ENVIRONMENT = "massed-cpu-build"
REMOTE_DIR = "szl-cpu-lane"
STAGES = ("keys", "provision", "qualify", "github", "run")
PLAIN_PATH = r"/[A-Za-z0-9._/@+-]{1,254}"


class Stop(RuntimeError):
    """A stage refused or failed; later stages do not run."""


class Lane:
    def __init__(self, args):
        self.args = args
        self.sha = args.release_sha
        self.work = Path(args.workdir).absolute()
        self.ci_key = self.work / "szl-cpu-ci"
        self.owner_key = self.work / "szl-owner-permit"
        self.host = args.vm.split("@", 1)[-1]

    # -- plumbing -------------------------------------------------------------
    def run(self, argv, *, stdin=None, capture=False, check=True):
        print("+ " + " ".join(shlex.quote(str(part)) for part in argv), flush=True)
        if self.args.dry_run:
            return subprocess.CompletedProcess(argv, 0, b"" if capture else None, b"")
        completed = subprocess.run([str(part) for part in argv], input=stdin,
                                   stdout=subprocess.PIPE if capture else None, check=False)
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

    def remote(self, command, *, tty=False, check=True, stdin=None):
        flag = ["-t"] if tty else ["-T", "-o", "BatchMode=yes"]
        return self.run(["ssh", *self.ssh_options(), *flag, self.args.vm, command], check=check,
                        stdin=stdin)

    def confirm(self, question, word):
        if self.args.dry_run:
            print("(dry run: would ask) " + question)
            return True
        return input(question + " Type %s to continue: " % word).strip() == word

    # -- stages ---------------------------------------------------------------
    def keys(self):
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
        print("keys: CI key %s, permit-signing key %s" % (self.ci_key, self.owner_key))

    def provision(self):
        self.require_window()
        hidden = []
        for path in self.args.hidden_path:
            if re.fullmatch(PLAIN_PATH, path) is None:
                raise Stop("hidden path must be a plain absolute path: " + path)
            hidden += ["--hidden-path", path]
        self.remote("mkdir -p " + REMOTE_DIR)
        # Public halves only, over plain ssh stdin (no scp/SFTP subsystem needed).
        for key, name in ((self.ci_key, "szl-cpu-ci.pub"), (self.owner_key, "szl-owner-permit.pub")):
            data = b"" if self.args.dry_run else Path(str(key) + ".pub").read_bytes()
            self.remote("cat > %s/%s" % (REMOTE_DIR, name), stdin=data)
        source = REMOTE_DIR + "/src"
        self.remote(" && ".join([
            "rm -rf " + source, "git init -q " + source,
            "git -C %s fetch -q --depth=1 --no-tags https://github.com/%s %s" % (source, REPO, self.sha),
            "git -C %s -c advice.detachedHead=false checkout -q --detach %s" % (source, self.sha),
            'test "$(git -C %s rev-parse HEAD)" = %s' % (source, self.sha), "echo SOURCE_OK " + self.sha]))
        base = ("sudo python3 -I -B %s/tools/ssh_ci_provision.py {action} --release-sha %s "
                "--ci-public-key %s/szl-cpu-ci.pub --owner-public-key %s/szl-owner-permit.pub "
                '--admin-user "$USER" %s' % (source, self.sha, REMOTE_DIR, REMOTE_DIR,
                                            " ".join(shlex.quote(part) for part in hidden)))
        plan = self.remote(base.format(action="plan"), tty=True, check=False)
        if plan.returncode != 0:
            raise Stop("the provisioning plan is not ready; read its failures above")
        if not self.confirm("The plan above is ready. Apply it to the VM now?", "APPLY"):
            raise Stop("provisioning not applied")
        self.remote(base.format(action="apply") + " --owner-window-ref "
                    + shlex.quote(self.args.window_ref), tty=True)
        print("provision: applied; broker socket enabled for szl-ci")

    def qualify(self):
        self.require_window()
        result = self.remote("sudo python3 -I -B /opt/szl-cpu-broker/current/tools/ssh_ci_qualify.py"
                             " --rehearse-build %s --owner-window-ref %s"
                             % (self.sha, shlex.quote(self.args.window_ref)), tty=True,
                             check=False)
        if result.returncode != 0:
            raise Stop("qualification is not QUALIFIED; do not enable the lane")
        print("qualify: QUALIFIED")

    def github(self):
        user = json.loads(self.run(["gh", "api", "user"], capture=True).stdout or b'{"id": 0}')
        environment = {"reviewers": [{"type": "User", "id": user["id"]}],
                       "prevent_self_review": False,
                       "deployment_branch_policy": {"protected_branches": True,
                                                    "custom_branch_policies": False}}
        self.run(["gh", "api", "-X", "PUT", "repos/%s/environments/%s" % (REPO, ENVIRONMENT),
                  "--input", "-"], stdin=json.dumps(environment).encode(), capture=True)
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
        self.run(["gh", "secret", "set", "SZL_CPU_SSH_KEY", "--env", ENVIRONMENT, "-R", REPO],
                 stdin=secret)
        for name, value in (("SZL_CPU_SSH_HOST", self.host), ("SZL_CPU_SSH_PORT", str(self.args.port)),
                            ("SZL_CPU_SSH_USER", "szl-ci"), ("SZL_CPU_KNOWN_HOSTS", "\n".join(pins))):
            self.run(["gh", "variable", "set", name, "--env", ENVIRONMENT, "-R", REPO, "--body", value])
        if self.confirm("Qualification printed QUALIFIED. Enable the CPU lane?", "ENABLE"):
            self.run(["gh", "variable", "set", "SZL_CPU_CI_ENABLED", "--env", ENVIRONMENT, "-R",
                      REPO, "--body", "true"])
        if self.confirm("The CI private key is now only needed in GitHub. Delete the local copy?",
                        "DELETE") and not self.args.dry_run:
            self.ci_key.unlink()
        print("github: environment %s configured" % ENVIRONMENT)

    def build(self):
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
        self.run(command)
        print("run: owner-verified PASS; evidence under %s" % (self.work / "evidence"))

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
            except (Stop, OSError, ValueError) as error:
                print("STOPPED at %s: %s" % (stage, error), file=sys.stderr)
                return 1
    print("done: " + ", ".join(stage for stage in STAGES if stage in stages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
