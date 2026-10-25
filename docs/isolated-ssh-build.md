# Isolated SSH CPU build admission

## Status and trust boundary

**BLOCKED for live VM CI execution.** This change provides the GitHub controller,
bounded SSH client, and strict request/receipt contract. It does not install or
qualify a VM broker, provision an account or key, create an owner permit, or
establish that isolation exists. Hosted contract tests are offline tests, not a
remote-build or deployment claim. No consumed GPU grant is accepted or reused.

The manually verified science build recipe is useful, but its CPU affinity,
environmental GPU hiding, and per-process limits are not a CI security boundary.
Do not register a general-purpose self-hosted Actions runner on the live-model
VM, and never copy a workstation or administrator SSH key into this workflow.

The reviewed design uses a GitHub-hosted controller and a separately provisioned
forced-command CPU broker. GitHub warns that public-repository code can compromise
self-hosted runners; see its [secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners).
[Bubblewrap](https://github.com/containers/bubblewrap) provides useful namespace
primitives, but is not by itself a complete sandbox policy or aggregate resource
boundary. This design does not claim a new isolation primitive or a separate
kernel. A dedicated VM remains the stronger boundary for hostile workloads.

## GitHub side

- `ssh-build-contract.yml` runs offline tests on GitHub-hosted Linux and Windows.
  PR code receives no model-host secrets and never runs on that host.
- `isolated-ssh-build.yml` is manual-only, repository-ID-bound, `main`-only,
  first-attempt-only, read-only-token, and action-SHA-pinned. It cannot execute
  an arbitrary caller-supplied shell command, branch, or repository.
- The `massed-cpu-build` environment must be explicitly provisioned and restricted
  to protected `main` before activation. Creating it automatically by dispatching
  this workflow is not a substitute for configuring protection.
- `SZL_CPU_CI_ENABLED` must remain unset until all qualification gates below pass.
  Missing configuration is BLOCKED, not a skipped remote success.
- A fresh CPU permit ID is required for each execution. GitHub concurrency does
  not replace a VM-wide lock or the owner's workload reservation. Automatic
  retries, cancellation of other jobs, and GPU inference are not implemented.
- Keep the dedicated private credential only in the protected environment secret
  `SZL_CPU_SSH_KEY`. Its corresponding root-owned authorized-key entry must force
  the fixed broker command and prohibit shell, PTY, forwarding, agent forwarding,
  SCP, and SFTP. Do not grant administrator, sudo, Docker, or model-account access.
- Configure environment variables `SZL_CPU_SSH_HOST`, `SZL_CPU_SSH_PORT`,
  `SZL_CPU_SSH_USER`, and independently verified `SZL_CPU_KNOWN_HOSTS`. Host-key
  scanning during the job is not authentication and must not replace pinning.

## Owner-provisioned broker requirements (not installed by this change)

The SSH entrypoint accepts only literal `szl-cpu-build-v1` and a bounded strict
JSON request. Its trusted executable, policy, and permit directory must be
outside the payload's writable tree. The CI identity cannot author permits,
replace the broker, or modify its isolation profile.

Before dispatch, reserve a fresh CPU permit UUID without granting execution.
Dispatch must pause at a preconfigured protected-environment approval gate:
GitHub allocates the run ID only after dispatch. While that run is waiting, the
owner binds a single-use CPU permit to the resulting exact request digest:
repository, source/controller SHA, workflow reference, run ID, run attempt,
request/permit IDs, approved window, host/boot/workload identity, and resource
profile. Only then approve that exact waiting run. An environment name without
an enforced approval gate does not implement this sequence; leave activation
disabled if that gate is unavailable. A GPU evaluation grant is a different authority and must be
rejected. Use a server-wide exclusive lock and durable exclusive claim; a lost
client connection never permits replay. Status/retrieval cannot execute a build.
GitHub run identity supplied over SSH is a declaration unless separately verified.

The fixed CPU profile is one CPU, 1 GiB aggregate memory, zero swap allowance,
64 tasks, a 420-second execution deadline, and at most 16 MiB of returned files.
The host must enforce aggregate cgroup limits and bounded scratch/log storage,
not just report those values or set per-process resource limits. No fallback to
the old general-shell runner is permitted.

Build code must have separate PID/mount/user/IPC/network namespaces, a read-only
reviewed runtime and exact source, minimal private devices, dropped capabilities,
no privilege escalation, and an appropriate seccomp policy. It must not see GPU
devices, host processes, the model endpoint, home/model paths, credentials,
runtime sockets, or the host network. Staging is a separate trusted operation;
the build performs no dependency installation or network fetch. Only the build's
own cgroup may be terminated on its deadline. Never stop the model or another job.

The broker must verify actual isolation and cgroup readback, check the approved
model identity/workload before and after execution, and collect bounded regular
files without following links. Its receipt must live outside payload control.
Archive outputs are retained as opaque bytes by the CI client, not extracted or
executed. An unsigned host response is DECLARED remote evidence; local transport
and byte consistency can be MEASURED. Hashes do not prove independent witness,
kernel isolation, scientific truth, or uninterrupted model latency.

## Activation and qualification gates

1. Obtain owner approval for a non-overlapping provisioning/CPU-test window,
   exact account/credential scope, and resource/storage ceilings. No GPU permit
   authorizes this work.
2. Provision the dedicated forced-command identity and audited broker; verify
   filesystem ownership, SSH restrictions, host-key pin, and server-wide lock.
3. Qualify namespace/device/network denial and aggregate cgroup enforcement with
   bounded negative controls. Verify host model paths and loopback are unreachable,
   fork/memory/output/deadline controls stop only the test job, and no job remains.
4. Provision and read back the protected GitHub environment, enforced approval
   gate, and dedicated secret. Verify that an unapproved job cannot start.
   Preserve all existing protected-main and exact-head checks. Solo owner approval
   is an authorization step, not an independent witness.
5. Reserve a new CPU permit UUID and dispatch into the approval wait. Bind the
   actual run ID and request digest in the owner's permit before approving the
   exact run. Run one reviewed immutable source build, retain
   failed as well as successful evidence, independently verify returned hashes,
   and read back the unchanged model identity. Do not auto-retry a failed permit.
6. Only then describe the path as operational for that qualified CPU profile and
   source. Training, inference, arbitrary workloads, and publication remain separate.

The SSH client refuses broad administrator usernames and missing configuration.
It preserves a local receipt and bounded raw response on failure. A PASS in the
remote receipt means its declared fixed build steps passed; it is not model or
production qualification. The canonical scientific and model gates stay unchanged.
