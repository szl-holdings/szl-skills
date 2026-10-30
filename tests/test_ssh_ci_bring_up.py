"""Offline tests for the owner bring-up script; no VM, SSH, or GitHub call is made."""

import argparse
import contextlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import ssh_ci_bring_up as bring_up


SHA = "c" * 40
WINDOW = "owner://test/window"
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
STAGING = "/tmp/szl-cpu-bringup-ABCDEF1234"


def lane_args(workdir, *, dry_run=False):
    return argparse.Namespace(
        release_sha=SHA, vm="Ubuntu@203.0.113.10", known_hosts="known_hosts",
        admin_key=None, port=22, workdir=str(workdir), window_ref=WINDOW, hidden_path=[],
        owner_config=None, stages="qualify,github", dry_run=dry_run)


def qualification_fixture():
    host = {"hostname": "fixture-host", "boot_id": "90941c8a-32c2-45c3-a526-381429617329"}
    processes = [{"pid": 4242, "start_ticks": 12345}]
    return {
        "schema": "szl.ssh-cpu-build.qualification.v1", "evidence_class": "MEASURED",
        "result": "QUALIFIED", "failures": [], "completed_at": NOW.isoformat(),
        "owner_window_ref": WINDOW, "host": host, "model_before": processes,
        "model_after": deepcopy(processes), "release_sha": SHA, "config_sha256": "a" * 64,
        "controls": [{"control": name, "probe": {}, "elapsed": 1.0,
                      "unit_result": "success", "readback": {"verified": True, "mismatched": []},
                      "failures": []}
                     for name in ("isolation", "tasks", "memory", "tmpfs", "deadline")],
        "rehearsal": {
            "source_sha": SHA, "failures": [], "collection_problems": [], "file_bytes": {},
            "runner": {"schema": "szl.ssh-cpu-build.runner.v1", "started": True,
                       "prepared": True, "truncated": [], "failure": None,
                       "steps": [{"name": name, "returncode": 0}
                                 for name in ("selfcheck", "sync", "unittest", "package")]},
        },
        "signed": False, "independent_witness": False,
        "scope": "this host, this release, the fixed CPU profile; not model or science quality",
    }


def state_fixture():
    q = qualification_fixture()
    return {"schema": "szl.ssh-cpu-build.activation-state.v1", "release_sha": SHA,
            "config_sha256": q["config_sha256"], "observation": {
                "schema": "szl.ssh-cpu-build.observation.v1",
                "observed_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "host": q["host"], "gpu_compute_processes": q["model_after"],
                "competing_cpu_jobs": 0}}


def arguments(workdir, *extra):
    return ["--release-sha", SHA, "--vm", "Ubuntu@203.0.113.10", "--known-hosts",
            str(Path(workdir) / "known_hosts"), "--workdir", str(Path(workdir) / "lane"),
            "--window-ref", "owner://test/window", *extra]


class DryRunTests(unittest.TestCase):
    def test_every_stage_prints_commands_bound_to_the_release_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()), \
                    mock.patch.object(subprocess, "run", side_effect=AssertionError("external call")):
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
        self.assertIn("SIMULATED", text)
        self.assertNotIn("rm -rf", text)
        for claim in ("provision: applied", "qualify: QUALIFIED", "run: owner-verified PASS"):
            self.assertNotIn(claim, text)

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
            for stage in ("provision", "qualify"):
                output = io.StringIO()
                with self.subTest(stage=stage), contextlib.redirect_stdout(output), \
                        contextlib.redirect_stderr(output):
                    self.assertEqual(bring_up.main(argv + ["--stages", stage, "--dry-run"]), 1)
                self.assertIn("--window-ref", output.getvalue())


