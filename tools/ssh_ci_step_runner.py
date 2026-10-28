"""Fixed in-sandbox step runner for the CPU build profile.

The broker launches this trusted, root-owned file inside the transient build
unit. It waits for the broker's ``GO`` line, which is sent only after cgroup and
sandbox readback pass, then copies the staged read-only source into private
scratch, checks the exact commit and clean tree, and runs the four fixed steps.
It accepts no command, branch, or repository from the request. Its stdout is
one bounded JSON line for the broker; step output goes to bounded files.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading


RUNNER_SCHEMA = "szl.ssh-cpu-build.runner.v1"
GO = b"GO\n"
STEP_NAMES = ("selfcheck", "sync", "unittest", "package")
STREAM_LIMIT = 1024 * 1024
NOT_RUN = -1
GITCONFIG = "[safe]\n\tdirectory = {work}\n[user]\n\tname = szl-cpu-build\n\temail = szl-cpu-build@invalid\n"


def steps(python, source_sha, out):
    """Return the only admitted commands; arguments are fixed or validated."""
    return (
        ("selfcheck", [python, "-B", "tools/selfcheck.py"]),
        ("sync", [python, "-B", "tools/sync_workbench.py", "--check"]),
        ("unittest", [python, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("package", [python, "-B", "tools/package_science.py", "--output-dir",
                     str(Path(out) / "packages"), "--revision", source_sha, "--manifest"]),
    )


def _copy_bounded(stream, path, limit, truncated, name):
    with open(path, "xb") as output:
        kept = 0
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            room = limit - kept
            if room:
                output.write(chunk[:room])
                kept += min(room, len(chunk))
            if len(chunk) > room and name not in truncated:
                # Keep draining so the child never blocks on a full pipe.
                truncated.append(name)


def run_step(name, argv, cwd, out, environment, limit=STREAM_LIMIT, popen=subprocess.Popen):
    """Run one fixed step with bounded stdout/stderr files; return (code, truncated)."""
    truncated = []
    process = popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    workers = [threading.Thread(target=_copy_bounded, daemon=True,
                                args=(process.stdout, Path(out) / (name + ".stdout"), limit,
                                      truncated, name + ".stdout")),
               threading.Thread(target=_copy_bounded, daemon=True,
                                args=(process.stderr, Path(out) / (name + ".stderr"), limit,
                                      truncated, name + ".stderr"))]
    for worker in workers:
        worker.start()
    code = process.wait()
    for worker in workers:
        worker.join()
    process.stdout.close()
    process.stderr.close()
    return code, truncated


def prepare_work(source, work, source_sha, environment, run=subprocess.run):
    """Copy staged source into private scratch and require the exact clean commit."""
    shutil.copytree(source, work, symlinks=True)
    head = run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=work, env=environment,
               capture_output=True, check=False, timeout=30)
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=work,
                 env=environment, capture_output=True, check=False, timeout=60)
    if (head.returncode != 0 or head.stdout.decode("ascii", "replace").strip() != source_sha
            or status.returncode != 0 or status.stdout):
        raise RuntimeError("staged source is not the exact clean commit")


def main(argv=None, stdin=None, stdout=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source", default="/src")
    parser.add_argument("--out", default="/out")
    parser.add_argument("--work", default="/tmp/work")
    parser.add_argument("--home", default="/tmp/home")
    args = parser.parse_args(argv)
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    result = {"schema": RUNNER_SCHEMA, "started": False, "prepared": False,
              "steps": [{"name": name, "returncode": NOT_RUN} for name in STEP_NAMES],
              "truncated": [], "failure": None}

    def finish(code):
        stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        stdout.flush()
        return code

    if re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is None:
        result["failure"] = "INVALID_SOURCE_SHA"
        return finish(2)
    # The broker sends GO only after readback; EOF or anything else is a denial.
    if stdin.read(len(GO)) != GO:
        result["failure"] = "NO_GO"
        return finish(125)
    result["started"] = True
    home = Path(args.home)
    home.mkdir(mode=0o700, parents=True, exist_ok=False)
    (home / ".gitconfig").write_text(GITCONFIG.format(work=args.work), encoding="ascii")
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(Path(args.work).parent),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"), "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        prepare_work(args.source, args.work, args.source_sha, environment)
    except Exception as error:
        result["failure"] = "PREPARE_FAILED:" + type(error).__name__
        return finish(3)
    result["prepared"] = True
    worst = 0
    for index, (name, command) in enumerate(steps(sys.executable, args.source_sha, args.out)):
        try:
            code, truncated = run_step(name, command, args.work, args.out, environment)
        except Exception as error:
            result["failure"] = "STEP_FAILED:" + name + ":" + type(error).__name__
            return finish(4)
        result["steps"][index]["returncode"] = code
        result["truncated"].extend(truncated)
        worst = worst or (code != 0)
    return finish(1 if worst or result["truncated"] else 0)


if __name__ == "__main__":
    raise SystemExit(main())
