# Isolated SSH CPU build admission

## Status and trust boundary

**Source-complete; live VM execution BLOCKED until the owner provisions,
qualifies, and configures it.** This repository now contains every component of
the path: the GitHub controller and bounded SSH client, the forced-command relay,
the root broker, the transient-unit sandbox, the fixed step runner, the runtime
observer, owner-signed permits, provisioning, qualification, and the owner's
one-command issue flow. None of it is installed on the VM by merging. A merge,
green hosted tests, or a provider page that says "running" is not a lease, a
qualification, or permission to run a workload. No consumed GPU grant is
accepted or reused, and no step here sends a GPU, inference, or training request.

The isolation is systemd/kernel namespace and cgroup isolation **inside the live
model VM**. It is not a separate kernel or VM, and a kernel or systemd escape
would reach the host. A dedicated CPU VM remains the stronger boundary for
hostile workloads; this lane builds only reviewed, protected-`main` source.
Do not register a general-purpose self-hosted Actions runner on the live-model
VM, and never copy a workstation or administrator SSH key into this workflow.
GitHub warns that public-repository code can compromise self-hosted runners; see
its [secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners).

Evidence classes stay explicit. The broker's response is DECLARED remote
evidence: unsigned, produced on the host it describes, and not an independent
witness. Local transport, byte, and hash consistency are MEASURED by the client.
GitHub run identity sent over SSH is a declaration bound by the owner's signed
permit, not separately authenticated by the VM. Provisioning and qualification
receipts are MEASURED on that host only. None of these proves kernel isolation in
general, scientific truth, model quality, or uninterrupted model latency.

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

- The run title is `CPU build permit <permit UUID>` so the owner tool can find
  the exact waiting run; the title is display-only and never executed.

## VM side (implemented here, installed only by the owner)

| Component | Runs as | Role |
|---|---|---|
| `tools/ssh_ci_relay.py` | `szl-ci` (sshd forced command) | Admits only the literal `szl-cpu-build-v1` and one <=4096-byte request; relays to the broker socket; strips one status byte |
| `szl-cpu-broker.socket` / `@.service` | root, one instance per connection | `MaxConnections=1`; the socket is `root:szl-ci 0660` |
| `tools/ssh_ci_broker.py` | root | Strict request, signed-permit loader, observer, durable claim, source staging, sandbox, bounded collection, contract self-check, broker receipt |
| `tools/ssh_ci_owner_signature.py` | root (verify) / owner (sign) | `ssh-keygen -Y` signature over one canonical permit+window bundle, namespace `szl-cpu-permit-v1`, root-owned `allowed_signers` |
| `tools/ssh_ci_observer.py` | root or admin | Hostname, boot ID, `nvidia-smi` compute PIDs with `/proc` start ticks, running build units; any read failure denies |
| `tools/ssh_ci_sandbox.py` | root, via `systemd-run` | One transient service per build with the fixed properties below; `GO` only after readback |
| `tools/ssh_ci_step_runner.py` | `szl-build` inside the unit | Waits for `GO`, copies read-only `/src` to private scratch, requires the exact clean commit, runs the four fixed steps with 1 MiB per stream |
| `tools/ssh_ci_provision.py` | root, owner-run | `plan` (read-only) / `apply` / `uninstall`; writes a provisioning receipt |
| `tools/ssh_ci_qualify.py` | root, owner-run | Negative controls and an optional real-build rehearsal; writes a qualification receipt |
| `tools/ssh_ci_owner_issue.py` | owner workstation | Dispatch, bind, observe, sign, install, approve one run, collect evidence |

**Sandbox properties (read back before `GO`).** One aggregate cgroup:
`CPUQuota=100%`, `MemoryMax=1 GiB`, `MemorySwapMax=0`, `TasksMax=64`,
`RuntimeMaxSec=420`, `OOMPolicy=kill`, `KillMode=control-group`. Namespaces:
`PrivateNetwork` (plus `IPAddressDeny=any` and `RestrictAddressFamilies=AF_UNIX`),
`PrivateUsers`, `PrivateIPC`, mount namespace with `ProtectSystem=strict`,
`ProtectHome`, read-only `/src`, writable `/out`, and tmpfs `/tmp`, `/var/tmp`,
and `/dev/shm` charged to the cgroup. `PrivateDevices` and `DevicePolicy=closed`
remove GPU devices. Broker, SSH, container-runtime, D-Bus, systemd, log, and
owner-listed model paths are inaccessible. `NoNewPrivileges`, an empty capability
bounding set, `RestrictNamespaces`, and the `@system-service` minus
`@privileged @resources` syscall filter apply. Host processes are hidden with
`ProtectProc=invisible` and the user namespace, **not** a separate PID namespace
(systemd 249 on Ubuntu 22.04 has no `PrivatePIDs=`); qualification measures that
the model PID is invisible. The kernel files `cpu.max`, `memory.max`,
`memory.swap.max`, and `pids.max` and the systemd properties must read back
exactly or the runner never receives `GO`.

