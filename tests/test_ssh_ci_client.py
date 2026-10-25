"""Offline client boundary tests: every subprocess is a local in-memory fake."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

from tools import ssh_ci_client as client


PERMIT = "7d301001-93d9-4fbc-a33a-87d37b67b7f6"


def github_environment():
    return {"GITHUB_REPOSITORY": client.REPOSITORY, "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "123456789", "GITHUB_SHA": "1" * 40,
            "GITHUB_WORKFLOW_REF": client.WORKFLOW_REF}


class InputBuffer(io.BytesIO):
    submitted = b""

    def close(self):
        if not self.closed:
            self.submitted = self.getvalue()
        super().close()


class FakeProcess:
    def __init__(self, stdout=b"{}", stderr=b"", code=0, stall=False):
        self.stdin = InputBuffer()
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = None if stall else code
        self.kill_count = 0

    def poll(self):
        return self.returncode

    def kill(self):
        self.kill_count += 1
        self.returncode = -9

    def wait(self, timeout):
        if self.returncode is None:
            raise AssertionError("Unbounded wait on a fake process")
        return self.returncode


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root / "dedicated-key"
        self.known = self.root / "known-hosts"
        self.key.write_text("TEST FIXTURE ONLY - NOT A KEY", encoding="utf-8")
        self.key.chmod(0o600)
        self.known.write_text("TEST FIXTURE ONLY - NOT A HOST KEY", encoding="utf-8")
        self.env = {**github_environment(), "SZL_CPU_SSH_HOST": "example.invalid",
                    "SZL_CPU_SSH_PORT": "22", "SZL_CPU_SSH_USER": "szl-cpu-ci",
                    "SZL_CPU_SSH_KEY_FILE": str(self.key),
                    "SZL_CPU_KNOWN_HOSTS_FILE": str(self.known)}

    def tearDown(self):
        self.temp.cleanup()

    def test_request_is_bound_and_deterministic(self):
        first = client.build_request(self.env, PERMIT)
        self.assertEqual(first, client.build_request(self.env, PERMIT))
        self.assertEqual(first["source_sha"], first["controller_sha"])
        self.assertEqual(first["request_id"], str(uuid.uuid5(
            client.REQUEST_NAMESPACE, "123456789:1:" + PERMIT)))
        self.assertIs(type(first["run_id"]), int)
        self.assertEqual(first["operation"], "build")

    def test_context_rejections_before_process_start(self):
        bad = {"GITHUB_REPOSITORY": "someone/fork", "GITHUB_REF": "refs/heads/untrusted",
               "GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_RUN_ATTEMPT": "2",
               "GITHUB_RUN_ID": "123; echo bad", "GITHUB_SHA": "main",
               "GITHUB_WORKFLOW_REF": client.WORKFLOW_REF.replace("main", "untrusted")}
        with mock.patch.object(client.subprocess, "Popen") as process:
            for field, value in bad.items():
                with self.subTest(field=field), self.assertRaises(ValueError):
                    client.build_request({**self.env, field: value}, PERMIT)
            process.assert_not_called()

    def test_noncanonical_context_integers_rejected(self):
        for value in ("0", "-1", "01", "1.0", "True", "", "9" * 21):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.build_request({**self.env, "GITHUB_RUN_ID": value}, PERMIT)

    def test_permit_is_canonical_uuid(self):
        for value in ("old-gpu-grant.json", PERMIT.upper(), "{" + PERMIT + "}", "../permit"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.build_request(self.env, value)

    def test_missing_context_rejected(self):
        with self.assertRaises(ValueError):
            client.build_request({}, PERMIT)

    def test_only_literal_forced_command_with_strict_options(self):
        args = client.ssh_arguments(self.env)
        self.assertEqual(args[0], "/usr/bin/ssh")
        self.assertEqual(args[-2:], ["example.invalid", "szl-cpu-build-v1"])
        for item in ("StrictHostKeyChecking=yes", "BatchMode=yes", "IdentitiesOnly=yes",
                     "IdentityAgent=none", "ClearAllForwardings=yes", "ConnectionAttempts=1",
                     "PasswordAuthentication=no", "PermitLocalCommand=no", "ProxyCommand=none"):
            self.assertIn(item, args)
        self.assertNotIn("scp", args)
        self.assertNotIn("bash", args)
        self.assertIn("/dev/null", args)

    def test_privileged_or_malformed_user_rejected(self):
        for value in ("Ubuntu", "ubuntu", "root", "Root", "-user", "user@host", "user name", "x;id"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.ssh_arguments({**self.env, "SZL_CPU_SSH_USER": value})

    def test_malformed_host_rejected(self):
        for value in ("", "-oProxyCommand=id", "host;id", "host name", "a/host", "a..b", "x\nwhoami", "::1%lo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.ssh_arguments({**self.env, "SZL_CPU_SSH_HOST": value})

    def test_valid_ip_addresses(self):
        for value in ("192.0.2.1", "2001:db8::1"):
            self.assertEqual(client.ssh_arguments({**self.env, "SZL_CPU_SSH_HOST": value})[-2], value)

    def test_malformed_or_missing_port_rejected(self):
        for value in ("", "0", "65536", "022", "22;id", "-22"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.ssh_arguments({**self.env, "SZL_CPU_SSH_PORT": value})

    def test_no_credential_or_known_hosts_fallback(self):
        for field in ("SZL_CPU_SSH_KEY_FILE", "SZL_CPU_KNOWN_HOSTS_FILE"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                client.ssh_arguments({**self.env, field: ""})

    def test_missing_or_relative_identity_file_rejected(self):
        for value in ("key", str(self.root / "missing-key")):
            with self.subTest(value=value), self.assertRaises((ValueError, OSError)):
                client.ssh_arguments({**self.env, "SZL_CPU_SSH_KEY_FILE": value})

    @unittest.skipIf(os.name != "posix", "POSIX file modes are not Windows ACLs")
    def test_key_permissions_must_be_private(self):
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):
            client.ssh_arguments(self.env)

    def transport(self, fake, request=b"{}", **kwargs):
        factory = mock.Mock(return_value=fake)
        result = client.bounded_transport(["fake-ssh", client.FORCED_COMMAND], request,
                                          self.root / "out", self.root / "err",
                                          popen=factory, timeout=0.5, **kwargs)
        return result, factory

    def test_transport_drains_both_streams_and_sends_exact_stdin(self):
        fake = FakeProcess(stdout=b"a" * 200000, stderr=b"b" * 200000)
        result, factory = self.transport(fake, request=b'{"schema":"fixture"}')
        self.assertEqual(result["failures"], [])
        self.assertEqual((result["stdout_bytes"], result["stderr_bytes"]), (200000, 200000))
        self.assertEqual(fake.stdin.submitted, b'{"schema":"fixture"}')
        self.assertEqual(factory.call_count, 1)
        self.assertFalse(factory.call_args.kwargs["shell"])
        self.assertNotIn("GITHUB_TOKEN", factory.call_args.kwargs["env"])
        self.assertNotIn("SSH_AUTH_SOCK", factory.call_args.kwargs["env"])
        self.assertEqual(fake.kill_count, 0)

    def test_stdout_cap_preserves_bounded_prefix_and_no_retry(self):
        fake = FakeProcess(stdout=b"x" * 65)
        result, factory = self.transport(fake, stdout_limit=64)
        self.assertTrue(result["stdout_truncated"])
        self.assertEqual((self.root / "out").read_bytes(), b"x" * 64)
        self.assertEqual(factory.call_count, 1)
        self.assertTrue(result["failures"])

    def test_stderr_cap_preserves_bounded_prefix(self):
        result, factory = self.transport(FakeProcess(stderr=b"x" * 65), stderr_limit=64)
        self.assertTrue(result["stderr_truncated"])
        self.assertEqual((self.root / "err").stat().st_size, 64)
        self.assertEqual(factory.call_count, 1)

    def test_request_cap_rejected_before_popen(self):
        factory = mock.Mock()
        with self.assertRaises(ValueError):
            client.bounded_transport([], b"x" * (client.MAX_REQUEST_BYTES + 1),
                                     self.root / "out", self.root / "err", popen=factory)
        factory.assert_not_called()

    def test_timeout_kills_only_owned_client_once(self):
        fake = FakeProcess(stall=True)
        sibling = FakeProcess(stall=True)
        factory = mock.Mock(return_value=fake)
        result = client.bounded_transport(["fake"], b"{}", self.root / "out",
                                          self.root / "err", timeout=0.03, popen=factory)
        self.assertTrue(result["timed_out"])
        self.assertEqual(fake.kill_count, 1)
        self.assertEqual(sibling.kill_count, 0)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(result["remote_completion"], "UNKNOWN")

    def test_transport_nonzero_retains_stderr(self):
        result, factory = self.transport(FakeProcess(stderr=b"access denied", code=255))
        self.assertEqual(result["exit_code"], 255)
        self.assertEqual((self.root / "err").read_bytes(), b"access denied")
        self.assertEqual(factory.call_count, 1)

    def test_spawn_failure_retains_empty_evidence(self):
        factory = mock.Mock(side_effect=OSError("fixture failure"))
        result = client.bounded_transport(["fake"], b"{}", self.root / "out",
                                          self.root / "err", popen=factory)
        self.assertFalse(result["started"])
        self.assertTrue(result["failures"])
        self.assertEqual((self.root / "out").read_bytes(), b"")
        self.assertEqual((self.root / "err").read_bytes(), b"")
        self.assertEqual(factory.call_count, 1)

    def run_fake(self, response, code=0):
        fake = FakeProcess(stdout=response, stderr=b"fixture stderr", code=code)
        with mock.patch.object(client.subprocess, "Popen", return_value=fake) as factory:
            receipt = client.run(self.root / "run", PERMIT, self.env)
        self.assertEqual(factory.call_count, 1)
        return receipt

    def test_empty_response_fails_closed_with_raw_evidence(self):
        receipt = self.run_fake(b"")
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertEqual((self.root / "run/response.raw.json").read_bytes(), b"")
        self.assertTrue((self.root / "run/request.json").is_file())
        self.assertTrue((self.root / "run/client-receipt.json").is_file())

    def test_invalid_envelope_fails_contract(self):
        receipt = self.run_fake(b'{"receipt":{"result":"PASS"}}')
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertFalse((self.root / "run/artifacts").exists())

    def test_duplicate_json_fields_rejected(self):
        receipt = self.run_fake(b'{"schema":"a","schema":"b"}')
        self.assertEqual(receipt["result"], "BLOCKED")

    def test_nonzero_exit_never_verifies_envelope(self):
        with mock.patch.object(client, "verify_response") as verifier:
            receipt = self.run_fake(b"{}", code=255)
        verifier.assert_not_called()
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertEqual((self.root / "run/ssh.stderr").read_bytes(), b"fixture stderr")

    def test_verified_response_writes_files_without_promoting_remote_claims(self):
        envelope = {"receipt": {"result": "PASS", "evidence_class": "DECLARED"}}
        with mock.patch.object(client, "verify_response", return_value={"selfcheck.stdout": b"ok"}) as verifier:
            receipt = self.run_fake(json.dumps(envelope).encode())
        verifier.assert_called_once()
        self.assertEqual(receipt["result"], "PASS")
        self.assertEqual(receipt["remote_observation_class"], "DECLARED")
        self.assertFalse(receipt["signed"])
        self.assertFalse(receipt["independent_witness"])
        self.assertFalse(receipt["publication_eligible"])
        self.assertEqual(receipt["inference_requests"], 0)
        self.assertEqual((self.root / "run/artifacts/selfcheck.stdout").read_bytes(), b"ok")
        self.assertEqual(json.loads((self.root / "run/client-receipt.json").read_bytes()), receipt)

    def test_blocked_response_retains_validated_files(self):
        with mock.patch.object(client, "verify_response", return_value={"selfcheck.stdout": b"failed"}):
            receipt = self.run_fake(b'{"receipt":{"result":"BLOCKED"}}')
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertEqual((self.root / "run/artifacts/selfcheck.stdout").read_bytes(), b"failed")

    def test_write_boundary_rejects_paths_even_if_validator_regresses(self):
        with mock.patch.object(client, "verify_response", return_value={"../escape": b"bad"}):
            receipt = self.run_fake(b'{"receipt":{"result":"PASS"}}')
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertFalse((self.root / "run/escape").exists())

    def test_invalid_context_still_writes_local_receipt_without_ssh(self):
        with mock.patch.object(client.subprocess, "Popen") as factory:
            receipt = client.run(self.root / "run", PERMIT, {})
        factory.assert_not_called()
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertIsNone(receipt["transport"])
        self.assertTrue((self.root / "run/client-receipt.json").is_file())
        self.assertTrue((self.root / "run/ssh.stderr").is_file())

    def test_existing_output_rejected_without_mutation(self):
        output = self.root / "run"
        output.mkdir()
        sentinel = output / "client-receipt.json"
        sentinel.write_bytes(b"previous receipt")
        with mock.patch.object(client.subprocess, "Popen") as factory, self.assertRaises(FileExistsError):
            client.run(output, PERMIT, self.env)
        factory.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"previous receipt")


if __name__ == "__main__":
    unittest.main()