class GithubStageTests(unittest.TestCase):
    def test_environment_secret_and_variables_with_enable_last_and_pin_required(self):
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch) / "lane"
            work.mkdir()
            (work / "szl-cpu-ci").write_bytes(b"PRIVATE-KEY-BYTES")
            lane = bring_up.Lane(lane_args(work))
            lane.clock = lambda: NOW
            lane.qualification = qualification_fixture()
            lane.activation_state = mock.Mock(return_value=state_fixture())
            calls = []

            def run(argv, *, stdin=None, capture=False, check=True):
                calls.append((list(map(str, argv)), stdin))
                output = b""
                if argv[:2] == ["gh", "api"] and argv[2] == "user":
                    output = b'{"id": 4242, "login": "owner"}'
                if argv[:3] == ["gh", "api", "repos/szl-holdings/szl-skills/branches/main"]:
                    output = b'{"protected": true}' if self.protected else b'{"protected": false}'
                if argv[:3] == ["gh", "api", "repos/szl-holdings/szl-skills/rules/branches/main"]:
                    output = b'[]'
                if argv[0] == "ssh-keygen":
                    output = (b"# Host 203.0.113.10 found: line 3\n"
                              b"203.0.113.10 ssh-ed25519 AAAAPIN\n") if self.pin else b""
                return subprocess.CompletedProcess(argv, 0, output, b"")
            lane.run = run
            self.pin = True
            self.protected = True
            with mock.patch("builtins.input", side_effect=["CONFIGURE", "ENABLE", "no"]), \
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
            self.assertEqual(lane.activation_state.call_count, 3)
            calls.clear()
            self.pin = False
            with self.assertRaisesRegex(bring_up.Stop, "no verified host-key pin"), \
                    mock.patch("builtins.input", return_value="CONFIGURE"), \
                    contextlib.redirect_stdout(io.StringIO()):
                lane.github()
            self.assertFalse(any(argv[:3] == ["gh", "secret", "set"] for argv, _ in calls))

    def test_unprotected_main_stops_before_a_secret_write(self):
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch) / "lane"
            work.mkdir()
            (work / "szl-cpu-ci").write_bytes(b"PRIVATE-KEY-BYTES")
            lane = bring_up.Lane(lane_args(work))
            lane.clock = lambda: NOW
            lane.qualification = qualification_fixture()
            lane.activation_state = mock.Mock(return_value=state_fixture())
            calls = []

            def run(argv, *, stdin=None, capture=False, check=True):
                calls.append(list(map(str, argv)))
                output = b""
                if argv[:2] == ["gh", "api"] and argv[2] == "user":
                    output = b'{"id": 4242}'
                if argv[:3] == ["gh", "api", "repos/szl-holdings/szl-skills/branches/main"]:
                    output = b'{"protected": false}'
                if argv[:3] == ["gh", "api", "repos/szl-holdings/szl-skills/rules/branches/main"]:
                    output = b'[]'
                if argv[0] == "ssh-keygen":
                    output = b"203.0.113.10 ssh-ed25519 AAAAPIN\n"
                return subprocess.CompletedProcess(argv, 0, output, b"")

            lane.run = run
            with self.assertRaisesRegex(bring_up.Stop, "no branch protection"), \
                    mock.patch("builtins.input", return_value="CONFIGURE"), \
                    contextlib.redirect_stdout(io.StringIO()):
                lane.github()
            self.assertFalse(any(argv[:3] == ["gh", "secret", "set"] for argv in calls))
            self.assertFalse(any("PUT" in argv for argv in calls))


