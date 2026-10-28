"""Offline forced-command relay tests over local socket pairs and pipes."""

import io
import os
import socket
import threading
import unittest

from tools import ssh_ci_relay as relay


POSIX = os.name == "posix"


def broker_pair(reply, received):
    left, right = socket.socketpair()

    def serve():
        data = bytearray()
        while True:
            chunk = right.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
        received.append(bytes(data))
        right.sendall(reply)
        right.close()
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    return left, worker


class RelayTests(unittest.TestCase):
    def test_only_the_literal_forced_command_is_admitted(self):
        for command in (None, "", "bash", "szl-cpu-build-v1 --shell", "SZL-CPU-BUILD-V1"):
            environ = {} if command is None else {"SSH_ORIGINAL_COMMAND": command}
            with self.subTest(command=command):
                self.assertEqual(relay.main(environ, output=io.BytesIO(),
                                            connect=lambda: self.fail("must not connect")), 2)

    def test_status_byte_is_stripped_and_body_relayed(self):
        for status, body in ((b"0", b'{"schema":"envelope"}'), (b"1", b'{"result":"BLOCKED"}')):
            received = []
            client, worker = broker_pair(status + body, received)
            output = io.BytesIO()
            self.assertEqual(relay.relay(b'{"request":1}', output, connect=lambda: client), status)
            worker.join(5)
            self.assertEqual(received, [b'{"request":1}'])
            self.assertEqual(output.getvalue(), body)

    def test_invalid_status_and_oversized_reply_fail_closed(self):
        for reply, limit in ((b"X{}", 100), (b"0" + b"x" * 101, 100), (b"", 100)):
            client, worker = broker_pair(reply, [])
            with self.subTest(reply=reply[:5]), self.assertRaises(ValueError):
                relay.relay(b"{}", io.BytesIO(), connect=lambda: client, limit=limit)
            worker.join(5)

    @unittest.skipUnless(POSIX, "select() on pipes is POSIX-only")
    def test_request_is_bounded_and_read_to_eof(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"x" * relay.MAX_REQUEST_BYTES)
        os.close(write_fd)
        self.assertEqual(len(relay.read_request(read_fd)), relay.MAX_REQUEST_BYTES)
        os.close(read_fd)
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"x" * (relay.MAX_REQUEST_BYTES + 1))
        os.close(write_fd)
        with self.assertRaisesRegex(ValueError, "limit"):
            relay.read_request(read_fd)
        os.close(read_fd)

    @unittest.skipUnless(POSIX, "select() on pipes is POSIX-only")
    def test_stalled_stdin_hits_deadline(self):
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(ValueError, "deadline"):
                relay.read_request(read_fd, seconds=0.05)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    @unittest.skipUnless(POSIX, "select() on pipes is POSIX-only")
    def test_broker_unavailable_is_blocked_exit(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"{}")
        os.close(write_fd)

        def unavailable():
            raise FileNotFoundError("no broker socket")
        try:
            code = relay.main({"SSH_ORIGINAL_COMMAND": relay.FORCED_COMMAND}, stdin_fd=read_fd,
                              output=io.BytesIO(), connect=unavailable)
        finally:
            os.close(read_fd)
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
