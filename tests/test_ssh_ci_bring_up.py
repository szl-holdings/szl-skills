"""Offline tests for the owner bring-up script; no VM, SSH, or GitHub call is made."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import ssh_ci_bring_up as bring_up


SHA = "c" * 40


def arguments(workdir, *extra):
    return ["--release-sha", SHA, "--vm", "Ubuntu@203.0.113.10", "--known-hosts",
            str(Path(workdir) / "known_hosts"), "--workdir", str(Path(workdir) / "lane"),
            "--window-ref", "owner://test/window", *extra]


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

    def test_invalid_arguments_are_refused_before_any_command(self):
        with tempfile.TemporaryDirectory() as scratch:
            bad = [
                ["--release-sha", "main"] + arguments(scratch)[2:],
                arguments(scratch)[:2] + ["--vm", "Ubuntu@host;rm -rf /"] + arguments(scratch)[4:],
                arguments(scratch, "--stages", "keys,deploy"),
            ]
            for argv in bad:
                with self.subTest(argv=argv[:4]), self.assertRaises(SystemExit), \
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


class GithubStageTests(unittest.TestCase):
    def test_environment_secret_and_variables_with_enable_last_and_pin_required(self):
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch) / "lane"
            work.mkdir()
            (work / "szl-cpu-ci").write_bytes(b"PRIVATE-KEY-BYTES")
            args = argparse.Namespace(
                release_sha=SHA, vm="Ubuntu@203.0.113.10", known_hosts="known_hosts",
                admin_key=None, port=22, workdir=str(work), window_ref="w", hidden_path=[],
                owner_config=None, stages="github", dry_run=False)
            lane = bring_up.Lane(args)
            calls = []

            def run(argv, *, stdin=None, capture=False, check=True):
                calls.append((list(map(str, argv)), stdin))
                output = b""
                if argv[:2] == ["gh", "api"] and argv[2] == "user":
                    output = b'{"id": 4242, "login": "owner"}'
                if argv[0] == "ssh-keygen":
                    output = (b"# Host 203.0.113.10 found: line 3\n"
                              b"203.0.113.10 ssh-ed25519 AAAAPIN\n") if self.pin else b""
                return subprocess.CompletedProcess(argv, 0, output, b"")
            lane.run = run
            self.pin = True
            with mock.patch("builtins.input", side_effect=["ENABLE", "no"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                lane.github()
            environment = next(json.loads(stdin) for argv, stdin in calls if "PUT" in argv)
            self.assertEqual(environment["reviewers"], [{"type": "User", "id": 4242}])
            self.assertTrue(environment["deployment_branch_policy"]["protected_branches"])
            secret = next(stdin for argv, stdin in calls if argv[:3] == ["gh", "secret", "set"])
            self.assertEqual(secret, b"PRIVATE-KEY-BYTES")
            variables = [argv[3] for argv, _ in calls if argv[:3] == ["gh", "variable", "set"]]
            self.assertEqual(variables[-1], "SZL_CPU_CI_ENABLED")
            pin = next(argv for argv, _ in calls if "SZL_CPU_KNOWN_HOSTS" in argv)
            self.assertEqual(pin[-1], "203.0.113.10 ssh-ed25519 AAAAPIN")
            self.assertTrue((work / "szl-cpu-ci").exists())
            calls.clear()
            self.pin = False
            with self.assertRaisesRegex(bring_up.Stop, "no verified host-key pin"), \
                    contextlib.redirect_stdout(io.StringIO()):
                lane.github()
            self.assertFalse(any(argv[:3] == ["gh", "secret", "set"] for argv, _ in calls))


if __name__ == "__main__":
    unittest.main()