class ProvisionStageTests(unittest.TestCase):
    def test_declined_stage_has_no_remote_call_or_key_read_and_clears_qualification(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.qualification = qualification_fixture()
        with mock.patch.object(lane, "confirm", return_value=False) as confirm, \
                mock.patch.object(lane, "remote", side_effect=AssertionError("remote call")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key read")), \
                self.assertRaises(bring_up.Stop):
            lane.provision()
        self.assertEqual(confirm.call_args.args[1], "STAGE")
        self.assertIsNone(lane.qualification)

    def test_declined_apply_retains_unique_staging_without_deleting_anything(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        calls = []

        def remote(command, **kwargs):
            calls.append(command)
            stdout = (STAGING + "\n").encode() if "mktemp -d" in command else b""
            return subprocess.CompletedProcess(["fixture"], 0, stdout, b"")

        with mock.patch.object(lane, "confirm", side_effect=[True, False]), \
                mock.patch.object(lane, "login", return_value=subprocess.CompletedProcess(["ssh"], 0)), \
                mock.patch.object(lane, "remote", side_effect=remote), \
                mock.patch.object(Path, "read_bytes", return_value=b"SYNTHETIC PUBLIC KEY"), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(bring_up.Stop):
            lane.provision()
        self.assertIn("mktemp -d", calls[0])
        self.assertTrue(any(STAGING + "/src" in cmd for cmd in calls))
        self.assertTrue(any("ssh_ci_provision.py plan" in cmd for cmd in calls))
        self.assertFalse(any("ssh_ci_provision.py apply" in cmd for cmd in calls))
        self.assertFalse(any("rm " in cmd or "rmdir" in cmd for cmd in calls))

    def test_invalid_staging_path_refused_before_public_key_transfer(self):
        for path in ("../existing-work", "/tmp/unrelated", "/tmp/szl-cpu-bringup-ok/../x",
                     STAGING + "\n/another/path"):
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            with self.subTest(path=path), mock.patch.object(lane, "confirm", return_value=True), \
                    mock.patch.object(lane, "login", return_value=subprocess.CompletedProcess(["ssh"], 0)), \
                    mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                        ["fixture"], 0, (path + "\n").encode(), b"")) as remote, \
                    mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key read")), \
                    self.assertRaises(bring_up.Stop):
                lane.provision()
            self.assertEqual(remote.call_count, 1)


class QualificationValidationTests(unittest.TestCase):
    def validate(self, receipt, state):
        return bring_up.validate_qualification(receipt, state, SHA, WINDOW, NOW)

    def test_valid_evidence_is_detached_and_matches_full_runtime_set(self):
        receipt, state = qualification_fixture(), state_fixture()
        validated = self.validate(receipt, state)
        self.assertEqual(validated, receipt)
        validated["model_after"][0]["pid"] = 99
        self.assertEqual(receipt["model_after"][0]["pid"], 4242)

    def test_missing_or_bad_receipt_evidence_is_rejected(self):
        changes = [
            lambda q: q.update(schema="unknown"), lambda q: q.update(result="BLOCKED"),
            lambda q: q.update(failures=["failed"]), lambda q: q.update(release_sha="d" * 40),
            lambda q: q.update(config_sha256="b" * 64), lambda q: q.update(owner_window_ref="other"),
            lambda q: q.update(evidence_class="SIMULATED"), lambda q: q.update(signed=True),
            lambda q: q.update(independent_witness=True), lambda q: q.pop("release_sha"),
            lambda q: q["host"].update(hostname="other-host"),
            lambda q: q["host"].update(boot_id="f" * 36),
            lambda q: q["model_after"][0].update(start_ticks=12346),
            lambda q: q.update(controls=q["controls"][:-1]),
            lambda q: q["controls"][0].update(control="tasks"),
            lambda q: q["controls"][0].update(failures=["failed"]),
            lambda q: q["controls"][0]["readback"].update(verified=False),
            lambda q: q.update(rehearsal=None),
            lambda q: q["rehearsal"].update(source_sha="d" * 40),
            lambda q: q["rehearsal"].update(failures=["failed"]),
            lambda q: q["rehearsal"].update(collection_problems=["failed"]),
            lambda q: q["rehearsal"]["runner"].update(started=False),
            lambda q: q["rehearsal"]["runner"].update(prepared=False),
            lambda q: q["rehearsal"]["runner"].update(truncated=["selfcheck.stdout"]),
            lambda q: q["rehearsal"]["runner"]["steps"][0].update(returncode=1),
            lambda q: q["rehearsal"]["runner"]["steps"][0].update(returncode=False),
            lambda q: q["rehearsal"]["runner"]["steps"].pop(),
            lambda q: q["rehearsal"]["runner"]["steps"][0].update(name="arbitrary"),
        ]
        for index, change in enumerate(changes):
            receipt = qualification_fixture()
            change(receipt)
            with self.subTest(case=index), self.assertRaises(bring_up.Stop):
                self.validate(receipt, state_fixture())
        for value in (None, [], {}, b"not-json", b'{"schema":"a","schema":"b"}', b" " * 262145):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(bring_up.Stop):
                self.validate(value, state_fixture())

    def test_stale_future_or_naive_completion_is_rejected(self):
        values = ((NOW - timedelta(seconds=901)).isoformat(),
                  (NOW + timedelta(seconds=1)).isoformat(), "2030-01-01T00:00:00", "bad")
        for completed in values:
            receipt = qualification_fixture()
            receipt["completed_at"] = completed
            with self.subTest(completed=completed), self.assertRaises(bring_up.Stop):
                self.validate(receipt, state_fixture())

    def test_exact_age_boundaries_and_valid_json_bytes_are_accepted(self):
        receipt, state = qualification_fixture(), state_fixture()
        receipt["completed_at"] = (NOW - timedelta(seconds=900)).isoformat()
        state["observation"]["observed_at"] = "2029-12-31T23:59:55Z"
        self.assertEqual(self.validate(json.dumps(receipt).encode(), json.dumps(state).encode()), receipt)

    def test_valid_structure_cannot_hide_duplicate_nonfinite_or_oversized_data(self):
        for fixture in (qualification_fixture, state_fixture):
            value = fixture()
            normal = json.dumps(value)
            duplicate = '{"schema":' + json.dumps(value["schema"]) + "," + normal[1:]
            nonfinite = '{"unexpected":NaN,' + normal[1:]
            value["padding"] = "x" * 262144
            oversized = json.dumps(value)
            for data in (duplicate, nonfinite, oversized):
                args = ((data.encode(), state_fixture()) if fixture is qualification_fixture else
                        (qualification_fixture(), data.encode()))
                with self.subTest(fixture=fixture.__name__, size=len(data)), self.assertRaises(bring_up.Stop):
                    self.validate(*args)

    def test_clock_must_be_aware_utc(self):
        for clock in (NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=1)))):
            with self.subTest(clock=clock), self.assertRaises(bring_up.Stop):
                bring_up.validate_qualification(qualification_fixture(), state_fixture(), SHA, WINDOW, clock)

    def test_current_state_drift_and_unavailability_are_rejected(self):
        changes = [
            lambda s: s.update(schema="unknown"), lambda s: s.update(release_sha="d" * 40),
            lambda s: s.update(config_sha256="b" * 64),
            lambda s: s["observation"].update(schema="unknown"),
            lambda s: s["observation"].update(competing_cpu_jobs=1),
            lambda s: s["observation"].update(competing_cpu_jobs=False),
            lambda s: s["observation"]["host"].update(hostname="other-host"),
            lambda s: s["observation"]["host"].update(boot_id="f" * 36),
            lambda s: s["observation"]["gpu_compute_processes"][0].update(start_ticks=1),
            lambda s: s["observation"].update(gpu_compute_processes=[]),
            lambda s: s["observation"].update(observed_at="2029-12-31T23:59:54Z"),
            lambda s: s["observation"].update(observed_at="2030-01-01T00:00:01Z"),
        ]
        for index, change in enumerate(changes):
            state = state_fixture()
            change(state)
            with self.subTest(case=index), self.assertRaises(bring_up.Stop):
                self.validate(qualification_fixture(), state)


