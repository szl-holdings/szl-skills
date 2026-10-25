"""Reconstruct a CPU request from supplied waiting GitHub run metadata.

This is an offline planning aid, NOT a permit issuer or an authenticity check.
It performs no network call, approval, claim, provisioning, or execution. A
trusted operator must independently authenticate the run and configure the
environment approval gate before using its digest in an owner-issued permit.
"""
import argparse
import os
from pathlib import Path
import stat
import sys

try:
    from .ssh_ci_client import REPOSITORY, WORKFLOW_REF, build_request
    from .ssh_ci_contract import canonical, sha256, strict_json_loads
except ImportError:
    from ssh_ci_client import REPOSITORY, WORKFLOW_REF, build_request
    from ssh_ci_contract import canonical, sha256, strict_json_loads


REPOSITORY_ID = 1389873753
WORKFLOW_PATH = '.github/workflows/isolated-ssh-build.yml'
MAX_METADATA_BYTES = 128 * 1024


def read_metadata(path):
    """Read one bounded regular input file, never a pipe or symlink."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_METADATA_BYTES:
        raise ValueError('Metadata must be a bounded regular file')
    flags = os.O_RDONLY
    for option in ('O_BINARY', 'O_NOFOLLOW', 'O_NONBLOCK', 'O_CLOEXEC'):
        flags |= getattr(os, option, 0)
    descriptor = os.open(path, flags)
    try:
        after = os.fstat(descriptor)
        if (not stat.S_ISREG(after.st_mode) or after.st_size > MAX_METADATA_BYTES
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            raise ValueError('Metadata file changed or is not regular')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            return stream.read(MAX_METADATA_BYTES + 1)
    finally:
        os.close(descriptor)


def plan_request(run_metadata, permit_id):
    """Return a DECLARED plan, preserving no arbitrary metadata or secrets."""
    if isinstance(run_metadata, dict):
        raw = canonical(run_metadata)
    else:
        raw = run_metadata
    run = strict_json_loads(raw, MAX_METADATA_BYTES)
    if type(run) is not dict:
        raise ValueError('Run metadata must be an object')
    repository = run.get('repository')
    if (type(repository) is not dict
            or type(repository.get('id')) is not int
            or repository['id'] != REPOSITORY_ID
            or repository.get('full_name') != REPOSITORY):
        raise ValueError('Repository identity mismatch')
    for field, expected in (('event', 'workflow_dispatch'), ('head_branch', 'main'),
                            ('path', WORKFLOW_PATH), ('status', 'waiting')):
        if type(run.get(field)) is not str or run[field] != expected:
            raise ValueError('Unapproved run metadata: ' + field)
    if type(run.get('run_attempt')) is not int or run['run_attempt'] != 1:
        raise ValueError('Only the first run attempt can be planned')
    if type(run.get('id')) is not int or not 1 <= run['id'] < (1 << 63):
        raise ValueError('Run ID must be an in-range integer')
    request = build_request({
        'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_RUN_ATTEMPT': '1',
        'GITHUB_WORKFLOW_REF': WORKFLOW_REF, 'GITHUB_RUN_ID': str(run['id']),
        'GITHUB_SHA': run.get('head_sha'),
    }, permit_id)
    return {
        'schema': 'szl.ssh-cpu-build.request-plan.v1', 'evidence_class': 'DECLARED',
        'request': request, 'request_sha256': sha256(canonical(request)),
        'metadata_sha256': sha256(canonical(run)),
        'owner_authenticated': False, 'environment_protection_verified': False,
        'permit_issued': False, 'execution_authorized': False,
        'remote_calls': 0, 'claims_written': 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-metadata', type=Path, required=True)
    parser.add_argument('--permit-id', required=True)
    args = parser.parse_args(argv)
    try:
        raw = read_metadata(args.run_metadata)
        plan = plan_request(raw, args.permit_id)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print('CPU request planning BLOCKED: ' + type(exc).__name__, file=sys.stderr)
        return 1
    print(canonical(plan).decode('utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
