"""Synthetic provider fixtures for the public OSF registration witness pilot."""

import copy
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PILOT = ROOT / "pilots/osf-registration-witness/szl-osf-registration-witness"
spec = importlib.util.spec_from_file_location("osf_registration_witness", PILOT / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.rid = "wekmb"
        self.fid = "650233e76d1e8962ef15164c"
        self.plan = {"id": "synthetic-plan", "version": 1, "frozen_at": "2026-01-01T00:00:00Z",
                     "hypotheses": [], "families": [], "stopping_rule": {}, "scheduled_attempts": []}
        self.raw = encoded(self.plan)
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.config = {"schema": kernel.SCHEMA, "registration_id": self.rid, "file_id": self.fid,
                       "expected_path": "/Archive of OSF Storage/plan.json", "expected_sha256": self.sha,
                       "artifact_kind": "canonical_analysis_plan_v1"}
        self.base = "https://api.osf.io/v2/registrations/wekmb/"
        self.file_url = "https://files.osf.io/v1/resources/wekmb/providers/osfstorage/" + self.fid
        self.responses = {
            self.base: {"data": {"id": self.rid, "type": "registrations", "attributes": {
                "registration": True, "public": True, "withdrawn": False, "pending_withdrawal": False,
                "embargoed": False, "pending_registration_approval": False, "archiving": False,
                "date_registered": "2026-01-01T00:00:00"}}},
            self.base + "identifiers/": {"data": [{"type": "identifiers",
                                                   "relationships": {"referent": {"data": {"id": self.rid, "type": "registrations"}}},
                                                   "attributes": {"category": "doi", "value": "10.17605/OSF.IO/WEKMB"}}],
                                           "links": {"next": None, "meta": {"total": 1, "per_page": 10}}},
            "https://api.datacite.org/dois/10.17605/OSF.IO/WEKMB": {
                "data": {"id": "10.17605/osf.io/wekmb", "type": "dois",
                         "attributes": {"doi": "10.17605/osf.io/wekmb", "state": "findable",
                                        "url": "https://osf.io/wekmb/"}}},
            self.base + "files/osfstorage/" + self.fid: {"data": {
                "id": self.fid, "type": "files", "attributes": {"kind": "file", "size": len(self.raw),
                "current_version": 1, "materialized_path": self.config["expected_path"],
                "extra": {"hashes": {"sha256": self.sha}}},
                "relationships": {"node": {"data": {"id": self.rid, "type": "registrations"}},
                                  "target": {"data": {"id": self.rid, "type": "registrations"}}},
                "links": {"html": "https://osf.io/wekmb/files/osfstorage/" + self.fid}}},
            self.file_url: self.raw,
        }
        self.calls = []

    def fetch(self, url, limit, expected_sha256=None):
        self.calls.append((url, limit))
        if url not in self.responses:
            raise kernel.Unavailable("synthetic missing response")
        response = self.responses[url]
        return response if isinstance(response, bytes) else encoded(response)

    def verify(self):
        return kernel.verify(self.config, self.fetch)

    def test_full_plan_byte_witness_keeps_timing_and_science_unverified(self):
        report = self.verify()
        self.assertEqual(report["status"], "PUBLIC_PLAN_BYTES_MATCHED")
        self.assertEqual(report["plan_binding"], "MATCHED_CANONICAL_SHA256")
        self.assertEqual(report["expected_pin_provenance"], "CALLER_SUPPLIED_UNVERIFIED")
        self.assertEqual(report["registered_at_timezone"], "UNSPECIFIED")
        self.assertEqual(report["pre_data_timing"], "NOT_VERIFIED")
        self.assertEqual(report["schema_response_revision"], "NOT_VERIFIED")
        self.assertEqual(report["scientific_validity"], "NOT_EVALUATED")
        self.assertFalse(report["provider_write_performed"])
        self.assertEqual(len(self.calls), 5)

    def test_opaque_file_matches_bytes_but_not_plan(self):
        self.config["artifact_kind"] = "opaque_file"
        report = self.verify()
        self.assertEqual(report["status"], "PUBLIC_FILE_BYTES_MATCHED")
        self.assertEqual(report["plan_binding"], "NOT_EVALUATED")

    def test_nonpublic_withdrawn_pending_or_archiving_fails_closed(self):
        attrs = self.responses[self.base]["data"]["attributes"]
        for key, value in (("public", False), ("withdrawn", True), ("pending_withdrawal", True),
                           ("embargoed", True), ("pending_registration_approval", True),
                           ("archiving", True), ("registration", False)):
            with self.subTest(key=key):
                before = copy.deepcopy(attrs)
                attrs[key] = value
                self.calls.clear()
                self.assertEqual(self.verify()["status"], "BLOCKED")
                self.assertEqual(len(self.calls), 1)
                attrs.clear(); attrs.update(before)

    def test_wrong_doi_file_path_hash_and_bytes_are_blocked(self):
        doi_url = "https://api.datacite.org/dois/10.17605/OSF.IO/WEKMB"
        metadata_url = self.base + "files/osfstorage/" + self.fid
        cases = [
            (doi_url, ("data", "attributes", "url"), "https://example.org/"),
            (metadata_url, ("data", "attributes", "materialized_path"), "/wrong.json"),
            (metadata_url, ("data", "attributes", "extra", "hashes", "sha256"), "0" * 64),
            (metadata_url, ("data", "links", "html"), "https://osf.io/other/files/osfstorage/" + self.fid),
            (metadata_url, ("data", "relationships", "node", "data", "id"), "other"),
        ]
        for url, keys, bad in cases:
            with self.subTest(keys=keys):
                original = copy.deepcopy(self.responses[url]); node = self.responses[url]
                for key in keys[:-1]:
                    node = node[key]
                node[keys[-1]] = bad
                self.assertEqual(self.verify()["status"], "BLOCKED")
                self.responses[url] = original
        self.responses[self.file_url] = self.raw + b" "
        self.assertEqual(self.verify()["status"], "BLOCKED")

    def test_noncanonical_and_wrong_shape_plan_are_blocked(self):
        escaped_surrogate = encoded(self.plan).replace(b'"synthetic-plan"', b'"\\ud800"')
        for raw in (json.dumps(self.plan).encode(), encoded({"hello": "world"}), escaped_surrogate):
            with self.subTest(raw=raw[:20]):
                sha = hashlib.sha256(raw).hexdigest()
                self.config["expected_sha256"] = sha
                self.responses[self.file_url] = raw
                attrs = self.responses[self.base + "files/osfstorage/" + self.fid]["data"]["attributes"]
                attrs["size"] = len(raw); attrs["extra"]["hashes"]["sha256"] = sha
                self.assertEqual(self.verify()["status"], "BLOCKED")

    def test_malformed_provider_and_unavailable_do_not_pass(self):
        metadata_url = self.base + "files/osfstorage/" + self.fid
        self.responses[metadata_url]["data"]["attributes"]["size"] = True
        self.assertEqual(self.verify()["status"], "BLOCKED")
        self.responses.pop(metadata_url)
        self.assertEqual(self.verify()["status"], "UNAVAILABLE")
        self.assertIn("file_metadata", self.verify()["findings"][0])

    def test_duplicate_keys_and_nonfinite_provider_json_are_blocked(self):
        self.responses[self.base] = b'{"data":{},"data":{}}'
        self.assertEqual(self.verify()["status"], "BLOCKED")
        self.responses[self.base] = b'{"data":NaN}'
        self.assertEqual(self.verify()["status"], "BLOCKED")
        self.responses[self.base] = b'{"data":1e999}'
        self.assertEqual(self.verify()["status"], "BLOCKED")

    def test_paginated_identifiers_cannot_establish_unique_doi(self):
        self.responses[self.base + "identifiers/"]["links"]["next"] = "https://api.osf.io/v2/registrations/wekmb/identifiers/?page=2"
        report = self.verify()
        self.assertEqual(report["status"], "UNAVAILABLE")
        self.assertEqual(report["findings"], ["IDENTIFIER_LIST_INCOMPLETE"])
        self.responses[self.base + "identifiers/"]["links"]["next"] = None
        self.responses[self.base + "identifiers/"]["links"]["meta"]["total"] = 2
        self.assertEqual(self.verify()["status"], "UNAVAILABLE")
        self.responses[self.base + "identifiers/"]["links"].pop("next")
        self.assertEqual(self.verify()["status"], "UNAVAILABLE")

    def test_identifier_referent_cannot_point_to_another_registration(self):
        row = self.responses[self.base + "identifiers/"]["data"][0]
        row["relationships"]["referent"]["data"]["id"] = "other"
        self.assertEqual(self.verify()["status"], "BLOCKED")

    def test_documented_node_link_relationships_are_accepted_when_exact(self):
        row = self.responses[self.base + "identifiers/"]["data"][0]
        row["relationships"]["referent"] = {"links": {"related": {"href": "https://api.osf.io/v2/nodes/wekmb/"}}}
        file = self.responses[self.base + "files/osfstorage/" + self.fid]["data"]
        file["relationships"] = {"node": {"links": {"related": {"href": "https://api.osf.io/v2/nodes/wekmb/"}}}}
        self.assertEqual(self.verify()["status"], "PUBLIC_PLAN_BYTES_MATCHED")
        row["relationships"]["referent"]["links"]["related"]["href"] = "https://api.osf.io/v2/nodes/other/"
        self.assertEqual(self.verify()["status"], "BLOCKED")

    def test_invalid_config_never_fetches(self):
        for key, bad in (("registration_id", "../../bad"), ("file_id", "bad"),
                         ("expected_sha256", ["0" * 64]), ("expected_path", "/../plan.json"),
                         ("artifact_kind", "unknown")):
            with self.subTest(key=key):
                value = dict(self.config); value[key] = bad
                with self.assertRaises(ValueError):
                    kernel.verify(value, self.fetch)
                self.assertEqual(self.calls, [])

    def test_redirect_allows_only_pinned_osf_storage_object(self):
        handler = kernel._BoundedRedirect(self.sha)
        request = kernel.urllib.request.Request(self.file_url)
        good = "https://storage.googleapis.com/cos-osf-prod-files-us-east1/" + self.sha + "?Expires=1"
        self.assertIsNotNone(handler.redirect_request(request, None, 302, "Found", {}, good))
        for bad in ("https://evil.example/object", "http://storage.googleapis.com/cos-osf-prod-files-us-east1/" + self.sha,
                    "https://storage.googleapis.com/cos-osf-prod-files-us-east1/" + "0" * 64,
                    "https://storage.googleapis.com.evil.example/cos-osf-prod-files-us-east1/" + self.sha):
            with self.subTest(bad=bad.split("?")[0]):
                self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, bad))
        self.assertIsNone(kernel._BoundedRedirect().redirect_request(request, None, 302, "Found", {}, good))
        self.assertIsNone(handler.redirect_request(request, None, 301, "Moved", {}, good))
        self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, good + "x" * 4096))

    def test_cli_invalid_config_is_typed_and_does_not_contact_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.json"
            path.write_text('{"schema":"wrong"}', encoding="utf-8")
            run = subprocess.run([sys.executable, "-B", str(PILOT / "scripts/run.py"), str(path)],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 3)
        self.assertEqual(json.loads(run.stdout)["status"], "INVALID_INPUT")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "large.json"
            path.write_bytes(b"x" * 8193)
            run = subprocess.run([sys.executable, "-B", str(PILOT / "scripts/run.py"), str(path)],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 3)
        self.assertEqual(json.loads(run.stdout)["status"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