class QualificationStageTests(unittest.TestCase):
    def test_real_stage_captures_json_without_pty_and_keeps_validated_evidence(self):
        receipt = qualification_fixture()
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.clock = lambda: NOW
        with mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                ["fixture"], 0, json.dumps(receipt).encode(), b"")) as remote, \
                mock.patch.object(lane, "activation_state", return_value=state_fixture()), \
                mock.patch.object(lane, "confirm", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            lane.qualify()
        self.assertEqual(lane.qualification, receipt)
        self.assertFalse(remote.call_args.kwargs.get("tty", False))
        self.assertTrue(remote.call_args.kwargs.get("capture"))
        self.assertIn("/releases/" + SHA, remote.call_args.args[0])
        self.assertIn("--expected-release " + SHA, remote.call_args.args[0])

    def test_failed_second_attempt_clears_prior_evidence(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.qualification = qualification_fixture()
        with mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                ["fixture"], 1, b"", b"")), mock.patch.object(lane, "confirm", return_value=True), \
                self.assertRaises(bring_up.Stop):
            lane.qualify()
        self.assertIsNone(lane.qualification)

    def test_preview_never_caches_live_qualification(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane", dry_run=True))
        lane.qualification = qualification_fixture()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("external call")):
            lane.qualify()
        self.assertIsNone(lane.qualification)

    def test_bad_or_oversized_success_stdout_does_not_qualify(self):
        for stdout in (b"", b"success", b'{"result":"QUALIFIED","result":"QUALIFIED"}',
                       b" " * 262145):
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            lane.qualification = qualification_fixture()
            with self.subTest(size=len(stdout)), \
                    mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                        ["fixture"], 0, stdout, b"")), \
                    mock.patch.object(lane, "confirm", return_value=True), self.assertRaises(bring_up.Stop):
                lane.qualify()
            self.assertIsNone(lane.qualification)


