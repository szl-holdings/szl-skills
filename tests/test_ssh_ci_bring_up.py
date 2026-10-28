"""Offline tests for the owner bring-up script; no VM, SSH, or GitHub call is made."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import unittest.mock

from tools import ssh_ci_bring_up as bring_up
from tools import ssh_ci_owner_issue as owner_issue


SHA = "c" * 40
VM = "Ubuntu@203.0.113.10"


def arguments(workdir, *extra):
    return ["--release-sha", SHA, "--vm", VM, "--known-hosts",
            str(Path(workdir) / "known_hosts"), "--workdir", str(Path(workdir) / "lane"),
            "--window-ref", "owner://test/window", *extra]


def lane_for(work, **overrides):
    values = dict(release_sha=SHA, vm=VM, known_hosts="known_hosts", admin_key=None, port=22,
                  workdir=str(work), window_ref="w", hidden_path=[], owner_config=None,
                  stages="", dry_run=False)
    values.update(overrides)
    return bring_up.Lane(argparse.Namespace(**values))


class Commands:
    """Stands in for subprocess.run; ``respond(argv)`` returns (exit code, stdout)."""

    def __init__(self, respond=None):
        self.respond, self.calls = respond or (lambda argv: (0, b"")), []

    def __call__(self, argv, **options):
        self.calls.append((list(argv), options.get("input")))
        code, output = self.respond(argv)
        return subprocess.CompletedProcess(argv, code, output, b"")

    def remote(self):
        return [argv[-1] for argv, _ in self.calls if argv[0] == "ssh"]

    def github_writes(self):
        return [argv for argv, _ in self.calls if argv[0] == "gh"
                and ("PUT" in argv or argv[1] in ("secret", "variable"))]


class DryRunTests(unittest.TestCase):
    def test_every_stage_prints_commands_bound_to_the_release_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = bring_up.main(arguments(scratch, "--hidden-path", "/data/models",
                                               "--stages", ",".join(bring_up.STAGES),
                                               "--dry-run"))
            self.assertEqual(list(Path(scratch).iterdir()), [])
        text = output.getvalue()
        self.assertEqual(code, 0)
        order = [text.index("== " + stage) for stage in bring_up.STAGES]
        self.assertEqual(order, sorted(order))
        self.assertIn("fetch -q --depth=1 --no-tags https://github.com/%s %s"
                      % (bring_up.REPO, SHA), text)
        self.assertIn("ssh_ci_provision.py plan --release-sha " + SHA, text)
        self.assertIn("ssh_ci_provision.py apply --release-sha " + SHA, text)
        self.assertIn("--rehearse-build " + SHA, text)
        self.assertIn("--hidden-path /data/models", text)
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertIn("would write an owner-config template", text)
        self.assertNotIn("scp", text)
        provision = text[text.index("== provision"):text.index("== qualify")]
        steps = ["-o BatchMode=yes -o ControlPath=none %s true" % VM,
                 "writes only under ~/szl-cpu-lane", "would ask for STAGE",
                 "mkdir -p szl-cpu-lane", "ssh_ci_provision.py plan", "second admin SSH session",
                 "would ask for APPLY", "ssh_ci_provision.py apply"]
        self.assertEqual([provision.index(step) for step in steps],
                         sorted(provision.index(step) for step in steps))
        self.assertTrue(provision.rstrip().endswith("a fresh admin login works"))
        self.assertIn(" %s true\n" % VM, provision[provision.index("ssh_ci_provision.py apply"):])
        github = text[text.index("== github"):text.index("== run")]
        steps = ["ssh-keygen -F", "repos/%s/branches/main" % bring_up.REPO, "gh api user",
                 "-X PUT", "gh secret set", "gh variable set"]
        self.assertEqual([github.index(step) for step in steps],
                         sorted(github.index(step) for step in steps))
        run = text[text.index("== run"):]
        self.assertLess(run.index(" %s true" % VM), run.index("git init"))
        self.assertIn(" --port 22 ", run)

    def test_invalid_arguments_are_refused_before_any_command(self):
        with tempfile.TemporaryDirectory() as scratch:
            bad = [
                ["--release-sha", "main"] + arguments(scratch)[2:],
                arguments(scratch)[:2] + ["--vm", "Ubuntu@host;rm -rf /"] + arguments(scratch)[4:],
                arguments(scratch, "--stages", "keys,deploy"),
                arguments(scratch, "--port", "0"),
                arguments(scratch, "--port", "65536"),
            ]
            for argv in bad:
                with self.subTest(argv=argv[-4:]), self.assertRaises(SystemExit), \
                        contextlib.redirect_stderr(io.StringIO()):
                    bring_up.main(argv + ["--dry-run"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = bring_up.main(arguments(scratch, "--hidden-path", "/data/my models",
                                               "--stages", "provision", "--dry-run"))
            self.assertEqual(code, 1)
            self.assertIn("plain absolute path", output.getvalue())

    def test_provision_and_qualify_need_a_window_reference(self):
        with tempfile.TemporaryDirectory() as scratch:
            argv = [part for part in arguments(scratch)
                    if part not in ("--window-ref", "owner://test/window")]
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(bring_up.main(argv + ["--stages", "qualify", "--dry-run"]), 1)
            self.assertIn("--window-ref", output.getvalue())


class ProvisionStageTests(unittest.TestCase):
    PLAN = "ssh_ci_provision.py plan"
    APPLY = "ssh_ci_provision.py apply"
    CLEANUP = "rm -rf -- szl-cpu-lane"

    def lane(self, scratch):
        work = Path(scratch) / "lane"
        work.mkdir()
        for name in ("szl-cpu-ci", "szl-owner-permit"):
            (work / (name + ".pub")).write_bytes(
                b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA " + name.encode() + b"\n")
        return lane_for(work, hidden_path=["/data/models"])

    def provision(self, lane, answers, respond=None):
        commands, output, prompts = Commands(respond), io.StringIO(), []

        def answer(prompt):
            prompts.append((prompt, output.getvalue(), len(commands.calls)))
            return answers.pop(0)
        error = None
        with unittest.mock.patch.object(bring_up.subprocess, "run", commands), \
                unittest.mock.patch("builtins.input", side_effect=answer), \
                contextlib.redirect_stdout(output):
            try:
                lane.provision()
            except (bring_up.Stop, OSError, ValueError) as stop:
                error = str(stop)
        return commands, output.getvalue(), prompts, error

    def scripted(self, logins, apply=0, cleanup=0, fail=None):
        """Exit codes: each fresh login in turn, the apply, the cleanup, one failing step."""
        logins = list(logins)

        def respond(argv):
            command = argv[-1]
            if command == "true":
                return logins.pop(0), b""
            if command == self.CLEANUP:
                return cleanup, b""
            if self.APPLY in command:
                return apply, b""
            return (1 if fail and fail in command else 0), b""
        return respond

    def test_nothing_is_written_to_the_vm_before_the_owner_types_stage(self):
        with tempfile.TemporaryDirectory() as scratch:
            lane = self.lane(scratch)
            commands, _, prompts, error = self.provision(lane, ["no"], self.scripted([0]))
            self.assertIn("nothing was written to the VM", error)
            self.assertEqual(commands.remote(), ["true"])
            self.assertEqual(len(prompts), 1)
            prompt, shown, calls = prompts[0]
            self.assertIn("Type STAGE", prompt)
            self.assertEqual(calls, 1)
            self.assertIn("writes only under ~/szl-cpu-lane on " + VM, shown)
            for line in ("~/szl-cpu-lane/szl-cpu-ci.pub: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA "
                         "szl-cpu-ci",
                         "~/szl-cpu-lane/szl-owner-permit.pub: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA "
                         "szl-owner-permit",
                         "~/szl-cpu-lane/src: replaced by a fresh fetch of https://github.com/%s %s"
                         % (bring_up.REPO, SHA)):
                self.assertIn(line, shown)
            (lane.work / "szl-owner-permit.pub").write_bytes(b"ssh-ed25519 AAAA one\nssh-rsa two\n")
            commands, _, prompts, error = self.provision(lane, [])
            self.assertIn("not one public key line", error)
            (lane.work / "szl-owner-permit.pub").unlink()
            missing, _, _, error = self.provision(lane, [])
            self.assertIsNotNone(error)
            self.assertEqual((commands.calls, missing.calls, prompts), ([], [], []))

    def test_declined_apply_or_failed_staging_or_plan_removes_the_staging_directory(self):
        removed = "; removed ~/szl-cpu-lane from the VM"
        cases = [("declined", ["STAGE", "no"], {}, "provisioning not applied" + removed),
                 ("plan", ["STAGE"], dict(fail=self.PLAN), "read its failures above" + removed),
                 ("staging", ["STAGE"], dict(fail="git init"), "exit 1: ssh" + removed),
                 ("cleanup", ["STAGE"], dict(fail=self.PLAN, cleanup=1),
                  "could not remove ~/szl-cpu-lane from the VM; remove it yourself")]
        for label, answers, script, message in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as scratch:
                commands, _, _, error = self.provision(self.lane(scratch), answers,
                                                       self.scripted([0], **script))
                self.assertIn(message, error)
                remote = commands.remote()
                self.assertEqual(remote[-1], self.CLEANUP)
                self.assertEqual(remote.count(self.CLEANUP), 1)
                self.assertFalse(any(self.APPLY in command for command in remote))
        self.assertNotIn("$", self.CLEANUP)

    def test_a_failed_batch_login_stops_before_any_change(self):
        with tempfile.TemporaryDirectory() as scratch:
            commands, _, prompts, error = self.provision(self.lane(scratch), [],
                                                         self.scripted([255]))
        self.assertIn("load the admin key with ssh-add (Windows: start the ssh-agent service)", error)
        self.assertEqual(prompts, [])
        self.assertEqual(len(commands.calls), 1)
        probe = commands.calls[0][0]
        for option in ("-T", "BatchMode=yes", "ControlPath=none", "StrictHostKeyChecking=yes"):
            self.assertIn(option, probe)
        self.assertEqual(probe[-2:], [VM, "true"])

    def test_apply_is_followed_by_a_fresh_admin_login_and_recovery_on_failure(self):
        with tempfile.TemporaryDirectory() as scratch:
            lane = self.lane(scratch)
            commands, output, prompts, error = self.provision(lane, ["STAGE", "APPLY"],
                                                              self.scripted([0, 0]))
            self.assertIsNone(error)
            remote = commands.remote()
            self.assertEqual(remote[:4], ["true", "mkdir -p szl-cpu-lane",
                                          "cat > szl-cpu-lane/szl-cpu-ci.pub",
                                          "cat > szl-cpu-lane/szl-owner-permit.pub"])
            self.assertIn(self.PLAN, remote[5])
            self.assertIn(self.APPLY, remote[6])
            self.assertEqual(remote[7:], ["true"])
            self.assertNotIn(self.CLEANUP, remote)
            uploads = [stdin for argv, stdin in commands.calls if argv[-1].startswith("cat > ")]
            self.assertEqual(uploads, [(lane.work / name).read_bytes()
                                       for name in ("szl-cpu-ci.pub", "szl-owner-permit.pub")])
            prompt, shown, _ = prompts[1]
            self.assertIn("Type APPLY", prompt)
            self.assertIn("second admin SSH session (or the provider's web terminal)", shown)
            self.assertIn(bring_up.UNINSTALL, shown)
            self.assertIn("a fresh admin login works", output)
            commands, _, _, error = self.provision(lane, ["STAGE", "APPLY"],
                                                   self.scripted([0, 255]))
            self.assertIn("fresh admin login to %s fails after apply" % VM, error)
            self.assertIn(bring_up.UNINSTALL, error)
            self.assertEqual(commands.remote()[-1], "true")
            self.assertNotIn(self.CLEANUP, commands.remote())
            commands, _, _, error = self.provision(lane, ["STAGE", "APPLY"],
                                                   self.scripted([0, 0], apply=1))
            self.assertIn("provisioning apply failed", error)
            self.assertIn(self.APPLY, commands.remote()[-2])
            self.assertEqual(commands.remote()[-1], "true")


class GithubStageTests(unittest.TestCase):
    def github(self, lane, commands, branch=b'{"protected": true}', rules=b"[]", pin=True):
        def respond(argv):
            if argv[:3] == ["gh", "api", "user"]:
                return 0, b'{"id": 4242, "login": "owner"}'
            if argv[:3] == ["gh", "api", "repos/%s/branches/main" % bring_up.REPO]:
                return 0, branch
            if argv[:3] == ["gh", "api", "repos/%s/rules/branches/main" % bring_up.REPO]:
                return 0, rules
            if argv[0] == "ssh-keygen":
                return 0, (b"# Host 203.0.113.10 found: line 3\n"
                           b"203.0.113.10 ssh-ed25519 AAAAPIN\n") if pin else b""
            return 0, b""
        commands.respond = respond
        with unittest.mock.patch.object(bring_up.subprocess, "run", commands), \
                unittest.mock.patch("builtins.input", side_effect=["ENABLE", "no"]), \
                contextlib.redirect_stdout(io.StringIO()):
            lane.github()

    def lane(self, scratch):
        work = Path(scratch) / "lane"
        work.mkdir()
        (work / "szl-cpu-ci").write_bytes(b"PRIVATE-KEY-BYTES")
        return lane_for(work)

    def test_environment_secret_and_variables_with_enable_last_after_pin_and_protection(self):
        with tempfile.TemporaryDirectory() as scratch:
            lane = self.lane(scratch)
            commands = Commands()
            self.github(lane, commands)
            calls = commands.calls
            environment = next(json.loads(stdin) for argv, stdin in calls if "PUT" in argv)
            self.assertEqual(environment["reviewers"], [{"type": "User", "id": 4242}])
            self.assertTrue(environment["deployment_branch_policy"]["protected_branches"])
            secret = next(stdin for argv, stdin in calls if argv[:3] == ["gh", "secret", "set"])
            self.assertEqual(secret, b"PRIVATE-KEY-BYTES")
            variables = [argv[3] for argv, _ in calls if argv[:3] == ["gh", "variable", "set"]]
            self.assertEqual(variables[-1], "SZL_CPU_CI_ENABLED")
            pin = next(argv for argv, _ in calls if "SZL_CPU_KNOWN_HOSTS" in argv)
            self.assertEqual(pin[-1], "203.0.113.10 ssh-ed25519 AAAAPIN")
            self.assertTrue((lane.work / "szl-cpu-ci").exists())
            first_write = [argv for argv, _ in calls].index(commands.github_writes()[0])
            self.assertIn("PUT", calls[first_write][0])
            self.assertEqual(calls[0][0][0], "ssh-keygen")
            self.assertIn(["gh", "api", "repos/%s/branches/main" % bring_up.REPO],
                          [argv for argv, _ in calls[:first_write]])
            ruleset_only = Commands()
            self.github(lane, ruleset_only, branch=b'{"protected": false}',
                        rules=b'[{"type": "pull_request", "ruleset_id": 7}]')
            self.assertTrue(ruleset_only.github_writes())

    def test_no_github_write_without_a_verified_pin_or_a_protected_main(self):
        cases = [("no pin", dict(pin=False), "no verified host-key pin"),
                 ("unprotected", dict(branch=b'{"protected": false}'), "no branch protection"),
                 ("untyped rules", dict(branch=b'{"protected": false}', rules=b"[{}]"),
                  "no branch protection"),
                 ("rules not a list", dict(branch=b"{}", rules=b'{"type": "x"}'),
                  "no branch protection"),
                 ("non-JSON", dict(branch=b"<html>"), "non-JSON")]
        with tempfile.TemporaryDirectory() as scratch:
            lane = self.lane(scratch)
            for label, options, message in cases:
                with self.subTest(label):
                    commands = Commands()
                    with self.assertRaisesRegex(bring_up.Stop, message):
                        self.github(lane, commands, **options)
                    self.assertTrue(commands.calls)
                    self.assertEqual(commands.github_writes(), [])
                    self.assertFalse(any(argv[:3] == ["gh", "api", "user"]
                                         for argv, _ in commands.calls))
                    if label == "no pin":
                        self.assertEqual([argv[0] for argv, _ in commands.calls], ["ssh-keygen"])


class RunStageTests(unittest.TestCase):
    def test_owner_config_template_has_only_fill_placeholders_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch) / "lane"
            work.mkdir()
            commands = Commands()
            with unittest.mock.patch.object(bring_up.subprocess, "run", commands), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(bring_up.Stop, "replace every FILL value"):
                lane_for(work).build()
            text = (work / "owner-cpu-window.json").read_text()
        self.assertEqual(commands.calls, [])
        value = json.loads(text)
        self.assertEqual(set(value), owner_issue.OWNER_FIELDS)
        self.assertEqual(value.pop("schema"), owner_issue.OWNER_CONFIG_SCHEMA)
        budget = value.pop("budget")
        self.assertEqual(set(budget), {"ledger_ref", "remaining_seconds", "remaining_usd"})
        attested = list(value.values()) + list(budget.values())
        self.assertEqual(len(attested), 7)
        for item in attested:
            self.assertIs(type(item), str)
            self.assertTrue(item.startswith("FILL"), item)
        self.assertIsNone(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text))

    def test_run_stage_proves_a_batch_login_then_passes_the_port_to_the_owner_flow(self):
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch) / "lane"
            work.mkdir()
            (work / "owner-cpu-window.json").write_text("{}")
            lane = lane_for(work, port=2222)
            commands = Commands()
            with unittest.mock.patch.object(bring_up.subprocess, "run", commands), \
                    contextlib.redirect_stdout(io.StringIO()):
                lane.build()
            probe, issue = commands.calls[0][0], commands.calls[-1][0]
            self.assertIn("Port=2222", probe)
            self.assertIn("BatchMode=yes", probe)
            self.assertEqual(probe[-2:], [VM, "true"])
            self.assertTrue(issue[2].endswith("ssh_ci_owner_issue.py"))
            self.assertEqual(issue[issue.index("--port") + 1], "2222")
            self.assertEqual(issue[issue.index("--admin-target") + 1], VM)
            failed = Commands(lambda argv: (255 if argv[-1] == "true" else 0, b""))
            with unittest.mock.patch.object(bring_up.subprocess, "run", failed), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(bring_up.Stop, "ssh-add"):
                lane.build()
            self.assertEqual(len(failed.calls), 1)


if __name__ == "__main__":
    unittest.main()
