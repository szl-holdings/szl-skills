#!/usr/bin/env python3
"""Forced-command relay for the dedicated CPU-build SSH identity.

sshd runs this file as the unprivileged CI account for every session. It admits
only the literal command ``szl-cpu-build-v1`` and one bounded request on stdin,
forwards it to the root broker's socket, and streams back one bounded response.
It never runs a shell, a build, or anything named by the client. Exit status 0
means a contract envelope was relayed; it is not a PASS by itself.
"""

import os
import select
import socket
import sys
import time


FORCED_COMMAND = "szl-cpu-build-v1"
BROKER_SOCKET = "/run/szl-cpu-broker.sock"
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
STDIN_SECONDS = 30
BROKER_SECONDS = 620  # Outlives the client's 600 s deadline.


def read_request(fd, limit=MAX_REQUEST_BYTES, seconds=STDIN_SECONDS, clock=time.monotonic):
    """Read stdin to EOF within a deadline; refuse anything over the limit."""
    deadline = clock() + seconds
    data = bytearray()
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("request stdin deadline exceeded")
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(fd, limit + 1 - len(data))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("request exceeds the fixed byte limit")


def relay(request, output, *, connect=None, path=BROKER_SOCKET, seconds=BROKER_SECONDS,
          limit=MAX_RESPONSE_BYTES):
    """Forward one request and copy the framed reply; return the status byte."""
    if connect is None:
        def connect():
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(seconds)
            client.connect(path)
            return client
    client = connect()
    try:
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        status = client.recv(1)
        if status not in (b"0", b"1"):
            raise ValueError("broker reply has no valid status byte")
        relayed = 0
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            relayed += len(chunk)
            if relayed > limit:
                raise ValueError("broker reply exceeds the response limit")
            output.write(chunk)
        output.flush()
        return status
    finally:
        client.close()


def main(environ=None, stdin_fd=0, output=None, connect=None):
    environ = os.environ if environ is None else environ
    output = sys.stdout.buffer if output is None else output
    if environ.get("SSH_ORIGINAL_COMMAND") != FORCED_COMMAND:
        print("BLOCKED: only " + FORCED_COMMAND + " is admitted", file=sys.stderr)
        return 2
    try:
        request = read_request(stdin_fd)
        status = relay(request, output, connect=connect)
    except (OSError, ValueError) as error:
        print("BLOCKED: relay failed: " + type(error).__name__, file=sys.stderr)
        return 3
    return 0 if status == b"0" else 1


if __name__ == "__main__":
    raise SystemExit(main())
