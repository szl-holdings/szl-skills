"""Offline step-runner tests; the real build runs only inside the broker unit."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools import ssh_ci_step_runner as runner


SHA = "a" * 40
GIT = shutil.which("git")


class Output(io.StringIO):
    def value(self):
        return json.loads(self.getvalue())


class RunnerTests(unittest.TestCase):
    def test_only_the_four_fixed_steps_are_admitted(self):
        commands = runner.steps("/usr/bin/python3", SHA, "/out")
        self.assertEqual([name for name, _argv in commands], list(runner.STEP_NAMES))
        self.assertEqual(commands[2][1], ["/usr/bin/python3", "-B", "-m", "unittest",
                                          "discover", "-s", "tests", "-v"])
        self.assertEqual(commands[3][1][-3:], ["--revision", SHA, "--manifest"])

    def test_no_go_or_bad_sha_runs_nothing(self):
        with tempfile.TemporaryDirectory() as scratch:
            for stdin, sha, code in ((io.BytesIO(b""), SHA, 125), (io.BytesIO(b"NO\n"), SHA, 125),
                                     (io.BytesIO(b"GO\n"), "main", 2)):
                output = Output()
                with self.subTest(stdin=stdin.getvalue(), sha=sha):
                    self.assertEqual(runner.main(
                        ["--source-sha", sha, "--home", str(Path(scratch) / "h"),
                         "--work", str(Path(scratch) / "w"), "--source", scratch],
                        stdin=stdin, stdout=output), code)
                    self.assertFalse(output.value()["started"])
                    self.assertTrue(all(step["returncode"] == runner.NOT_RUN
                                        for step in output.value()["steps"]))
                    self.assertFalse((Path(scratch) / "w").exists())

    def test_streams_are_bounded_and_truncation_is_reported(self):
        with tempfile.TemporaryDirectory() as out:
            code, truncated = runner.run_step(
                "selfcheck", [sys.executable, "-c", "import sys; sys.stdout.write('x' * 5000)"],
                out, out, None, limit=1000)
            self.assertEqual(code, 0)
            self.assertEqual(truncated, ["selfcheck.stdout"])
            self.assertEqual((Path(out) / "selfcheck.stdout").stat().st_size, 1000)
            code, truncated = runner.run_step(
                "sync", [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000)"],
                out, out, None, limit=1000)
            self.assertEqual(truncated, [])

    @unittest.skipUnless(GIT and os.name == "posix", "POSIX git is not installed")
    def test_prepare_requires_the_exact_clean_commit(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "src"
            source.mkdir()
            environment = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": scratch, "PATH": "/usr/bin:/bin",
                           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid",
                           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@invalid"}
            for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
                subprocess.run([GIT, *argv], cwd=source, env=environment, check=True,
                               capture_output=True)
            head = subprocess.run([GIT, "rev-parse", "HEAD"], cwd=source, env=environment,
                                  check=True, capture_output=True, text=True).stdout.strip()
            runner.prepare_work(source, Path(scratch) / "ok", head, environment)
            with self.assertRaises(RuntimeError):
                runner.prepare_work(source, Path(scratch) / "wrong", "b" * 40, environment)
            (source / "untracked.txt").write_text("dirty")
            with self.assertRaises(RuntimeError):
                runner.prepare_work(source, Path(scratch) / "dirty", head, environment)


if __name__ == "__main__":
    unittest.main()