class BuildStageTests(unittest.TestCase):
    def test_child_uses_own_stage_deadlines(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        with mock.patch.object(lane, "confirm", return_value=True), \
                mock.patch.object(lane, "login", return_value=subprocess.CompletedProcess(["ssh"], 0)), \
                mock.patch.object(Path, "exists", return_value=True), \
                mock.patch.object(Path, "write_text", side_effect=AssertionError("file write")), \
                mock.patch.object(lane, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            lane.build()
        owner_call = run.call_args
        self.assertTrue(any(str(part).endswith("ssh_ci_owner_issue.py") for part in owner_call.args[0]))
        # The receipt-writing child owns its per-step deadlines. The ordinary
        # command timeout must not terminate it midway through dispatched work.
        self.assertIn("timeout", owner_call.kwargs)
        self.assertIsNone(owner_call.kwargs["timeout"])

    def test_declined_run_does_not_inspect_or_change_any_local_state(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        with mock.patch.object(lane, "confirm", return_value=False) as confirm, \
                mock.patch.object(Path, "exists", side_effect=AssertionError("file inspection")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key read")), \
                mock.patch.object(Path, "write_text", side_effect=AssertionError("file write")), \
                mock.patch.object(lane, "run", side_effect=AssertionError("command")), \
                self.assertRaises(bring_up.Stop):
            lane.build()
        self.assertEqual(confirm.call_args.args[1], "RUN")


class GithubAdmissionTests(unittest.TestCase):
    def test_no_qualification_refuses_before_any_remote_github_or_key_access(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        self.assertIsNone(lane.qualification)
        with mock.patch.object(lane, "run", side_effect=AssertionError("command")), \
                mock.patch.object(lane, "remote", side_effect=AssertionError("remote")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key read")), \
                self.assertRaises(bring_up.Stop):
            lane.github()

    def test_declined_configuration_has_no_github_command_or_key_read(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.qualification = qualification_fixture()
        lane.clock = lambda: NOW
        with mock.patch.object(lane, "activation_state", return_value=state_fixture()), \
                mock.patch.object(lane, "confirm", return_value=False), \
                mock.patch.object(lane, "run", side_effect=AssertionError("command")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key read")), \
                self.assertRaises(bring_up.Stop):
            lane.github()

    def test_rechecks_before_first_write_and_before_enable(self):
        for drift_at in (1, 2):
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            lane.qualification = qualification_fixture()
            lane.clock = lambda: NOW
            states = [state_fixture() for _ in range(drift_at + 1)]
            states[-1]["observation"]["gpu_compute_processes"][0]["start_ticks"] += 1
            calls = []

            def run(argv, **kwargs):
                calls.append(argv)
                out = b'{"id":4242}' if argv[:3] == ["gh", "api", "user"] else b""
                if argv[0] == "ssh-keygen":
                    out = b"203.0.113.10 ssh-ed25519 SYNTHETIC-PIN\n"
                return subprocess.CompletedProcess(argv, 0, out, b"")

            with self.subTest(drift_at=drift_at), \
                    mock.patch.object(lane, "activation_state", side_effect=states), \
                    mock.patch.object(lane, "confirm", return_value=True), \
                    mock.patch.object(lane, "run", side_effect=run), \
                    mock.patch.object(Path, "read_bytes", return_value=b"SYNTHETIC-KEY"), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(bring_up.Stop):
                lane.github()
            self.assertFalse(any(argv[:4] == ["gh", "variable", "set", "SZL_CPU_CI_ENABLED"]
                                 and argv[-1] == "true" for argv in calls))
            if drift_at == 1:
                self.assertFalse(any("PUT" in argv or argv[:2] in (["gh", "secret"], ["gh", "variable"])
                                     for argv in calls))


if __name__ == "__main__":
    unittest.main()
