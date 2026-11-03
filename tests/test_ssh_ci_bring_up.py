"""Offline tests for the owner bring-up script; no VM, SSH, or GitHub call is made."""

import argparse
import base64
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


def ed25519_blob(fill):
    key_type = b"ssh-ed25519"
    blob = (len(key_type).to_bytes(4, "big") + key_type
            + (32).to_bytes(4, "big") + bytes([fill]) * 32)
    return base64.b64encode(blob).decode("ascii")


def plan_fixture():
    return {"pins": ["203.0.113.10 ssh-ed25519 AAAAPIN"],
            "public_keys": {
                "szl-cpu-ci.pub": "ssh-ed25519 %s ci" % ed25519_blob(65),
                "szl-owner-permit.pub": "ssh-ed25519 %s owner" % ed25519_blob(66),
            },
            "environment_sha256": "e" * 64,
            "secret_names": ["SZL_CPU_SSH_KEY"], "variables_sha256": "v" * 64,
            "variables": {"EXISTING": "keep"}}


def environment_fixture():
    environment = {
        "id": 41, "name": bring_up.ENVIRONMENT, "created_at": "2029-01-01T00:00:00Z",
        "updated_at": "2030-01-01T00:00:00Z",
        "deployment_branch_policy": {
            "protected_branches": False, "custom_branch_policies": True},
        "protection_rules": [{
            "id": 9, "type": "required_reviewers", "prevent_self_review": True,
            "reviewers": [{"type": "User", "reviewer": {"id": 4242,
                                                           "login": "owner"}}],
        }],
    }
    policies = {"total_count": 1, "branch_policies": [
        {"id": 17, "name": "main", "type": "branch"}]}
    secrets = {"total_count": 1, "secrets": [
        {"name": "SZL_CPU_SSH_KEY", "created_at": "2029-01-01T00:00:00Z",
         "updated_at": "2030-01-01T00:00:00Z"}]}
    return environment, policies, secrets