**Broker behaviour.** The broker shares the host mount namespace, so its per-run
256 MiB `noexec` tmpfs is visible to the unit it starts. It stages exactly one
commit with `git fetch --depth=1` (hooks disabled, HTTPS only, 30 seconds total)
and verifies the object ID. The build performs no dependency install. Collection
opens files without following links, accepts only flat `*.zip` archives and the
manifest, and builds a deterministic `packages.tar`. Status byte `0` precedes a
contract-checked v1 envelope (PASS or BLOCKED result); `1` precedes a BLOCKED
notice with reason codes only. A model-identity change, unverified readback, a
still-present unit, or a rejected output file yields no envelope. Every outcome
leaves a root-only broker receipt outside payload control. The claim stays
consumed after any failure, and nothing retries.

**Python.** Ubuntu 22.04 ships Python 3.10. The VM components and the full test
suite are exercised on 3.10; `tests/test_ssh_ci_workflow.py` no longer uses the
3.11-only `contextlib.chdir`, which previously failed the in-VM `unittest` step.

## Admission primitives

These pure modules are the building blocks the broker composes. Their tests,
like every hosted test here, are offline and do not enable the manual workflow.

`tools/ssh_ci_plan.py` reconstructs the exact controller request from a supplied
GitHub run API response while that run is waiting for environment approval:

```sh
python -B tools/ssh_ci_plan.py --run-metadata waiting-run.json --permit-id CPU-PERMIT-UUID
```

It accepts only this repository's numeric ID and name, the exact manual workflow,
`main`, attempt one, a full source SHA, and `waiting` status. It shares the actual
client request builder, including the deterministic request UUID, and emits its
canonical digest. Input must be a bounded regular file. The output is DECLARED:
local JSON is not authenticated GitHub evidence. It issues no permit, approval,
claim, or network call. An authorized operator must independently authenticate
the waiting run and protected environment before using that digest.

`tools/ssh_ci_admission.py::validate_permit` requires a distinct closed
`szl.ssh-cpu-build.permit.v1` object bound to that digest and permit UUID, one run,
the fixed CPU profile, and the exact hostname/boot ID. The UTC window is at most
900 seconds; admission requires at least 450 seconds remaining (420 execution
plus 30 margin). Its separate strict observation must be no more than five
seconds old, not future-dated, and show no competing CPU job. The permitted GPU
state is either empty or exactly one explicitly pinned PID/start-ticks pair.
Unknown fields, duplicate JSON keys, legacy GPU grants, mismatched identities,
and stale observations are rejected. The function does no I/O: it cannot
authenticate an owner, clock, observation, or isolation boundary.

`tools/ssh_ci_claims.py::ClaimStore` is the local durable-consumption primitive.
All trusted brokers must use one pre-provisioned root-owned claim directory with
safe root-owned ancestors and final mode `0700` or `0750`. Directory components
are opened without following symlinks; lock/claim files are regular, single-link,
root-owned `0600` files. A nonblocking `flock` is held for the whole context.
Canonical permit UUIDs name exclusive-create claims; file and directory `fsync`
complete before success. Claims persist after success, failure, disconnect, or
partial write. Never delete them to retry. Windows and unsupported filesystems
fail closed. This lock is advisory, not a provider-enforced CPU/GPU reservation.

The broker acquires that context, loads the owner-signed permit from the
root-owned spool, obtains a fresh observation, validates, durably claims,
rechecks deadline and workload, and only then executes, keeping the context until
cleanup and receipt collection finish. Passing caller-provided JSON to these
libraries directly does not establish authority.

`tools/ssh_ci_executor.py::execute` supplies this ordering as a pure,
dependency-injected state machine, which the broker drives. It requires an
`OwnerPermit` loader result whose authentication was performed outside the
module; then it observes and validates, rejects idle permits while the v1
receipt still requires positive process identity, acquires the claim context,
claims once, re-observes/revalidates under the lock, and invokes the injected
executor. Loader, observer, claim-store, revalidation, and executor failures
return explicit BLOCKED reason codes. A failed revalidation or executor leaves
the durable claim consumed; no retry is offered. The returned executor mapping
is only a bounded local receipt handoff and is not promoted to a verified v1
remote receipt or independent witness.

`tools/ssh_ci_owner_window.py::validate_owner_window` is the closed envelope
that the signed bundle carries alongside the permit. It binds the exact
request and permit digest, a fresh <=900-second window with >=450 seconds
remaining, Massed Compute instance and host/boot identity, execution ID and run
nonce, GitHub source SHA and admitted HF revision, the fixed CPU profile, a
bounded budget-ledger reference, checkpoint-manifest digest, and an explicit
no-GPU/no-model/no-training/no-inference workload. GPU-grant-shaped objects,
unknown revisions, missing backup/budget references, duplicate members, and
scope-expanding fields are rejected. This validator does not read a provider ledger;
the signature loader authenticates the owner before the executor sees the
envelope, and the budget and checkpoint fields remain the owner's attestations.
No owner window exists until the owner issues one for a specific waiting run.

