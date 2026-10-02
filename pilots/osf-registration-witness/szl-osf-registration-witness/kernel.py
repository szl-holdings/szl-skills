# SPDX-License-Identifier: Apache-2.0
"""Bounded public OSF file and DOI witness; never writes to a provider."""

import hashlib
import http.client
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

SCHEMA = "szl.osf-registration-witness.v1"
MAX_METADATA_BYTES = 262144
MAX_FILE_BYTES = 1048576
REGISTRATION_ID = re.compile(r"[a-z0-9]{5,16}\Z")
FILE_ID = re.compile(r"[a-f0-9]{24}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
DOI = re.compile(r"10\.[0-9]{4,9}/[A-Za-z0-9._;()/:-]{1,200}\Z")
PLAN_KEYS = {"id", "version", "frozen_at", "hypotheses", "families", "stopping_rule", "scheduled_attempts"}


class Unavailable(Exception):
    """A bounded public read did not return usable evidence."""


class InvalidEvidence(Exception):
    """A provider read returned malformed or contradictory evidence."""


class _BoundedRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 2

    def __init__(self, expected_sha256=None):
        self.expected_sha256 = expected_sha256

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Only file reads may follow OSF's current signed GCS object redirect.
        # A new backend/host must be reviewed rather than silently trusted.
        if self.expected_sha256 is None or code not in (302, 307, 308) or len(newurl) > 4096:
            return None
        try:
            target = urllib.parse.urlsplit(newurl)
        except ValueError:
            return None
        path = r"/cos-osf-prod-files-us-east1/" + self.expected_sha256
        if (target.scheme != "https" or target.netloc != "storage.googleapis.com"
                or target.fragment or not re.fullmatch(path, target.path)):
            return None
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_bytes(url, limit, expected_sha256=None):
    """GET only caller-constructed HTTPS URLs with a bounded file redirect."""
    if not url.startswith(("https://api.osf.io/", "https://files.osf.io/", "https://api.datacite.org/")):
        raise ValueError("Unapproved provider URL")
    request = urllib.request.Request(url, headers={"User-Agent": "szl-osf-registration-witness/0.1", "Accept": "application/json"})
    try:
        with urllib.request.build_opener(_BoundedRedirect(expected_sha256)).open(request, timeout=15) as response:
            if response.status != 200:
                raise Unavailable("Provider did not return HTTP 200")
            length = response.headers.get("Content-Length")
            if length is not None and (len(length) > 20 or not length.isascii() or not length.isdigit() or int(length) > limit):
                raise Unavailable("Provider response exceeds byte limit")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise Unavailable("Provider response exceeds byte limit")
            return data
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as error:
        raise Unavailable("Provider read unavailable") from error


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Nonfinite JSON number")
    return parsed


def _json(raw, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise InvalidEvidence("Response is not bounded bytes")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_float=_finite_float,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise InvalidEvidence("Invalid provider JSON") from error


def _object(value):
    if not isinstance(value, dict):
        raise InvalidEvidence("Expected JSON object")
    return value


def _at(value, *keys):
    for key in keys:
        value = _object(value).get(key)
    return value


def _registration_relation(record, name, registration_id, required=True):
    relations = _at(record, "relationships")
    if name not in relations:
        if required:
            raise InvalidEvidence("Registration relationship missing")
        return
    relation = _object(relations[name])
    if "data" not in relation and "links" not in relation:
        raise InvalidEvidence("Registration relationship empty")
    if "data" in relation:
        data = _object(relation["data"])
        if data.get("id") != registration_id or data.get("type") not in ("registrations", "nodes"):
            raise InvalidEvidence("Registration relationship data mismatch")
    if "links" in relation:
        href = _at(relation, "links", "related", "href")
        permitted = {"https://api.osf.io/v2/registrations/" + registration_id + "/",
                     "https://api.osf.io/v2/nodes/" + registration_id + "/"}
        if href not in permitted:
            raise InvalidEvidence("Registration relationship URL mismatch")


def _date(value):
    if not isinstance(value, str) or len(value) > 40:
        raise InvalidEvidence("Registration date missing or malformed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidEvidence("Registration date malformed") from error
    # OSF has returned timestamps without an offset. Retain the raw value and
    # never use it to infer ordering against a data-access declaration.
    return value, "EXPLICIT" if parsed.tzinfo is not None else "UNSPECIFIED"


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _config(value):
    if not isinstance(value, dict) or set(value) != {"schema", "registration_id", "file_id", "expected_sha256", "expected_path", "artifact_kind"}:
        raise ValueError("Config must have schema, registration_id, file_id, expected_sha256, expected_path, artifact_kind")
    if value["schema"] != SCHEMA:
        raise ValueError("Unsupported witness schema")
    for key, pattern in (("registration_id", REGISTRATION_ID), ("file_id", FILE_ID), ("expected_sha256", SHA256)):
        if not isinstance(value[key], str) or not pattern.fullmatch(value[key]):
            raise ValueError("Invalid " + key)
    path = value["expected_path"]
    if (not isinstance(path, str) or not 1 < len(path) <= 512 or not path.startswith("/")
            or any(part in ("", ".", "..") for part in path[1:].split("/"))
            or any(ord(char) < 32 for char in path)):
        raise ValueError("Invalid expected_path")
    if value["artifact_kind"] not in ("canonical_analysis_plan_v1", "opaque_file"):
        raise ValueError("Unsupported artifact_kind")
    return value


def _result(status, config, findings, **evidence):
    result = {"schema": "szl.osf-registration-witness-report.v1", "status": status,
              "registration_id": config["registration_id"], "file_id": config["file_id"],
              "expected_sha256": config["expected_sha256"], "findings": findings,
              "expected_pin_provenance": "CALLER_SUPPLIED_UNVERIFIED",
              "pre_data_timing": "NOT_VERIFIED", "scientific_validity": "NOT_EVALUATED",
              "schema_response_revision": "NOT_VERIFIED", "provider_write_performed": False}
    result.update(evidence)
    return result


def verify(config, fetch=fetch_bytes):
    """Check a public registration, OSF-hosted file bytes, and DataCite DOI."""
    config = _config(config)
    rid, fid, expected = (config[key] for key in ("registration_id", "file_id", "expected_sha256"))
    base = "https://api.osf.io/v2/registrations/" + rid + "/"
    stage = "registration"
    try:
        registration = _json(fetch(base, MAX_METADATA_BYTES), MAX_METADATA_BYTES)
        data = _at(registration, "data")
        if _at(data, "id") != rid or _at(data, "type") != "registrations":
            raise InvalidEvidence("Registration identity mismatch")
        attributes = _at(data, "attributes")
        registered_at, timezone_state = _date(_at(attributes, "date_registered"))
        if _at(attributes, "registration") is not True or _at(attributes, "public") is not True:
            return _result("BLOCKED", config, ["NOT_PUBLIC_REGISTRATION"])
        for key in ("withdrawn", "pending_withdrawal", "embargoed", "pending_registration_approval", "archiving"):
            if _at(attributes, key) is not False:
                return _result("BLOCKED", config, ["REGISTRATION_NOT_ACTIVE:" + key])

        stage = "identifiers"
        identifiers = _json(fetch(base + "identifiers/", MAX_METADATA_BYTES), MAX_METADATA_BYTES)
        rows = _at(identifiers, "data")
        if not isinstance(rows, list) or len(rows) > 16:
            raise InvalidEvidence("Identifier list malformed or unbounded")
        links = _at(identifiers, "links")
        if ("next" not in links or links["next"] is not None
                or type(_at(links, "meta", "total")) is not int
                or _at(links, "meta", "total") != len(rows)):
            return _result("UNAVAILABLE", config, ["IDENTIFIER_LIST_INCOMPLETE"])
        for item in rows:
            if _at(item, "type") != "identifiers":
                raise InvalidEvidence("Identifier type mismatch")
            _registration_relation(item, "referent", rid)
        dois = [_at(item, "attributes", "value") for item in rows if _at(item, "attributes", "category") == "doi"]
        if len(dois) != 1 or not isinstance(dois[0], str) or not DOI.fullmatch(dois[0]):
            return _result("BLOCKED", config, ["DOI_MISSING_OR_AMBIGUOUS"])
        doi = dois[0]

        stage = "datacite"
        doi_url = "https://api.datacite.org/dois/" + urllib.parse.quote(doi, safe="/")
        datacite = _json(fetch(doi_url, MAX_METADATA_BYTES), MAX_METADATA_BYTES)
        dc_data = _at(datacite, "data")
        if _at(dc_data, "type") != "dois" or not isinstance(_at(dc_data, "id"), str) or _at(dc_data, "id").casefold() != doi.casefold():
            raise InvalidEvidence("DataCite DOI identity mismatch")
        dc = _at(dc_data, "attributes")
        if (_at(dc, "doi") or "").casefold() != doi.casefold() or _at(dc, "state") != "findable" or _at(dc, "url") != "https://osf.io/" + rid + "/":
            return _result("BLOCKED", config, ["DOI_RECORD_MISMATCH"], doi=doi)

        stage = "file_metadata"
        # Registration-scoped detail establishes membership; global /files/{id}/
        # plus a display HTML URL is insufficient for that assertion. The OSF
        # Files API detail URL is an exception to canonical trailing slashes.
        file_record = _json(fetch(base + "files/osfstorage/" + fid, MAX_METADATA_BYTES), MAX_METADATA_BYTES)
        file_data = _at(file_record, "data")
        if _at(file_data, "id") != fid or _at(file_data, "type") != "files" or _at(file_data, "attributes", "kind") != "file":
            raise InvalidEvidence("File identity mismatch")
        _registration_relation(file_data, "node", rid)
        _registration_relation(file_data, "target", rid, required=False)
        file_attrs = _at(file_data, "attributes")
        if _at(file_attrs, "materialized_path") != config["expected_path"]:
            return _result("BLOCKED", config, ["FILE_PATH_MISMATCH"], doi=doi)
        size, version = _at(file_attrs, "size"), _at(file_attrs, "current_version")
        if type(size) is not int or not 0 < size <= MAX_FILE_BYTES or type(version) is not int or version < 1:
            raise InvalidEvidence("OSF file size or version missing or invalid")
        html = _at(file_data, "links", "html")
        if html != "https://osf.io/" + rid + "/files/osfstorage/" + fid:
            return _result("BLOCKED", config, ["FILE_NOT_BOUND_TO_REGISTRATION"], doi=doi)
        osf_sha = _at(file_attrs, "extra", "hashes", "sha256")
        if not isinstance(osf_sha, str) or not SHA256.fullmatch(osf_sha):
            raise InvalidEvidence("OSF file digest missing or malformed")
        if osf_sha != expected:
            return _result("BLOCKED", config, ["OSF_DIGEST_MISMATCH"], doi=doi, osf_sha256=osf_sha)

        stage = "file_bytes"
        file_url = "https://files.osf.io/v1/resources/" + rid + "/providers/osfstorage/" + fid
        raw = fetch(file_url, MAX_FILE_BYTES, expected)
        if not isinstance(raw, bytes) or len(raw) != size or _digest(raw) != expected:
            return _result("BLOCKED", config, ["FILE_BYTES_MISMATCH"], doi=doi, osf_sha256=osf_sha)
        if config["artifact_kind"] == "canonical_analysis_plan_v1":
            plan = _json(raw, MAX_FILE_BYTES)
            if not isinstance(plan, dict) or set(plan) != PLAN_KEYS:
                return _result("BLOCKED", config, ["PLAN_SHAPE_MISMATCH"], doi=doi)
            try:
                canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (ValueError, UnicodeError, OverflowError, RecursionError) as error:
                raise InvalidEvidence("Plan canonicalization failed") from error
            if raw != canonical:
                return _result("BLOCKED", config, ["PLAN_NOT_CANONICAL"], doi=doi)
            return _result("PUBLIC_PLAN_BYTES_MATCHED", config, [], doi=doi, registered_at=registered_at,
                           registered_at_timezone=timezone_state, osf_sha256=osf_sha, file_version=version,
                           downloaded_sha256=_digest(raw), plan_binding="MATCHED_CANONICAL_SHA256")
        return _result("PUBLIC_FILE_BYTES_MATCHED", config, [], doi=doi, registered_at=registered_at,
                       registered_at_timezone=timezone_state, osf_sha256=osf_sha, file_version=version,
                       downloaded_sha256=_digest(raw), plan_binding="NOT_EVALUATED")
    except (Unavailable, urllib.error.URLError, TimeoutError, OSError):
        return _result("UNAVAILABLE", config, ["PUBLIC_READ_UNAVAILABLE:" + stage])
    except (InvalidEvidence, TypeError, AttributeError):
        return _result("BLOCKED", config, ["MALFORMED_PROVIDER_EVIDENCE:" + stage])