def variables_fixture(enabled="false"):
    return {"total_count": 2, "variables": [
        {"name": "EXISTING", "value": "keep", "created_at": "2029-01-01T00:00:00Z",
         "updated_at": "2030-01-01T00:00:00Z"},
        {"name": "SZL_CPU_CI_ENABLED", "value": enabled,
         "created_at": "2029-01-01T00:00:00Z", "updated_at": "2030-01-01T00:00:00Z"},
    ]}


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
            for path in ("/var", "/tmp/models", "/dev"):  # Refused before STAGE, not by the VM plan.
                output = io.StringIO()
                with self.subTest(path=path), contextlib.redirect_stdout(output), \
                        contextlib.redirect_stderr(output):
                    code = bring_up.main(arguments(scratch, "--hidden-path", path,
                                                   "--stages", "provision", "--dry-run"))
                    self.assertEqual(code, 1)
                    self.assertIn("cover a build unit mount point", output.getvalue())
                    self.assertNotIn("STAGE", output.getvalue())

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
    def test_release_must_equal_the_current_main_commit(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.gh_json = mock.Mock(return_value={"sha": SHA})
        lane.require_release_is_current_main()
        lane.gh_json.assert_called_once_with(
            ["api", "repos/%s/commits/main" % bring_up.REPO])
        for response in ({"sha": "d" * 40}, {}, [], None):
            lane.gh_json = mock.Mock(return_value=response)
            with self.subTest(response=response), self.assertRaisesRegex(
                    bring_up.Stop, "not the current main"):
                lane.require_release_is_current_main()

    def test_main_protection_needs_json_true_or_typed_active_rules(self):
        branch_path = "repos/%s/branches/main" % bring_up.REPO
        rules_path = "repos/%s/rules/branches/main" % bring_up.REPO

        def protect(branch, rules):
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            lane.gh_json = mock.Mock(side_effect=lambda argv: deepcopy(
                {branch_path: branch, rules_path: rules}[argv[1]]))
            lane.require_protected_main()
            return [call.args[0][1] for call in lane.gh_json.call_args_list]

        # Only the JSON boolean true counts; anything else falls through to the rules.
        for branch in ({"protected": "yes"}, {"protected": 1}, {"protected": "true"},
                       {"name": "main"}, [], None):
            with self.subTest(branch=branch), self.assertRaisesRegex(
                    bring_up.Stop, "no branch protection rule or active ruleset"):
                protect(branch, [])
        for rules in ([], [{}], [{"type": ""}], ["pull_request"], {"message": "Not Found"}, None):
            with self.subTest(rules=rules), self.assertRaisesRegex(
                    bring_up.Stop, "no branch protection rule or active ruleset"):
                protect({"protected": False}, rules)
        self.assertEqual(protect({"protected": True}, None), [branch_path])
        self.assertEqual(protect({"protected": False}, [{"type": "deletion", "ruleset_id": 9}]),
                         [branch_path, rules_path])

    def test_environment_snapshot_digest_binds_full_authority_preimage(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))

        def snapshot(environment, policies, secrets, variables, *, listed_count=1,
                     allow_enabled=False):
            endpoint = "repos/%s/environments/%s" % (bring_up.REPO, bring_up.ENVIRONMENT)
            responses = {
                "repos/%s/environments?per_page=100" % bring_up.REPO: {
                    "total_count": listed_count,
                    "environments": [{"name": bring_up.ENVIRONMENT}]},
                endpoint: environment,
                endpoint + "/deployment-branch-policies?per_page=100": policies,
                endpoint + "/secrets?per_page=100": secrets,
                endpoint + "/variables?per_page=100": variables,
            }

            def read(argv):
                self.assertEqual(argv[0], "api")
                return deepcopy(responses[argv[1]])

            lane.gh_json = mock.Mock(side_effect=read)
            return lane.environment_snapshot(allow_enabled=allow_enabled)

        environment, policies, secrets = environment_fixture()
        variables = variables_fixture()
        base = snapshot(environment, policies, secrets, variables)
        self.assertEqual(base["secret_names"], ["SZL_CPU_SSH_KEY"])
        self.assertEqual(base["variables"], {"EXISTING": "keep",
                                              "SZL_CPU_CI_ENABLED": "false"})
        digests = set()
        for mutate in (
                lambda e, p, s: e["protection_rules"][0].update(prevent_self_review=False),
                lambda e, p, s: e["protection_rules"][0]["reviewers"][0]["reviewer"].update(
                    id=4343),
                lambda e, p, s: p["branch_policies"][0].update(id=18),
                lambda e, p, s: s["secrets"][0].update(
                    updated_at="2030-01-02T00:00:00Z"),
                lambda e, p, s: (s["secrets"].append({"name": "OTHER", "updated_at":
                                                        "2030-01-01T00:00:00Z",
                                                        "created_at":
                                                        "2029-01-01T00:00:00Z"}),
                                   s.update(total_count=2)),
        ):
            changed_environment, changed_policies, changed_secrets = environment_fixture()
            mutate(changed_environment, changed_policies, changed_secrets)
            changed = snapshot(changed_environment, changed_policies, changed_secrets, variables)
            self.assertNotEqual(changed["environment_sha256"], base["environment_sha256"])
            digests.add(changed["environment_sha256"])
        self.assertEqual(len(digests), 5)
        changed_variables = variables_fixture()
        changed_variables["variables"][0]["value"] = "changed"
        changed = snapshot(environment, policies, secrets, changed_variables)
        self.assertNotEqual(changed["variables_sha256"], base["variables_sha256"])
        with self.assertRaisesRegex(bring_up.Stop, "listing is incomplete"):
            snapshot(environment, policies, secrets, variables, listed_count=2)
        with self.assertRaisesRegex(bring_up.Stop, "absent or exactly false"):
            snapshot(environment, policies, secrets, variables_fixture("true"))
        enabled = snapshot(environment, policies, secrets, variables_fixture("true"),
                           allow_enabled=True)
        self.assertEqual(enabled["variables"]["SZL_CPU_CI_ENABLED"], "true")

    def test_variables_enable_last_without_environment_secret_or_key_mutation(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.clock = lambda: NOW
        lane.qualification = qualification_fixture()
        lane.plan_state = plan_fixture()
        lane.activation_state = mock.Mock(return_value=state_fixture())
        lane.verify_plan = mock.Mock(return_value=plan_fixture())
        calls = []

        def run(argv, **kwargs):
            calls.append(list(map(str, argv)))
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        lane.run = run
        with mock.patch.object(lane, "confirm", side_effect=[True, True]), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("private key read")), \
                contextlib.redirect_stdout(io.StringIO()):
            lane.github()
        variables = [argv[3] for argv in calls if argv[:3] == ["gh", "variable", "set"]]
        self.assertEqual(variables, ["SZL_CPU_SSH_HOST", "SZL_CPU_SSH_PORT",
                                     "SZL_CPU_SSH_USER", "SZL_CPU_KNOWN_HOSTS",
                                     "SZL_CPU_CI_ENABLED"])
        self.assertFalse(any("-X" in argv for argv in calls))
        self.assertFalse(any(argv[:2] == ["gh", "secret"] for argv in calls))
        self.assertEqual(lane.activation_state.call_count, 3)
        self.assertEqual(lane.verify_plan.call_count, 3)
        expected = {"EXISTING": "keep", **lane.connection_variables(plan_fixture()["pins"])}
        self.assertEqual(lane.verify_plan.call_args_list[1],
                         mock.call(expected_variables=expected))
        expected["SZL_CPU_CI_ENABLED"] = "true"
        self.assertEqual(lane.verify_plan.call_args_list[2],
                         mock.call(expected_variables=expected, allow_enabled=True))

    def test_failed_activation_is_disabled_and_uncertain_recovery_is_reported(self):
        cases = ((False, False, False), (False, True, False),
                 (False, False, True), (True, False, False))
        for uncertain, deactivation_error, enable_error in cases:
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            lane.clock = lambda: NOW
            lane.qualification = qualification_fixture()
            lane.plan_state = plan_fixture()
            lane.activation_state = mock.Mock(return_value=state_fixture())
            lane.verify_plan = mock.Mock(side_effect=[
                plan_fixture(), plan_fixture(), bring_up.Stop("post-activation drift")])
            calls = []

            def run(argv, **kwargs):
                calls.append(list(map(str, argv)))
                if (enable_error and argv[:4] == ["gh", "variable", "set",
                                                 "SZL_CPU_CI_ENABLED"]
                        and argv[-1] == "true"):
                    raise bring_up.Stop("activation command uncertain")
                if (deactivation_error and argv[:4] == ["gh", "variable", "set",
                                                       "SZL_CPU_CI_ENABLED"]
                        and argv[-1] == "false"):
                    raise bring_up.Stop("deactivation command uncertain")
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            readback = (bring_up.Stop("readback unavailable") if uncertain else {
                "variables_sha256": "r" * 64,
                "variables": {"SZL_CPU_CI_ENABLED": "false"},
            })
            message = "not proven" if uncertain else "false readback verified"
            with self.subTest(uncertain=uncertain, deactivation_error=deactivation_error,
                              enable_error=enable_error), \
                    mock.patch.object(lane, "confirm", side_effect=[True, True]), \
                    mock.patch.object(lane, "run", side_effect=run), \
                    mock.patch.object(lane, "environment_variable_snapshot",
                                      side_effect=readback if uncertain else None,
                                      return_value=None if uncertain else readback), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(bring_up.Stop, message):
                lane.github()
            enabled_writes = [argv[-1] for argv in calls
                              if argv[:4] == ["gh", "variable", "set",
                                              "SZL_CPU_CI_ENABLED"]]
            self.assertEqual(enabled_writes, ["true", "false"])

    def test_changed_plan_stops_before_any_variable_write(self):
        cases = (
            ("public key", {**plan_fixture()["public_keys"],
                            "szl-cpu-ci.pub": "ssh-ed25519 CHANGED"},
             plan_fixture()["pins"], "e" * 64, "v" * 64, {"sha": SHA}),
            ("host pin", plan_fixture()["public_keys"],
             ["203.0.113.10 ssh-ed25519 CHANGED"], "e" * 64, "v" * 64, {"sha": SHA}),
            ("environment", plan_fixture()["public_keys"], plan_fixture()["pins"],
             "f" * 64, "v" * 64, {"sha": SHA}),
            ("variables", plan_fixture()["public_keys"], plan_fixture()["pins"],
             "e" * 64, "w" * 64, {"sha": SHA}),
            ("main advance", plan_fixture()["public_keys"], plan_fixture()["pins"],
             "e" * 64, "v" * 64, {"sha": "d" * 40}),
        )
        for label, public_keys, pins, digest, variable_digest, commit in cases:
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            lane.plan_state = plan_fixture()
            calls = []

            def run(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with self.subTest(label=label), \
                    mock.patch.object(lane, "require_qualification"), \
                    mock.patch.object(lane, "confirm", return_value=True), \
                    mock.patch.object(lane, "public_key_snapshot", return_value=public_keys), \
                    mock.patch.object(lane, "host_pins", return_value=pins), \
                    mock.patch.object(lane, "require_protected_main"), \
                    mock.patch.object(lane, "gh_json", return_value=commit), \
                    mock.patch.object(lane, "environment_snapshot", return_value={
                        "environment_sha256": digest,
                        "secret_names": ["SZL_CPU_SSH_KEY"],
                        "variables_sha256": variable_digest,
                        "variables": {"EXISTING": "keep"}}), \
                    mock.patch.object(lane, "run", side_effect=run), \
                    self.assertRaises(bring_up.Stop):
                lane.github()
            self.assertEqual(calls, [])

    def test_live_plan_records_only_the_read_preimage(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        snapshot = {"environment_sha256": "e" * 64,
                    "secret_names": ["SZL_CPU_SSH_KEY"],
                    "variables_sha256": "v" * 64,
                    "variables": {"EXISTING": "keep"}}
        output = io.StringIO()
        with mock.patch.object(lane, "preprovisioned_keys", return_value=("ci", "owner")), \
                mock.patch.object(lane, "host_pins", return_value=plan_fixture()["pins"]), \
                mock.patch.object(lane, "require_login"), \
                mock.patch.object(lane, "require_protected_main"), \
                mock.patch.object(lane, "require_release_is_current_main"), \
                mock.patch.object(lane, "environment_snapshot", return_value=snapshot), \
                mock.patch.object(lane, "run", side_effect=AssertionError("write command")), \
                contextlib.redirect_stdout(output):
            lane.plan()
        planned_keys = {"szl-cpu-ci.pub": "ci", "szl-owner-permit.pub": "owner"}
        self.assertEqual(lane.plan_state, {"pins": plan_fixture()["pins"],
                                           "public_keys": planned_keys, **snapshot})
        payload = next(json.loads(line) for line in output.getvalue().splitlines()
                       if line.startswith("{"))
        self.assertEqual(payload["schema"], "szl.ssh-cpu-build.live-plan.v1")
        self.assertRegex(payload["public_keys_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual({row["name"]: row["value"] for row in payload["writes"]}, {
            **lane.connection_variables(plan_fixture()["pins"]),
            "SZL_CPU_CI_ENABLED": "true",
        })


class KeyStageTests(unittest.TestCase):
    def test_existing_distinct_keys_are_validated_without_generation_or_private_read(self):
        with tempfile.TemporaryDirectory() as scratch:
            lane = bring_up.Lane(lane_args(scratch))
            lane.ci_key.write_bytes(b"opaque-private-ci")
            Path(str(lane.ci_key) + ".pub").write_text(
                "ssh-ed25519 %s ci\n" % ed25519_blob(65), encoding="ascii")
            lane.owner_key.write_bytes(b"opaque-private-owner")
            Path(str(lane.owner_key) + ".pub").write_text(
                "ssh-ed25519 %s owner\n" % ed25519_blob(66), encoding="ascii")
            original_open = Path.open

            def refuse_private_read(path, *args, **kwargs):
                if path in {lane.ci_key, lane.owner_key}:
                    raise AssertionError("private key read")
                return original_open(path, *args, **kwargs)

            with mock.patch.object(lane, "run", side_effect=AssertionError("command")), \
                    mock.patch.object(lane, "confirm", side_effect=AssertionError("prompt")), \
                    mock.patch.object(Path, "open", refuse_private_read), \
                    contextlib.redirect_stdout(io.StringIO()):
                lane.keys()
            self.assertEqual(lane.ci_key.read_bytes(), b"opaque-private-ci")
            self.assertEqual(lane.owner_key.read_bytes(), b"opaque-private-owner")

    def test_missing_or_reused_key_material_is_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            lane = bring_up.Lane(lane_args(scratch))
            with self.assertRaisesRegex(bring_up.Stop, "pre-provision"):
                lane.keys()
            lane.ci_key.write_bytes(b"opaque-private")
            Path(str(lane.ci_key) + ".pub").write_text(
                "ssh-ed25519 QUFBQQ== malformed\n", encoding="ascii")
            lane.owner_key.write_bytes(b"opaque-private")
            Path(str(lane.owner_key) + ".pub").write_text(
                "ssh-ed25519 %s owner\n" % ed25519_blob(66), encoding="ascii")
            with self.assertRaises(bring_up.Stop):
                lane.keys()
            for key in (lane.ci_key, lane.owner_key):
                key.write_bytes(b"opaque-private")
                Path(str(key) + ".pub").write_text(
                    "ssh-ed25519 %s same\n" % ed25519_blob(65), encoding="ascii")
            with self.assertRaisesRegex(bring_up.Stop, "must differ"):
                lane.keys()


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
        lane.plan_state = plan_fixture()
        calls = []

        def remote(command, **kwargs):
            calls.append(command)
            stdout = (STAGING + "\n").encode() if "mktemp -d" in command else b""
            return subprocess.CompletedProcess(["fixture"], 0, stdout, b"")

        with mock.patch.object(lane, "confirm", side_effect=[True, False]), \
                mock.patch.object(lane, "verify_plan") as verify_plan, \
                mock.patch.object(lane, "login", return_value=subprocess.CompletedProcess(["ssh"], 0)), \
                mock.patch.object(lane, "remote", side_effect=remote), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key reread")), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(bring_up.Stop):
            lane.provision()
        verify_plan.assert_called_once_with()
        self.assertIn("mktemp -d", calls[0])
        self.assertTrue(any(STAGING + "/src" in cmd for cmd in calls))
        self.assertTrue(any("ssh_ci_provision.py plan" in cmd for cmd in calls))
        self.assertFalse(any("ssh_ci_provision.py apply" in cmd for cmd in calls))
        self.assertFalse(any("rm " in cmd or "rmdir" in cmd for cmd in calls))

    def test_provision_stages_the_public_keys_captured_by_the_plan(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.plan_state = plan_fixture()
        writes = []

        def remote(command, **kwargs):
            if "mktemp -d" in command:
                return subprocess.CompletedProcess(["fixture"], 0, (STAGING + "\n").encode(), b"")
            if kwargs.get("stdin") is not None:
                writes.append(kwargs["stdin"])
            return subprocess.CompletedProcess(["fixture"], 0, b"", b"")

        with mock.patch.object(lane, "confirm", side_effect=[True, False]), \
                mock.patch.object(lane, "verify_plan"), \
                mock.patch.object(lane, "login", return_value=subprocess.CompletedProcess(["ssh"], 0)), \
                mock.patch.object(lane, "remote", side_effect=remote), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key reread")), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(bring_up.Stop):
            lane.provision()
        self.assertEqual(writes, [
            plan_fixture()["public_keys"]["szl-cpu-ci.pub"].encode("ascii"),
            plan_fixture()["public_keys"]["szl-owner-permit.pub"].encode("ascii"),
        ])

    def provision_through_apply(self, probe_after, apply_code):
        """Provision with both answers given; every ssh, including both logins, is recorded."""
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.plan_state = plan_fixture()
        calls, probes, shown, output = [], [0, probe_after], [], io.StringIO()

        def run(argv, **kwargs):
            argv = list(map(str, argv))
            calls.append(argv)
            if argv[-1] == "true":
                return subprocess.CompletedProcess(argv, probes.pop(0), None, b"")
            if "mktemp -d" in argv[-1]:
                return subprocess.CompletedProcess(argv, 0, (STAGING + "\n").encode(), b"")
            code = apply_code if "ssh_ci_provision.py apply" in argv[-1] else 0
            return subprocess.CompletedProcess(argv, code, b"", b"")

        def confirm(_question, word):
            shown.append((word, output.getvalue()))  # What the owner has read at this prompt.
            return True

        error = None
        with mock.patch.object(lane, "confirm", side_effect=confirm), \
                mock.patch.object(lane, "verify_plan"), \
                mock.patch.object(lane, "run", side_effect=run), \
                contextlib.redirect_stdout(output):
            try:
                lane.provision()
            except bring_up.Stop as stop:
                error = str(stop)
        return calls, shown, output.getvalue(), error

    def test_apply_is_followed_by_a_fresh_admin_login_and_recovery_on_failure(self):
        recovery = (bring_up.UNINSTALL + "; if fresh admin logins still fail",
                    "pending sshd_config or sshd_config.d edit",
                    "check sudo sshd -T -C user=Ubuntu,host=localhost,addr=127.0.0.1",
                    "addr= your workstation's address", "Match Address/Host",
                    "revert any edit szl did not make", "then run sudo systemctl reload ssh")
        for label, probe_after, apply_code, message in (
                ("works", 0, 0, None),
                ("locked out", 255, 0, "fresh admin login to Ubuntu@203.0.113.10 fails after apply"),
                ("apply failed", 0, 1, "provisioning apply failed")):
            with self.subTest(label):
                calls, shown, output, error = self.provision_through_apply(probe_after, apply_code)
                self.assertEqual([word for word, _ in shown], ["STAGE", "APPLY"])
                before_apply = shown[1][1]
                self.assertIn("second admin SSH session (or the provider's web terminal)", before_apply)
                for part in recovery:
                    self.assertIn(part, before_apply)
                applied = next(i for i, argv in enumerate(calls)
                               if "ssh_ci_provision.py apply" in argv[-1])
                self.assertEqual(len(calls), applied + 2)  # Only the fresh login follows apply.
                probe = calls[-1]
                for option in ("-T", "BatchMode=yes", "ControlPath=none",
                               "StrictHostKeyChecking=yes"):
                    self.assertIn(option, probe)
                self.assertEqual(probe[-2:], ["Ubuntu@203.0.113.10", "true"])
                self.assertFalse(any("rm " in argv[-1] or "rmdir" in argv[-1] for argv in calls))
                if message is None:
                    self.assertIsNone(error)
                    self.assertIn("a fresh admin login works", output)
                    continue
                self.assertIn(message, error)
                self.assertIn("retained directory %s was not deleted" % STAGING, error)
                if probe_after:  # Locked out: the error itself carries the whole recovery.
                    for part in recovery:
                        self.assertIn(part, error)

    def test_invalid_staging_path_refused_before_public_key_transfer(self):
        for path in ("../existing-work", "/tmp/unrelated", "/tmp/szl-cpu-bringup-ok/../x",
                     STAGING + "\n/another/path"):
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            lane.plan_state = plan_fixture()
            with self.subTest(path=path), mock.patch.object(lane, "confirm", return_value=True), \
                    mock.patch.object(lane, "verify_plan"), \
                    mock.patch.object(lane, "login", return_value=subprocess.CompletedProcess(["ssh"], 0)), \
                    mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                        ["fixture"], 0, (path + "\n").encode(), b"")) as remote, \
                    mock.patch.object(Path, "read_bytes", side_effect=AssertionError("key read")), \
                    self.assertRaises(bring_up.Stop):
                lane.provision()
            self.assertEqual(remote.call_count, 1)

    def test_mutating_bring_up_stages_require_a_same_invocation_plan(self):
        for method in ("provision", "qualify"):
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            with self.subTest(method=method), \
                    mock.patch.object(lane, "confirm", return_value=True), \
                    mock.patch.object(lane, "remote", side_effect=AssertionError("remote write")), \
                    self.assertRaisesRegex(bring_up.Stop, "run plan"):
                getattr(lane, method)()


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
        lane.plan_state = plan_fixture()
        lane.clock = lambda: NOW
        with mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                ["fixture"], 0, json.dumps(receipt).encode(), b"")) as remote, \
                mock.patch.object(lane, "activation_state", return_value=state_fixture()), \
                mock.patch.object(lane, "confirm", return_value=True), \
                mock.patch.object(lane, "verify_plan") as verify_plan, \
                contextlib.redirect_stdout(io.StringIO()):
            lane.qualify()
        verify_plan.assert_called_once_with()
        self.assertEqual(lane.qualification, receipt)
        self.assertFalse(remote.call_args.kwargs.get("tty", False))
        self.assertTrue(remote.call_args.kwargs.get("capture"))
        self.assertIn("/releases/" + SHA, remote.call_args.args[0])
        self.assertIn("--expected-release " + SHA, remote.call_args.args[0])

    def test_failed_second_attempt_clears_prior_evidence(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
        lane.plan_state = plan_fixture()
        lane.qualification = qualification_fixture()
        with mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                ["fixture"], 1, b"", b"")), mock.patch.object(lane, "confirm", return_value=True), \
                mock.patch.object(lane, "verify_plan"), \
                self.assertRaises(bring_up.Stop):
            lane.qualify()
        self.assertIsNone(lane.qualification)

    def test_preview_never_caches_live_qualification(self):
        lane = bring_up.Lane(lane_args("unused-synthetic-lane", dry_run=True))
        lane.plan_state = plan_fixture()
        lane.qualification = qualification_fixture()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(lane, "verify_plan"), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("external call")):
            lane.qualify()
        self.assertIsNone(lane.qualification)

    def test_bad_or_oversized_success_stdout_does_not_qualify(self):
        for stdout in (b"", b"success", b'{"result":"QUALIFIED","result":"QUALIFIED"}',
                       b" " * 262145):
            lane = bring_up.Lane(lane_args("unused-synthetic-lane"))
            lane.plan_state = plan_fixture()
            lane.qualification = qualification_fixture()
            with self.subTest(size=len(stdout)), \
                    mock.patch.object(lane, "remote", return_value=subprocess.CompletedProcess(
                        ["fixture"], 0, stdout, b"")), \
                    mock.patch.object(lane, "confirm", return_value=True), \
                    mock.patch.object(lane, "verify_plan"), self.assertRaises(bring_up.Stop):
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
        lane.plan_state = plan_fixture()
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
            lane.plan_state = plan_fixture()
            lane.clock = lambda: NOW
            states = [state_fixture() for _ in range(drift_at + 1)]
            states[-1]["observation"]["gpu_compute_processes"][0]["start_ticks"] += 1
            calls = []

            def run(argv, **kwargs):
                calls.append(argv)
                out = b'{"id":4242}' if argv[:3] == ["gh", "api", "user"] else b""
                if argv[:3] == ["gh", "api", "repos/szl-holdings/szl-skills/branches/main"]:
                    out = b'{"protected":true}'
                if argv[:3] == ["gh", "api", "repos/szl-holdings/szl-skills/environments?per_page=100"]:
                    out = b'{"total_count":0,"environments":[]}'
                if argv[:2] == ["gh", "api"] and "deployment-branch-policies?" in argv[2]:
                    out = b'{"total_count":1,"branch_policies":[{"name":"main","type":"branch"}]}'
                if argv[0] == "ssh-keygen":
                    out = b"203.0.113.10 ssh-ed25519 SYNTHETIC-PIN\n"
                return subprocess.CompletedProcess(argv, 0, out, b"")

            with self.subTest(drift_at=drift_at), \
                    mock.patch.object(lane, "activation_state", side_effect=states) as readback, \
                    mock.patch.object(lane, "verify_plan", return_value=plan_fixture()) as plan_check, \
                    mock.patch.object(lane, "confirm", return_value=True), \
                    mock.patch.object(lane, "run", side_effect=run), \
                    mock.patch.object(Path, "read_bytes", return_value=b"SYNTHETIC-KEY"), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(bring_up.Stop):
                lane.github()
            self.assertEqual(readback.call_count, drift_at + 1)
            self.assertEqual(plan_check.call_count, 1)
            self.assertFalse(any(argv[:4] == ["gh", "variable", "set", "SZL_CPU_CI_ENABLED"]
                                 and argv[-1] == "true" for argv in calls))
            if drift_at == 1:
                self.assertFalse(any("PUT" in argv or argv[:2] in (["gh", "secret"], ["gh", "variable"])
                                     for argv in calls))


if __name__ == "__main__":
    unittest.main()
