import json
import socket
import unittest
import threading

from src.p2p_forca.peer_connection import PeerConnection


class TestPeerConnection(unittest.TestCase):

    def setUp(self):
        self.sock1, self.sock2 = socket.socketpair()
        self.connection = PeerConnection(
            self.sock1,
            ("localhost", 8080)
        )

    def tearDown(self):
        self.connection.close()
        self.sock2.close()

    def test_send_message(self):
        message = {
            "type": "PING",
            "src": "alice",
            "dst": "bob"
        }

        self.connection.send_message(message)

        received = self.sock2.recv(4096)
        data = json.loads(received.decode("utf-8"))

        self.assertEqual(data["type"], "PING")
        self.assertEqual(data["src"], "alice")
        self.assertEqual(data["dst"], "bob")
        self.assertEqual(data["ttl"], 1)
        self.assertIn("msg_id", data)

    def test_message_uses_json_line(self):
        message = {"type": "HELLO"}

        self.connection.send_message(message)

        received = self.sock2.recv(4096)

        self.assertTrue(received.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()

import threading


class TestPeerReceive(unittest.TestCase):

    def test_receive_message(self):
        sock1, sock2 = socket.socketpair()
        received_messages = []
        event = threading.Event()

        def on_message(connection, message):
            received_messages.append(message)
            event.set()

        connection = PeerConnection(
            sock1,
            ("localhost", 8080),
            on_message=on_message
        )

        try:
            connection.start()

            message = {
                "type": "HELLO",
                "src": "alice",
                "dst": "bob"
            }

            sock2.sendall(
                (json.dumps(message) + "\n").encode("utf-8")
            )

            self.assertTrue(event.wait(timeout=2))
            self.assertEqual(received_messages[0]["type"], "HELLO")
            self.assertEqual(received_messages[0]["src"], "alice")

        finally:
            connection.close()
            sock2.close()


class TestPeerReceive(unittest.TestCase):

    def test_receive_message(self):
        sock1, sock2 = socket.socketpair()
        received_messages = []
        event = threading.Event()

        def on_message(connection, message):
            received_messages.append(message)
            event.set()

        connection = PeerConnection(
            sock1,
            ("localhost", 8080),
            on_message=on_message
        )

        try:
            connection.start()

            message = {
                "type": "HELLO",
                "src": "alice",
                "dst": "bob"
            }

            sock2.sendall(
                (json.dumps(message) + "\n").encode("utf-8")
            )

            self.assertTrue(event.wait(timeout=2))
            self.assertEqual(received_messages[0]["type"], "HELLO")
            self.assertEqual(received_messages[0]["src"], "alice")

        finally:
            connection.close()
            sock2.close()