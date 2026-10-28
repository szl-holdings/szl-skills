"""Owner SSH-signature binding for one CPU permit and its owner window.

The owner signs one canonical bundle with ``ssh-keygen -Y sign`` under a fixed
namespace. The broker verifies those exact bytes against a root-owned
allowed-signers file before the executor sees the permit or window. Successful
verification authenticates possession of an allowed owner key only: it does not
prove a provider lease, a budget balance, isolation, or that the permit is wise.
The executor still validates every field, freshness, and single use.
"""

import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from tools.ssh_ci_contract import ContractError, canonical, strict_json_loads, validate_request
from tools.ssh_ci_executor import OwnerPermit


BUNDLE_SCHEMA = "szl.ssh-cpu-build.signed-permit.v1"
NAMESPACE = "szl-cpu-permit-v1"
SSH_KEYGEN = "/usr/bin/ssh-keygen"
MAX_BUNDLE_BYTES = 16384
MAX_SIGNATURE_BYTES = 8192
MAX_ALLOWED_SIGNERS_BYTES = 8192
VERIFY_TIMEOUT_SECONDS = 15
PRINCIPAL = r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}"
BUNDLE_SUFFIX = ".permit.json"
SIGNATURE_SUFFIX = ".permit.json.sig"


def _principal(value):
    if type(value) is not str or re.fullmatch(PRINCIPAL, value) is None:
        raise ContractError("owner principal must be a bounded plain identifier")
    return value


def parse_bundle(data):
    """Strictly parse canonical signed-bundle bytes; no field authorization."""
    value = strict_json_loads(data, MAX_BUNDLE_BYTES)
    if type(value) is not dict or set(value) != {"schema", "permit", "window"}:
        raise ContractError("signed bundle has missing or unknown fields")
    if type(value["schema"]) is not str or value["schema"] != BUNDLE_SCHEMA:
        raise ContractError("signed bundle schema does not match")
    if type(value["permit"]) is not dict or type(value["window"]) is not dict:
        raise ContractError("signed bundle permit and window must be objects")
    if canonical(value) != (data.encode("utf-8") if type(data) is str else data):
        # One byte representation per authorization; no re-encoding ambiguity.
        raise ContractError("signed bundle must be canonical JSON bytes")
    return value


def bundle(permit, window):
    """Return the exact canonical bytes the owner signs."""
    data = canonical({"schema": BUNDLE_SCHEMA, "permit": permit, "window": window})
    if len(data) > MAX_BUNDLE_BYTES:
        raise ContractError("signed bundle exceeds byte limit")
    parse_bundle(data)
    return data


def _safe_directory(fd):
    metadata = os.fstat(fd)
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022):
        raise ContractError("protected path ancestry must be root-owned and not writable")


def read_protected(path, limit):
    """Read one bounded root-owned regular file without following any symlink."""
    path = Path(path)
    if (os.name != "posix" or not path.is_absolute() or ".." in path.parts
            or len(path.parts) < 2 or len(path.parts) > 64):
        raise ContractError("protected file must be a bounded absolute POSIX path")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path.anchor, directory_flags)
    try:
        _safe_directory(descriptor)
        for component in path.parts[1:-1]:
            successor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = successor
            _safe_directory(descriptor)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        handle = os.open(path.parts[-1], flags, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    try:
        metadata = os.fstat(handle)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) & 0o022
                or metadata.st_size > limit):
            raise ContractError("protected file must be a bounded root-owned regular file")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(handle, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise ContractError("protected file exceeds byte limit")
        return data
    finally:
        os.close(handle)


def verify_bundle(data, signature, allowed_signers, principal, *,
                  ssh_keygen=SSH_KEYGEN, run=subprocess.run):
    """Return parsed bundle bytes only when ``ssh-keygen -Y verify`` accepts them.

    ``allowed_signers`` is a path the caller has already established as trusted
    (for example via :func:`read_protected`). The signature is written to a
    private temporary file because ssh-keygen accepts it only by path.
    """
    principal = _principal(principal)
    if type(data) is not bytes or not 0 < len(data) <= MAX_BUNDLE_BYTES:
        raise ContractError("signed bundle must be bounded bytes")
    if type(signature) is not bytes or not 0 < len(signature) <= MAX_SIGNATURE_BYTES:
        raise ContractError("signature must be bounded bytes")
    if not signature.startswith(b"-----BEGIN SSH SIGNATURE-----\n"):
        raise ContractError("signature is not an armored SSH signature")
    value = parse_bundle(data)
    with tempfile.TemporaryDirectory(prefix="szl-permit-verify-") as scratch:
        signature_path = Path(scratch) / "bundle.sig"
        descriptor = os.open(signature_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(signature)
        try:
            completed = run(
                [ssh_keygen, "-Y", "verify", "-f", str(allowed_signers), "-I", principal,
                 "-n", NAMESPACE, "-s", str(signature_path)],
                input=data, capture_output=True, timeout=VERIFY_TIMEOUT_SECONDS,
                shell=False, check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ContractError("owner signature verifier unavailable") from error
    expected = ('Good "%s" signature for %s ' % (NAMESPACE, principal)).encode("ascii")
    if completed.returncode != 0 or not completed.stdout.startswith(expected):
        raise ContractError("owner signature rejected")
    return value


def make_permit_loader(permit_dir, allowed_signers, principal, *,
                       ssh_keygen=SSH_KEYGEN, run=subprocess.run, reader=read_protected):
    """Return an executor ``permit_loader`` for one protected permit spool.

    The loader reads ``<permit_id>.permit.json`` and its ``.sig`` from the
    root-owned spool, verifies the owner signature, and returns an
    ``OwnerPermit`` whose ``owner_authenticated`` flag reflects only that check.
    """
    principal = _principal(principal)
    permit_dir = Path(permit_dir)
    allowed_signers = Path(allowed_signers)

    def load(request):
        request = validate_request(request)
        permit_id = request["permit_id"]
        reader(allowed_signers, MAX_ALLOWED_SIGNERS_BYTES)
        data = reader(permit_dir / (permit_id + BUNDLE_SUFFIX), MAX_BUNDLE_BYTES)
        signature = reader(permit_dir / (permit_id + SIGNATURE_SUFFIX), MAX_SIGNATURE_BYTES)
        value = verify_bundle(data, signature, allowed_signers, principal,
                              ssh_keygen=ssh_keygen, run=run)
        for part in ("permit", "window"):
            if value[part].get("permit_id") != permit_id:
                raise ContractError("signed " + part + " is bound to another permit")
        return OwnerPermit(permit=value["permit"], window=value["window"],
                           owner_authenticated=True)

    return load