**Idle-host execution is still BLOCKED.** The permit validator can describe an
idle host, but the existing v1 remote receipt contract requires positive pinned
model PID/start-ticks fields. Supporting an idle host end to end requires a
versioned receipt contract and qualification; do not invent a PID or relax v1.

The hosted Linux contract job additionally runs real root-owned claim-store,
signature-spool, and broker integration tests under fresh `/run` test
directories on its disposable runner (real `ssh-keygen`, faked systemd unit). Windows
explicitly skips POSIX cases and tests unsupported-platform denial. Neither test
job connects to the VM, receives its credentials, or grants live execution.

## Operator runbook

Run every VM step yourself, inside one owner-approved window, from the
workstation that holds the verified admin key and host pin (or a ThinLinc
terminal). Nothing here touches the model process or the GPU.

1. **Keys (workstation).** Create a dedicated CI key and a separate owner
   permit-signing key; never reuse the admin key for either.

   ```sh
   ssh-keygen -t ed25519 -N "" -C szl-cpu-ci -f szl-cpu-ci
   ssh-keygen -t ed25519 -C stephen-szl-permit -f szl-owner-permit
   ```

2. **Provision (VM, as root).** From a clean checkout of the merged `main`
   commit, plan first; apply only if the plan is ready and the window is approved.

   ```sh
   sudo python3 -I -B tools/ssh_ci_provision.py plan --release-sha <main-sha> \
     --ci-public-key szl-cpu-ci.pub --owner-public-key szl-owner-permit.pub \
     --admin-user "$USER" --hidden-path <model-or-data-dir>
   sudo python3 -I -B tools/ssh_ci_provision.py apply <same flags> \
     --owner-window-ref <your approval reference>
   ```

   `apply` refuses unmanaged files, validates `sshd -t`, checks the `szl-ci`
   effective settings, compares the admin user's effective sshd settings before
   and after (rolling back on any change), reloads `ssh`, and enables the socket.
   `uninstall` removes the SSH entry point and socket and keeps all receipts.

3. **Qualify (VM, as root).** Rehearse the real build of the same commit.

   ```sh
   sudo python3 -I -B /opt/szl-cpu-broker/current/tools/ssh_ci_qualify.py \
     --rehearse-build <main-sha> --owner-window-ref <reference>
   ```

   Proceed only on `"result": "QUALIFIED"`. A readback mismatch (for example a
   systemd property printed differently on this host) is a reviewed source
   change, never a local relaxation. Model paths found in the model process's
   memory maps are probed automatically; add any that are visible with
   `--hidden-path` and re-provision.

4. **GitHub environment (workstation, owner login).** Create `massed-cpu-build`
   with yourself as required reviewer and protected-branch deployment only, then
   add the secret and variables. Set `SZL_CPU_CI_ENABLED` last.

   ```sh
   gh api -X PUT repos/szl-holdings/szl-skills/environments/massed-cpu-build \
     --input - <<'JSON'
   {"reviewers":[{"type":"User","id":<your-numeric-user-id>}],
    "prevent_self_review":false,
    "deployment_branch_policy":{"protected_branches":true,"custom_branch_policies":false}}
   JSON
   gh secret set SZL_CPU_SSH_KEY --env massed-cpu-build -R szl-holdings/szl-skills < szl-cpu-ci
   gh variable set SZL_CPU_SSH_HOST --env massed-cpu-build -R szl-holdings/szl-skills -b <vm-ip>
   gh variable set SZL_CPU_SSH_PORT --env massed-cpu-build -R szl-holdings/szl-skills -b 22
   gh variable set SZL_CPU_SSH_USER --env massed-cpu-build -R szl-holdings/szl-skills -b szl-ci
   gh variable set SZL_CPU_KNOWN_HOSTS --env massed-cpu-build -R szl-holdings/szl-skills \
     -b "<the independently verified host-key line>"
   gh variable set SZL_CPU_CI_ENABLED --env massed-cpu-build -R szl-holdings/szl-skills -b true
   ```

   Then delete the local copy of the CI private key.

5. **One build (workstation).** Write your own attestations to an owner config
   (`szl.ssh-cpu-build.owner-config.v1`: provider instance UUID, admitted HF
   revision, budget ledger reference and remaining seconds/USD, checkpoint
   manifest SHA-256, owner receipt reference). The tool never invents them.

   ```sh
   python tools/ssh_ci_owner_issue.py run --owner-config owner-cpu-window.json \
     --signing-key szl-owner-permit --admin-target Ubuntu@<vm-ip> \
     --admin-known-hosts <pinned-known-hosts>
   ```

   It refuses without a required-reviewer gate, with zero or several GPU compute
   processes (the v1 receipt pins exactly one), or when more than 300 seconds
   pass between observation and approval. It approves only the run whose title
   carries the fresh permit, then downloads and re-hashes the evidence.

6. Only after one such run returns PASS with matching hashes and an unchanged
   model identity, describe the lane as operational for that qualified CPU
   profile and source. Training, inference, arbitrary workloads, and publication
   remain separate authorities.
