"""Local POSIX single-use storage for a separately provisioned trusted broker.

This helper does not authorize a permit, run a build, inspect a model, or prove
isolation. The caller must validate its trusted CPU permit and recheck runtime
admission while holding this context's lock, then claim before any execution.
Keep the context open until all execution and receipt collection have ended.

The directory must already exist beneath root-owned, non-writable ancestors.
Only a trusted broker may write it; build payloads must not see this directory.
Claims, including incomplete claims after an error, are never deleted or reused.
No compatibility fallback is provided on Windows or filesystems without fsync.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import uuid

try:
    import fcntl
except ImportError:  # Windows must fail closed, not emulate a weaker lock.
    fcntl = None


CLAIM_SCHEMA = "szl.ssh-cpu-build.execution-claim.v1"
MAX_CLAIM_BYTES = 1024
LOCK_NAME = ".execution.lock"


class ClaimError(ValueError):
    """Admission/storage failed; never infer permission to execute or retry."""


def validate_identity(request_id, permit_id, request_sha256):
    """Check bounded canonical identifiers, without checking authorization."""
    for label, value in (("request_id", request_id), ("permit_id", permit_id)):
        if type(value) is not str or len(value) != 36:
            raise ClaimError(label + " must be a canonical UUID")
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError("noncanonical UUID")
        except ValueError as error:
            raise ClaimError(label + " must be a canonical UUID") from error
    if type(request_sha256) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", request_sha256):
        raise ClaimError("request_sha256 must be a lowercase SHA-256 digest")


def _directory_metadata(fd, final=False):
    metadata = os.fstat(fd)
    mode = stat.S_IMODE(metadata.st_mode)
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
            or mode & 0o7022):
        raise ClaimError("claim directory ancestry must be root-owned and safe")
    if final and mode not in (0o700, 0o750):
        raise ClaimError("claim directory must have mode 0700 or 0750")


def _file_metadata(fd):
    metadata = os.fstat(fd)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600):
        raise ClaimError("claim/lock file must be root-owned, regular, single-link 0600")
    return metadata


def _open_directory(directory):
    """Open each absolute component separately; never follow any symlink."""
    path = Path(directory)
    if (not path.is_absolute() or len(path.parts) < 2 or ".." in path.parts
            or len(path.parts) > 128 or len(os.fsencode(path)) > 4096):
        raise ClaimError("claim directory must be a bounded absolute non-root path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path.anchor, flags)
    try:
        _directory_metadata(descriptor)
        for index, component in enumerate(path.parts[1:], start=1):
            successor = os.open(component, flags, dir_fd=descriptor)
            try:
                _directory_metadata(successor, final=index == len(path.parts) - 1)
            except BaseException:
                os.close(successor)
                raise
            os.close(descriptor)
            descriptor = successor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class ClaimStore:
    """Hold a nonblocking host-wide lock and durably consume CPU permit IDs.

    All brokers for this host must use the same trusted directory. The lock is
    advisory, not a provider lease: other software can still use CPU/GPU. Only
    the process which entered this context may claim or explicitly unlock it.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self._directory_fd = None
        self._lock_fd = None
        self._pid = None

    def __enter__(self):
        if self._directory_fd is not None:
            raise ClaimError("claim context is already entered")
        required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
        if (os.name != "posix" or fcntl is None
                or any(not hasattr(os, name) for name in required)
                or os.open not in os.supports_dir_fd):
            raise ClaimError("trusted POSIX descriptor and flock support required")
        directory_fd = None
        lock_fd = None
        try:
            directory_fd = _open_directory(self.directory)
            flags = (os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                     | os.O_CLOEXEC | os.O_NONBLOCK)
            lock_fd = os.open(LOCK_NAME, flags, 0o600, dir_fd=directory_fd)
            if _file_metadata(lock_fd).st_size != 0:
                raise ClaimError("lock file must be empty")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(lock_fd)
            os.fsync(directory_fd)
            self._directory_fd = directory_fd
            self._lock_fd = lock_fd
            self._pid = os.getpid()
            return self
        except BaseException as error:
            if lock_fd is not None:
                os.close(lock_fd)
            if directory_fd is not None:
                os.close(directory_fd)
            if isinstance(error, (ClaimError, KeyboardInterrupt, SystemExit)):
                raise
            raise ClaimError("cannot acquire trusted claim store; execution denied") from error

    def claim(self, request_id: str, permit_id: str, request_sha256: str):
        """Durably consume one permit, returning its unsigned claim receipt.

        A different request/run cannot reclaim a consumed permit. Any error
        after O_EXCL creation leaves the file in place, even when incomplete.
        Storage failures must not trigger inference, execution, or retries.
        """
        validate_identity(request_id, permit_id, request_sha256)
        if self._lock_fd is None or self._pid != os.getpid():
            raise ClaimError("claim requires this process's held context lock")
        _directory_metadata(self._directory_fd, final=True)
        _file_metadata(self._lock_fd)
        receipt = {
            "schema": CLAIM_SCHEMA,
            "claimed_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="microseconds").replace("+00:00", "Z"),
            "request_id": request_id,
            "permit_id": permit_id,
            "request_sha256": request_sha256,
            "evidence_class": "MEASURED",
            "signed": False,
            "independent_witness": False,
        }
        encoded = (json.dumps(receipt, sort_keys=True, separators=(",", ":"))
                   + "\n").encode("ascii")
        if len(encoded) > MAX_CLAIM_BYTES:
            raise ClaimError("claim receipt exceeds byte limit")
        filename = permit_id + ".execution-claim.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(filename, flags, 0o600, dir_fd=self._directory_fd)
        except FileExistsError as error:
            raise ClaimError("permit already claimed; do not retry or remove claim") from error
        except OSError as error:
            raise ClaimError("cannot create durable claim; execution denied") from error
        try:
            try:
                _file_metadata(descriptor)
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise ClaimError("incomplete claim write")
                    remaining = remaining[written:]
            finally:
                # Persist the entry even after a failed/partial write. A failed
                # fsync is a hard denial, never a reason to unlink and retry.
                try:
                    os.fsync(descriptor)
                finally:
                    os.fsync(self._directory_fd)
        except OSError as error:
            raise ClaimError("claim persistence failed; preserve claim and deny execution") from error
        finally:
            os.close(descriptor)
        return receipt

    def __exit__(self, _kind, _value, _traceback):
        lock_fd, directory_fd, owner_pid = self._lock_fd, self._directory_fd, self._pid
        self._lock_fd = self._directory_fd = self._pid = None
        try:
            if lock_fd is not None:
                try:
                    if owner_pid == os.getpid():
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        # Neither lock nor claim files are ever removed.
        return False
