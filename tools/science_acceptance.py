#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run reviewed synthetic acceptance tests under Linux Landlock and seccomp.

Fails closed when the required OS controls are unavailable. No dependencies installed.
Only the explicit science package/test inventory is copied; upstream suites are not run.
"""
import argparse
import contextlib
import ctypes
import copy
import datetime
import decimal
import errno
import hashlib
import importlib.util
import io
import json
import math
import os
import pathlib
import random
import re
import resource
import runpy
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import unittest

PACKAGES = (
    "szl-dataset-readiness", "szl-paired-science", "szl-model-evaluation",
    "szl-reproducibility-capsule", "szl-math-claim-check", "szl-research-anatomy",
    "szl-artifact-lineage", "szl-unit-invariants", "szl-negative-control-audit",
    "szl-analysis-plan-audit",
)
TESTS = (
    "test_science_binding.py", "test_science_attempts_replay.py",
    "test_science_scope_ledger.py", "test_science_lineage_units.py",
    "test_science_plan_controls.py", "test_science_forward.py",
)


def install_controls(stage, stdlib):
    if sys.platform != "linux" or os.getuid() == 0:
        raise RuntimeError("Requires an existing non-root Linux runtime")
    libc = ctypes.CDLL(None, use_errno=True)
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "no_new_privs")
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 1:
        raise RuntimeError("Landlock ABI unavailable")
    mask = (1 << 13) - 1
    if abi >= 2:
        mask |= 1 << 13
    if abi >= 3:
        mask |= 1 << 14

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    attrs = Ruleset(mask)
    ruleset = libc.syscall(444, ctypes.byref(attrs), ctypes.sizeof(attrs), 0)
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "Landlock create")
    try:
        for root, access in ((stage, mask), (stdlib, (1 << 2) | (1 << 3))):
            fd = os.open(str(root), os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(access, fd)
                if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) < 0:
                    raise OSError(ctypes.get_errno(), "Landlock path rule")
            finally:
                os.close(fd)
        if libc.syscall(446, ruleset, 0) < 0:
            raise OSError(ctypes.get_errno(), "Landlock restrict")
    finally:
        os.close(ruleset)

    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7fff0000)
    if not context:
        raise RuntimeError("seccomp context unavailable")
    deny = ("socket", "socketpair", "connect", "bind", "listen", "accept",
            "accept4", "sendto", "sendmsg", "recvfrom", "recvmsg",
            "execve", "execveat", "fork", "vfork", "clone", "clone3",
            "ptrace", "process_vm_readv", "process_vm_writev", "open_by_handle_at",
            "mount", "umount2", "pivot_root", "setns", "unshare", "bpf",
            "io_uring_setup", "keyctl", "add_key", "request_key")
    try:
        for name in deny:
            syscall = seccomp.seccomp_syscall_resolve_name(name.encode())
            if syscall >= 0 and seccomp.seccomp_rule_add(context, 0x50000 | errno.EPERM, syscall, 0) != 0:
                raise RuntimeError("seccomp rule failed: " + name)
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError("seccomp load failed")
    finally:
        seccomp.seccomp_release(context)
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 ** 2, 512 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 ** 2, 2 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return {"landlock_abi": abi, "seccomp": "ENFORCED", "no_new_privs": True,
            "uid": os.getuid(), "network": "DENIED", "child_process": "DENIED",
            "outside_files": "DENIED", "memory_bytes": 512 * 1024 ** 2,
            "cpu_seconds": 60, "file_size_bytes": 2 * 1024 ** 2}


def stage_inputs(source, stage, packages, tests):
    inventory = []
    selected = []
    for name in packages:
        folder = source / "skills" / name
        if not (folder / "SKILL.md").is_file():
            raise ValueError("Package incomplete: " + name)
        selected.extend(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    selected.extend(source / "tests" / name for name in tests)
    if len(selected) > 256 or sum(p.stat().st_size for p in selected) > 4 * 1024 ** 2:
        raise ValueError("Acceptance inventory exceeds bounded snapshot")
    for path in sorted(selected):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Only regular reviewed input files")
        relative = path.relative_to(source)
        content = path.read_bytes()
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        inventory.append({"path": relative.as_posix(), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    return inventory


def run(source, probe_only=False, scope="all", tests=None, export_fixture=False):
    stage = pathlib.Path(tempfile.mkdtemp(prefix="szl-science-acceptance-"))
    packages = PACKAGES[:6] if scope == "upgrades" else PACKAGES
    chosen_tests = tests or (TESTS[:3] if scope == "upgrades" else TESTS[:5])
    inventory = [] if probe_only else stage_inputs(source, stage, packages, chosen_tests)
    outside = stage.parent / (stage.name + "-fake-secret")
    outside.write_text("SYNTHETIC_CANARY_ONLY", encoding="utf-8")
    os.environ.clear()
    os.environ["TMPDIR"] = str(stage / "tmp")
    (stage / "tmp").mkdir()
    tempfile.tempdir = str(stage / "tmp")
    os.chdir(stage)
    controls = install_controls(stage, pathlib.Path(os.__file__).parent)
    probes = {}
    for name, action in (
        ("outside_fake_secret", lambda: outside.read_bytes()),
        ("host_mount", lambda: pathlib.Path("/mnt/c/Windows/win.ini").read_bytes()),
        ("network_socket", lambda: socket.socket()),
        ("child_process", lambda: subprocess.Popen(["/bin/true"])),
    ):
        try:
            action()
            probes[name] = "FAILED_OPEN"
        except PermissionError:
            probes[name] = "DENIED"
    if any(value != "DENIED" for value in probes.values()):
        raise RuntimeError("OS isolation preflight failed")
    if probe_only:
        print(json.dumps({"status": "CONTROLS_VERIFIED", "controls": controls,
                          "preflight": probes, "candidate_execution": "NOT_RUN"}, indent=2))
        return 0
    suite = unittest.TestSuite()
    modules = {}
    for name in chosen_tests:
        path = stage / "tests" / name
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[path.stem] = module
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    output = io.StringIO()
    emitted = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stdout(emitted), contextlib.redirect_stderr(emitted):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    exports = {}
    if export_fixture and result.wasSuccessful():
        builder = modules['test_science_binding']
        fixture = builder.experiment_v2()
        digests = json.loads(fixture['bindings']['plan']['utf8'])['artifact_digests']
        exports = {'example-v2.json': fixture,
                   'fixture-lock.json': {'expected_manifest_sha256': builder.hash_object(digests),
                                         'scope': 'SYNTHETIC', 'signed': False}}
    report = {"schema": "szl.science-acceptance.v1", "status": "PASS" if result.wasSuccessful() else "FAIL",
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": result.skipped, "seconds": round(time.monotonic() - started, 3),
              "controls": controls, "preflight": probes, "input_inventory": inventory,
              "test_output": output.getvalue(), "snapshot_path": str(stage),
              "emitted_test_output": emitted.getvalue(),
              "scientific_performance": "NOT_MEASURED", "model_behavior": "NOT_MEASURED",
              "scope": "Reviewed synthetic helper tests; no hostile-code certification or supplied-command replay"}
    if exports:
        report['exports'] = exports
    print(json.dumps(report, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--scope", choices=("all", "upgrades"), default="all")
    parser.add_argument("--tests", nargs="+", choices=TESTS)
    parser.add_argument("--export-paired-fixture", action="store_true")
    args = parser.parse_args()
    try:
        sys.exit(run(args.source.resolve(strict=True), args.probe_only, args.scope, args.tests,
                     args.export_paired_fixture))
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "BLOCKED", "behavioral_execution": "NOT_RUN", "reason": str(error)}))
        sys.exit(2)
