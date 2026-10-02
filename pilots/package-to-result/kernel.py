"""Offline, structural package-to-result pilot. Supplied records are not witnesses."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import unquote, urlsplit


SCHEMA = "szl.package-result-pilot.v1"
RECEIPT_SCHEMA = "szl.package-result-pilot.receipt.v1"
MAX_BYTES = 128 * 1024
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_TAG_REF = re.compile(r"refs/tags/[A-Za-z0-9._/-]*[A-Za-z0-9._-]\Z")
_IMPORT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_WHEEL = re.compile(r"[A-Za-z0-9_.+-]+\.whl\Z")


class _Missing(ValueError):
    pass


class _Invalid(ValueError):
    pass


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _Invalid("NON_JSON_VALUE") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def loads_strict(data: bytes | str) -> dict:
    """Parse bounded JSON and reject duplicate keys or non-finite numbers."""
    if not isinstance(data, (bytes, str)):
        raise ValueError("JSON_INPUT_TYPE")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_BYTES:
        raise ValueError("JSON_TOO_LARGE")

    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError("NON_FINITE_JSON_NUMBER")

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
    except RecursionError as exc:
        raise ValueError("JSON_TOO_DEEP") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON_ROOT_NOT_OBJECT")
    return value


def _obj(parent: dict, key: str) -> dict:
    if key not in parent:
        raise _Missing(key.upper() + "_MISSING")
    value = parent[key]
    if not isinstance(value, dict):
        raise _Invalid(key.upper() + "_NOT_OBJECT")
    return value


def _list(parent: dict, key: str) -> list:
    if key not in parent:
        raise _Missing(key.upper() + "_MISSING")
    value = parent[key]
    if not isinstance(value, list):
        raise _Invalid(key.upper() + "_NOT_LIST")
    if len(value) > 1000:
        raise _Invalid(key.upper() + "_TOO_MANY")
    return value


def _text(parent: dict, key: str) -> str:
    if key not in parent:
        raise _Missing(key.upper() + "_MISSING")
    value = parent[key]
    if not isinstance(value, str) or not value or len(value) > 512:
        raise _Invalid(key.upper() + "_INVALID")
    return value


def _hex(parent: dict, key: str, pattern: re.Pattern) -> str:
    value = _text(parent, key)
    if not pattern.fullmatch(value):
        raise _Invalid(key.upper() + "_INVALID")
    return value


def _normalized_project(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise _Invalid("PROJECT_NAME_INVALID")
    return re.sub(r"[-_.]+", "-", name).lower()


def _report_item(report: dict, array_key: str, project: str) -> dict:
    matches = []
    for item in _list(report, array_key):
        if not isinstance(item, dict):
            raise _Invalid(array_key.upper() + "_ITEM_INVALID")
        metadata = _obj(item, "metadata")
        name = _text(metadata, "name")
        if _normalized_project(name) == project:
            matches.append(item)
    if not matches:
        raise _Missing(array_key.upper() + "_TARGET_ABSENT")
    if len(matches) != 1:
        raise _Invalid(array_key.upper() + "_TARGET_AMBIGUOUS")
    return matches[0]


def _receipt(status: str, record_sha256: str | None, findings: list[str]) -> dict:
    return {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "record_sha256": record_sha256,
        "findings": sorted(set(findings)),
        "evidence_class": "CONSISTENCY_ON_SUPPLIED_RECORDS",
        "attestation_cryptographically_verified": False,
        "actual_installation_observed": False,
        "analysis_execution_observed": False,
        "scientific_result_validity": "NOT_EVALUATED",
        "network_calls": 0,
        "candidate_code_executed": False,
    }


def check(record: dict) -> dict:
    """Compare bounded supplied observations; never fetch, install or execute code."""
    try:
        if not isinstance(record, dict):
            raise _Invalid("RECORD_NOT_OBJECT")
        raw = _canonical(record)
        if len(raw) > MAX_BYTES:
            raise _Invalid("RECORD_TOO_LARGE")
        record_hash = hashlib.sha256(raw).hexdigest()
        if set(record) != {"schema", "evidence_kind", "expected", "observed"}:
            raise _Invalid("RECORD_FIELDS_INVALID")
        if record["schema"] != SCHEMA:
            raise _Invalid("SCHEMA_UNSUPPORTED")
        if record["evidence_kind"] not in {"synthetic_fixture", "operator_supplied_export"}:
            raise _Invalid("EVIDENCE_KIND_INVALID")

        expected = _obj(record, "expected")
        observed = _obj(record, "observed")
        project = _normalized_project(_text(expected, "project"))
        version = _text(expected, "version")
        filename = _text(expected, "wheel_filename")
        if not _WHEEL.fullmatch(filename):
            raise _Invalid("WHEEL_FILENAME_INVALID")
        parts = filename[:-4].split("-")
        if len(parts) < 5 or _normalized_project(parts[0]) != project or parts[1] != version:
            raise _Invalid("WHEEL_IDENTITY_INVALID")
        wheel_sha = _hex(expected, "wheel_sha256", _HEX64)
        repo = _text(expected, "source_repository")
        if not _REPO.fullmatch(repo) or ".." in repo:
            raise _Invalid("SOURCE_REPOSITORY_INVALID")
        commit = _hex(expected, "source_commit", _HEX40)
        ref = _text(expected, "source_ref")
        if not _TAG_REF.fullmatch(ref) or ".." in ref or "\\" in ref:
            raise _Invalid("SOURCE_REF_INVALID")
        import_name = _text(expected, "import_name")
        if not _IMPORT.fullmatch(import_name):
            raise _Invalid("IMPORT_NAME_INVALID")
        session_id = _text(expected, "session_id")

        pypi = _obj(observed, "pypi")
        provenance = _obj(observed, "provenance")
        github = _obj(observed, "github")
        install_report = _obj(observed, "install_report")
        inspect_report = _obj(observed, "inspect_report")
        invocation = _obj(observed, "install_invocation")
        session = _obj(observed, "session")

        if _text(install_report, "version") != "1":
            raise _Invalid("PIP_INSTALL_REPORT_VERSION_UNSUPPORTED")
        if _text(inspect_report, "version") != "1":
            raise _Invalid("PIP_INSPECT_REPORT_VERSION_UNSUPPORTED")
        installed = _report_item(install_report, "install", project)
        inspected = _report_item(inspect_report, "installed", project)
        downloaded = _obj(installed, "download_info")
        archive = _obj(downloaded, "archive_info")
        hashes = _obj(archive, "hashes")
        url = _text(downloaded, "url")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"https", "file"}
            or (parsed.scheme == "https" and not parsed.hostname)
            or (parsed.scheme == "file" and parsed.netloc not in {"", "localhost"})
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise _Invalid("INSTALL_ARTIFACT_URL_INVALID")
        if unquote(parsed.path.rsplit("/", 1)[-1]) != filename:
            raise _Invalid("INSTALL_ARTIFACT_FILENAME_INVALID")
        if _text(invocation, "tool") != "pip":
            raise _Invalid("INSTALL_TOOL_UNSUPPORTED")
        if type(invocation.get("dry_run")) is not bool or type(invocation.get("exit_code")) is not int:
            raise _Invalid("INSTALL_INVOCATION_INVALID")
        if type(installed.get("is_yanked")) is not bool:
            raise _Invalid("INSTALL_YANKED_FLAG_INVALID")

        install_metadata = _obj(installed, "metadata")
        inspect_metadata = _obj(inspected, "metadata")
        mismatches = []

        def compare(code: str, actual: str, wanted: str) -> None:
            if actual != wanted:
                mismatches.append(code)

        compare("PYPI_PROJECT_MISMATCH", _normalized_project(_text(pypi, "project")), project)
        compare("PYPI_VERSION_MISMATCH", _text(pypi, "version"), version)
        compare("PYPI_FILENAME_MISMATCH", _text(pypi, "filename"), filename)
        compare("PYPI_SHA_MISMATCH", _hex(pypi, "sha256", _HEX64), wheel_sha)
        compare("PUBLISHER_REPOSITORY_MISMATCH", _text(provenance, "publisher_repository"), repo)
        compare("PROVENANCE_SOURCE_MISMATCH", _hex(provenance, "source_digest", _HEX40), commit)
        compare("PROVENANCE_REF_MISMATCH", _text(provenance, "source_ref"), ref)
        compare("PROVENANCE_ARTIFACT_MISMATCH", _hex(provenance, "artifact_sha256", _HEX64), wheel_sha)
        compare("GITHUB_REPOSITORY_MISMATCH", _text(github, "repository"), repo)
        compare("GITHUB_REF_MISMATCH", _text(github, "tag_ref"), ref)
        compare("GITHUB_COMMIT_MISMATCH", _hex(github, "resolved_commit", _HEX40), commit)
        compare("INSTALL_VERSION_MISMATCH", _text(install_metadata, "version"), version)
        compare("INSTALL_SHA_MISMATCH", _hex(hashes, "sha256", _HEX64), wheel_sha)
        compare("INSPECT_VERSION_MISMATCH", _text(inspect_metadata, "version"), version)
        compare("INSPECT_INSTALLER_MISMATCH", _text(inspected, "installer"), "pip")
        compare("SESSION_ID_MISMATCH", _text(session, "session_id"), session_id)
        compare("SESSION_IMPORT_MISMATCH", _text(session, "import_name"), import_name)
        interpreter = _text(invocation, "interpreter_id")
        compare("SESSION_INTERPRETER_MISMATCH", _text(session, "interpreter_id"), interpreter)
        compare("SESSION_INSTALL_REPORT_MISMATCH", _hex(session, "install_report_sha256", _HEX64), _digest(install_report))
        compare("SESSION_ENVIRONMENT_MISMATCH", _hex(session, "environment_report_sha256", _HEX64), _digest(inspect_report))
        _hex(session, "input_sha256", _HEX64)
        _hex(session, "output_sha256", _HEX64)
        if invocation["dry_run"]:
            mismatches.append("INSTALL_DRY_RUN_DECLARED")
        if invocation["exit_code"] != 0:
            mismatches.append("INSTALL_EXIT_NONZERO_DECLARED")
        if installed["is_yanked"]:
            mismatches.append("INSTALL_YANKED_DISTRIBUTION")
        return _receipt(
            "GAP_OR_CONFLICT" if mismatches else "CONSISTENT_SUPPLIED_EVIDENCE",
            record_hash,
            mismatches,
        )
    except _Missing as exc:
        return _receipt("INCOMPLETE", locals().get("record_hash"), [str(exc)])
    except (_Invalid, ValueError, TypeError) as exc:
        return _receipt("REFUSED", locals().get("record_hash"), [str(exc)])
